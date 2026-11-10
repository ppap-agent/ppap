"""Classify legacy carrier heads. Do not move a pointer without an explicit publish.

Normal seed no longer installs historical carriers. A head that already points at
one stays in place until a caller names a verified directory revision. Tenant pins
are never updated here. Production apply, customer notice, and traffic cutover
belong to a separate authorization.
"""

from __future__ import annotations

from typing import Any

from audit.standard_suites import predefined_directory_suites
from psycopg.rows import dict_row

_DIRECTORY_IMPORT = b"from audit.standard_library import"
_OFFICIAL_TENANT = "00000000-0000-0000-0000-000000000000"
_STATUSES = (
    "directory_ready",
    "verified",
    "not_seeded",
    "pinned_historical",
    "in_flight",
    "missing",
    "blocked",
)

# Read-only diagnosis. It does not read checker bytes or customer file content.
LEGACY_HEAD_INSPECT_SQL = """
WITH heads AS (
    SELECT 'official'::text AS scope, NULL::uuid AS tenant_id, h.suite_key,
           h.current_revision_id, h.revision AS head_revision,
           r.source_path, r.resolved_digest, r.related_suite_keys
    FROM product.official_suite_heads h
    JOIN product.suite_revisions r ON r.id = h.current_revision_id
    UNION ALL
    SELECT 'tenant', t.tenant_id, t.suite_key, t.current_revision_id, t.revision,
           r.source_path, r.resolved_digest, r.related_suite_keys
    FROM product.tenant_suite_heads t
    JOIN product.suite_revisions r ON r.id = t.current_revision_id
),
inflight AS (
    SELECT r.id::text AS round_id, target->>'suite_revision_id' AS suite_revision_id
    FROM product.audit_rounds r
    JOIN product.checker_execution_snapshots s ON s.round_id = r.id
    CROSS JOIN LATERAL jsonb_array_elements(COALESCE(s.body->'targets', '[]'::jsonb)) AS target
    WHERE r.execution IN ('queued', 'running')
    UNION
    SELECT j.audit_round_id::text, target->>'suite_revision_id'
    FROM engine.jobs j
    JOIN product.checker_execution_snapshots s ON s.round_id = j.audit_round_id
    CROSS JOIN LATERAL jsonb_array_elements(COALESCE(s.body->'targets', '[]'::jsonb)) AS target
    WHERE j.status IN ('queued', 'leased') AND j.audit_round_id IS NOT NULL
)
SELECT h.scope, h.tenant_id::text AS tenant_id, h.suite_key, h.current_revision_id::text AS revision_id,
       h.head_revision, h.source_path, h.resolved_digest, h.related_suite_keys,
       COALESCE((
           SELECT jsonb_agg(DISTINCT i.round_id) FROM inflight i
           WHERE i.suite_revision_id = h.current_revision_id::text
       ), '[]'::jsonb) AS in_flight_round_ids
FROM heads h
ORDER BY h.suite_key, h.scope, h.tenant_id
"""


def _library_revision() -> str:
    from audit.standard_suites import library_revision

    return library_revision()


def refuse_related_carrier(store, conn, tenant_id: str | None, related_keys) -> None:
    """A related current head that is a carrier blocks publish. Do not select another revision."""
    from product.store.suite_releases import _selected_or_none

    lookup = tenant_id or _OFFICIAL_TENANT
    for key in related_keys:
        selected = _selected_or_none(conn, tenant_id, key)
        if selected is None:
            continue
        suite = store.suites.resolve_suite(str(selected["row"]["id"]), lookup, conn=conn)
        if legacy_carrier_program(suite.files):
            raise ValueError("suite_version_confirmation_required")


def legacy_carrier_program(files: Any) -> bool:
    """True when the frozen program dispatches through the historical class loader."""
    source = files.get("checker.py", b"") if hasattr(files, "get") else b""
    return isinstance(source, bytes) and _DIRECTORY_IMPORT in source


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def _text_list(value) -> list[str]:
    if value is None:
        return []
    return sorted({str(item) for item in value})


def _program_state(store, conn, revision_id: str, tenant_id: str | None) -> tuple[bool | None, str | None]:
    try:
        suite = store.suites.resolve_suite(revision_id, tenant_id or _OFFICIAL_TENANT, conn=conn)
    except (KeyError, ValueError) as exc:
        return None, str(exc)
    return legacy_carrier_program(suite.files), None


