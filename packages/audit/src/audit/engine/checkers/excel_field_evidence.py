"""Read facts from labelled Excel fields with application-owned provenance."""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Callable, Literal, cast
from urllib.parse import unquote, urlparse

import fitz
from openpyxl.utils.cell import get_column_letter, range_boundaries
from openpyxl.worksheet.worksheet import Worksheet
from PIL import Image

from audit.contracts import AuditEvidence, AuditInput, AuditResultItem, EvidenceDiagnostic, EvidenceSource
from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images
from audit.engine.checkers.psw_visual_evidence import PSW_RULE_FIELDS, evaluate_psw_observation
from audit.engine.rule_engine import RuleContext


def observe_excel_region(
    audit: AuditInput, sheet: Worksheet, cell_range: str | None, fields: dict[str, str], keywords: tuple[str, ...], *, cache: dict[Any, Any], witness: Callable[..., Any], whole_field: bool = False
) -> dict[str, object]:
    from documents.excel.visual import render_xlsx_field, render_xlsx_pdf, renderable_xlsx
    from documents.ocr.env import ocr_model_name, ocr_openai_api_key, ocr_openai_base_url, ocr_policy_identity

    from audit.engine.file_bytes import load_file_bytes

    original, _ = load_file_bytes(str(audit.file_url))
    digest = hashlib.sha256(original).hexdigest()
    expected = (audit.file_metadata or {}).get("source_sha256") or (audit.file_metadata or {}).get("sha256")
    if (expected and expected != digest) or cache.get("source_sha256", digest) != digest:
        raise ValueError("Frozen Excel identity changed")
    cache["source_sha256"] = digest
    try:
        configured = bool(ocr_model_name() and ocr_openai_api_key() and ocr_openai_base_url())
    except ValueError:
        configured = False
    if not configured:
        return dict.fromkeys(fields)
    normalized = renderable_xlsx(original)
    if not normalized.startswith(b"PK"):
        return dict.fromkeys(fields)
    key = (digest, hashlib.sha256(normalized).hexdigest(), sheet.title, cell_range, tuple(sorted(fields.items())), whole_field, keywords, ocr_policy_identity())
    if key not in cache:
        for previous, value in tuple(cache.items()):
            if not isinstance(previous, tuple) or len(previous) != len(key) or previous[:4] != key[:4] or previous[5:] != key[5:]:
                continue
            if not set(fields.items()).issubset(set(previous[4])):
                continue
            if isinstance(value, Exception):
                cache[key] = value
            else:
                known, recorded = value
                relevant = [(args, kwargs) for args, kwargs in recorded if args[0].get("fact") in fields]
                cache[key] = ({field: known[field] for field in fields}, relevant)
            break
    if key not in cache:
        sources = []
        try:
            region_location: dict[str, Any] = {}
            native_context: str | None = None
            if whole_field:
                from audit.engine.checkers.pdf_field_region import pdf_field_region

                print_key = ("xlsx_pdf", hashlib.sha256(normalized).hexdigest(), sheet.title, cell_range)
                if print_key not in cache:
                    try:
                        cache[print_key] = render_xlsx_pdf(normalized, sheet_name=sheet.title, cell_range=cell_range)
                    except Exception as error:
                        cache[print_key] = error
                pdf = cache[print_key]
                if isinstance(pdf, Exception):
                    raise pdf
                with fitz.open(stream=pdf, filetype="pdf") as document:
                    if len(document) != 1:
                        raise ValueError("Excel field did not render to one page")
                    page = document[0]
                    bounds = pdf_field_region(page, fields)
                    if not any(page.search_for(keyword) for keyword in keywords):
                        cache[key] = (dict.fromkeys(fields), [])
                        return dict.fromkeys(fields)
                    scale = min(4, 4096 / max(bounds.width, bounds.height))
                    data = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=bounds, alpha=False).tobytes("png")
                    native_context = page.get_textbox(bounds)
                    region_location = {"prepared_page": 1, "prepared_bbox": list(bounds), "prepared_sha256": hashlib.sha256(pdf).hexdigest()}
            else:
                if cell_range is None:
                    raise ValueError("An Excel field needs a selected range")
                data = render_xlsx_field(normalized, sheet_name=sheet.title, cell_range=cell_range, keywords=keywords)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
            located = OcrImage(
                data,
                {
                    "kind": "xlsx_field",
                    "sheet": sheet.title,
                    "cell_range": cell_range,
                    "source_sha256": digest,
                    "file_version_id": str((audit.file_metadata or {}).get("file_version_id") or audit.audit_id),
                    "scope": f"xlsx:{sheet.title}:{cell_range or 'worksheet'}",
                    "context": True,
                    "located_region": True,
                    "bbox": [0, 0, width, height],
                    **region_location,
                },
                width,
                height,
            )
            context = native_context if native_context is not None else "\n".join(f"{cell.coordinate}: {cell.value}" for row in sheet[cell_range] for cell in row if cell.value is not None)
            facts = observe_ocr_images([located], fields, lambda *args, **kwargs: sources.append((args, kwargs)), context=context)
            cache[key] = facts, sources
        except Exception as error:
            cache[key] = error
    cached = cache[key]
    if isinstance(cached, Exception):
        raise cached
    facts, sources = cached
    for args, kwargs in sources:
        witness(*args, **kwargs)
    return dict(facts)


