"""Immutable product rule snapshots derived from in-repo checkers. Not a live catalog mutation."""
from __future__ import annotations

import json

from ppap_core.files import sha256_bytes
from ppap_core.fixtures import CATALOG_PROJECT


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot_hash(content: dict) -> str:
    return sha256_bytes(canonical_json(content).encode("utf-8"))


def catalog_snapshots(project_id: str = CATALOG_PROJECT) -> list[dict]:
    from ppap_excel_audit_engine.rule_engine.registry import get_doc_resource_by_doc_type
    from ppap_excel_audit_engine.routing import V2_EXCEL_AGENTS

    snapshots: list[dict] = []
    for doc_type, agent_cls in V2_EXCEL_AGENTS.items():
        checks: list[dict[str, str]] = []
        declared = getattr(agent_cls, "declared_rules", None)
        if callable(declared):
            for name, description in declared():
                checks.append({"id": name, "description": description})
        resource = get_doc_resource_by_doc_type(doc_type)
        sheets = list(resource.sheets.keys()) if resource is not None else []
        engine_rule_id = doc_type.get_type_name()
        content = {
            "document_type": engine_rule_id,
            "document_type_key": doc_type.name,
            "engine_rule_id": engine_rule_id,
            "checks": checks,
            "sheets": sheets,
        }
        snapshots.append(
            {
                "project_id": project_id,
                "engine_rule_id": engine_rule_id,
                "document_type": engine_rule_id,
                "label": engine_rule_id,
                "content": content,
                "content_sha256": snapshot_hash(content),
                "check_count": len(checks),
            }
        )
    return snapshots
