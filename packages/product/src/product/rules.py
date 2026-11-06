"""Prepare engine rules from customer files, then validate before publication."""

from __future__ import annotations

import json
import uuid
from typing import Any

from audit.contracts import RuleItem
from audit.kinds import PpapDocumentType
from audit.rule_profile import normalize_rule_profile

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
    if not isinstance(document_type, str):
        raise ValueError("unknown_rule_type")
    if document_type:
        document_type = normalize_document_type(document_type)
    return ids[0], {"source_file_id": ids[1], "source_file_version_id": ids[2], "document_type": document_type}


def rule_parse_view(job_id: str, payload: dict, status: str, result, expired: bool) -> dict:
    return {"id": job_id, **payload, "status": "unknown" if status == "leased" and expired else status, "result": result}


def document_types() -> list[str]:
    return [item.get_type_name() for item in PpapDocumentType if item != PpapDocumentType.UNKNOWN]


def normalize_document_type(value: Any) -> str:
    """Known names are canonicalized; new enterprise document types need no seed."""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200 or any(ord(char) < 32 for char in value):
        raise ValueError("unknown_rule_type")
    value = value.strip()
    kind = PpapDocumentType.from_string(value)
    if value == PpapDocumentType.UNKNOWN.get_type_name():
        raise ValueError("unknown_rule_type")
    return kind.get_type_name() if kind != PpapDocumentType.UNKNOWN else value


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
    document_type = normalize_document_type(value.get("type"))
    raw_profile = value.get("rule_profile")
    if raw_profile is not None and not isinstance(raw_profile, str):
        raise ValueError("invalid_rule_profile")
    try:
        profile = normalize_rule_profile(raw_profile)
    except ValueError:
        raise ValueError("invalid_rule_profile") from None
    if profile == "v4":
        raise ValueError("product_preset_required")
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
    body = {"kind": "engine", "rule_profile": profile, "type": document_type, "rules": rules, **metadata}
    if "appendix" in value:
        body["appendix"] = normalize_rule_appendix(value["appendix"], ids)
    return body


def normalize_rule_appendix(value: Any, rule_ids: set[str]) -> list[dict]:
    """An explicit input, never reconstructed from reviews or past runs."""
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("invalid_rule_appendix")
    result: list[dict] = []
    seen: set[str] = set()
    allowed = {"id", "kind", "question_id", "candidate_id", "content", "scope", "confirmed_by", "confirmed_at", "confirmation_source"}
    for item in value:
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError("invalid_rule_appendix")
        for key in ("id", "content"):
            if not isinstance(item.get(key), str) or not item[key].strip() or len(item[key]) > 8192:
                raise ValueError("invalid_rule_appendix")
        if item["id"] in seen or item.get("kind") not in {"interpretation", "mapping", "standard_change"}:
            raise ValueError("invalid_rule_appendix")
        seen.add(item["id"])
        scope = item.get("scope")
        if not isinstance(scope, dict) or not {"document_ids", "rule_ids"} <= set(scope) or set(scope) - {"document_ids", "rule_ids", "rule_targets"}:
            raise ValueError("invalid_rule_appendix")
        for key in ("document_ids", "rule_ids"):
            ids = scope[key]
            if not isinstance(ids, list) or not 1 <= len(ids) <= 256 or any(not isinstance(ident, str) or not ident.strip() for ident in ids) or len(set(ids)) != len(ids):
                raise ValueError("invalid_rule_appendix")
        if not set(scope["rule_ids"]) <= rule_ids:
            raise ValueError("invalid_rule_appendix")
        if "rule_targets" in scope:
            targets = scope["rule_targets"]
            if not isinstance(targets, list) or not 1 <= len(targets) <= 16384:
                raise ValueError("invalid_rule_appendix")
            pairs = set()
            for target in targets:
                if not isinstance(target, dict) or set(target) != {"document_id", "rule_id"} or any(not isinstance(target[key], str) or not target[key].strip() for key in target):
                    raise ValueError("invalid_rule_appendix")
                pair = (target["document_id"], target["rule_id"])
                if pair in pairs or target["rule_id"] not in scope["rule_ids"]:
                    raise ValueError("invalid_rule_appendix")
                pairs.add(pair)
        for key in allowed - {"id", "kind", "content", "scope"}:
            if key in item and (not isinstance(item[key], str) or not item[key].strip() or len(item[key]) > 1024):
                raise ValueError("invalid_rule_appendix")
        if item.get("confirmation_source", "user") not in {"user", "synthetic"}:
            raise ValueError("invalid_rule_appendix")
        if item.get("question_id") and item["kind"] in {"interpretation", "mapping"}:
            for previous in result:
                if previous.get("question_id") != item["question_id"] or previous["kind"] not in {"interpretation", "mapping"}:
                    continue
                if (previous.get("candidate_id"), previous["content"]) == (item.get("candidate_id"), item["content"]):
                    continue
                prior_scope = previous["scope"]
                common_rules = set(scope["rule_ids"]) & set(prior_scope["rule_ids"])
                if not common_rules or not set(scope["document_ids"]) & set(prior_scope["document_ids"]):
                    continue
                targets, prior_targets = scope.get("rule_targets"), prior_scope.get("rule_targets")
                if targets is not None and prior_targets is not None:
                    overlap = bool({(target["document_id"], target["rule_id"]) for target in targets} & {(target["document_id"], target["rule_id"]) for target in prior_targets})
                else:
                    explicit_targets = targets if targets is not None else prior_targets
                    overlap = explicit_targets is None or any(target["rule_id"] in common_rules for target in explicit_targets)
                if overlap:
                    raise ValueError("invalid_rule_appendix")
        result.append({**item, "scope": {key: [dict(target) for target in scope[key]] if key == "rule_targets" else list(scope[key]) for key in scope}})
    return result


def rule_title(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("invalid_rule_title")
    return value.strip()


def install_customer_presets(conn, tenant_id: str) -> None:
    """Idempotently publish product-owned inputs into the existing rule table.

    Source revisions create new immutable versions. User uploads cannot replace
    these rows or become their successors through the publication API.
    """
    from audit.presets import customer_presets, preset_rule_body
    from psycopg.types.json import Jsonb

    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("customer-presets:" + tenant_id,))
    existing = conn.execute(
        """SELECT id, body FROM product.rule_versions
           WHERE tenant_id = %s AND body->>'rule_profile' = 'v4'
           ORDER BY created_at DESC, id DESC""",
        (tenant_id,),
    ).fetchall()
    latest: dict[str, tuple[str, dict]] = {}
    for identifier, body in existing:
        latest.setdefault(body.get("preset_key"), (str(identifier), body))
    for preset in customer_presets():
        body = preset_rule_body(preset.customer, preset.entry_id)
        predecessor = latest.get(preset.entry_id)
        if predecessor is not None and predecessor[1] == body:
            continue
        rule_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO product.rule_versions (id, tenant_id, title, body, predecessor_id, case_context)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (rule_id, tenant_id, preset.title, Jsonb(body), predecessor[0] if predecessor else None, Jsonb({"origin": "product_preset", "source_path": preset.source_path})),
        )
