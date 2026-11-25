"""UAES U08 revision 4 clauses, with unchanged clauses inherited from revision 3."""

from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.excel import Place, original_name, unable
from audit.checker_lib.uaes.materials import related_by_content
from audit.checker_lib.uaes.resources import control_plan as res
from audit.checker_lib.uaes.resources import drawing_for_materials
from audit.engine.rule_engine import Decision, GuardStop, Sheet

from .control_plan import _NO_DEVICE_FILLS, _PLAN_NUMBER, UaesControlPlanRevision3, _cjk, _fold, _independent_name, _spec_key
from .control_plan_requirements_r4 import ControlPlanRequirementsR4
from .workbook_drawings_r4 import drawing_regions


def _manual_only(name):
    normalized = _fold(name)
    return any(word in normalized for word in ("目视", "自然冷却", "visualinspection", "naturalcooling")) or any(
        prefix + activity in normalized for prefix in ("手工", "人工") for activity in ("去毛刺", "包装", "装箱", "检视", "检查")
    )


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r" 00:00:00(?:\.0+)?$", "", text)
    if not (match := re.fullmatch(r"(\d{2}|\d{4})[./\-年](\d{1,2})[./\-月](\d{1,2})日?", text)):
        return None
    year, month, day = map(int, match.groups())
    try:
        return date(2000 + year if len(match[1]) == 2 else year, month, day)
    except ValueError:
        return None


_PROJECT = ("项目", "项目编号", "项目号", "项目编号(可选)", "项目名称", "UAES项目编号", "车型项目", "Project", "Project No.", "Project Name")
_FIELDS = (
    ("控制计划编号", (*_PLAN_NUMBER, "CP number", "Control Plan Number")),
    ("供应商名称", (*res.SUPPLIER_NAME, "Supplier/Plant", "Supplier Name")),
    ("供应商代码", (*res.SUPPLIER_CODE, "Supplier Code")),
    ("编制人", (*res.PREPARER, "Prepared by", "Made by")),
    ("版本号", (*res.VERSION, "Rev. No.", "Rev. No", "Version")),
    ("受控日期/创建日期/修订日期", (*res.DATES, "Date (Orig.)", "Date (Rev.)")),
    ("零件名称", (*res.PART_NAME, "Part Name", "Part Name/Description")),
    ("零件号", (*res.PART_NUMBER, "物料编号", "零件编号", "Part No.", "Part Number")),
)
_HISTORY = ("变更履历", "更改履历", "修订履历", "修改履历", "版本履历", "变更记录", "更改记录", "修订记录", "修改记录", "修改历史", "变更历史", "revisionhistory", "changerecord", "changelog")
_HISTORY_CONTENT = ("修改说明", "变更内容", "修改内容", "更改内容", "改版理由", "修订说明", "revisionreason", "revisionexplanation", "revisionexplaination")
_HISTORY_DATE = ("日期", "修改日期", "变更日期", "修订日期", "date", "revisiondate", "changedate")
_DRAWING_FIELDS = {
    "part": ("物料编号", "零件号", "产品件号", "Part No.", "Part Number"),
    "drawing_number": ("图号", "图纸编号", "DWG No.", "Drawing Number"),
    "name": ("零件名称", "零件名", "名称", "Part Name", "Designation"),
    "project": _PROJECT,
    "version": ("图纸版本", "版本号", "版本", "Version", "Rev."),
    "issue": ("图纸日期", "发布日期", "更改日期", "修订日期", "Drawing Date", "Issue Date", "Revision Date"),
    "drawn": ("制图日期", "绘图日期", "设计日期", "Date Drawn", "Drawn Date"),
    "review": ("审核日期", "审查日期", "校核日期", "Review Date", "Checked Date", "Check Date"),
}
_LABEL_KEYS = frozenset(_fold(alias) for _, aliases in _FIELDS for alias in aliases) | frozenset(_fold(alias) for aliases in _DRAWING_FIELDS.values() for alias in aliases)
_TITLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {key: {"type": ["string", "null"]} for key in ("title", *_DRAWING_FIELDS)},
    "required": ["title", *_DRAWING_FIELDS],
}
_TITLE_PROMPT = (
    "只转写这份原件的标题栏字段，不判断，不从图形中的数字或其他字段推断。"
    "title=原件类型或标题原文；part=物料编号或零件号；drawing_number=图号；name=零件名称或名称/Designation；"
    "project=项目名或项目号；version=图纸版本；issue=当前图纸发行或修订日期；drawn=制图日期；review=审核或校核日期。"
    "所有值只取同一图纸当前版本；不抄历史版本，不抄首次用于、相邻图号或通用页脚。不明确或不存在的字段填null。"
)