def _compact(value: Any) -> str:
    return re.sub(r"[\s:：()（）]", "", str(value or "")).casefold()


_SIGNATURE_LABELS = ("经授权的供方代表签字", "经授权签字", "organization authorized signature", "authorized signature")
_STAMP_LABELS = ("公司名称（公司章）", "公司名称(公司章)", "公司章", "company stamp")
_SIGNATURE_STOPS = ("日期", "date", "代表印刷体姓名", "print name", "电话", "phone", "传真", "fax", "职务", "title", "顾客签字", "customer signature", "公司名称", "认可情况")
_STAMP_STOPS = ("认可情况", "仅供理想使用", "warrant disposition", "for li use only")


def _field_region(sheet: Worksheet, labels: tuple[str, ...], stops: tuple[str, ...], *, block: bool = False) -> str | None:
    labels = tuple(_compact(label) for label in labels)
    anchors = [cell for row in sheet.iter_rows(max_row=min(sheet.max_row, 200), max_col=min(sheet.max_column, 60)) for cell in row if any(_compact(cell.value).startswith(label) for label in labels)]
    if not anchors:
        return None
    first = anchors[0]
    # Bilingual labels can occupy adjacent rows; separate business fields must
    # never be collapsed into the first matching location.
    if any(cell.column != first.column or abs(cell.row - first.row) > 1 for cell in anchors):
        return None
    merged = next((area for area in sheet.merged_cells.ranges if first.coordinate in area), None)
    left, top = first.column, first.row
    label_right = merged.max_col if merged else first.column
    bottom = min(sheet.max_row, 200) if block else max((next((area.max_row for area in sheet.merged_cells.ranges if cell.coordinate in area), cell.row) for cell in anchors))
    right = sheet.max_column
    if not block and right == label_right:
        return None
    stops = tuple(_compact(label) for label in stops)
    for row in sheet.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right):
        for cell in row:
            if not any(_compact(cell.value).startswith(stop) for stop in stops):
                continue
            if cell.row == top and cell.column > label_right:
                right = min(right, cell.column - 1)
            elif cell.row > top and cell.column <= right:
                bottom = min(bottom, cell.row - 1)
    if block and sheet.max_row > 200 and bottom == 200:
        return None  # A search limit does not close the business field.
    bottom = max(top, bottom)
    right = max(label_right, right)
    return f"{get_column_letter(left)}{top}:{get_column_letter(right)}{bottom}"


