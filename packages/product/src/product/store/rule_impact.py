"""Read-only impact of published rules on the currently adopted Checker."""

from __future__ import annotations

import json

from audit.checker_core import rule_digest

from product.rules import rule_changes


def suite_rule_body(suite):
    return {**json.loads(suite.files["rules.json"]), "appendix": json.loads(suite.files.get("appendix.json", b"[]"))}


def rule_impact(before, after) -> dict:
    previous, current = suite_rule_body(before), suite_rule_body(after)
    return {
        "source_revision_id": before.revision.id,
        "target_revision_id": after.revision.id,
        "changed": rule_digest({"rules": previous.get("rules")}, previous["appendix"]) != rule_digest({"rules": current.get("rules")}, current["appendix"]),
        "appendix_changed": previous["appendix"] != current["appendix"],
        "clauses": rule_changes(previous, current),
    }


def published_rule_update(conn, suites, tenant_id: str, active):
    """A row publication is development input, never implicit Suite adoption."""
    row = conn.execute(
        """SELECT body->>'suite_revision_id' FROM product.rule_versions
           WHERE tenant_id=%s AND body->>'suite_key'=%s AND body->>'suite_revision_id' IS NOT NULL
           ORDER BY created_at DESC,id DESC LIMIT 1""",
        (tenant_id, active.revision.suite_key),
    ).fetchone()
    if row is None or row[0] == active.revision.id:
        return None
    candidate = suites.resolve_suite(row[0], tenant_id, conn=conn)
    impact = rule_impact(active, candidate)
    return impact if impact["changed"] else None