def inspect_legacy_heads(store, conn) -> list[dict]:
    """One stable row per affected suite_key. This function does not write."""
    heads = _rows(conn, LEGACY_HEAD_INSPECT_SQL)
    installed = _rows(
        conn,
        """SELECT id::text AS revision_id, suite_key, source_path, resolved_digest, related_suite_keys
           FROM product.suite_revisions
           WHERE tenant_id IS NULL AND NOT history_only AND source_path IS NOT NULL
           ORDER BY suite_key, source_path, revision_number, id""",
    )
    validations = {
        row["revision_id"]: row
        for row in _rows(
            conn,
            """SELECT revision_id::text AS revision_id, status, revision, suite_digest, related_suite_digests, proof
               FROM product.suite_validations WHERE tenant_id IS NULL""",
        )
    }
    official_digest = {row["suite_key"]: row["resolved_digest"] for row in heads if row["scope"] == "official"}
    leaves: dict[str, list[str]] = {}
    for source in predefined_directory_suites():
        leaves.setdefault(source.suite_key, []).append(source.source_path)
    by_key: dict[str, dict] = {}
    for row in heads:
        item = by_key.setdefault(row["suite_key"], {"official": None, "pins": []})
        if row["scope"] == "official":
            item["official"] = row
        else:
            item["pins"].append(row)
    for key in leaves:
        by_key.setdefault(key, {"official": None, "pins": []})

    report = []
    for key in sorted(by_key):
        facts = by_key[key]
        official = facts["official"]
        blocked_reason = None
        official_legacy = False
        if official is not None:
            legacy, error = _program_state(store, conn, official["revision_id"], None)
            if error:
                blocked_reason = error
            official_legacy = bool(legacy)
        pins = []
        for pin in facts["pins"]:
            legacy, error = _program_state(store, conn, pin["revision_id"], pin["tenant_id"])
            if error and blocked_reason is None:
                blocked_reason = error
            pins.append(
                {
                    "tenant_id": pin["tenant_id"],
                    "revision_id": pin["revision_id"],
                    "resolved_digest": pin["resolved_digest"],
                    "head_revision": pin["head_revision"],
                    "legacy": bool(legacy),
                }
            )
        leaf_paths = set(leaves.get(key, ()))
        directory_revisions = []
        for row in installed:
            if row["suite_key"] != key:
                continue
            proof = validations.get(row["revision_id"])
            related = list(row["related_suite_keys"] or [])
            dependency_blocked = False
            if proof is not None and proof["status"] == "verified":
                stored = proof["related_suite_digests"] or {}
                body = proof["proof"] if isinstance(proof["proof"], dict) else {}
                dependency_blocked = (
                    proof["suite_digest"] != row["resolved_digest"]
                    or any(official_digest.get(dependency) != stored.get(dependency) for dependency in related)
                    or body.get("library_revision") != _library_revision()
                )
            directory_revisions.append(
                {
                    "revision_id": row["revision_id"],
                    "source_path": row["source_path"],
                    "resolved_digest": row["resolved_digest"],
                    "validation": proof["status"] if proof else "missing",
                    "validation_revision": proof["revision"] if proof else 0,
                    "dependency_blocked": dependency_blocked,
                }
            )
        in_flight = _text_list(official["in_flight_round_ids"] if official else [])
        for pin in facts["pins"]:
            in_flight.extend(_text_list(pin["in_flight_round_ids"]))
        in_flight = sorted(set(in_flight))
        publishable = [row for row in directory_revisions if row["validation"] == "verified" and not row["dependency_blocked"]]
        if blocked_reason:
            status = "blocked"
        elif in_flight and (official_legacy or any(pin["legacy"] for pin in pins)):
            status = "in_flight"
        elif any(pin["legacy"] for pin in pins):
            status = "pinned_historical"
        elif official_legacy or (official is None and not directory_revisions):
            if not leaf_paths and not directory_revisions:
                status = "missing"
            elif leaf_paths and not directory_revisions:
                status = "not_seeded"
            elif publishable:
                status = "verified"
            elif any(row["dependency_blocked"] for row in directory_revisions):
                status = "blocked"
            elif directory_revisions:
                status = "directory_ready"
            else:
                status = "missing"
        elif publishable:
            status = "verified"
        elif directory_revisions:
            status = "directory_ready"
        elif leaf_paths:
            status = "not_seeded"
        else:
            status = "missing"
        if status not in _STATUSES:
            raise RuntimeError("invalid_legacy_head_status")
        report.append(
            {
                "suite_key": key,
                "status": status,
                "official_revision_id": official["revision_id"] if official else None,
                "official_source_path": official["source_path"] if official else None,
                "official_digest": official["resolved_digest"] if official else None,
                "official_head_revision": official["head_revision"] if official else 0,
                "official_legacy": official_legacy,
                "blocked_reason": blocked_reason,
                "directory_revisions": directory_revisions,
                "tenant_pins": pins,
                "in_flight_round_ids": in_flight,
            }
        )
    return report


