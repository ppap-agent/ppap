"""Catalog facts of one immutable Suite revision, derived once from its verified bytes.

A revision never changes, so neither does its title, its semantic rule digest or what its
leaf declares. Writing them next to the revision lets the catalog answer with set queries
instead of resolving every Suite on every read. Bytes are still verified wherever a
revision is opened, adopted or executed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# Bump when the derivation changes; older rows stay as they were and new rows are derived.
FACTS_VERSION = 1


def derive_facts(files: Mapping[str, bytes], source_path: str | None) -> dict:
    from audit.checker_core import rule_digest
    from audit.standard_suites import validate_source_identity

    from product.store.rule_impact import semantic_rule_packet
    from product.suite_releases import leaf_capability

    rules = json.loads(files["rules.json"])
    if not isinstance(rules, dict):
        raise ValueError("invalid_suite_rules")
    appendix = json.loads(files.get("appendix.json", b"[]"))
    identity_valid = None
    if source_path is not None:
        try:
            validate_source_identity(source_path, rules)
            identity_valid = True
        except ValueError:
            identity_valid = False
    title = str(rules.get("title") or rules.get("type") or "文件检查")[:500]
    return {
        "title": title,
        "rule_digest": rule_digest(semantic_rule_packet({**rules, "appendix": appendix}), appendix),
        "capability": leaf_capability(files),
        "source_identity_valid": identity_valid,
    }


def insert_facts(conn, revision_id: str, tenant_id: str | None, facts: dict) -> None:
    conn.execute(
        """INSERT INTO product.suite_revision_facts
           (revision_id, facts_version, tenant_id, title, rule_digest, capability, source_identity_valid)
           VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (revision_id, FACTS_VERSION, tenant_id, facts["title"], facts["rule_digest"], Jsonb(facts["capability"]), facts["source_identity_valid"]),
    )


def read_facts(conn, revision_ids: Iterable[str]) -> dict[str, dict]:
    ids = sorted(set(revision_ids))
    if not ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM product.suite_revision_facts WHERE facts_version = %s AND revision_id = ANY(%s::uuid[])",
            (FACTS_VERSION, ids),
        )
        return {str(row["revision_id"]): row for row in cur.fetchall()}


def facts_for(conn, suites, tenant_id: str, revisions: Mapping[str, dict]) -> tuple[dict[str, dict], set[str]]:
    """Facts for these revision rows; a revision written before facts existed is derived once, here.

    Returns the facts and the revision ids that could not be resolved. Those stay unknown;
    nothing is guessed for them.
    """
    found = read_facts(conn, revisions)
    broken: set[str] = set()
    for revision_id, row in revisions.items():
        if revision_id in found:
            continue
        from product.files import ObjectMissing

        try:
            resolved = suites.resolve_suite(revision_id, tenant_id, conn=conn)
            facts = derive_facts(resolved.files, row.get("source_path"))
        except (KeyError, ValueError, ObjectMissing, json.JSONDecodeError, UnicodeDecodeError):
            broken.add(revision_id)
            continue
        insert_facts(conn, revision_id, str(row["tenant_id"]) if row["tenant_id"] else None, facts)
        found[revision_id] = {"revision_id": revision_id, "tenant_id": row["tenant_id"], **facts}
    return found, broken