def read_native_signature(ctx: RuleContext, sheet: Worksheet, region: str | None = None) -> tuple[bool | None, str]:
    region = region or _field_region(sheet, _SIGNATURE_LABELS, _SIGNATURE_STOPS)
    if region is None:
        return None, ""
    values = []
    unknown = False
    for row in sheet[region]:
        for cell in row:
            ctx.note_cell(sheet.title, cell.row, cell.column)
            if cell.data_type == "e":
                unknown = True
                continue
            text = _compact(cell.value)
            for label in _SIGNATURE_LABELS:
                if text.startswith(_compact(label)):
                    text = text.removeprefix(_compact(label))
            if text and text not in {"/", "na", "n/a", "none", "nan"} and re.fullmatch(r"[_\-—.]+", text) is None:
                values.append(text)
    return (None if unknown else bool(values)), "\n".join(values)


def classify_psw_disposition(sheet: Any, checked_row: int, checked_col: int, disposition_row: int) -> str:
    """Bind a control to a nearby option label; column numbers carry no meaning."""
    choices = []
    for row in sheet.ws.iter_rows(min_row=max(1, disposition_row - 1), max_row=disposition_row + 2, max_col=min(sheet.ws.max_column, 30)):
        for cell in row:
            text = _compact(cell.value)
            ordinary = re.sub(r"条件批准|conditional(?:approved)?", "", text)
            options = [
                name
                for name, found in (
                    ("conditional", "条件批准" in text or "conditional" in text),
                    ("rejected", "拒绝" in text or "rejected" in text),
                    ("approved", "批准" in ordinary or "approved" in ordinary),
                )
                if found
            ]
            if len(options) != 1:
                continue
            option = options[0]
            area = next((area for area in sheet.ws.merged_cells.ranges if cell.coordinate in area), None)
            left, right = cell.column, area.max_col if area else cell.column
            top, bottom = cell.row, area.max_row if area else cell.row
            row_distance = max(top - checked_row, checked_row - bottom, 0)
            col_distance = max(left - checked_col, checked_col - right, 0)
            if row_distance <= 1 and col_distance <= 3:
                choices.append((row_distance * 10 + col_distance, option, cell))
    if not choices:
        return "unknown"
    nearest = min(item[0] for item in choices)
    matched = [item for item in choices if item[0] == nearest]
    if len({item[1] for item in matched}) != 1:
        return "unknown"
    for _, _, cell in matched:
        sheet.ctx.note_cell(sheet.title, cell.row, cell.column)
    sheet.ctx.note_cell(sheet.title, checked_row, checked_col)
    return matched[0][1]


def psw_submission_results(sheet: Any) -> bool | None:
    """Each required result owns its mark; an adjacent option cannot lend one."""
    aliases = {
        "dimension": ("尺寸测量", "dimensionalmeasurement"),
        "appearance": ("外观标准", "appearancecriteria"),
        "material": ("材料和功能试验", "材料及功能试验", "materialandfunctionaltests", "materialfunctionaltests"),
        "statistics": ("统计过程数据", "statisticalprocesspackage", "statisticalprocessdata"),
    }
    labels: dict[str, Any] = {}
    required = (*aliases, "yes")
    overall = []
    for row in sheet.ws.iter_rows(max_row=min(sheet.ws.max_row, 200), max_col=min(sheet.ws.max_column, 60)):
        for cell in row:
            text = _compact(cell.value)
            if "这些结果均满足" in text or "theseresult" in text and "meetall" in text:
                overall.append(cell)
            for name, options in aliases.items():
                if any(option in text for option in options):
                    if name in labels:
                        return None
                    labels[name] = cell
    if len(overall) != 1 or any(name not in labels for name in aliases):
        return None
    anchor = overall[0]
    sheet.ctx.note_cell(sheet.title, anchor.row, anchor.column)
    for row in sheet.ws.iter_rows(min_row=anchor.row, max_row=anchor.row + 1, min_col=anchor.column, max_col=min(sheet.ws.max_column, anchor.column + 15)):
        for cell in row:
            text = re.sub(r"^[☑☒✓✔√□☐\s]+", "", str(cell.value or "")).strip().casefold()
            option = "yes" if text in {"是", "yes", "是yes"} else "no" if text in {"否", "no", "否no"} else None
            if option is not None:
                if option in labels:
                    return None
                labels[option] = cell
    if "yes" not in labels or max(cell.row for cell in labels.values()) - min(cell.row for cell in labels.values()) > 20:
        return None
    observations: dict[str, list[bool]] = {name: [] for name in labels}
    for name, cell in labels.items():
        sheet.ctx.note_cell(sheet.title, cell.row, cell.column)
        marks = re.findall(r"[☑☒✓✔√□☐]", str(cell.value or ""))
        if len(marks) == 1:
            observations[name].append(marks[0] not in "□☐")
    for number, column, checked in sheet.checkbox_controls():
        candidates = []
        for name, cell in labels.items():
            area = next((area for area in sheet.ws.merged_cells.ranges if cell.coordinate in area), None)
            bottom = area.max_row if area else cell.row
            right = area.max_col if area else cell.column
            row_distance = max(cell.row - number, number - bottom, 0)
            col_distance = max(cell.column - column, column - right, 0)
            if row_distance <= 1 and col_distance <= 3:
                candidates.append((row_distance * 10 + col_distance, name))
        if not candidates:
            continue
        best = min(distance for distance, _ in candidates)
        owners = [name for distance, name in candidates if distance == best]
        if len(owners) == 1:
            sheet.ctx.note_cell(sheet.title, number, column)
            observations[owners[0]].append(checked)
    values = [set(observations[name]) for name in required]
    if any(value == {False} for value in values):
        return False
    return True if all(value == {True} for value in values) else None


