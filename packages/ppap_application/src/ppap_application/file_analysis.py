"""Application-level pre-audit metadata and rule suggestions."""

from __future__ import annotations

import json
from pathlib import Path

from ppap_core.rules import document_types, latest_rule_versions
from ppap_core.types import PpapDocumentType


def analyze_file(filename: str, content: bytes, rules: list[dict], *, ai=None, customer_preference: str | None = None, scoped: bool = False) -> dict:
    """Recognize a file; scoped tasks supply their already-frozen current candidates."""
    from ppap_core.rule_matching import RuleMatchingAIService
    from ppap_docops.extractors import get_extractor
    from ppap_excel_audit_engine.checkers.client import AIClient
    from ppap_excel_audit_engine.checkers.prompts import PromptManager

    notices = []
    text = ""
    try:
        extension = Path(filename).suffix.lower()
        if extension == ".doc":
            from ppap_docops.word.legacy import convert_legacy_doc_to_docx

            content = convert_legacy_doc_to_docx(content, filename)
            extension = ".docx"
        text = get_extractor(extension).extract_text(content)
        if not text.strip():
            notices.append("empty_document_text")
    except Exception:
        notices.append("document_text_unavailable")

    # The excerpt is for preparation only; the checker still receives the full file.
    if len(text) > 20000:
        notices.append("metadata_uses_excerpt")
    excerpt = text[:20000]
    metadata: dict[str, str | None] = {"customer": None, "model": None, "part": None, "part_number": None}
    ai = ai if ai is not None else AIClient()
    if excerpt.strip():
        try:
            response = ai.structured_completion(
                system_prompt=PromptManager.get_system_prompt("document_analyst"),
                user_prompt=PromptManager.get_document_metadata_prompt(excerpt, filename) + "\n同时返回 part_number（零件号）；part 仅表示零件名称，无法确定时返回 null。",
                max_tokens=1000,
                temperature=0.1,
                json_format=True,
                operation="document_metadata_extraction",
                request_timeout=120,
                retry_limit=0,
            )
            parsed = json.loads(response.strip().removeprefix("```json").removesuffix("```").strip())
            if not isinstance(parsed, dict):
                raise ValueError("invalid_metadata")
            for field in metadata:
                value = parsed.get(field)
                if isinstance(value, list):
                    value = "、".join(item for item in value if isinstance(item, str))
                if isinstance(value, str):
                    metadata[field] = value.strip()[:500] or None
        except Exception:
            notices.append("metadata_extraction_failed")

    candidates = [rule for rule in (rules if scoped else latest_rule_versions(rules)) if rule.get("kind") == "engine"]
    inferred = PpapDocumentType.infer_from_signals(filename=filename, content=excerpt[:2000], min_margin=1 if scoped else 0)
    if scoped and excerpt.strip() and "metadata_extraction_failed" not in notices:
        evidence = PpapDocumentType.rank_from_signals(filename=filename, content=excerpt[:2000])
        if inferred == PpapDocumentType.UNKNOWN or not evidence[0].alias_hits:
            inferred = PpapDocumentType.UNKNOWN
            try:
                classified = ai.structured_completion(
                    system_prompt=PromptManager.get_system_prompt("ppap_expert"),
                    user_prompt=PromptManager.get_document_classification_prompt(excerpt, "\n".join(document_types())),
                    max_tokens=100,
                    temperature=0.0,
                    operation="document_classification",
                    request_timeout=120,
                    retry_limit=0,
                )
                if not isinstance(classified, str) or not classified.strip():
                    raise ValueError("invalid_document_classification")
                inferred = PpapDocumentType.from_string(classified)
                if inferred == PpapDocumentType.UNKNOWN and classified.strip().casefold() not in {"未知", "unknown"}:
                    raise ValueError("invalid_document_classification")
            except Exception:
                notices.append("document_classification_failed")
    matched_id = None
    matching = "unmatched"
    if scoped and any(notice in notices for notice in ("document_text_unavailable", "empty_document_text", "metadata_extraction_failed", "document_classification_failed")):
        matching = "failed"
    elif scoped:
        ambiguous = False
        customers = tuple(dict.fromkeys(value.strip().casefold() for value in (customer_preference, metadata["customer"]) if value and value.strip()))
        typed_rules = [rule for rule in candidates if inferred != PpapDocumentType.UNKNOWN and PpapDocumentType.from_string(rule.get("type") or "") == inferred]
        scoped_rules = [rule for rule in typed_rules if isinstance(rule.get("customers"), list) and any(isinstance(value, str) and value.strip() for value in rule["customers"])]
        for customer in customers:
            applicable = [rule for rule in scoped_rules if customer in {value.strip().casefold() for value in rule["customers"] if isinstance(value, str)}]
            if len(applicable) == 1:
                matched_id = applicable[0]["id"]
                matching = "matched"
                break
            ambiguous = ambiguous or len(applicable) > 1
        if not matched_id:
            matching = "failed" if ambiguous else "unmatched"
            if ambiguous:
                notices.append("ambiguous_available_rules")
            elif inferred == PpapDocumentType.UNKNOWN:
                notices.append("matching_document_type_required")
            elif not typed_rules:
                notices.append("matching_document_type_rules_missing")
            elif not scoped_rules:
                notices.append("matching_rules_customer_unassigned")
            elif not customers:
                notices.append("matching_customer_required")
            else:
                notices.append("matching_customer_rules_missing")
    elif candidates:
        try:
            matched_id = RuleMatchingAIService(ai, PromptManager()).get_matched_rule_id(
                filename,
                excerpt,
                [{"candidateId": f"R{index:02d}", "ruleId": rule["id"], "rule_file_name": rule["title"], "type": rule.get("type") or ""} for index, rule in enumerate(candidates, start=1)],
            )
            matching = "matched" if matched_id else "unmatched"
        except Exception:
            matching = "failed"
            notices.append("rule_matching_failed")
    else:
        notices.append("no_available_rules")
    selected = next((rule for rule in candidates if rule["id"] == matched_id), None)
    return {
        "metadata": metadata,
        "document_type": selected.get("type") if selected else (None if inferred == PpapDocumentType.UNKNOWN else inferred.get_type_name()),
        "matched_rule_id": matched_id,
        "matching_status": matching,
        "notices": notices,
    }
