"""Application rule-file parsing backed by document and engine capabilities."""

from __future__ import annotations

import json
from pathlib import Path

from ppap_core.rules import (
    MAX_RULE_TEXT,
    RULE_FILE_EXTENSIONS,
    RuleParseError,
    document_types,
    normalize_rule_body,
)
from ppap_core.types import PpapDocumentType


def _word_rule_table(content: bytes) -> list[dict] | None:
    """Read explicitly labelled rule tables verbatim, never infer missing text."""
    from ppap_application.rule_sources import rule_sources

    units = rule_sources("requirements.docx", content)
    tables: dict[str, dict[int, dict[int, str]]] = {}
    for unit in units:
        parts = unit["id"].split(":")
        if len(parts) != 4 or parts[1] == "p":
            # Free-form scope or additional instructions still need extraction.
            return None
        tables.setdefault(parts[1], {}).setdefault(int(parts[2]), {})[int(parts[3])] = unit["excerpt"]
    rules = []
    for rows in tables.values():
        header_row = min(rows)
        headers = rows[header_row]
        identities = [column for column, text in headers.items() if text in ("审核条款", "条款编号", "规则编号", "序号", "编号")]
        bodies = [column for column, text in headers.items() if text in ("规则说明", "规则描述", "审核要求", "要求正文")]
        titles = [column for column, text in headers.items() if text in ("规则简述", "条款名称")]
        if len(identities) != 1 or len(bodies) != 1 or len(titles) > 1:
            return None
        if any(text not in ("样例/具体位置", "样例", "具体位置") for column, text in headers.items() if column not in (*identities, *bodies, *titles)):
            return None
        for number, cells in sorted(rows.items()):
            if number == header_row:
                continue
            identity, text = cells.get(identities[0], ""), cells.get(bodies[0], "")
            if not identity or not text:
                raise RuleParseError("rule_table_incomplete")
            title = cells.get(titles[0], "") if titles else ""
            rules.append({"id": identity, "category": title or text.splitlines()[0][:120], "priority": None, "rule_to_check": text, "type_of_rule": "text"})
    return rules or None


def parse_rule_file(filename: str, content: bytes, *, document_type: str = "", ai=None) -> dict:
    extension = Path(filename).suffix.lower()
    if extension not in RULE_FILE_EXTENSIONS:
        raise RuleParseError("unsupported_rule_file")
    if extension == ".json":
        try:
            body = normalize_rule_body(json.loads(content.decode("utf-8-sig")))
        except (ValueError, TypeError):
            raise RuleParseError("invalid_rule_json") from None
        if document_type and document_type != body["type"]:
            raise RuleParseError("rule_document_type_mismatch")
        return body

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
    if extension == ".docx" and doc_type != PpapDocumentType.UNKNOWN:
        rules = _word_rule_table(content)
        if rules is not None:
            return normalize_rule_body({"kind": "engine", "type": doc_type.get_type_name(), "rule_profile": "v3", "rules": rules})
    try:
        if ai is None:
            from ppap_excel_audit_engine.checkers.client import AIClient

            ai = AIClient()
        if doc_type == PpapDocumentType.UNKNOWN:
            classified = ai.structured_completion(
                system_prompt=PromptManager.get_system_prompt("ppap_expert"),
                user_prompt=PromptManager.get_document_classification_prompt(text, "\n".join(document_types())),
                max_tokens=100,
                temperature=0.0,
                operation="document_classification",
                request_timeout=120,
                retry_limit=0,
            )
            doc_type = PpapDocumentType.from_string(classified)
        if doc_type == PpapDocumentType.UNKNOWN:
            raise RuleParseError("unknown_rule_type")
        prompt = PromptManager.get_rule_extraction_prompt(text, doc_type.get_type_name())
        prompt += "\n同时返回 customers、models、parts 三个字符串数组，仅包含原文明确的适用客户、型号、零件；没有则为空数组。"
        response = ai.structured_completion(
            system_prompt=PromptManager.get_system_prompt("rule_extractor"),
            user_prompt=prompt,
            max_tokens=8192,
            temperature=0.0,
            json_format=True,
            operation="rule_extraction",
            request_timeout=120,
            retry_limit=0,
        )
        parsed = json.loads(response.strip().removeprefix("```json").removesuffix("```").strip())
        if not isinstance(parsed, dict):
            raise ValueError("invalid_rule_body")
        return normalize_rule_body({**parsed, "type": doc_type.get_type_name(), "rule_profile": "v3"})
    except RuleParseError:
        raise
    except Exception:
        raise RuleParseError("rule_parse_failed") from None
