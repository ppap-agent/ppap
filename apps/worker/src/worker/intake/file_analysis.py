"""Application-level pre-audit metadata and rule suggestions."""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import documents.ai
from audit.kinds import PpapDocumentType
from product.customers import COUNTERPART_CONTENTS, COUNTERPARTS
from product.rules import document_types

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


# These are study form headings, not incidental mentions of MSA in a checklist.
# Measurement-process capability (Cg/Cgk) is distinct from part process capability (Cp/Cpk).
_MSA_FORM = re.compile(
    r"^(?:[计計][数數量]型(?:[测測]量系[统統])?\s*MSA\s*分析[表报報]|"
    r"量具重复性[与和及]再现性(?:数据表|分析表|报告)|"
    r"(?:量具|测量(?:工序|过程))能力(?:研究|分析|方法)|"
    r"capability of measurement processes\b|"
    r"gage\s+r\s*&\s*r[_ :–-]*hypothesis test analyses\b)",
    re.IGNORECASE,
)


def _sheet_heads(text: str) -> Iterator[tuple[str, list[list[tuple[str, str]]]]]:
    for sheet in re.finditer(r"""<SHEET name=(["'])(?P<title>.*?)\1>(?P<body>.*?)</SHEET>""", text, re.DOTALL):
        rows = []
        for row in re.finditer(r"<ROW[^>]*>(.*?)</ROW>", sheet["body"], re.DOTALL):
            found = re.finditer(r'<CELL[^>]*address="([A-Z]+\d+)"[^>]*>(.*?)</CELL>', row.group(1), re.DOTALL)
            cells = [(cell.group(1), value) for cell in found if (value := html.unescape(cell.group(2)).strip())]
            if cells:
                rows.append(cells)
            if len(rows) == _SHEET_HEAD_ROWS:
                break
        if rows:
            yield html.unescape(sheet["title"]), rows


def _compact(sheets: list[tuple[str, list[list[tuple[str, str]]]]], limit: int) -> tuple[str, bool]:
    """Share the prompt budget across sheet heads, keeping only complete rows."""
    blocks = [[f"[工作表 {title}]", *(" | ".join(f"{addr}={value}" for addr, value in row) for row in rows)] for title, rows in sheets]
    sizes = [sum(len(line) + 1 for line in lines) for lines in blocks]
    budget, clipped = limit + 1, False  # one newline per line, except the last
    kept = [""] * len(blocks)
    for rank, index in enumerate(sorted(range(len(blocks)), key=sizes.__getitem__)):
        # Short sheets take only what they need; share the rest among the larger ones.
        room = budget // (len(blocks) - rank)
        lines = []
        for line in blocks[index]:
            if len(line) + 1 > room:
                clipped = True
                break
            lines.append(line)
            room -= len(line) + 1
            budget -= len(line) + 1
        kept[index] = "\n".join(lines)
    return "\n".join(block for block in kept if block), clipped


def _has_msa_heading(sheets: list[tuple[str, list[list[tuple[str, str]]]]]) -> bool:
    # Read the leading value of the sheet itself; a study named in another form's attachment list is not its title.
    return any(_MSA_FORM.match(unicodedata.normalize("NFKC", rows[0][0][1])) for _, rows in sheets)


def _placeholder(value: str) -> bool:
    from product.precheck_content import blank

    return blank(value) or bool(re.search(r"x{4,}", value, re.IGNORECASE))


