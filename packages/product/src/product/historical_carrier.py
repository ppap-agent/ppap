"""Queue a frozen carrier round without publishing that carrier as a head.

New admission still refuses ``from audit.standard_library import``. These
helpers only store the bytes and replay the snapshot the caller already froze.
"""

from __future__ import annotations

import json
import uuid

from audit.checker_core import ExecutionSnapshot, FrozenInput, RuleBinding, checker_digest, runtime_revision
from audit.standard_suites import StandardSuite, library_revision, standard_suites
from psycopg.types.json import Jsonb

from product.store.suites import SuiteStore, freeze_execution


def queue_historical_carrier_round(store, principal, items: list[dict]) -> dict:
    """Insert one queued audit for carrier bytes that are not the current head.

    Each item names an uploaded version, its role, and, for a target, the
    carrier ``suite`` plus the rule row to show on the round. A reference item
    is frozen as input only. Related carrier revisions are stored for the
    digest check and are not selected.
    """
    suites = SuiteStore(store.settings, store.objects, role="migrator")
    catalog = {row.suite_key: row for row in standard_suites()}
    stored: dict[str, str] = {}

    def store_carrier(suite: StandardSuite, files: dict[str, bytes]) -> str:
        revision = suites.create_revision(
            tenant_id=None,
            suite_key=suite.suite_key,
            files=files,
            created_by=None,
            related_suite_keys=suite.related_suite_keys,
        )
        return revision.id

    def related_digests(suite: StandardSuite) -> dict[str, str]:
        digests = {}
        for key in suite.related_suite_keys:
            if key not in stored:
                related = catalog[key]
                stored[key] = store_carrier(related, dict(related.files))
            resolved = suites.resolve_suite(stored[key], principal.tenant_id)
            digests[key] = resolved.revision.resolved_digest
        return digests

    prepared = []
    for item in items:
        with store._conn() as conn:
            row = conn.execute(
                "SELECT sha256, original_filename, file_id::text FROM product.file_versions WHERE id=%s AND tenant_id=%s AND status='ready'",
                (item["version_id"], principal.tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError("historical_file_missing")
        suite = item.get("suite")
        rules = None
        target = None
        if item["role"] == "target":
            if not isinstance(suite, StandardSuite):
                raise ValueError("historical_suite_required")
            rules = dict(item["rule_body"])
            rules["checker_binding"] = dict(suite.entrypoints)
            files = dict(suite.files)
            files["rules.json"] = json.dumps(rules, ensure_ascii=False).encode()
            revision_id = store_carrier(suite, files)
            resolved = suites.resolve_suite(revision_id, principal.tenant_id)
            code = checker_digest({path: body for path, body in resolved.files.items() if path in {"checker.py", "resources.py"}})
            binding = RuleBinding(resolved.revision.suite_key, code, dict(suite.entrypoints))
            target = freeze_execution(
                suite=resolved,
                file_version_id=item["version_id"],
                binding=binding,
                validation=None,
                related_suite_digests=related_digests(suite),
                library_revision=library_revision(),
                allow_unverified=True,
            )
            rules = json.loads(resolved.files["rules.json"])
        prepared.append((item, row, rules, target))

    identity = store.model_identity(principal.tenant_id)
    library = library_revision()
    round_id = str(uuid.uuid4())
    snapshot = ExecutionSnapshot(
        principal.tenant_id,
        principal.user_id,
        tuple(
            FrozenInput(
                item["version_id"],
                row[0],
                item["role"],
                row[1],
                item.get("document_type") or (item.get("rule_body") or {}).get("type"),
                row[2],
            )
            for item, row, _rules, _target in prepared
        ),
        tuple(target for _item, _row, _rules, target in prepared if target is not None),
        runtime_revision(store.settings.deep_execution_revision(identity), library),
        library,
        identity,
        round_id,
    )
    job_id = str(uuid.uuid4())
    payload = {
        "round_id": round_id,
        "executor": "ppap-checker-core",
        "executor_revision": snapshot.runtime_revision,
        "checker_snapshot_digest": snapshot.input_digest,
        "model_identity": dict(identity),
    }
    with store._conn() as conn, conn.transaction():
        conn.execute("INSERT INTO product.resources(id, tenant_id, kind) VALUES (%s, %s, 'audit_round')", (round_id, principal.tenant_id))
        conn.execute(
            """INSERT INTO product.audit_rounds(id, tenant_id, created_by, idempotency_key, request_fingerprint, execution, analysis_mode)
               VALUES (%s, %s, %s, %s, 'historical-carrier', 'queued', 'standard')""",
            (round_id, principal.tenant_id, principal.user_id, job_id),
        )
        for item, _row, rules, _target in prepared:
            conn.execute(
                """INSERT INTO product.round_inputs(round_id, file_version_id, role, rule_version_id, rule_title, rule_body)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    round_id,
                    item["version_id"],
                    item["role"],
                    item.get("rule_id"),
                    item.get("rule_title"),
                    Jsonb(rules) if rules is not None else None,
                ),
            )
        suites.save_snapshot(conn, snapshot=snapshot)
        conn.execute(
            "INSERT INTO product.resource_grants(resource_id, user_id, capability) VALUES (%s, %s, 'read'), (%s, %s, 'review')",
            (round_id, principal.user_id, round_id, principal.user_id),
        )
        conn.execute(
            "INSERT INTO engine.jobs(id, tenant_id, audit_round_id, kind, payload, status) VALUES (%s, %s, %s, 'audit', %s, 'queued')",
            (job_id, principal.tenant_id, round_id, Jsonb(payload)),
        )
    with store._conn() as conn:
        public = store._round_public(conn, round_id)
    if public is None:
        raise KeyError("historical_round_missing")
    return public
