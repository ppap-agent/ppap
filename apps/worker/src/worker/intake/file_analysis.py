"""Application-level pre-audit metadata and rule suggestions."""

from __future__ import annotations

import html
import re
from pathlib import Path

import documents.ai
from audit.kinds import PpapDocumentType
from product.rules import document_types, latest_rule_versions


def analyze_file(
    filename: str,
    content: bytes,
    rules: list[dict],
    *,
    ai=None,
    customer_preference: str | None = None,
    scoped: bool = False,
    reference_only: bool = False,
    validation_scope: dict | None = None,
) -> dict:
    """Recognize a file; scoped tasks supply their already-frozen current candidates."""
    ai = ai or documents.ai.run_json
    from audit.engine.checkers.prompts import PromptManager
    from documents.extractors import get_extractor
    from product.rule_matching import RuleMatchingAIService

    if validation_scope is not None:
        result = {
            "metadata": {"customer": None, "model": None, "part": None, "part_number": None},
            "document_type": None,
            "matched_rule_id": None,
            "matching_status": "failed",
            "notices": ["matching_document_type_required"],
        }
        if not scoped or customer_preference != "UAES" or validation_scope != {"customer_preference": "UAES", "preset_key": "U13"}:
            return result
        # Scope comes from a successful, file-version-bound validation receipt.
        # The user explicitly chose U13; a picture has no required text layer.
        candidates = [rule for rule in rules if rule.get("kind") == "engine" and rule.get("rule_profile") == "v4" and rule.get("type") == "E07" and rule.get("customers") == ["UAES"]]
        if reference_only:
            return {**result, "document_type": "E07", "matching_status": "reference", "notices": ["photo_content_requires_manual_confirmation"]}
        if len(candidates) != 1:
            return {**result, "document_type": "E07", "matching_status": "unmatched", "notices": ["matching_document_type_rules_missing"]}
        return {**result, "document_type": "E07", "matched_rule_id": candidates[0]["id"], "matching_status": "matched", "notices": ["photo_content_requires_manual_confirmation"]}

    notices = []
    text = ""
    try:
        extension = Path(filename).suffix.lower()
        if extension == ".doc":
            from documents.word.legacy import convert_legacy_doc_to_docx

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
    if excerpt.strip():
        try:
            parsed = ai(
                "document_metadata_extraction",
                instructions=PromptManager.get_system_prompt("document_analyst"),
                prompt=PromptManager.get_document_metadata_prompt(excerpt, filename) + "\n同时返回 part_number（零件号）；part 仅表示零件名称，无法确定时返回 null。",
                max_tokens=1000,
                timeout=120,
            ).output
            for field in metadata:
                value = parsed.get(field)
                if isinstance(value, list):
                    value = "、".join(item for item in value if isinstance(item, str))
                if isinstance(value, str):
                    metadata[field] = value.strip()[:500] or None
        except Exception:
            notices.append("metadata_extraction_failed")

    candidates = [rule for rule in (rules if scoped else latest_rule_versions(rules)) if rule.get("kind") == "engine"]
    aliases = {"uaes": "uaes", "联电": "uaes", "理想": "理想", "理想汽车": "理想", "蔚来": "蔚来", "nio": "蔚来"}
    customer_text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    explicit_customers = {
        aliases[match.group(1).casefold()]
        for match in re.finditer(r"(?:审核客户|客户(?:名称|名)?|customer(?:\s+name)?)\s*[:：]?\s*(UAES|联电|理想汽车|理想|蔚来|NIO)(?=\s|$|[，,;；/])", customer_text, re.IGNORECASE)
    }
    selected_customer = (customer_preference or "").strip().casefold()
    if not selected_customer and len(explicit_customers) == 1:
        selected_customer = next(iter(explicit_customers))
    customer_presets = [rule for rule in candidates if rule.get("rule_profile") == "v4" and selected_customer in {value.casefold() for value in rule.get("customers") or []}]
    v4_mode = bool(scoped and customer_presets)
    unresolved_v4_customer = bool(scoped and not selected_customer and any(rule.get("rule_profile") == "v4" for rule in candidates))
    candidates = [rule for rule in candidates if rule.get("rule_profile") != "v4"] + (customer_presets if v4_mode else [])
    source_type = None
    source_types = {}
    if v4_mode:
        from audit.presets import customer_presets as source_presets

        # E document identities are source-owned. They do not extend the old
        # enum or use an unrelated customer's nearest document type.
        source_types = {
            preset.source_type: re.sub(r"审核规则$", "", preset.title) for preset in source_presets() if preset.customer.casefold() == selected_customer and preset.source_type.startswith("E")
        }
        source_matches = {
            preset.source_type
            for preset in source_presets()
            if preset.customer.casefold() == selected_customer and preset.source_type.startswith("E") and re.sub(r"审核规则$", "", preset.title) in filename
        }
        if len(source_matches) == 1:
            source_type = next(iter(source_matches))
        if selected_customer == "uaes":
            # E12 is an allowed original reference, never a 42nd formal rule.
            source_types["E12"] = "采购件质量计划"
            if (
                source_type is None
                and PpapDocumentType.infer_from_signals(filename=filename) == PpapDocumentType.UNKNOWN
                and re.search(r"PGQP1|F[-_ ]?PU[-_ ]?127|采购件质量计划", Path(filename).stem, re.IGNORECASE)
            ):
                source_type = "E12"
    # An enterprise-published document type is an explicit available identity,
    # not an invitation to route it through the nearest historical checker type.
    custom_types = {
        rule["type"]: rule["type"]
        for rule in candidates
        if rule.get("rule_profile") != "v4" and isinstance(rule.get("type"), str) and rule["type"].strip() and PpapDocumentType.from_string(rule["type"]) == PpapDocumentType.UNKNOWN
    }
    source_types.update(custom_types)
    custom_matches = [kind for kind in custom_types if kind in Path(filename).stem]
    if source_type is None and len(custom_matches) == 1:
        source_type = custom_matches[0]
    inferred = PpapDocumentType.infer_from_signals(filename=filename, content=excerpt[:2000], min_margin=1 if scoped else 0)
    if scoped and not source_type and excerpt.strip() and "metadata_extraction_failed" not in notices:
        evidence = PpapDocumentType.rank_from_signals(filename=filename, content=excerpt[:2000])
        if inferred == PpapDocumentType.UNKNOWN or not evidence[0].alias_hits:
            inferred = PpapDocumentType.UNKNOWN
            try:
                classified = ai(
                    "document_classification",
                    instructions=PromptManager.get_system_prompt("ppap_expert"),
                    prompt=PromptManager.get_document_classification_prompt(excerpt, "\n".join([*document_types(), *(f"{code} {title}" for code, title in source_types.items())]))
                    + '\n只返回 JSON：{"document_type": "上面列表中的一项，无法判断时为 未知"}',
                    max_tokens=100,
                    timeout=120,
                ).output.get("document_type")
                if not isinstance(classified, str) or not classified.strip():
                    raise ValueError("invalid_document_classification")
                classified_source = [code for code, title in source_types.items() if classified.strip() in {code, title, f"{code} {title}"}]
                source_type = classified_source[0] if len(classified_source) == 1 else None
                inferred = PpapDocumentType.from_string(classified)
                if not source_type and inferred == PpapDocumentType.UNKNOWN and classified.strip().casefold() not in {"未知", "unknown"}:
                    raise ValueError("invalid_document_classification")
            except Exception:
                notices.append("document_classification_failed")
    missing_v4_customer = unresolved_v4_customer and not (
        source_type in custom_types or inferred != PpapDocumentType.UNKNOWN and any(PpapDocumentType.from_string(rule.get("type") or "") == inferred for rule in candidates)
    )
    customer_conflict = bool((v4_mode or missing_v4_customer) and explicit_customers - {selected_customer})
    matched_id = None
    ambiguous_ids: list[str] = []
    matching = "unmatched"
    if scoped and any(notice in notices for notice in ("document_text_unavailable", "empty_document_text", "metadata_extraction_failed", "document_classification_failed")):
        matching = "failed"
    elif customer_conflict:
        matching = "failed"
        notices.append("matching_customer_conflict")
    elif missing_v4_customer:
        matching = "unmatched"
        notices.append("matching_customer_required")
    elif scoped and (reference_only or source_type == "E12"):
        matching = "reference"
    elif scoped and inferred == PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST and not v4_mode:
        matching = "reference"
    elif scoped:
        customers = tuple(dict.fromkeys(value.strip().casefold() for value in (selected_customer if v4_mode else customer_preference, metadata["customer"]) if value and value.strip()))
        typed_rules = [
            rule
            for rule in candidates
            if (rule.get("type") == source_type if source_type else inferred != PpapDocumentType.UNKNOWN and PpapDocumentType.from_string(rule.get("type") or "") == inferred)
        ]
        preset_typed = [rule for rule in typed_rules if rule.get("rule_profile") == "v4"]
        if preset_typed:
            typed_rules = preset_typed
        scoped_rules = [rule for rule in typed_rules if isinstance(rule.get("customers"), list) and any(isinstance(value, str) and value.strip() for value in rule["customers"])]
        unrestricted = [rule for rule in typed_rules if rule.get("customer_scope") == "all" and not rule.get("customers")]
        for customer in customers or (None,):
            applicable = unrestricted + [rule for rule in scoped_rules if customer in {value.strip().casefold() for value in rule["customers"] if isinstance(value, str)}]
            if len(applicable) == 1:
                matched_id = applicable[0]["id"]
                matching = "matched"
                break
            if len(applicable) > 1:
                ambiguous_ids = sorted(rule["id"] for rule in applicable)
                break
        if not matched_id:
            matching = "failed" if ambiguous_ids else "unmatched"
            if ambiguous_ids:
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
        "document_type": selected.get("type") if selected else (source_type or (None if inferred == PpapDocumentType.UNKNOWN else inferred.get_type_name())),
        "matched_rule_id": matched_id,
        "matching_status": matching,
        "notices": notices,
        **({"candidate_rule_ids": ambiguous_ids} if ambiguous_ids else {}),
    }