def check_psw_submission_reason(agent: Any, ctx: RuleContext) -> tuple[str, str]:
    from documents.excel.visual import has_region_graphics, renderable_xlsx

    from audit.engine.file_bytes import load_file_bytes
    from audit.engine.rule_engine import Table

    sheet = Table(ctx).sheet("PSW主表")
    labels = ("提交原因", "reason for submission")
    region = _field_region(sheet.ws, labels, ("提交结果", "submission results", "供方声明", "declaration", "认可情况", "warrant disposition", *_SIGNATURE_LABELS), block=True)
    if region is None:
        return "UNABLE", "无法确定“提交原因”的完整区域。"
    left, top, right, bottom = range_boundaries(region)
    facts: dict[str, object] = {"submission_any_checked": False, "submission_other_present": False}
    for row, column, checked in sheet.checkbox_controls():
        if left <= column <= right and top <= row <= bottom:
            ctx.note_cell(sheet.title, row, column)
            if checked:
                facts["submission_any_checked"] = True
    other_label = re.compile(r"其他\s*[-—:：]?\s*请在下方说明|other\s*[-:]?\s*specify\s*below", re.I)
    for row in sheet.ws[region]:
        for cell in row:
            ctx.note_cell(sheet.title, cell.row, cell.column)
            if cell.data_type == "e":
                facts["submission_other_present"] = None
                continue
            text = str(cell.value or "")
            if any(mark in text for mark in "☑☒✓✔√"):
                facts["submission_any_checked"] = True
            label = other_label.search(text)
            if label is None:
                continue
            area = next((area for area in sheet.ws.merged_cells.ranges if cell.coordinate in area), None)
            positions = [(cell.row, (area.max_col if area else cell.column) + 1), ((area.max_row if area else cell.row) + 1, cell.column)]
            notes = [text[label.end() :]]
            for number, column in positions:
                if left <= column <= right and top <= number <= bottom:
                    candidate = sheet.ws.cell(number, column)
                    ctx.note_cell(sheet.title, number, column)
                    if candidate.data_type == "e":
                        facts["submission_other_present"] = None
                    else:
                        notes.append(str(candidate.value or ""))
            if any(other_label.sub("", value).strip(" \r\n:-：—_/□☐") for value in notes):
                facts["submission_other_present"] = True
    sources = []
    if True not in facts.values():
        original, _ = load_file_bytes(str(agent.audit_file.file_url))
        normalized = renderable_xlsx(original)
        suffix = Path(unquote(urlparse(str(agent.audit_file.file_url)).path)).suffix.lower()
        if suffix in {".xls", ".xlsx", ".xlsm"} and normalized.startswith(b"PK") and has_region_graphics(normalized, sheet_name=sheet.title, cell_range=region):
            if not hasattr(agent, "_psw_field_cache"):
                agent._psw_field_cache = {}

            def witness(locator, excerpt, *, visual=False):
                sources.append(EvidenceSource(kind="visual" if visual else "structure", locator=json.dumps(locator, ensure_ascii=False, sort_keys=True), excerpt=str(excerpt)[:500]))

            facts.update(observe_excel_region(agent.audit_file, sheet.ws, region, PSW_RULE_FIELDS[7], labels, cache=agent._psw_field_cache, witness=witness, whole_field=True))
    evidence_reader = getattr(agent, "_evidence_from_reads", None)
    evidence = evidence_reader(ctx) if callable(evidence_reader) else native_cell_evidence(ctx, str(agent.audit_file.file_url))
    if evidence is not None and sources:
        evidence = evidence.model_copy(update={"required": "visual", "used": "both", "prepared_format": "png", "sources": (*evidence.sources, *sources)})
    if not hasattr(agent, "_psw_field_evidence"):
        agent._psw_field_evidence = {}
    agent._psw_field_evidence[7] = evidence
    result = evaluate_psw_observation(7, facts)
    if result.checks_performed:
        ctx.record_execution()  # The composite submission-reason predicate was decided.
    return result.status, result.suggestions


