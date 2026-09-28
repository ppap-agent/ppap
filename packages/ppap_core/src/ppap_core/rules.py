"""Prepare engine rules from customer files, then validate before publication."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from ppap_core.contracts import RuleItem
from ppap_core.types import PpapDocumentType

RULE_FILE_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".doc", ".docx", ".md", ".pdf", ".json"}
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


def normalize_rule_body(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("kind", "engine") != "engine":
        raise ValueError("invalid_rule_body")
    raw_type = value.get("type")
    doc_type = PpapDocumentType.from_string(raw_type) if isinstance(raw_type, str) else PpapDocumentType.UNKNOWN
    if doc_type == PpapDocumentType.UNKNOWN:
        raise ValueError("unknown_rule_type")
    profile = value.get("rule_profile", "v3")
    if not isinstance(profile, str) or profile not in {"v2", "v3"}:
        raise ValueError("invalid_rule_profile")
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


def parse_rule_file(filename: str, content: bytes, *, document_type: str = "", ai=None) -> dict:
    extension = Path(filename).suffix.lower()
    if extension not in RULE_FILE_EXTENSIONS:
        raise RuleParseError("unsupported_rule_file")
    if extension == ".json":
        try:
            return normalize_rule_body(json.loads(content.decode("utf-8-sig")))
        except (ValueError, TypeError):
            raise RuleParseError("invalid_rule_json") from None

    from ppap_docops.extractors import get_extractor
    from ppap_excel_audit_engine.checkers.prompts import PromptManager

    try:
        if extension == ".doc":
            from ppap_docops.word.legacy import convert_legacy_doc_to_docx
            content = convert_legacy_doc_to_docx(content, filename)
            extension = ".docx"
        text = get_extractor(extension).extract_text(content)
    except Exception:
        raise RuleParseError("rule_text_extraction_failed") from None
    if not text.strip():
        raise RuleParseError("empty_rule_document")
    if len(text) > MAX_RULE_TEXT:
        raise RuleParseError("rule_document_too_long")
    if not isinstance(document_type, str):
        raise RuleParseError("unknown_rule_type")
    doc_type = PpapDocumentType.from_string(document_type or filename)
    if document_type and doc_type == PpapDocumentType.UNKNOWN:
        raise RuleParseError("unknown_rule_type")
    if doc_type == PpapDocumentType.UNKNOWN and len(text) > 4000:
        raise RuleParseError("rule_type_required")
    try:
        if ai is None:
            from ppap_excel_audit_engine.checkers.client import AIClient

            ai = AIClient()
        if doc_type == PpapDocumentType.UNKNOWN:
            classified = ai.structured_completion(
                system_prompt=PromptManager.get_system_prompt("ppap_expert"),
                user_prompt=PromptManager.get_document_classification_prompt(text, "\n".join(document_types())),
                max_tokens=100, temperature=0.0, operation="document_classification",
                request_timeout=120, retry_limit=0,
            )
            doc_type = PpapDocumentType.from_string(classified)
        if doc_type == PpapDocumentType.UNKNOWN:
            raise RuleParseError("unknown_rule_type")
        prompt = PromptManager.get_rule_extraction_prompt(text, doc_type.get_type_name())
        prompt += "\n同时返回 customers、models、parts 三个字符串数组，仅包含原文明确的适用客户、型号、零件；没有则为空数组。"
        response = ai.structured_completion(
            system_prompt=PromptManager.get_system_prompt("rule_extractor"), user_prompt=prompt,
            max_tokens=8192, temperature=0.0, json_format=True, operation="rule_extraction",
            request_timeout=120, retry_limit=0,
        )
        parsed = json.loads(response.strip().removeprefix("```json").removesuffix("```").strip())
        if not isinstance(parsed, dict):
            raise ValueError("invalid_rule_body")
        return normalize_rule_body({**parsed, "type": doc_type.get_type_name()})
    except RuleParseError:
        raise
    except Exception:
        raise RuleParseError("rule_parse_failed") from None
