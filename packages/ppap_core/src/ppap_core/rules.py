"""Prepare engine rules from customer files, then validate before publication."""

from __future__ import annotations

import json
import uuid
from typing import Any

from ppap_core.contracts import RuleItem
from ppap_core.rule_profile import normalize_rule_profile
from ppap_core.types import PpapDocumentType

RULE_FILE_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".doc", ".docx", ".md", ".pdf", ".json"}
FILE_PREPARATION_KINDS = ("rule_parse", "file_analysis", "file_validation")
MAX_RULE_TEXT = 20000  # The migrated extraction prompt accepts this much source text.


class RuleParseError(ValueError):
    """Public error codes must not contain source text or provider responses."""


def rule_parse_request(request_id, source_file_id, source_file_version_id, document_type="") -> tuple[str, dict]:
    try:
        ids = [str(uuid.UUID(str(value))) for value in (request_id, source_file_id, source_file_version_id)]
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_rule_request") from None
    if not isinstance(document_type, str) or (document_type and document_type not in document_types()):
        raise ValueError("unknown_rule_type")
    return ids[0], {"source_file_id": ids[1], "source_file_version_id": ids[2], "document_type": document_type}


def rule_parse_view(job_id: str, payload: dict, status: str, result, expired: bool) -> dict:
    return {"id": job_id, **payload, "status": "unknown" if status == "leased" and expired else status, "result": result}


def document_types() -> list[str]:
    return [item.get_type_name() for item in PpapDocumentType if item != PpapDocumentType.UNKNOWN]


def customer_scope(body: dict, case_context: dict | None = None) -> str:
    """An empty customer list is not proof that the user confirmed all customers."""
    if body.get("customers"):
        return "restricted"
    if (case_context or {}).get("customer_scope") == "all":
        return "all"
    try:
        registration = json.loads((case_context or {}).get("registration") or "{}")
        if isinstance(registration, dict) and isinstance(registration.get("scope"), dict) and registration["scope"].get("allCustomers") is True:
            return "all"
    except (TypeError, ValueError):
        pass
    return "unconfirmed"


def latest_rule_versions(rules: list[dict]) -> list[dict]:
    """One newest published version per explicit predecessor chain, not per type."""
    by_id = {rule["id"]: rule for rule in rules}
    newest: dict[Any, dict[Any, Any]] = {}
    for rule in rules:
        root = rule["id"]
        seen = {root}
        while by_id[root].get("predecessor_id") in by_id:
            root = by_id[root]["predecessor_id"]
            if root in seen:
                raise ValueError("invalid_rule_versions")
            seen.add(root)
        previous = newest.get(root)
        if previous is None or (rule.get("created_at") or "", rule["id"]) > (previous.get("created_at") or "", previous["id"]):
            newest[root] = rule
    return sorted(newest.values(), key=lambda rule: (rule.get("type") or "", rule["title"], rule["id"]))


def normalize_rule_body(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("kind", "engine") != "engine":
        raise ValueError("invalid_rule_body")
    raw_type = value.get("type")
    doc_type = PpapDocumentType.from_string(raw_type) if isinstance(raw_type, str) else PpapDocumentType.UNKNOWN
    if doc_type == PpapDocumentType.UNKNOWN:
        raise ValueError("unknown_rule_type")
    raw_profile = value.get("rule_profile")
    if raw_profile is not None and not isinstance(raw_profile, str):
        raise ValueError("invalid_rule_profile")
    try:
        profile = normalize_rule_profile(raw_profile)
    except ValueError:
        raise ValueError("invalid_rule_profile") from None
    raw_rules = value.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("empty_rules")
    rules = []
    ids = set()
    for raw in raw_rules:
        try:
            rule = RuleItem.model_validate(raw).model_dump()
        except Exception:
            raise ValueError("invalid_rule_item") from None
        for field in ("id", "category", "rule_to_check", "type_of_rule"):
            rule[field] = rule[field].strip()
            if not rule[field]:
                raise ValueError("invalid_rule_item")
        if rule["type_of_rule"] not in {"text", "table", "image"} or rule["priority"] not in {None, "P0", "P1", "P2"}:
            raise ValueError("invalid_rule_item")
        if rule["id"] in ids:
            raise ValueError("duplicate_rule_id")
        ids.add(rule["id"])
        rules.append(rule)
    metadata = {}
    for field in ("customers", "models", "parts"):
        values = value.get(field, [])
        if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("invalid_rule_metadata")
        metadata[field] = [item.strip() for item in values]
    return {"kind": "engine", "rule_profile": profile, "type": doc_type.get_type_name(), "rules": rules, **metadata}


def rule_title(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("invalid_rule_title")
    return value.strip()