def check_psw_excel_fields(agent: Any, ctx: RuleContext, rule_no: int) -> AuditResultItem:
    from documents.excel.visual import has_region_graphics, renderable_xlsx

    from audit.engine.file_bytes import load_file_bytes
    from audit.engine.rule_engine import Decision, Table

    table = Table(ctx)
    sheet = table.sheet("PSW主表").ws
    original, _ = load_file_bytes(str(agent.audit_file.file_url))
    normalized = renderable_xlsx(original)
    facts: dict[str, Any] = dict.fromkeys(PSW_RULE_FIELDS[rule_no])
    sources: list[EvidenceSource] = []
    cache = getattr(agent, "_psw_field_cache", None)
    if cache is None:
        cache = agent._psw_field_cache = {}

    digest = hashlib.sha256(original).hexdigest()
    expected = (agent.audit_file.file_metadata or {}).get("source_sha256") or (agent.audit_file.file_metadata or {}).get("sha256")
    if (expected and expected != digest) or cache.get("source_sha256", digest) != digest:
        raise ValueError("Frozen Excel identity changed")
    cache["source_sha256"] = digest

    def witness(locator, excerpt, *, visual=False):
        source = EvidenceSource(kind="visual" if visual else "structure", locator=json.dumps(locator, ensure_ascii=False, sort_keys=True), excerpt=str(excerpt)[:500])
        if source not in sources:
            sources.append(source)

    def read(target, labels, stops, fields, *, signature=False):
        region = _field_region(target, labels, stops, block=not signature)
        if not normalized.startswith(b"PK"):
            return dict.fromkeys(fields)
        if region is None:
            # Some originals have no printed stamp box. Absence then requires
            # the complete warrant worksheet; another format is not evidence.
            if has_region_graphics(normalized, sheet_name=target.title, cell_range=None):
                return observe_excel_region(agent.audit_file, target, None, fields, (*labels, *_SIGNATURE_LABELS), cache=cache, witness=witness, whole_field=True)
            region = f"A1:{get_column_letter(target.max_column)}{target.max_row}"
            if signature:
                return dict.fromkeys(fields)
        graphics = has_region_graphics(normalized, sheet_name=target.title, cell_range=region)
        if graphics:
            return observe_excel_region(agent.audit_file, target, region, fields, labels, cache=cache, witness=witness, whole_field=True)
        locator = {
            "kind": "xlsx_field",
            "sheet": target.title,
            "cell_range": region,
            "source_sha256": hashlib.sha256(original).hexdigest(),
            "file_version_id": str((agent.audit_file.file_metadata or {}).get("file_version_id") or agent.audit_file.audit_id),
        }
        value, excerpt = read_native_signature(ctx, target, region) if signature else (False, "完整区域未包含图形印章对象")
        witness(locator, excerpt)
        return {field: ("" if field == "psw_stamp_ending_text" and value is False else value) for field in fields}

    try:
        if rule_no == 5:
            fields = {"authorized_signature_present": PSW_RULE_FIELDS[5]["authorized_signature_present"]}
            facts.update(read(sheet, _SIGNATURE_LABELS, _SIGNATURE_STOPS, fields, signature=True))
            if facts["authorized_signature_present"] is False:
                return evaluate_psw_observation(rule_no, facts)
        stamp_fields = {field: description for field, description in PSW_RULE_FIELDS[rule_no].items() if field in {"psw_stamp_present", "psw_stamp_ending_text"}}
        facts.update(read(sheet, _STAMP_LABELS, _STAMP_STOPS, stamp_fields))
        decision = evaluate_psw_observation(rule_no, facts)
        if rule_no == 3 and decision.status == "UNABLE":
            declarations = table.find_sheets("部门章授权声明", on_missing=Decision.IGNORE)
            facts["authorization_table_present"] = bool(declarations)
            if len(declarations) == 1:
                fields = {"authorization_company_stamp_present": PSW_RULE_FIELDS[3]["authorization_company_stamp_present"]}
                facts.update(read(declarations[0].ws, _STAMP_LABELS, (), fields))
            decision = evaluate_psw_observation(rule_no, facts)
        return decision
    finally:
        kinds = {source.kind for source in sources}
        suffix = Path(unquote(urlparse(str(agent.audit_file.file_url)).path)).suffix.lstrip(".").lower()
        used = cast(Literal["none", "structure", "visual", "both"], "both" if len(kinds) == 2 else next(iter(kinds), "none"))
        evidence = AuditEvidence(
            required="visual", used=used, source_format=cast(Literal["xls", "xlsx", "xlsm"], suffix), prepared_format="png" if "visual" in kinds else "xlsx", sources=tuple(sources)
        )
        if not hasattr(agent, "_psw_field_evidence"):
            agent._psw_field_evidence = {}
        agent._psw_field_evidence[rule_no] = evidence