def analyze_file(
    filename: str,
    content: bytes,
    rules: list[dict],
    *,
    customer_preference: str,
    ai=None,
    reference_only: bool = False,
    validation_scope: dict | None = None,
) -> dict:
    """Recognize a file of an accepted task among the candidates the task froze for it, within the rule set the task chose.

    The customer a file writes is extracted as a fact only; it never picks or checks the rule set.
    """
    ai = ai or documents.ai.run_json
    from audit.engine.checkers.prompts import PromptManager
    from documents.extractors import get_extractor

    if validation_scope is not None:
        result = {
            "metadata": {"customer": None, "model": None, "project": None, "part": None, "part_number": None},
            "document_type": None,
            "matched_rule_id": None,
            "matching_status": "failed",
            "notices": ["matching_document_type_required"],
        }
        if customer_preference != "UAES" or validation_scope != {"customer_preference": "UAES", "preset_key": "U13"}:
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
    sheets = list(_sheet_heads(text)) if "<SHEET" in text else []
    metadata_text, clipped = _compact(sheets, _METADATA_EXCERPT) if sheets else (text[:_METADATA_EXCERPT], len(text) > _METADATA_EXCERPT)
    if clipped or len(text) > 20000:
        notices.append("metadata_uses_excerpt")
    metadata: dict[str, str | None] = dict.fromkeys(_METADATA_FIELDS)
    if text.strip():
        try:
            # The file name is no source of these fields (裁定 R-06, R-13): the model reads the content only.
            parsed = ai(
                "document_metadata_extraction",
                instructions=PromptManager.get_system_prompt("document_analyst"),
                prompt=_METADATA_PROMPT + metadata_text,
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

    candidates = [rule for rule in rules if rule.get("kind") == "engine"]
    customer_presets = [rule for rule in candidates if rule.get("rule_profile") == "v4" and customer_preference in (rule.get("customers") or [])]
    v4_mode = bool(customer_presets)
    candidates = [rule for rule in candidates if rule.get("rule_profile") != "v4"] + customer_presets
    source_type = None
    source_types = {}
    # What each of the customer's rules expects the document to contain, from the rule itself.
    described: dict[str, str] = {}
    if v4_mode:
        from audit.presets import customer_presets as source_presets
        from audit.presets import preset_document_type

        described = {preset_document_type(preset): re.sub(r"\s+", " ", preset.clauses[0].text)[:120] for preset in source_presets() if preset.customer == customer_preference and preset.clauses}

        # E document identities are source-owned. They do not extend the old
        # enum or use an unrelated customer's nearest document type.
        source_types = {preset.source_type: re.sub(r"审核规则$", "", preset.title) for preset in source_presets() if preset.customer == customer_preference and preset.source_type.startswith("E")}
        source_matches = {
            preset.source_type for preset in source_presets() if preset.customer == customer_preference and preset.source_type.startswith("E") and re.sub(r"审核规则$", "", preset.title) in filename
        }
        if len(source_matches) == 1:
            source_type = next(iter(source_matches))
        if customer_preference == "UAES":
            # Counterparts (E12 PGQP1, E11A deviation list) are allowed original references, never formal rules.
            source_types["E12"] = "采购件质量计划"
            source_types["E11A"] = COUNTERPARTS["E11A"]
            described.update(COUNTERPART_CONTENTS)
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
    # Prefer an explicit filename alias. If none, a UAES study form's own heading
    # is stronger evidence than model interpretation of raw spreadsheet XML.
    inferred = PpapDocumentType.infer_from_signals(filename=filename, min_margin=1)
    if not source_type and text.strip() and "metadata_extraction_failed" not in notices:
        evidence = PpapDocumentType.rank_from_signals(filename=filename)
        needs_content = inferred == PpapDocumentType.UNKNOWN or not evidence or not evidence[0].alias_hits
        if needs_content and customer_preference == "UAES" and _has_msa_heading(sheets):
            inferred = PpapDocumentType.MSA_REPORT
        elif needs_content:
            inferred = PpapDocumentType.UNKNOWN
            try:
                classified = ai(
                    "document_classification",
                    instructions=PromptManager.get_system_prompt("ppap_expert"),
                    prompt=PromptManager.get_submitted_document_classification_prompt(
                        _compact(sheets, 4000)[0] if sheets else text,  # the classification prompt admits 4000 characters
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
    matched_id = None
    ambiguous_ids: list[str] = []
    matching = "unmatched"
    if any(notice in notices for notice in ("document_text_unavailable", "empty_document_text", "metadata_extraction_failed", "document_classification_failed")):
        matching = "failed"
    elif reference_only or source_type in COUNTERPARTS:
        matching = "reference"
    elif inferred == PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST and not v4_mode:
        matching = "reference"
    else:
        # The chosen rule set is the only scope; the customer name a file writes is one of its facts, not a rule set.
        typed_rules = [
            rule
            for rule in candidates
            if (rule.get("type") == source_type if source_type else inferred != PpapDocumentType.UNKNOWN and PpapDocumentType.from_string(rule.get("type") or "") == inferred)
        ]
        preset_typed = [rule for rule in typed_rules if rule.get("rule_profile") == "v4"]
        if preset_typed:
            typed_rules = preset_typed
        # A rule belongs to the chosen rule set by its registered key, exactly, or applies to every customer.
        applicable = [rule for rule in typed_rules if customer_preference in (rule.get("customers") or []) or (rule.get("customer_scope") == "all" and not rule.get("customers"))]
        if len(applicable) == 1:
            matched_id = applicable[0]["id"]
            matching = "matched"
        elif len(applicable) > 1:
            ambiguous_ids = sorted(rule["id"] for rule in applicable)
        if not matched_id:
            matching = "failed" if ambiguous_ids else "unmatched"
            if ambiguous_ids:
                notices.append("ambiguous_available_rules")
            elif inferred == PpapDocumentType.UNKNOWN:
                notices.append("matching_document_type_required")
            elif not typed_rules:
                notices.append("matching_document_type_rules_missing")
            elif not any(rule.get("customers") for rule in typed_rules):
                notices.append("matching_rules_customer_unassigned")
            else:
                notices.append("matching_customer_rules_missing")
    selected = next((rule for rule in candidates if rule["id"] == matched_id), None)
    return {
        "metadata": metadata,
        "document_type": selected.get("type") if selected else (source_type or (None if inferred == PpapDocumentType.UNKNOWN else inferred.get_type_name())),
        "matched_rule_id": matched_id,
        "matching_status": matching,
        "notices": notices,
        **({"candidate_rule_ids": ambiguous_ids} if ambiguous_ids else {}),
    }