class History(NamedTuple):
    state: str
    latest: date | None = None
    place: str = ""


def _label(value, aliases):
    """Return whether the cell has an independent label and any inline value, never match nested text."""
    if value is None:
        return False, ""
    left, *right = re.split(r"[:：]", str(value).strip(), maxsplit=1)
    key = _fold(left)
    options = tuple(_fold(alias) for alias in aliases)
    if key not in options:
        # A bilingual form prints the translation next to the exact Chinese label.
        zh = _cjk(left)
        if not zh or zh not in options or not any(char.isascii() and char.isalpha() for char in left):
            return False, ""
    return True, right[0].strip() if right else ""


def _label_cell(value):
    return any(_label(value, (key,))[0] for key in _LABEL_KEYS)


class DrawingFeature(NamedTuple):
    name: str
    specification: str
    cpk: str | None
    place: str


# A structured drawing inventory only supplies a complete baseline when the source itself
# calls it complete. An arbitrary control plan, partial list or similar-number chart is not a drawing.
_DRAWING_TABLES = {"图纸全部特殊特性", "图纸特殊特性全表", "allmarkedcharacteristics", "drawingallspecialcharacteristics"}
_DRAWING_TABLE_COLUMNS = {
    "name": {"特性名称", "特殊特性名称", "特性", "characteristicname"},
    "specification": {"图纸规格", "规格", "规格/公差", "尺寸/要求", "尺寸规格", "specification"},
    "cpk": {"cpk检验要求", "cpk要求", "cpk控制要求", "cpkrequirement"},
}
_NO_CPK = {"无", "/", "不要求", "无需", "否", "n/a", "na", "no", "notrequired"}
_CP_RESOURCE = UaesControlPlanRevision3.EXTRA_RESOURCE
_CP_FREQUENCY = _CP_RESOURCE.columns["CP频率"]


