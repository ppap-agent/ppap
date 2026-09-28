"""Pre-audit metadata and rule suggestions; never executes customer rule text."""
from __future__ import annotations

import json
from pathlib import Path

from ppap_core.types import PpapDocumentType


def latest_rule_versions(rules: list[dict]) -> list[dict]:
    """One newest published version per explicit predecessor chain, not per type."""
    by_id = {rule["id"]: rule for rule in rules}
    newest = {}
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


def analyze_file(filename: str, content: bytes, rules: list[dict], *, ai=None) -> dict:
    from ppap_docops.extractors import get_extractor
    from ppap_excel_audit_engine.checkers.client import AIClient
    from ppap_excel_audit_engine.checkers.prompts import PromptManager
    from ppap_core.rule_matching import RuleMatchingAIService

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
    metadata = {"customer": None, "model": None, "part": None}
    ai = ai if ai is not None else AIClient()
    if excerpt.strip():
        try:
            response = ai.structured_completion(
                system_prompt=PromptManager.get_system_prompt("document_analyst"),
                user_prompt=PromptManager.get_document_metadata_prompt(excerpt, filename),
                max_tokens=1000, temperature=0.1, json_format=True,
                operation="document_metadata_extraction", request_timeout=120, retry_limit=0,
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

    candidates = [rule for rule in latest_rule_versions(rules) if rule.get("kind") == "engine"]
    matched_id = None
    matching = "unmatched"
    if candidates:
        try:
            matched_id = RuleMatchingAIService(ai).get_matched_rule_id(filename, excerpt, [
                {"candidateId": f"R{index:02d}", "ruleId": rule["id"], "rule_file_name": rule["title"], "type": rule.get("type") or ""}
                for index, rule in enumerate(candidates, start=1)
            ])
            matching = "matched" if matched_id else "unmatched"
        except Exception:
            matching = "failed"
            notices.append("rule_matching_failed")
    else:
        notices.append("no_available_rules")
    inferred = PpapDocumentType.infer_from_signals(filename=filename, content=excerpt[:2000])
    selected = next((rule for rule in candidates if rule["id"] == matched_id), None)
    return {
        "metadata": metadata,
        "document_type": selected.get("type") if selected else (None if inferred == PpapDocumentType.UNKNOWN else inferred.get_type_name()),
        "matched_rule_id": matched_id, "matching_status": matching, "notices": notices,
    }