def native_cell_evidence(ctx: RuleContext, location: str) -> AuditEvidence | None:
    reads = ctx.cell_reads()
    if not reads:
        return None
    sources = []
    for version, sheet, row, column in reads:
        payload = {"kind": "xlsx_cell", "sheet": sheet, "cell": f"{get_column_letter(column)}{row}"}
        if version:
            payload["file_version_id"] = version
        sources.append(
            EvidenceSource(
                kind="structure",
                locator=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                excerpt=ctx.cell_excerpt(version, sheet, row, column),
            )
        )
    diagnostics: tuple[EvidenceDiagnostic, ...] = ()
    if ctx.cell_reads_truncated():
        diagnostics = (
            EvidenceDiagnostic(
                code="read_locations_truncated",
                stage="structure",
                severity="warning",
                message="本次读取位置超过展示上限。未列出的读取已经执行，展示上限不是完整定位。",
            ),
        )
    suffix = Path(unquote(urlparse(location).path)).suffix.lower().lstrip(".")
    source_format = suffix if suffix in {"xlsx", "xls", "xlsm", "pdf", "docx", "doc"} else "xlsx"
    prepared_format = "xlsx" if source_format in {"pdf", "doc"} else source_format if source_format in {"xlsx", "xls", "xlsm", "docx"} else "xlsx"
    return AuditEvidence(
        required="structure",
        used="structure",
        source_format=cast(Literal["xlsx", "xls", "xlsm", "pdf", "docx", "doc"], source_format),
        prepared_format=cast(Literal["xlsx", "xls", "xlsm", "pdf", "docx"], prepared_format),
        sources=tuple(sources),
        diagnostics=diagnostics,
    )
