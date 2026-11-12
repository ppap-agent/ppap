"""Suite persistence in the existing PostgreSQL and object-storage boundary."""

from __future__ import annotations

import json
import uuid
from contextlib import nullcontext

from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, ValidationRecord, checker_digest, rule_digest, runtime_revision
from psycopg.types.json import Jsonb

from product.db import connect
from product.files import ObjectExists, ObjectMissing
from product.storage import S3Objects
from product.suites import MAX_OBJECT_BYTES, UNIMPLEMENTED_CHECKER_SOURCE, ResolvedSuite, SuiteRevision, content_digest, is_unimplemented_suite, resolve_suite, resolved_digest, validate_content


class SuiteStore:
    def __init__(self, settings, objects=None, role: str = "api") -> None:
        self.settings, self.objects, self.role = settings, objects or S3Objects(settings), role

    def _conn(self):
        return connect(self.settings, self.role)

    def get_revision(self, revision_id: str, tenant_id: str, *, conn=None) -> SuiteRevision | None:
        with nullcontext(conn) if conn is not None else self._conn() as db:
            row = db.execute(
                """SELECT id, tenant_id, suite_key, related_suite_keys, base_revision_id, overrides, resolved_digest, created_by, revision_number
                   FROM product.suite_revisions WHERE id = %s AND (tenant_id IS NULL OR tenant_id = %s) AND NOT history_only""",
                (revision_id, tenant_id),
            ).fetchone()
        if row is None:
            return None
        return SuiteRevision(str(row[0]), str(row[1]) if row[1] else None, row[2], tuple(row[3]), str(row[4]) if row[4] else None, row[5], row[6], str(row[7]) if row[7] else None, row[8])

    def resolve_suite(self, revision_id: str, tenant_id: str, *, conn=None) -> ResolvedSuite:
        with nullcontext(conn) if conn is not None else self._conn() as db:

            def load_object(owner: str | None, digest: str) -> bytes:
                row = db.execute("SELECT object_key FROM product.suite_objects WHERE owner_scope = %s AND digest = %s", (owner or "official", digest)).fetchone()
                if row is None:
                    raise ValueError("suite_artifact_missing")
                try:
                    body = self.objects.get(row[0])
                except ObjectMissing:
                    raise ValueError("suite_artifact_missing") from None
                if len(body) > MAX_OBJECT_BYTES:
                    raise ValueError("suite_object_too_large")
                return body

            return resolve_suite(revision_id, tenant_id, load_revision=lambda identifier: self.get_revision(identifier, tenant_id, conn=db), load_object=load_object)

    def create_revision(
        self,
        *,
        tenant_id: str | None,
        suite_key: str,
        files: dict[str, bytes],
        created_by: str | None,
        base_revision_id: str | None = None,
        related_suite_keys: tuple[str, ...] = (),
        deleted_paths: tuple[str, ...] = (),
        source_path: str | None = None,
        conn=None,
    ) -> SuiteRevision:
        """Caller authenticates the tenant. Only the platform migrator may seed official assets.

        `files` contains changed paths only; inherited bytes are never stored again.
        The optional connection keeps candidate creation inside its owner's transaction.
        """
        if tenant_id is None and self.role != "migrator":
            raise PermissionError("official_suite_write_forbidden")
        if source_path is not None and (
            tenant_id is not None or not isinstance(source_path, str) or not source_path or source_path.strip() != source_path or "\x00" in source_path or len(source_path) > 512
        ):
            raise ValueError("invalid_suite_source_path")
        if tenant_id is not None and not created_by:
            raise ValueError("suite_creator_required")
        if set(deleted_paths) - {"resources.py"} or set(deleted_paths).intersection(files) or deleted_paths and base_revision_id is None:
            raise ValueError("invalid_suite_deleted_paths")
        with nullcontext(conn) if conn is not None else self._conn() as db, db.transaction():
            if tenant_id is None:
                db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("suite-publication:official",))
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("suite-revision:" + (tenant_id or "official") + ":" + suite_key,))
            number = db.execute(
                "SELECT coalesce(max(revision_number), 0)+1 FROM product.suite_revisions WHERE tenant_id IS NOT DISTINCT FROM %s::uuid AND suite_key=%s", (tenant_id, suite_key)
            ).fetchone()[0]
            lookup_tenant = tenant_id or "00000000-0000-0000-0000-000000000000"
            base = self.resolve_suite(base_revision_id, lookup_tenant, conn=db) if base_revision_id else None
            if base is not None and (base.revision.suite_key != suite_key or tenant_id is None and base.revision.tenant_id is not None):
                raise ValueError("invalid_suite_base")
            all_files, all_digests = (dict(base.files), dict(base.object_digests)) if base else ({}, {})
            overrides: dict[str, str | None] = {path: content_digest(body) for path, body in files.items() if path not in all_digests or content_digest(body) != all_digests[path]}
            all_files.update(files)
            all_digests.update({path: digest for path, digest in overrides.items() if digest is not None})
            for path in deleted_paths:
                if path not in all_files:
                    raise ValueError("suite_deleted_object_missing")
                overrides[path] = None
                del all_files[path]
                del all_digests[path]
            validate_content(all_files, official=tenant_id is None)
            revision = SuiteRevision(
                str(uuid.uuid4()), tenant_id, suite_key, tuple(related_suite_keys), base_revision_id, overrides, resolved_digest(suite_key, tuple(related_suite_keys), all_digests), created_by, number
            )
            for path, digest in overrides.items():
                if digest is None:
                    continue
                owner = tenant_id or "official"
                key = f"suites/{owner}/objects/{digest}"
                try:
                    self.objects.put_exclusive(key, files[path], "application/octet-stream")
                except ObjectExists:
                    if self.objects.get(key) != files[path]:
                        raise ValueError("suite_object_digest_mismatch") from None
                db.execute("INSERT INTO product.suite_objects (owner_scope, digest, object_key) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (owner, digest, key))
            db.execute(
                """INSERT INTO product.suite_revisions
                   (id,tenant_id,suite_key,related_suite_keys,base_revision_id,overrides,resolved_digest,created_by,revision_number,source_path)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    revision.id,
                    tenant_id,
                    suite_key,
                    list(revision.related_suite_keys),
                    base_revision_id,
                    Jsonb(dict(overrides)),
                    revision.resolved_digest,
                    created_by,
                    revision.revision_number,
                    source_path,
                ),
            )
            # The same bounded resolver used by execution rejects chains that grew too deep.
            self.resolve_suite(revision.id, lookup_tenant, conn=db)
            from product.store.suite_facts import derive_facts, insert_facts

            insert_facts(db, revision.id, tenant_id, derive_facts(all_files, source_path))
            return revision

    def official_revision_id_for_source(self, source_path: str, *, conn=None) -> str | None:
        """Latest official revision stored for one predefined directory path.

        Content digest is not part of the lookup, so ``uaes/U08`` and an identical
        leaf at another path stay distinct. Legacy asset paths are not stored.
        """
        if not isinstance(source_path, str) or not source_path or source_path.strip() != source_path:
            raise ValueError("invalid_suite_source_path")
        with nullcontext(conn) if conn is not None else self._conn() as db:
            row = db.execute(
                """SELECT id FROM product.suite_revisions
                   WHERE tenant_id IS NULL AND source_path=%s AND NOT history_only
                   ORDER BY revision_number DESC, id DESC LIMIT 1""",
                (source_path,),
            ).fetchone()
        return str(row[0]) if row else None

    def save_snapshot(self, conn, *, snapshot: ExecutionSnapshot) -> None:
        """Called inside acceptance, after audit_rounds exists and before its job is queued."""
        if not snapshot.round_id:
            raise ValueError("snapshot_round_required")
        conn.execute(
            """INSERT INTO product.checker_execution_snapshots (round_id,tenant_id,input_digest,body)
               SELECT id,tenant_id,%s,%s FROM product.audit_rounds WHERE id=%s AND tenant_id=%s""",
            (snapshot.input_digest, Jsonb(snapshot.to_dict()), snapshot.round_id, snapshot.tenant_id),
        )
        row = conn.execute("SELECT input_digest FROM product.checker_execution_snapshots WHERE round_id=%s AND tenant_id=%s", (snapshot.round_id, snapshot.tenant_id)).fetchone()
        if row is None or row[0] != snapshot.input_digest:
            raise ValueError("snapshot_round_mismatch")

    def read_snapshot(self, round_id: str, tenant_id: str, *, conn=None) -> ExecutionSnapshot | None:
        with nullcontext(conn) if conn is not None else self._conn() as db:
            row = db.execute("SELECT input_digest,body FROM product.checker_execution_snapshots WHERE round_id=%s AND tenant_id=%s", (round_id, tenant_id)).fetchone()
        if row is None:
            return None
        snapshot = ExecutionSnapshot.from_dict(row[1])
        if snapshot.input_digest != row[0] or snapshot.tenant_id != tenant_id or snapshot.round_id != round_id:
            raise ValueError("snapshot_digest_mismatch")
        return snapshot


def freeze_execution(
    *,
    suite: ResolvedSuite,
    file_version_id: str,
    binding: RuleBinding,
    validation: ValidationRecord | None,
    related_suite_digests: dict[str, str],
    library_revision: str,
    allow_unverified: bool = False,
) -> FrozenTarget:
    """Freeze one target after object authorization; aggregate targets into ExecutionSnapshot."""
    if is_unimplemented_suite(suite):
        # An uploaded rule is offered for audit only after its program is generated on the rule page.
        raise ValueError("checker_not_generated")
    revision = suite.revision
    rules = json.loads(suite.files["rules.json"])
    appendix = tuple(json.loads(suite.files.get("appendix.json", b"[]")))
    code_digest = checker_digest({path: body for path, body in suite.files.items() if path in {"checker.py", "resources.py"}})
    if binding.suite_key != revision.suite_key or binding.checker_digest != code_digest:
        raise ValueError("binding_suite_mismatch")
    if set(related_suite_digests) != set(revision.related_suite_keys):
        raise ValueError("related_suite_missing")
    rules_digest = rule_digest(rules, appendix)
    status = (
        validation.status(suite_digest=revision.resolved_digest, rule_digest=rules_digest, checker_digest=code_digest, related_suite_digests=related_suite_digests, library_revision=library_revision)
        if validation
        else {"state": "unverified", "samples_not_rerun": False}
    )
    verified = status["state"] == "verified" and binding.validated_rule_digest == rules_digest
    if not verified and not allow_unverified:
        raise ValueError("suite_validation_required")
    return FrozenTarget(
        file_version_id,
        revision.id,
        revision.suite_key,
        revision.resolved_digest,
        rules,
        binding,
        appendix,
        "verified" if verified else "unverified",
        related_suite_digests,
        bool(status["samples_not_rerun"]),
    )


def freeze_authorized_inputs(conn, principal, prepared: list[dict]) -> tuple[FrozenInput, ...]:
    """Consume only file rows already authorized by AuthStore._prepared_items."""
    inputs = []
    for item in prepared:
        row = conn.execute(
            "SELECT sha256,original_filename,file_id FROM product.file_versions WHERE id=%s AND tenant_id=%s AND status='ready'", (item["file_version_id"], principal.tenant_id)
        ).fetchone()
        if row is None or str(row[2]) != item["file_id"]:
            raise KeyError("snapshot_file_missing")
        # Do not infer access from tenant membership. Recheck the object grant here.
        allowed = conn.execute("SELECT 1 FROM product.resource_grants WHERE resource_id=%s AND user_id=%s AND capability IN ('read','review')", (item["file_id"], principal.user_id)).fetchone()
        if allowed is None:
            raise KeyError("snapshot_file_missing")
        metadata = item.get("frozen_metadata") or {}
        customers = (item.get("rule_body") or {}).get("customers") or []
        inputs.append(
            FrozenInput(
                item["file_version_id"],
                row[0],
                item["role"],
                row[1],
                item.get("document_type") or (item.get("rule_body") or {}).get("type"),
                item["file_id"],
                metadata.get("customer") or (customers[0] if len(customers) == 1 else None),
                metadata.get("model"),
                metadata.get("part"),
            )
        )
    return tuple(inputs)


def programless_suite_keys(conn, tenant_id: str, suite_keys) -> set[str]:
    """Adopted heads whose program is still the inert starter. Their rules cannot be chosen for audit."""
    keys = sorted(set(suite_keys))
    if not keys:
        return set()
    rows = conn.execute(
        """WITH RECURSIVE chain AS (
               SELECT h.suite_key, r.base_revision_id, r.overrides, 0 AS depth
               FROM product.tenant_suite_heads h JOIN product.suite_revisions r ON r.id = h.current_revision_id
               WHERE h.tenant_id = %s AND h.suite_key = ANY(%s)
               UNION ALL
               SELECT c.suite_key, r.base_revision_id, r.overrides, c.depth + 1
               FROM chain c JOIN product.suite_revisions r ON r.id = c.base_revision_id
           )
           SELECT DISTINCT ON (suite_key) suite_key, overrides->>'checker.py' FROM chain
           WHERE overrides ? 'checker.py' ORDER BY suite_key, depth""",
        (tenant_id, keys),
    ).fetchall()
    programs = {row[0]: row[1] for row in rows}
    return {key for key in keys if programs.get(key) in {None, content_digest(UNIMPLEMENTED_CHECKER_SOURCE)}}


def freeze_selected_targets(store, conn, tenant_id: str, prepared: list[dict], *, preparation: bool = False) -> tuple[FrozenTarget, ...]:
    """Resolve the same adopted immutable Suite for preparation and acceptance.

    Preparation may inspect unverified source without publishing it or approving
    formal use. Its verification status is preserved and it never grants billing.
    """
    from audit.standard_suites import library_revision

    from product.legacy_head_cutover import legacy_carrier_program
    from product.store.suite_releases import current_selection

    suites = SuiteStore(store.settings, store.objects, store.role)
    library = library_revision()
    targets = []
    for item in prepared:
        if item["role"] != "target":
            continue
        body = item.get("rule_body") or {}
        key = body.get("suite_key")
        if not key and body.get("rule_profile") == "v4" and len(body.get("customers") or []) == 1 and body.get("preset_key"):
            key = f"{body['customers'][0]}:{body['preset_key']}"
        if not key:
            raise ValueError("checker_suite_required")
        selection = current_selection(conn, tenant_id, key)
        suite = suites.resolve_suite(selection["revision_id"], tenant_id, conn=conn)
        # A persisted carrier head is not a directory source. Do not select another
        # revision, including another row with the same suite_key.
        if legacy_carrier_program(suite.files):
            raise ValueError("suite_version_confirmation_required")
        rules = json.loads(suite.files["rules.json"])
        appendix = tuple(json.loads(suite.files.get("appendix.json", b"[]")))
        # The accepted rule version is provenance. Actual rules are the adopted
        # immutable Suite, including enterprise edits, never the mutable preset.
        entrypoints = rules.get("checker_binding")
        if not isinstance(entrypoints, dict):
            raise ValueError("checker_binding_required")
        code = checker_digest({path: value for path, value in suite.files.items() if path in {"checker.py", "resources.py"}})
        related = {}
        for related_key, identifier in selection["related_revision_ids"].items():
            related_suite = suites.resolve_suite(identifier, tenant_id, conn=conn)
            # A related head is part of this new audit. Do not keep a carrier there
            # or replace it with another revision of the same key.
            if legacy_carrier_program(related_suite.files):
                raise ValueError("suite_version_confirmation_required")
            related[related_key] = related_suite.revision.resolved_digest
        # A repository default is validated offline: it is admitted without a proof row to freeze.
        verified = selection["verification_status"] == "verified" and not selection["repository_default"]
        accepted = verified or selection["force_unverified"] or selection["repository_default"]
        binding = RuleBinding(suite.revision.suite_key, code, entrypoints, rule_digest(rules, appendix) if verified else None)
        receipt = selection["validation"]
        validation = ValidationRecord(suite.revision.resolved_digest, rule_digest(rules, appendix), code, related, receipt["library_revision"]) if verified else None
        target = freeze_execution(
            suite=suite,
            file_version_id=item["file_version_id"],
            binding=binding,
            validation=validation,
            related_suite_digests=related,
            library_revision=library,
            allow_unverified=preparation or accepted,
        )
        targets.append(target)
        # Preparation remains inspectable even when formal admission needs fresh
        # proof. Mirror freeze_execution's policy, including explicit risk
        # adoption; library drift alone is a notice.
        item["suite_admission"] = {
            "state": "ready" if accepted else "validation_required",
            "suite_key": suite.revision.suite_key,
            "suite_revision_id": suite.revision.id,
            "verification_status": selection["verification_status"],
            "force_unverified": selection["force_unverified"],
            "samples_not_rerun": receipt["samples_not_rerun"],
        }
        # Existing Round/report readers retain actual accepted text and the new
        # explicit clause mapping, without deriving coverage from method order.
        item["rule_body"] = {**rules, "suite_key": suite.revision.suite_key, "appendix": list(appendix)}
        from product.store.rule_impact import published_rule_update

        rule_update = published_rule_update(conn, suites, tenant_id, suite)
        item["rule_execution"] = {
            "rule_version_mismatch": rule_update is not None,
            "latest_rule_suite_revision_id": rule_update["target_revision_id"] if rule_update else None,
            "mode": "checker_core",
            "suite_revision_id": suite.revision.id,
            "suite_revision_number": suite.revision.revision_number,
            "binding": binding.to_dict(),
            "verification": target.verification,
            "samples_not_rerun": target.samples_not_rerun,
        }
    return tuple(targets)


def freeze_round_execution(store, conn, principal, *, round_id: str, prepared: list[dict]) -> ExecutionSnapshot:
    """Resolve the adopted Suite under the publication lock during Round acceptance.

    No missing seed, invalid source or stale proof silently falls back to a legacy
    engine. A user's explicit adoption of unverified code remains runnable and is
    permanently recorded as unverified in this Round.
    """
    from audit.standard_suites import library_revision

    library = library_revision()
    identity = store.model_identity(principal.tenant_id, conn=conn)
    policy = store.settings.deep_execution_policy(identity)
    targets = freeze_selected_targets(store, conn, principal.tenant_id, prepared)
    return ExecutionSnapshot(
        tenant_id=principal.tenant_id,
        initiating_user_id=principal.user_id,
        inputs=freeze_authorized_inputs(conn, principal, prepared),
        targets=tuple(targets),
        runtime_revision=runtime_revision(policy.engine_revision(), library),
        library_revision=library,
        model_identity=identity,
        round_id=round_id,
    )