def _confirmation_target(store, conn, item: dict) -> dict:
    if "tenant_id" in item:
        raise ValueError("tenant_pin_not_moved")
    suite_key = item["suite_key"]
    revision_id = str(item["revision_id"])
    expected = item["expected_head_revision"]
    validation_revision = item["expected_validation_revision"]
    if type(expected) is not int or type(validation_revision) is not int:
        raise ValueError("invalid_official_suite_publication")
    rows = _rows(
        conn,
        """SELECT id::text AS revision_id, suite_key, source_path, resolved_digest
           FROM product.suite_revisions
           WHERE id = %s AND tenant_id IS NULL AND NOT history_only""",
        (revision_id,),
    )
    if len(rows) != 1 or rows[0]["suite_key"] != suite_key:
        raise ValueError("suite_access_denied")
    from product.seed_standard_suites import persisted_source_path

    if persisted_source_path(rows[0]["source_path"] or "") != rows[0]["source_path"]:
        raise ValueError("historical_source_not_selectable")
    legacy, error = _program_state(store, conn, revision_id, None)
    if error:
        raise ValueError(error)
    if legacy:
        raise ValueError("historical_source_not_selectable")
    proof = _rows(
        conn,
        """SELECT status, revision, suite_digest, proof FROM product.suite_validations
           WHERE tenant_id IS NULL AND revision_id = %s""",
        (revision_id,),
    )
    proof_row = proof[0] if len(proof) == 1 else None
    proof_body = proof_row["proof"] if proof_row else {}
    current_library = proof_body.get("library_revision") if isinstance(proof_body, dict) else None
    verified = (
        proof_row is not None
        and proof_row["status"] == "verified"
        and proof_row["revision"] == validation_revision
        and proof_row["suite_digest"] == rows[0]["resolved_digest"]
        and current_library == _library_revision()
    )
    if not verified:
        raise ValueError("official_suite_validation_required")
    head = _rows(conn, "SELECT current_revision_id::text AS revision_id, revision FROM product.official_suite_heads WHERE suite_key = %s", (suite_key,))
    current_revision = head[0]["revision"] if head else 0
    current_id = head[0]["revision_id"] if head else None
    if current_revision != expected:
        raise ValueError("suite_head_conflict")
    return {
        "suite_key": suite_key,
        "revision_id": revision_id,
        "expected_head_revision": expected,
        "expected_validation_revision": validation_revision,
        "current_revision_id": current_id,
        "resolved_digest": rows[0]["resolved_digest"],
        "proof_digest": item.get("expected_proof_digest"),
    }


def apply_legacy_head_cutover(store, confirmations: list[dict] | None = None, *, dry_run: bool = True) -> dict:
    """Repeatable plan. ``dry_run`` and an empty confirmation list do not publish.

    A named confirmation publishes one official directory revision through the
    existing official CAS. It does not change tenant pins, snapshots, or results.
    """
    confirmations = list(confirmations or [])
    with store._conn() as conn:
        before = inspect_legacy_heads(store, conn)
        pins_before = _rows(conn, "SELECT tenant_id::text, suite_key, current_revision_id::text AS revision_id, revision FROM product.tenant_suite_heads ORDER BY tenant_id, suite_key")
        heads_before = _rows(conn, "SELECT suite_key, current_revision_id::text AS revision_id, revision FROM product.official_suite_heads ORDER BY suite_key")
        planned = []
        if not dry_run:
            for item in confirmations:
                planned.append(_confirmation_target(store, conn, item))
    applied = []
    if not dry_run:
        for item in planned:
            if item["current_revision_id"] == item["revision_id"]:
                applied.append({"suite_key": item["suite_key"], "revision_id": item["revision_id"], "result": "unchanged"})
                continue
            store.publish_official(
                item["revision_id"],
                expected_revision=item["expected_head_revision"],
                expected_validation_revision=item["expected_validation_revision"],
                expected_proof_digest=item["proof_digest"],
            )
            applied.append({"suite_key": item["suite_key"], "revision_id": item["revision_id"], "resolved_digest": item["resolved_digest"], "result": "published"})
    with store._conn() as conn:
        after = inspect_legacy_heads(store, conn)
        pins_after = _rows(conn, "SELECT tenant_id::text, suite_key, current_revision_id::text AS revision_id, revision FROM product.tenant_suite_heads ORDER BY tenant_id, suite_key")
        heads_after = _rows(conn, "SELECT suite_key, current_revision_id::text AS revision_id, revision FROM product.official_suite_heads ORDER BY suite_key")
    if pins_after != pins_before:
        raise RuntimeError("tenant_pin_changed")
    return {
        "dry_run": dry_run,
        "before": before,
        "after": after,
        "applied": applied,
        "official_heads_before": heads_before,
        "official_heads_after": heads_after,
        "tenant_pins": pins_after,
    }