class UaesControlPlanRevision4(ControlPlanRequirementsR4):
    EXTRA_RESOURCE = replace(_CP_RESOURCE, columns={**_CP_RESOURCE.columns, "CP频率": _CP_FREQUENCY.model_copy(update={"aliases": (*_CP_FREQUENCY.aliases, "频次")})})

    @rule("逐工序核对独立的生产资源列")
    def rule_1(self, ctx):
        m = self._m
        sheets = self.plan_sheets(ctx)
        rows = self.plan(ctx)
        # Determine actual, distinct column positions; the two alternatives cannot share evidence.
        aliases = ({"设备工装", "机器装置夹具工装"}, {"生产资源", "生产资源信息", "生产设备", "工装设备"})
        alternatives = {way: [] for way in range(len(aliases))}
        witnessed = {way: [] for way in alternatives}
        count = 0
        for sheet in sheets:
            section = self.section(sheet, "控制计划区")
            header = section.resolved.header_row
            columns = section.columns("工序名称", optional=("CP工序号", "评价测量技术"))
            op_col = columns["工序名称"].header_col
            width = self.extent(sheet)[1]
            resource_cols = [set(), set()]
            for cell in sheet.scan_non_empty(row_min=max(1, header - 3), row_max=header + 2, col_min=1, col_max=width):
                for way, labels in enumerate(aliases):
                    if _cjk(str(cell.value())) in labels:
                        resource_cols[way].add(cell.col_index)
                        break
            resource_cols[1] -= resource_cols[0]
            masters = {}
            last = None
            for row in (r for r in rows if r.sheet is not None and r.sheet.ws is sheet.ws):
                key = (row.step, row.operation)
                if row.operation and key != last:
                    merge = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, row.row, op_col)
                    idx = merge.min_row if merge else row.row
                    if _fold(sheet.ws.cell(idx, op_col).value) == _fold(row.operation):
                        masters[idx] = row.operation
                        last = key
            # A numbered operation with no evaluation-method value can be absent from plan()'s
            # characteristic rows altogether. The written operation still falls under CP:1.
            if step := columns.get("CP工序号"):
                for idx in range(max(col.data_start_row for col in columns.values()), self.extent(sheet)[0] + 1):
                    number = sheet.ws.cell(idx, step.header_col).value
                    name = sheet.ws.cell(idx, op_col).value
                    if name and number is not None and re.match(r"^(?:op\s*)?\d+(?:[.\-\s]|$)", str(number).strip(), re.I):
                        masters[idx] = str(name).strip()
            if not masters:
                raise m.unable(f"控制计划“{sheet.title}”未能定位工序主行，不能按空范围通过。")
            count += len(masters)
            for way, cols in enumerate(resource_cols):
                if len(cols) != 1:
                    alternatives[way].append(("UNABLE" if len(cols) > 1 else "NG", f"“{sheet.title}”未能唯一定位该类生产资源列" if cols else f"“{sheet.title}”无该类生产资源列"))
                    continue
                col = next(iter(cols))
                for idx, name in masters.items():
                    merge = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, idx, col)
                    cell = sheet.ws.cell(merge.min_row, merge.min_col) if merge else sheet.ws.cell(idx, col)
                    value = _fold(cell.value)
                    ctx.record_execution()  # tested this original operation's resource field
                    if value not in m._PLACEHOLDERS or value in _NO_DEVICE_FILLS and _manual_only(name):
                        witnessed[way].append((sheet, idx, col))
                        continue
                    self.cite(sheet, idx, col)
                    status = "UNABLE" if not value and self._resource_graphic(sheet, idx, col) else "NG"
                    alternatives[way].append((status, f"“{sheet.title}”{cell.coordinate}工序“{' '.join(name.split())}”生产资源为“{cell.value or '空'}”"))
        accepted = next((way for way, problems in alternatives.items() if not problems), None)
        if accepted is not None:
            for evidence in witnessed[accepted][:10]:
                self.cite(*evidence)
            return "PASS", f"已在同一类独立生产资源列逐一核对{count}个工序主行，均已填写生产资源或合规的手工作业标记。"
        state = "NG" if all(any(status == "NG" for status, _ in problems) for problems in alternatives.values()) else "UNABLE"
        reasons = []
        for way, problems in alternatives.items():
            confirmed = [reason for status, reason in problems if status == "NG"]
            why = "、".join(confirmed[:5]) if confirmed else problems[0][1] + "（位置或图形需人工核实）"
            reasons.append(f"方式{way + 1}：{why}")
        return state, "；".join(reasons) + "。"

    def _resource_graphic(self, sheet, row, col):
        path = str(self.audit_file.file_url)
        if path.lower().endswith(".pdf"):
            return self.drawn(sheet, row, col) is not False
        if not path.lower().endswith((".xlsx", ".xlsm")):
            return False
        if "_r4_resource_shapes" not in self.__dict__:
            self._r4_resource_shapes = {}
        if sheet.title not in self._r4_resource_shapes:
            self._r4_resource_shapes[sheet.title] = drawing_regions(path, sheet.title)
        shapes = self._r4_resource_shapes[sheet.title]
        return shapes is None or any(lo <= row <= hi and left <= col <= right for lo, left, hi, right in shapes)

    def _fields(self, sheet, aliases, *, stop=None):
        """An explicit field label and the inline value or a directly aligned field area."""
        rows, cols = self.extent(sheet)
        end = min(rows, stop) if stop is not None else rows
        for cell in sheet.scan_non_empty(row_min=1, row_max=end, col_min=1, col_max=cols):
            whole, inline = _label(cell.value(), aliases)
            if not whole:
                continue
            row, col = cell.row_index, cell.col_index
            ops, ws = sheet.ctx.ops, sheet.ws
            merge = ops.get_detailed_merge_info(ws, row, col)
            edge, base = (merge.max_col, merge.max_row) if merge else (col, row)
            origin = ws.cell(row, col)
            if inline:
                yield inline, Place.of(f"“{sheet.title}”{origin.coordinate}", sheet, row, col)
                continue
            # A populated next field takes precedence over a blank spacer, but never cross a label.
            adjacent = []
            for y, x in ((row, edge + 1), (base + 1, col)):
                if y > end or x > cols:
                    continue
                nxt = ws.cell(y, x)
                if _label_cell(nxt.value):
                    continue
                adjacent.append(nxt)
            present = next((candidate for candidate in adjacent if candidate.value not in (None, "")), None)
            slot = present if present is not None else next((candidate for candidate in adjacent if candidate.has_style), None)
            if slot is not None:
                yield "" if slot.value is None else str(slot.value).strip(), Place.of(f"“{sheet.title}”{slot.coordinate}", sheet, slot.row, slot.column)

    def _history(self, ctx):
        """Find dates in the original revision log, including a separate worksheet or footer."""
        seen = False
        records = []
        for ws in ctx.workbook.worksheets:
            if ws.max_row * ws.max_column > 1_000_000:
                return History("unread")
            sheet = Sheet(ctx, ws)
            title = any(token in _fold(ws.title) for token in _HISTORY)
            seen |= title
            for index, row in enumerate(ws.iter_rows(), 1):
                labels = [_fold(cell.value) for cell in row]
                title |= any(any(token in label for token in _HISTORY) for label in labels)
                seen |= title
                if not any(_cjk(label) in _HISTORY_CONTENT or label in _HISTORY_CONTENT for label in labels) and not title:
                    continue
                date_cols = [cell.column for cell, label in zip(row, labels, strict=True) if label in _HISTORY_DATE or _cjk(label) in _HISTORY_DATE]
                if len(date_cols) != 1:
                    continue
                content_cols = [cell.column for cell, label in zip(row, labels, strict=True) if label in _HISTORY_CONTENT or _cjk(label) in _HISTORY_CONTENT]
                if not content_cols and not any(label in ("版本", "版本号", "version", "rev.no.") for label in labels):
                    continue
                seen = True
                col = date_cols[0]
                # Many logs put the labels below the body. Follow the side(s) with an aligned date,
                # and stop before another table or unrelated text. An unread date in a record
                # leaves the last revision unresolved instead of skipping the record.
                local = {}
                for direction in (1, -1):
                    cursor = index + direction
                    while 1 <= cursor <= ws.max_row:
                        entry = ws.cell(cursor, col)
                        when = _date(entry.value)
                        if when is None:
                            if content_cols and any(ws.cell(cursor, c).value not in (None, "") for c in content_cols):
                                return History("unread")
                            break
                        local[cursor] = when
                        cursor += direction
                chronology = [local[i] for i in sorted(local)]
                if chronology not in (sorted(chronology), sorted(chronology, reverse=True)):
                    return History("unread")
                records.extend((when, Place.of(f"“{sheet.title}”{ws.cell(i, col).coordinate}", sheet, i, col)) for i, when in local.items())
                title = False
        if records:
            newest, place = max(records, key=lambda record: record[0])
            return History("read", newest, place)
        if seen:
            return History("empty")
        path = str(self.audit_file.file_url)
        if not path.lower().endswith((".xlsx", ".xlsm")):
            return History("unread")  # a converted PDF or BIFF sheet could omit drawn records
        with zipfile.ZipFile(path) as archive:
            if any(name.startswith("xl/drawings/") for name in archive.namelist()):
                return History("unread")
        return History("absent")

    def _drawing_spreadsheet(self, ctx, item):
        data = {key: set() for key in _DRAWING_FIELDS}
        with self.reading(ctx, item, drawing_for_materials):
            for ws in ctx.workbook.worksheets:
                sheet = Sheet(ctx, ws)
                for key, aliases in _DRAWING_FIELDS.items():
                    for value, place in self._fields(sheet, aliases):
                        if value:
                            data[key].add(value)
                            self.named(place)
        return data

    def _drawing_pdf(self, item):
        import fitz

        path = str(item.file_url)
        data = {key: set() for key in _DRAWING_FIELDS}
        self.pdf_text(item)  # verifies the frozen source bytes before direct page-local extraction
        with fitz.open(path) as drawing:
            text = drawing[0].get_text()
        for line in text.splitlines():
            for key, aliases in _DRAWING_FIELDS.items():
                present, value = _label(line, aliases)
                if present and value:
                    data[key].add(value)
        if data["part"] and data["name"] and data["project"] and (data["review"] or data["issue"] or data["drawn"]):
            with fitz.open(path) as drawing:
                page = drawing[0]
                rect = page.rect
                self._drawing_witness(item, rect, data, visual=False)
            return data
        with fitz.open(path) as drawing:
            page = drawing[0]
            area = fitz.Rect(page.rect.x1 * 0.50, page.rect.y1 * 0.47, page.rect.x1, page.rect.y1)
            image = page.get_pixmap(dpi=min(230, int(2400 * 72 / area.width)), clip=area).tobytes("png")
        facts = self.transcribe(image, _TITLE_PROMPT, _TITLE_SCHEMA, "图纸当前版标题栏")
        for key in data:
            if value := facts.get(key):
                data[key].add(value.strip())
        self._drawing_witness(item, area, data, visual=True)
        return data

    def _drawing_witness(self, item, area, data, *, visual):
        version = str((getattr(item, "file_metadata", None) or {}).get("file_version_id") or item.audit_id)
        self.legacy().witness(
            {"kind": "pdf_region", "page": 1, "bbox": [area.x0, area.y0, area.x1, area.y1], "engine": "ocr_llm" if visual else "pdf_text", "fact": "drawing_title_block"},
            "；".join(f"{_DRAWING_FIELDS[key][0]}：{'、'.join(sorted(values))}" for key, values in data.items() if values),
            version,
            visual=visual,
        )

    def _paired_drawing(self, ctx, part, name, project):
        """Never use the date of a different part, name, project, or an ambiguous drawing version."""
        candidates = related_by_content(self, ("图纸", "drawing"))
        if not candidates.items:
            self.need("图纸", part=part or None)
            return "NG", "同批次未提交图纸独立文件，第3项不满足", None
        if not all((part, name, project)):
            return "UNABLE", "控制计划未能确定项目、物料编号和零件名称，不能选择同项目同物料图纸", None
        maybe, matched = [], []
        for item in candidates.items:
            path = Path(str(item.file_url))
            try:
                if path.suffix.lower() == ".pdf":
                    data = self._drawing_pdf(item)
                elif path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
                    data = self._drawing_spreadsheet(ctx, item)
                else:
                    maybe.append(original_name(item))
                    continue
            except GuardStop as stop:
                if stop.decision != Decision.UNABLE:
                    raise
                maybe.append(original_name(item))
                continue
            # Material number is not the drawing number; never case-fold an identity.
            fields = [(data["part"], part), (data["name"], name), (data["project"], project)]
            if any(values and want not in values for values, want in fields):
                continue
            if any(values != {want} for values, want in fields):
                maybe.append(original_name(item))
                continue
            # Named review has priority over drawing date when there is no separate issue date.
            baseline = data["issue"] | data["review"] or data["drawn"]
            dates = {_date(text) for text in baseline}
            if not dates or None in dates or any(len({_date(text) for text in data[k]}) > 1 for k in ("issue", "review")) or len(data["version"]) > 1:
                maybe.append(original_name(item))
                continue
            matched.append((original_name(item), max(d for d in dates if d is not None)))
        if len(matched) == 1 and not maybe:
            drawing, d = matched[0]
            return "PASS", f"同项目、同物料号和零件名的图纸“{drawing}”的基准日期为{d}", d
        if matched or maybe:
            return "UNABLE", "无法从同批次原件确认唯一同项目同物料图纸及其当前版审核/签发日期" + (f"（待核对：{'、'.join(maybe)}）" if maybe else ""), None
        self.need("图纸", part=part)
        return "NG", f"同批次没有同项目“{project}”、物料编号“{part}”、名称“{name}”的图纸独立文件，第3项不满足", None

    @rule("独立表头字段、图纸身份与最后变更履历日期")
    def rule_6(self, ctx):
        sheets = self.plan_sheets(ctx)
        headers = [(sheet, self.section(sheet, "控制计划区").resolved.header_row - 1) for sheet in sheets]
        values, missing, opened, failures = {}, [], [], []
        is_pdf = str(self.audit_file.file_url).lower().endswith(".pdf")

        def unique(texts):
            values = {text for text, _ in texts if _fold(text) not in self._m._PLACEHOLDERS}
            return next(iter(values)) if len(values) == 1 else None

        raw_part = unique([pair for sheet, end in headers for pair in self._fields(sheet, _FIELDS[-1][1], stop=end)])
        for field, aliases in _FIELDS:
            readings = [pair for sheet, end in headers for pair in self._fields(sheet, aliases, stop=end)]
            value = unique(readings)
            ctx.record_execution()  # evaluated one independently required title-block field
            if field == "控制计划编号" and value is not None and raw_part and value == raw_part:
                value = None
            if readings and (field == "受控日期/创建日期/修订日期" or value is not None):
                values[field] = value
                self.named(readings[0][1])
            elif is_pdf and not readings:
                opened.append(f"PDF未能定位独立字段“{field}”")
            else:
                missing.append(field)
                if readings:
                    self.named(readings[0][1])
        if missing:
            severity = "重要" if len(missing) < 3 else "致命"
            failures.append(f"独立表头字段缺失或无有效内容：{'、'.join(missing)}（{len(missing)}项，{severity}）")
        history = self._history(ctx)
        if history.state in {"absent", "empty"}:
            failures.append("控制计划没有可用的独立变更履历记录；表头日期不能替代最后一条变更日期，第4项不满足")
        elif history.state == "unread":
            opened.append("未能完整读取控制计划变更履历最后一条记录的日期")
        project = unique([pair for sheet, end in headers for pair in self._fields(sheet, _PROJECT, stop=end)])
        state, drawing_reason, baseline = self._paired_drawing(ctx, raw_part, values.get("零件名称"), project)
        if state == "NG":
            failures.append(drawing_reason)
        elif state == "UNABLE":
            opened.append(drawing_reason)
        change_place = ""
        if history.latest is not None and baseline is not None:
            ctx.record_execution()  # compared the CP history date with this same-part drawing
            change_place = self.named(history.place)
            if history.latest < baseline:
                failures.append(f"控制计划{change_place}最后一条变更日期{history.latest}早于{drawing_reason}，第4项不满足")
        if failures:
            if len(failures) > 1:
                self.__dict__.pop("_need", None)
            return "NG", "；".join(failures + opened) + "。"
        if opened:
            raise unable("；".join(opened) + "，需人工复核。")
        return "PASS", f"表头独立字段齐全；{drawing_reason}；控制计划{change_place}最后一条变更日期{history.latest}不早于图纸。"

    def _drawing_features(self, ctx, require_cpk):
        """One same-project/part independent drawing with an explicitly complete native inventory."""
        candidates = related_by_content(self, ("图纸", "drawing"))
        if not candidates.items:
            self.need("图纸")
            return "NG", "同批次未提交图纸独立文件。", []
        workbook_headers = [(sheet, self.section(sheet, "控制计划区").resolved.header_row - 1) for sheet in self.plan_sheets(ctx)]
        ids = [{v for sheet, end in workbook_headers for v, _ in self._fields(sheet, aliases, stop=end) if _fold(v) not in self._m._PLACEHOLDERS} for aliases in (_FIELDS[-1][1], _PROJECT)]
        if any(len(values) != 1 for values in ids):
            raise unable("控制计划物料编号或项目未能唯一确定，不能核对图纸特性。")
        part, project = (next(iter(values)) for values in ids)
        unresolved, matched = [], []
        for item in candidates.items:
            title = original_name(item)
            source_path = str(item.file_url)
            if Path(source_path).suffix.lower() not in {".xlsx", ".xlsm"}:
                unresolved.append(f"{title}图纸标注不是已知完整结构表，尚不能逐项读取")
                continue
            with self.reading(ctx, item, drawing_for_materials):
                sheets = [Sheet(ctx, ws) for ws in ctx.workbook.worksheets]
                i_part = {v for s in sheets for v, _ in self._fields(s, _DRAWING_FIELDS["part"]) if v}
                i_project = {v for s in sheets for v, _ in self._fields(s, _PROJECT) if v}
                fields = [(part, i_part), (project, i_project)]
                if any(values and expected not in values for expected, values in fields):
                    continue
                if any(values != {expected} for expected, values in fields):
                    unresolved.append(f"{title}未能定位明确的同项目、同物料身份")
                    continue
                inventory = [s for s in sheets if self._complete_drawing_heading(s)]
                if len(inventory) != 1:
                    unresolved.append(f"{title}未能定位唯一完整图纸特殊特性明细")
                    continue
                # A tabular restatement cannot certify completeness if its native shapes could
                # cover columns the control plan would rely on. Do not equate text with graphics.
                locations = drawing_regions(source_path, inventory[0].title)
                if locations is None or locations:
                    unresolved.append(f"{title}包含未逐项核实的图形标注")
                    continue
                features = self._structured_features(inventory[0], require_cpk)
                if features is None:
                    unresolved.append(f"{title}明细的逐项特性名称、规格或要求不完整")
                    continue
                ctx.record_execution()  # verified the independent drawing identity and complete native inventory
                matched.append((title, features))
        if len(matched) == 1 and not unresolved:
            return "PASS", matched[0][0], matched[0][1]
        if matched or unresolved:
            raise unable("无法唯一读取同项目同物料图纸的全部特殊特性：" + "；".join(unresolved[:3] or ["同批存在多份图纸明细，版本未能唯一核对"]) + "。")
        self.need("图纸", part=part)
        return "NG", f"同批次未提交项目“{project}”、物料号“{part}”的图纸独立文件。", []

    def _complete_drawing_heading(self, sheet):
        # An explicit title may be printed on the sheet rather than used as its tab name.
        # Only a stand-alone top heading qualifies; never treat a detail row or note as a completeness declaration.
        if _fold(sheet.title) in _DRAWING_TABLES:
            return True
        rows, width = self.extent(sheet)
        if (rows, width) != (sheet.ws.max_row, sheet.ws.max_column) or rows * width > 1_000_000:
            return False
        for row in sheet.ws.iter_rows(values_only=True):
            values = [value for cell in row if (value := _fold(cell))]
            if values:
                return len(values) == 1 and values[0] in _DRAWING_TABLES
        return False

    def _structured_features(self, sheet, require_cpk):
        rows, width = self.extent(sheet)
        if (rows, width) != (sheet.ws.max_row, sheet.ws.max_column) or rows * width > 1_000_000:
            return None
        columns = {}
        seen_at = 0
        for index, row in enumerate(sheet.ws.iter_rows(min_row=1, max_row=rows, max_col=width), 1):
            candidates = {key: [cell.column for cell in row if _fold(cell.value) in synonyms] for key, synonyms in _DRAWING_TABLE_COLUMNS.items()}
            mandatory = ("name", "specification", "cpk") if require_cpk else ("name", "specification")
            if all(len(candidates[key]) == 1 for key in mandatory):
                columns = {key: positions[0] for key, positions in candidates.items() if len(positions) == 1}
                seen_at = index
                break
        if not seen_at:
            return None
        read, declared_empty = [], False
        for index in range(seen_at + 1, rows + 1):
            cells = {key: sheet.ws.cell(index, col) for key, col in columns.items()}
            text = {key: str(cell.value).strip() if cell.value is not None else "" for key, cell in cells.items()}
            if not any(text.values()):
                continue
            name, spec, cpk = text["name"], text["specification"], text.get("cpk")
            if name in {"无", "没有特殊特性"} and not spec and not cpk:
                declared_empty = True
                continue
            if _fold(name) in self._m._PLACEHOLDERS or _fold(spec) in self._m._PLACEHOLDERS or require_cpk and not cpk:
                return None
            self.cite(sheet, index, columns["specification"])
            if require_cpk:
                self.cite(sheet, index, columns["cpk"])
            read.append(DrawingFeature(name, spec, cpk, f"“{sheet.title}”{cells['specification'].coordinate}"))
        return None if (not read and not declared_empty) or (read and declared_empty) else read

    def _check_drawing_requirements(self, ctx, *, require_cpk):
        state, reference, listed = self._drawing_features(ctx, require_cpk)
        if state == "NG":
            return state, reference
        observed = self.plan(ctx)
        if not self.plan_column(ctx, "CP规格公差") and listed:
            raise unable("未能定位控制计划产品/过程规格/公差列，不能逐项核对图纸。")
        failures, unopened, examined = [], [], 0
        for feature in listed:
            label = f"{feature.place}“{feature.name} {feature.specification}”"
            if require_cpk:
                requirement = _fold(feature.cpk)
                if requirement in _NO_CPK:
                    continue
                if requirement not in {"是", "yes", "要求", "required", "√", "✓"} and "cpk" not in requirement and not re.fullmatch(r"[≥>]=?\d+(?:\.\d+)?", requirement):
                    unopened.append(f"{label}的CPK检验要求“{feature.cpk}”无法确认")
                    continue
                if any(word in requirement for word in ("不要求", "无需", "notrequired")):
                    continue
            examined += 1
            ctx.record_execution()  # evaluated coverage of this drawing requirement in CP
            name, specification = _independent_name(feature.name, feature.specification), _spec_key(feature.specification)
            if not name or not specification:
                unopened.append(f"{label}不能用规格或图号替代独立特性名")
                continue
            pairs = []
            for row in observed:
                product, process, spec = (self.spanned(row, col) for col in ("CP产品特性", "CP过程特性", "CP规格公差"))
                sources = (product, process, spec)
                keys = {_spec_key(source) for source in sources if source}
                if specification not in keys:
                    continue
                if name and name not in {_independent_name(source, feature.specification) for source in sources if source}:
                    continue
                pairs.append(row)
            if not pairs:
                # Empty converted target cells may still contain drawing callouts.
                if str(self.audit_file.file_url).lower().endswith(".pdf"):
                    unopened.append(f"{label}未找到可从控制计划文字直接定位的对应项，图形尺寸仍待核实")
                else:
                    failures.append(f"{label}在控制计划没有可定位的对应项")
                continue
            for row in pairs:
                self.at(row, "CP产品特性", "CP过程特性", "CP规格公差")
            if require_cpk:
                if len(pairs) != 1:
                    unopened.append(f"{label}在控制计划有多行同名同规格，尚不能唯一确定对应的频率要求")
                    continue
                frequency = _fold(self.spanned(pairs[0], "CP频率"))
                spc_terms = ("spc", "控制图", "x-r", "x̄-r", "xbar-r")
                no_spc = any(prefix + term in frequency for prefix in ("无需", "不要求", "无", "不做", "不作", "不实施", "不使用", "不采用") for term in spc_terms)
                if not any(term in frequency for term in spc_terms) or no_spc:
                    failures.append(f"{label}对应控制计划{self.at(pairs[0], 'CP频率')}的频率/频次未写SPC管控")
        if failures:
            return "NG", f"图纸“{reference}”：" + "；".join(failures[:6]) + "。"
        if unopened:
            raise unable("；".join(unopened[:6]) + "，须人工复核。")
        if require_cpk:
            return "PASS", f"图纸“{reference}”完整明细中{examined}项有CPK要求的特殊尺寸均有控制计划对应行，其频率/频次写明SPC管控。"
        return "PASS", f"图纸“{reference}”完整明细中{examined}项特殊特性均在控制计划同一对应行写明。"

    @rule("图纸CPK特殊尺寸在控制计划频次中受SPC管控")
    def rule_11(self, ctx):
        return self._check_drawing_requirements(ctx, require_cpk=True)

    @rule("独立图纸的特殊特性在控制计划按行覆盖")
    def rule_12(self, ctx):
        return self._check_drawing_requirements(ctx, require_cpk=False)
