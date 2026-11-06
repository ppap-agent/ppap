"""Publish uploaded rules through the same immutable Suite and adoption authority."""

from __future__ import annotations

from product.store.suite_releases import SuiteReleaseStore
from product.suites import UNIMPLEMENTED_CHECKER_SOURCE, canonical_json


def publish_custom_rule_suite(store, conn, principal, *, rule_id: str, title: str, body: dict, previous: dict | None) -> dict:
    """Caller owns the tenant publication lock and rule publication transaction.

    A rule upload has no demonstrated executable coverage. Its inert starter is
    a development input, not a legacy routing request or an Oracle proof.
    """
    releases = SuiteReleaseStore(store.settings, store.objects, role=store.role)
    base_id = None
    base = None
    expected = 0
    key = "custom:" + rule_id
    if previous is not None:
        previous_key, base_id = previous.get("suite_key"), previous.get("suite_revision_id")
        if not isinstance(previous_key, str) or not previous_key or not isinstance(base_id, str) or not base_id:
            raise ValueError("checker_suite_required")
        key = previous_key
        if body["type"] != previous["type"]:
            raise ValueError("suite_document_type_changed")
        head = conn.execute(
            "SELECT current_revision_id,revision FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key=%s FOR UPDATE",
            (principal.tenant_id, key),
        ).fetchone()
        if head is None:
            raise ValueError("suite_head_conflict")
        # A new rule is a candidate against the currently adopted implementation,
        # not permission to replace it. Preserve enterprise code and resources.
        expected = head[1]
        base_id = str(head[0])
        base = releases.suites.resolve_suite(base_id, principal.tenant_id, conn=conn)
    import json

    binding = json.loads(base.files["rules.json"]).get("checker_binding", {}) if base else {}
    clause_ids = {row["id"] for row in body["rules"]}
    rules = {**body, "title": title, "suite_key": key, "checker_binding": {key: value for key, value in binding.items() if key in clause_ids}}
    appendix = rules.pop("appendix", [])
    revision = releases.suites.create_revision(
        tenant_id=principal.tenant_id,
        suite_key=key,
        files={"rules.json": canonical_json(rules), "appendix.json": canonical_json(appendix), **({} if base else {"checker.py": UNIMPLEMENTED_CHECKER_SOURCE})},
        created_by=principal.user_id,
        base_revision_id=base_id,
        related_suite_keys=base.revision.related_suite_keys if base else (),
        conn=conn,
    )
    if previous is None:
        releases.publish(principal, key, revision.id, expected, rule_id, conn=conn)
    return {**body, "suite_key": key, "suite_revision_id": revision.id}
