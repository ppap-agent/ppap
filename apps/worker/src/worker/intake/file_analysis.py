"""Application-level pre-audit metadata and rule suggestions."""

from __future__ import annotations

import html
import re
from pathlib import Path

import documents.ai
from audit.kinds import PpapDocumentType
from product.rules import document_types, latest_rule_versions

# Both answers are short JSON, but some providers count hidden reasoning in the cap.
_ANSWER_TOKENS = 4000
_METADATA_FIELDS = ("customer", "model", "project", "part", "part_number")
# The metadata model reads the head of every sheet (its first rows that hold values), not one sheet's empty cells.
_SHEET_HEAD_ROWS = 40
_METADATA_EXCERPT = 12000
_METADATA_PROMPT = "\n".join(
    (
        "从下面这份 PPAP 文件的内容中摘录业务信息。只返回 JSON：",
        '{"project": 项目, "customer": 客户, "model": 车型, "part_number": 零件号, "part": 零件名称, "blank": [标签存在但值为空或占位的字段名]}',
        "",
        "- 零件号、零件名称：取表头区或标签处写明的值。"
        "标签写法如「零件号 / 零件编号 / 件号 / 产品件号 / 零件代号 / 物料编号 / 测量对象 / Part No. / Part Number」"
        "「零件名称 / 产品名称 / 零件名 / Part Name」。值可能在标签右侧，也可能在标签正下方的格子里（标签在上、值在下）。",
        "- 写在名称栏里、但内容是件号编码的值归 part_number，不归 part；一格内件号与名称连写（如 F01RD0YA41毛坯壳体）时，件号归 part_number、名称归 part。",
        "- 项目、客户、车型：只取写明了「项目 / Project」「客户 / Customer / 顾客 / 接受方 / 买方 / 采购方」「车型 / Vehicle」这类标签的值；"
        "不从文件名、文件编号（如 QJ-FMEA-GEELY-776）、表单号、公司名或没有标签的格子推断。",
        "- 照录原文，不改写、不翻译、不统一写法。同一格或同一栏内并列写了多个值时全部照录：分行或分列写的返回字符串列表，一格内用「/」并列的原样返回。"
        "一列里（如「产品名称/零件号」列）出现多个不同的值时，按出现顺序全部列出；相同的值只列一次。同一标签在每页页眉重复出现时只取第一处。",
        "- 标签存在但值为空或只是占位（如 /、-、N/A、TBD、待定、待确认、XXXX、CPJ-XXXXXX 这类模板占位）时，该字段返回 null，并把字段名放进 blank。",
        "- 文件里没有该字段时返回 null。",
        "",
        "文件内容（工作表只列出有值的格子，形如 地址=值）：",
        "",
    )
)


def _compact(text: str) -> str:
    """Spreadsheet text as the head of each sheet: one line per row, only cells that hold a value."""
    if "<SHEET" not in text:
        return text
    sheets = []
    for sheet in re.finditer(r'<SHEET name="([^"]*)"[^>]*>(.*?)</SHEET>', text, re.DOTALL):
        lines = []
        for row in re.finditer(r"<ROW[^>]*>(.*?)</ROW>", sheet.group(2), re.DOTALL):
            found = re.finditer(r'<CELL[^>]*address="([A-Z]+\d+)"[^>]*>(.*?)</CELL>', row.group(1), re.DOTALL)
            cells = [f"{cell.group(1)}={value}" for cell in found if (value := html.unescape(cell.group(2)).strip())]
            if cells:
                lines.append(" | ".join(cells))
            if len(lines) == _SHEET_HEAD_ROWS:
                break
        if lines:
            sheets.append(f"[工作表 {html.unescape(sheet.group(1))}]\n" + "\n".join(lines))
    return "\n".join(sheets)


def _placeholder(value: str) -> bool:
    from product.precheck_content import blank

    return blank(value) or bool(re.search(r"x{4,}", value, re.IGNORECASE))


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
            "metadata": {"customer": None, "model": None, "project": None, "part": None, "part_number": None},
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
    metadata: dict[str, str | None] = dict.fromkeys(_METADATA_FIELDS)
    if excerpt.strip():
        try:
            # The file name is no source of these fields (裁定 R-06, R-13): the model reads the content only.
            parsed = ai(
                "document_metadata_extraction",
                instructions=PromptManager.get_system_prompt("document_analyst"),
                prompt=_METADATA_PROMPT + _compact(text)[:_METADATA_EXCERPT],
                max_tokens=_ANSWER_TOKENS,
                timeout=240,  # every sheet head of a long table takes the model longer than classification
            ).output
            blank_fields = {field for field in parsed.get("blank") or [] if isinstance(field, str) and field in metadata} if isinstance(parsed.get("blank"), list) else set()
            for field in metadata:
                value = parsed.get(field)
                if isinstance(value, list):
                    # 裁定 R-09: several values written side by side are all kept, never narrowed to one.
                    value = "\n".join(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))
                if isinstance(value, str) and _placeholder(value):
                    blank_fields.add(field)
                elif isinstance(value, str):
                    metadata[field] = value.strip()[:500] or None
            notices.extend(f"metadata_blank_{field}" for field in _METADATA_FIELDS if field in blank_fields)
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
    # What each of the customer's rules expects the document to contain, from the rule itself.
    described: dict[str, str] = {}
    if v4_mode:
        from audit.presets import customer_presets as source_presets
        from audit.presets import preset_document_type

        described = {
            preset_document_type(preset): re.sub(r"\s+", " ", preset.clauses[0].text)[:120] for preset in source_presets() if preset.customer.casefold() == selected_customer and preset.clauses
        }

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
    # Body text cites other documents (cover checklists, Cp next to CP), so only
    # the submitted filename decides a scoped type; otherwise the model reads the text.
    signal_content = "" if scoped else excerpt[:2000]
    inferred = PpapDocumentType.infer_from_signals(filename=filename, content=signal_content, min_margin=1 if scoped else 0)
    if scoped and not source_type and excerpt.strip() and "metadata_extraction_failed" not in notices:
        evidence = PpapDocumentType.rank_from_signals(filename=filename)
        if inferred == PpapDocumentType.UNKNOWN or not evidence or not evidence[0].alias_hits:
            inferred = PpapDocumentType.UNKNOWN
            try:
                classified = ai(
                    "document_classification",
                    instructions=PromptManager.get_system_prompt("ppap_expert"),
                    prompt=PromptManager.get_submitted_document_classification_prompt(
                        excerpt,
                        "\n".join(
                            f"{option}：{described[kind]}" if kind in described else option
                            for option, kind in [*((name, name) for name in document_types()), *((f"{code} {title}", code) for code, title in source_types.items())]
                        ),
                    )
                    + '\n只返回 JSON：{"document_type": "上面列表中的一项类型名称，无法判断时为 未知"}',
                    max_tokens=_ANSWER_TOKENS,
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
        # The chosen rule set is the only scope; the customer name a file writes is one of its facts, not a rule set.
        # Only when no rule set is chosen ("自动识别") does that name pick the rules.
        chosen = (selected_customer if v4_mode else customer_preference or "").strip().casefold()
        written = (metadata["customer"] or "").strip().casefold()
        customers = (chosen,) if chosen else ((written,) if written else ())
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
