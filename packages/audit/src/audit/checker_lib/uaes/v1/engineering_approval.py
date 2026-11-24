"""UAES U19: customer engineering approval (C sample release report), UAES v1.0 revision 3 (CEA:1).

The cover (F-NE-185) and the deviation release list are read as tables, and the cover's PDF text layer and
drawings for the sign-off and the reject column. Only when the reject column or the sign-off cannot be told from
those is an image of the cover transcribed by the image model; every verdict is decided in code.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.v1.reader import PER_PAGE, STITCHED, WHOLE_SHEET, Reader, compact, original_name, resource, unable
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table
from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

# ---- CEA ------------------------------------------------------------------------------------------


_SHEETS = ("封面", "cover", "认可报告", "偏差", "deviation", "table_", "page-", "sheet")
COVER = (
    (SheetModel(canonical_name="认可封面", include_keywords=_SHEETS),),
    (
        SectionModel(
            canonical_name="认可区",
            sheet_name="认可封面",
            header_anchors=("报告内容", "带条件批准", "否决"),
            header_hit=3,
            header_search_max_row=WHOLE_SHEET,
            header_search_max_col=WHOLE_SHEET,
            row_anchors=("报告内容",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET,
            row_stop_markers=("审核：", "Checked by"),
        ),
    ),
    (
        ColumnModel(canonical_name="报告内容", section_name="认可区", aliases=("报告内容",)),
        ColumnModel(canonical_name="带条件批准", section_name="认可区", aliases=("带条件批准",)),
        ColumnModel(canonical_name="否决", section_name="认可区", aliases=("否决",)),
    ),
)
DEVIATIONS = (
    (SheetModel(canonical_name="偏差认可汇总", include_keywords=_SHEETS),),
    (
        SectionModel(
            canonical_name="偏差区",
            sheet_name="偏差认可汇总",
            header_anchors=("偏差项", "UAES评估确认", "是否需要偏差"),
            header_hit=3,
            header_search_max_row=WHOLE_SHEET,
            header_search_max_col=WHOLE_SHEET,
            row_anchors=("是否需要偏差",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET,
            row_stop_markers=("供应商申请人", "Supplier Proposer"),
        ),
    ),
    (
        ColumnModel(canonical_name="偏差项", section_name="偏差区", aliases=("偏差项",)),
        ColumnModel(canonical_name="UAES评估确认", section_name="偏差区", aliases=("UAES 评估确认", "UAES评估确认")),
        # The clause's 是否需要偏差 is headed "是否同意\n偏差\nYes or\nNo" on the list.
        ColumnModel(canonical_name="是否需要偏差", section_name="偏差区", aliases=("是否同意 偏差", "是否同意偏差", "是否需要偏差")),
        ColumnModel(canonical_name="偏差备注", section_name="偏差区", aliases=("备注",)),
    ),
)
PART_NUMBER = ("零件号\nPart No.",)
PART_NAME = ("零件名称\nPart name", "零件名称\nPart Name")
REMARK = "UAES批准的前提"
CATEGORIES = ("尺寸", "材料", "性能", "试装")
ROLES = ("组长", "审核", "批准")
ROLE_NAMES = {"组长": "组长（Team Leader）", "审核": "审核（Checked by）", "批准": "批准（Approved by）"}
LABEL_WORDS = {"日期", "date", "team", "leader", "checked", "approved", "by"}
_DATE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
_MARK = re.compile(r"[×xX☑✓√✔■●]")
FULL = "完全认可"
# CEA:1: a remark that carries a deviation, an out-of-tolerance or a rectification promise takes the deviation path.
DEVIATION_TERMS = ("偏差", "超差", "整改", "条件认可")
# Two statements of the remark that contradict each other: an approval and its refusal.
REFUSALS = ("不认可", "不予认可", "不同意", "不批准")
_APPROVAL_KINDS = ("E11", "顾客工程批准")
# The deviation release list as recognition registers it: a counterpart compared against, never audited on its own.
_LIST_KIND = "E11A"

_MARK_SCHEMA = {"type": ["boolean", "null"]}
REJECT_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(CATEGORIES), "properties": {name: _MARK_SCHEMA for name in CATEGORIES}}
REJECT_PROMPT = (
    "这是C样品认可报告封面的原件图像。请只转写封面“报告内容 / 批准 / 带条件批准 / 否决(Reject)”表格中“否决 Reject”一列，不判断是否合格。\n"
    "尺寸、材料、性能、试装四行各一个字段：该行“否决”格里有实际的勾、叉、符号或涂色填充为 true；空格为 false；看不清为 null。"
)
_ROLE = {"type": "object", "additionalProperties": False, "required": ["signed", "date"], "properties": {"signed": _MARK_SCHEMA, "date": {"type": ["string", "null"]}}}
SIGN_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(ROLES), "properties": {role: _ROLE for role in ROLES}}
SIGN_PROMPT = (
    "这是C样品认可报告封面的原件图像。请转写封面底部组长(Team Leader)、审核(Checked by)、批准(Approved by)三处，不判断是否合格。\n"
    "signed：该处有实际签名（手写或姓名）为 true，空白为 false，看不清为 null。date：照原样转写该处日期，空白写空字符串，看不清写 null。"
)


def _day(year: str, month: str, day: str) -> date | None:
    try:
        return date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day))
    except ValueError:
        return None


def deadlines(text: str) -> list[date | None]:
    """Promised latest dates: "截止<date>", "临时偏差至<date>", "<date>前完成"; never an expected rectification time."""
    found = re.findall(r"(?:截止|临时偏差至)\s*(\d{2,4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", text)
    found += re.findall(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?\s*前完成", text)
    return [_day(*parts) for parts in found]


def temporary(text: str) -> list[date | None]:
    """The "临时偏差至<date>" validities a list remark writes."""
    return [_day(*parts) for parts in re.findall(r"临时偏差至\s*(\d{2,4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", "".join(text.split()))]


def _parse(text: Any) -> date | None:
    from audit.engine.checkers.date_utils import normalize_excel_date

    match = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", unicodedata.normalize("NFKC", str(text or "")))
    if match:
        return _day(*match.groups())
    found = normalize_excel_date(text) if text else None
    return found if isinstance(found, date) else None


class _Here:
    def __init__(self, ctx: RuleContext) -> None:
        self.ctx = ctx

    def __enter__(self) -> RuleContext:
        return self.ctx

    def __exit__(self, *exc: object) -> None:
        return None


class ApprovalR3(Reader):
    DOCUMENT = COVER
    PDF_SPECS = (PER_PAGE, STITCHED)
    EXTRA_RESOURCE = resource(COVER, DEVIATIONS)

    def pdf(self) -> bool:
        return str(self.audit_file.file_url).lower().endswith(".pdf")

    def pdf_witness(self, page: int, bounds: Any, text: str) -> None:
        self.witness({"kind": "pdf_region", "page": page, "bbox": [round(float(value), 2) for value in bounds], "coordinate_system": "pdf_points_unrotated"}, text)

    def page_image(self) -> bytes:
        import fitz

        with fitz.open(str(self.audit_file.file_url)) as document:
            return document[0].get_pixmap(dpi=200).tobytes("png")

    # ---- cover ---------------------------------------------------------------------------------

    def cover(self, ctx: RuleContext) -> tuple[Sheet, Any]:
        found = [(sheet, section) for sheet in Table(ctx).find_sheets("认可封面", on_missing=Decision.IGNORE) for section in [self.located(sheet, "认可区")] if section]
        if len(found) != 1:
            if not found and (self.untabled() or self.unread_pages()):
                raise unable("PDF原件未能读出表格，检查程序未能读取认可报告封面，需人工复核。")
            raise unable(f"检查程序{'未能定位' if not found else f'定位到{len(found)}处'}封面“报告内容/批准/带条件批准/否决”表格，需人工复核。")
        return found[0]

    def rejected(self, sheet: Sheet, section: Any) -> str | None:
        """The category whose 否决 cell carries a mark; "" when none does; None when a drawn mark cannot be excluded."""
        rows = {}
        for row in section.rows("报告内容"):
            name = str(row.value("报告内容") or "")
            category = next((item for item in CATEGORIES if item in name), None)
            if category:
                rows[category] = row
        if set(rows) != set(CATEGORIES):
            missing = "、".join(item for item in CATEGORIES if item not in rows)
            raise unable(f"封面“报告内容”下未能读取{missing}行，检查程序未能确认否决列，需人工复核。")
        column = section.column("否决").header_col
        for category, row in rows.items():
            mark = str(sheet.cell(row.row_index, column).value() or "").strip()
            if _MARK.search(mark):
                self.cite(sheet, row.row_index, column)
                return category
            if mark:
                return None
        if self.pdf():
            return self.drawn_reject()
        path = str(self.audit_file.file_url)
        if path.lower().endswith((".xlsx", ".xlsm")):
            import zipfile

            with zipfile.ZipFile(path) as archive:
                if not any(name.startswith("xl/drawings/") for name in archive.namelist()):
                    return ""
        return None

    def drawn_reject(self) -> str | None:
        """Strokes, pictures or words inside the 否决 column cells of the PDF cover; "" when it is clean."""
        import fitz

        with fitz.open(str(self.audit_file.file_url)) as document:
            page = document[0]
            words = [(fitz.Rect(word[:4]), word[4]) for word in page.get_text("words")]
            drawings = page.get_drawings()
            images = [fitz.Rect(image["bbox"]) for image in page.get_image_info()]
        heading = [rect for rect, text in words if text == "否决"]
        tryout = [rect for rect, text in words if text in {"试装", "Tryout"}]
        if len(heading) != 1 or not tryout:
            return None
        head = heading[0]
        under = max(rect.y1 for rect, text in words if text in {"否决", "Reject"} and abs(rect.x0 - head.x0) < 40)
        vertical = [item["rect"] for item in drawings if item["rect"].width <= 3 and item["rect"].height > 40 and item["rect"].y0 <= head.y0]
        horizontal = [item["rect"] for item in drawings if item["rect"].height <= 3 and item["rect"].width > 40]
        left = max((rect.x1 for rect in vertical if rect.x1 <= head.x0), default=None)
        right = min((rect.x0 for rect in vertical if rect.x0 >= head.x1), default=None)
        top = min((rect.y1 for rect in horizontal if rect.y0 >= under and rect.x0 < head.x0 and rect.x1 > head.x1), default=None)
        bottom = min((rect.y0 for rect in horizontal if rect.y0 >= max(rect.y1 for rect in tryout) and rect.x0 < head.x0 and rect.x1 > head.x1), default=None)
        if left is None or right is None or top is None or bottom is None:
            return None
        cells = fitz.Rect(left + 1.5, top + 1.5, right - 1.5, bottom - 1.5)

        def stroke(item: Any) -> bool:
            return any(part[0] == "c" or part[0] == "l" and abs(part[1].x - part[2].x) > 1 and abs(part[1].y - part[2].y) > 1 for part in item["items"])

        marks = [item["rect"] for item in drawings if stroke(item) and item["rect"].intersects(cells) and (item["rect"] & cells).get_area() > 0]
        filled = [
            item["rect"] for item in drawings if item.get("fill") is not None and item["rect"].intersects(cells) and (item["rect"] & cells).get_area() > 4 and item["rect"].width < cells.width - 1
        ]
        self.pdf_witness(1, cells, f"封面否决列：{len(marks) + len(filled)}处图形标记")
        if marks or filled:
            return "否决列"
        if any(rect.intersects(cells) for rect in images) or any(rect.intersects(cells) for rect, _text in words):
            return None
        return ""

    def remark(self, sheet: Sheet) -> tuple[str, Any]:
        rows, columns = self.extent(sheet)
        cells = [cell for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns) if compact(REMARK) in compact(cell.value())]
        if len(cells) != 1:
            raise unable(f"封面{'未能定位' if not cells else '有多处'}“UAES 批准的前提/备注”栏，检查程序未能读取，需人工复核。")
        text = str(cells[0].value())
        text = re.split(r"remark", text, maxsplit=1, flags=re.I)[-1]
        return re.split(r"组长\s*[:：]", text, maxsplit=1)[0].strip(), cells[0]

    def cover_signatures(self) -> dict[str, tuple[str, date | None]] | None:
        """Role -> (name, date) read beside each cover role label on the text layer; None when only an image could tell."""
        import fitz

        from audit.engine.checkers.date_utils import normalize_excel_date

        if not self.pdf():
            return None
        with fitz.open(str(self.audit_file.file_url)) as document:
            page = document[0]
            words = [(fitz.Rect(word[:4]), word[4]) for word in page.get_text("words")]
            images = [fitz.Rect(image["bbox"]) for image in page.get_image_info()]
        labels = {}
        for role in ROLES:
            found = [rect for rect, text in words if text.rstrip(":：") == role and text[-1:] in ":："]
            if len(found) != 1:
                return None
            labels[role] = found[0]
        signed = {}
        for role, label in labels.items():
            bottom = label.y1 + label.height
            right = min((other.x0 for other in labels.values() if other.x0 > label.x0 and other.y0 < bottom and other.y1 > label.y0), default=float("inf"))
            entry = [(rect, text) for rect, text in words if rect.x0 > label.x1 - 1 and rect.x1 <= right + 1 and rect.y0 < bottom and rect.y1 > label.y0]
            day = next((normalize_excel_date(text) for _rect, text in entry if _DATE.fullmatch(text)), None)
            name = " ".join(text for _rect, text in entry if not _DATE.fullmatch(text) and text.rstrip(":：").casefold() not in LABEL_WORDS)
            if (not name or day is None) and any(image.intersects(fitz.Rect(label.x0, label.y0, min(right, 1e6), bottom)) for image in images):
                return None
            if entry:
                area = fitz.Rect(label)
                for rect, _text in entry:
                    area |= rect
                self.pdf_witness(1, area, f"{role}：{name} {day or ''}".strip())
            signed[role] = (name, day)
        return signed

    def identity(self, ctx: RuleContext, sheet: Sheet) -> tuple[str, str]:
        values = []
        for name, aliases in (("零件号", PART_NUMBER), ("零件名称", PART_NAME)):
            wanted = {compact(alias) for alias in aliases}
            rows, columns = self.extent(sheet)
            labels = [cell for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns) if compact(cell.value()) in wanted]
            if len(labels) != 1:
                raise unable(f"“{sheet.title}”中{'未能定位' if not labels else '有多处'}{name}字段，检查程序未能读取，需人工复核。")
            merged = ctx.ops.get_detailed_merge_info(sheet.ws, labels[0].row_index, labels[0].col_index)
            values.append(str(sheet.cell(labels[0].row_index, (merged.max_col if merged else labels[0].col_index) + 1).value() or "").strip())
        return values[0], values[1]

    # ---- CEA:1 ---------------------------------------------------------------------------------

    @rule("否决前置与完全认可或偏差认可两路判定")
    def rule_1(self, ctx: RuleContext):
        if unread := self.unread_pages():
            raise unable(f"原件第{'、'.join(map(str, unread))}页为扫描页（没有文字层），检查程序不读取扫描件，需人工复核。")
        sheet, section = self.cover(ctx)
        rejected = self.rejected(sheet, section)
        if rejected is None:
            if not self.pdf():
                raise unable("封面否决列有图形或文字，检查程序尚不能对表格原件做视觉层取证，需人工复核。")
            marks = self.transcribe(self.page_image(), REJECT_PROMPT, REJECT_SCHEMA, "封面否决列")
            self.witness({"kind": "pdf_page", "page": 1, "engine": "ocr_llm", "fact": "reject"}, "、".join(f"{name}:{marks.get(name)}" for name in CATEGORIES), visual=True)
            marked = [name for name in CATEGORIES if marks.get(name) is True]
            if marked:
                return "NG", f"封面否决列{'、'.join(marked)}有否决标记，为否决件，直接不通过。"
            if any(marks.get(name) is None for name in CATEGORIES):
                return "NG", "封面否决列的勾选经视觉层取证后仍无法读出，判不通过，请人工复核。"
        elif rejected:
            return "NG", f"封面否决列{rejected}有否决标记，为否决件，直接不通过。"
        remarks, cell = self.remark(sheet)
        self.cite(sheet, cell.row_index, cell.col_index)
        full = FULL in remarks
        deviation = [term for term in DEVIATION_TERMS if term in remarks]
        refused = [term for term in REFUSALS if term in remarks]
        if refused and (full or deviation):
            return "NG", f"封面备注“{remarks}”既写认可又写“{'、'.join(refused)}”，两处表述相互冲突，认可结论无法辨识，判不通过，请人工复核。"
        if not full and not deviation:
            return "NG", f"封面备注“{remarks}”不能归入完全认可或偏差认可／条件认可，认可结论无法辨识，判不通过，请人工复核。"
        if not deviation:
            return self.full_approval()
        route = f"备注写有“{FULL}”，但同时载有“{'、'.join(deviation)}”，按偏差／条件认可路径判定；" if full else f"备注载有“{'、'.join(deviation)}”，按偏差／条件认可路径判定；"
        return self.deviation_approval(ctx, sheet, remarks, route)

    def full_approval(self) -> tuple[str, str]:
        na = "偏差认可汇总清单、整改期限、临时偏差有效期、两处期限一致性四项在完全认可路径下无判定对象，记不适用（仅作提醒）。"
        signed = self.cover_signatures()
        if signed is None:
            if not self.pdf():
                raise unable("封面完全认可；检查程序尚未能从表格原件读取组长、审核、批准的签名与日期，需人工复核。")
            read = self.transcribe(self.page_image(), SIGN_PROMPT, SIGN_SCHEMA, "封面三方签名与日期")
            self.witness({"kind": "pdf_page", "page": 1, "engine": "ocr_llm", "fact": "sign_off"}, json.dumps(read, ensure_ascii=False), visual=True)
            if any((read.get(role) or {}).get("signed") is None or (read.get(role) or {}).get("date") is None for role in ROLES):
                raise unable("封面完全认可；组长、审核、批准的签名或日期未能从原件图像辨识，需人工复核。")
            signed = {role: ("签名" if read[role]["signed"] else "", _parse(read[role]["date"])) for role in ROLES}
        missing = [ROLE_NAMES[role] for role, (name, day) in signed.items() if not name or day is None]
        if missing:
            return "NG", "封面完全认可，但" + "、".join(missing) + "缺少签名或日期。" + na
        listed = "，".join(f"{role}{name} {day}" for role, (name, day) in signed.items())
        days = [day for _name, day in signed.values()]
        if days != sorted(days):  # type: ignore[type-var]
            return "NG", "封面完全认可，但签字日期不满足组长≤审核≤批准：" + listed + "。" + na
        return "PASS", "封面完全认可，" + listed + "，签名及日期齐全且组长≤审核≤批准。" + na

    def deviation_approval(self, ctx: RuleContext, sheet: Sheet, remarks: str, route: str) -> tuple[str, str]:
        today = date.today()
        failures: list[str] = []
        promised = deadlines(remarks)
        deadline: date | None = None
        if any(item is None for item in promised):
            raise unable(f"{route}封面备注“{remarks}”中的承诺期限未能读为日期，需人工复核。")
        if promised:
            deadline = max(item for item in promised if item is not None)
            if deadline <= today:
                failures.append(f"封面整改承诺的最晚期限{deadline}不晚于当前日期{today}")
        elif re.search(r"\d{1,4}\s*[-/.年月]\s*\d{1,2}", remarks):
            raise unable(f"{route}封面备注“{remarks}”中写有日期，但未能读出整改承诺的最晚期限（…前完成／截止…；预计整改时间不算），需人工复核。")
        else:
            failures.append("封面没有整改承诺的最晚期限")
        part, name = self.identity(ctx, sheet)
        lists, open_points = self.deviation_lists(ctx, part, name)
        if not lists and not open_points:
            self.need("偏差认可汇总清单", part=part)
            failures.append(f"文件内及同批次没有零件号“{part}”、零件名称“{name}”的偏差认可汇总清单")
            return "NG", route + "；".join(failures) + "。"
        if len(lists) > 1:
            open_points.append(f"文件内及同批次有{len(lists)}份同零件的偏差认可汇总清单，尚不能确定按哪一份核对")
        if open_points:
            # The list's own conditions cannot be decided; a failure already found on the cover still decides.
            pending = "；".join(open_points) + "，清单相关各项（清单是否存在、临时偏差有效期、两处期限一致性）未核对，需人工复核"
            if failures:
                return "NG", route + "；".join(failures) + "。另：" + pending + "。"
            raise unable(route + pending + "。")
        item = lists[0]
        with self.reading(ctx, item, DEVIATIONS, (PER_PAGE, STITCHED)) if item is not None else _Here(ctx):
            listed = self.list_deadlines(ctx)
        valid = [day for day, _item in listed]
        expired: dict[date, list[str]] = {}
        for day, entry in listed:
            if day <= today and entry not in expired.setdefault(day, []):
                expired[day].append(entry)
        for day, entries in sorted(expired.items()):
            if entries:
                failures.append(f"清单{'、'.join(entries)}临时偏差至{day}，有效期不晚于当前日期{today}")
        if deadline is not None:
            if not valid:
                if not failures:
                    raise unable(f"{route}清单备注中没有“临时偏差至<日期>”，封面期限{deadline}没有可比的清单期限，需人工复核。")
            elif deadline != max(valid):
                failures.append(f"封面期限{deadline}与清单期限（{'、'.join(str(day) for day in sorted(set(valid)))}，最晚{max(valid)}）不一致")
        if failures:
            return "NG", route + "；".join(failures) + "。"
        return "PASS", route + f"同零件的偏差认可汇总清单四个字段齐全；封面整改承诺最晚期限{deadline}晚于当前日期，与清单最晚有效期一致；清单临时偏差有效期均晚于当前日期。"

    def deviation_lists(self, ctx: RuleContext, part: str, name: str) -> tuple[list, list[str]]:
        """(lists of this part number and part name, None for this file; what keeps a same-round list undecided).

        Every other same-round original is a candidate, whatever it was recognised as (an unknown type, a reference), as
        the clause only asks for a list "in the file or in the same batch"; a list is told by its content, never its name."""
        found: list[Any] = []
        open_points: list[str] = []
        unread: list[str] = []
        if self.list_matches(ctx, part, name)[0] == "same":
            found.append(None)
        if self.group_context is None:
            return found, open_points
        typed = {item.audit_id: kind for kind, items in self.group_context.by_doc_type.items() for item in items}
        # Same-round E11A counterparts (deviation release lists by recognition) first, then every other original.
        ordered = sorted(self.group_context.by_audit_id.values(), key=lambda item: typed.get(item.audit_id) != _LIST_KIND)
        for item in ordered:
            if item.audit_id == self.audit_file.audit_id or Path(str(item.file_url)).suffix.lower() not in type(self).SUPPORTED_EXTENSIONS:
                continue
            # A file recognised as another document type and unreadable as a table is not taken as a possible list.
            loose = typed.get(item.audit_id) in (None, "", _LIST_KIND, *_APPROVAL_KINDS)
            with self.reading(ctx, item, DEVIATIONS, (PER_PAGE, STITCHED)):
                state, theirs = self.list_matches(ctx, part, name)
            if state == "none" and loose and self.untabled(str(item.file_url)):
                state = "unread"
            if state == "same":
                found.append(item)
            elif state == "joint":
                open_points.append(
                    f"同批《{original_name(item)}》是偏差认可汇总清单，零件号写作“{theirs[0]}”、零件名称写作“{theirs[1]}”，并列了多个零件；"
                    f"条文“同零件编号、同零件名称”是否包括这种并列多个零件的清单，按字面判不了"
                )
            elif state == "unread":
                unread.append(original_name(item))
        if self.group_context.unavailable_files:
            unread.append(f"{len(self.group_context.unavailable_files)}份无法读取的冻结资料")
        if not found and unread:
            open_points.append(f"同批次原件（{'、'.join(unread)}）未能读取，尚不能确认是否为同零件的偏差认可汇总清单")
        return found, open_points

    def list_matches(self, ctx: RuleContext, part: str, name: str) -> tuple[str, tuple[str, str]]:
        """Whether the table read is a deviation release list of this part, and the identity it writes.

        "same": its 零件号 and 零件名称 are exactly this part's; "joint": it writes several parts ("F01RD0YC50/F01RC0Y796")
        and this one among them, which the clause's "same part number" does not settle; "other": another part;
        "unread": a list whose identity cannot be read; "none": no list with the clause's fields."""
        for sheet in Table(ctx).find_sheets("偏差认可汇总", on_missing=Decision.IGNORE):
            if self.located(sheet, "偏差区") is None:
                continue
            try:
                theirs = self.identity(ctx, sheet)
            except GuardStop:
                return "unread", ("", "")
            if compact(theirs[0]) == compact(part) and compact(theirs[1]) == compact(name):
                return "same", theirs
            parts = {compact(value) for value in re.split(r"[/、,，;；]", theirs[0])}
            names = {compact(value) for value in re.split(r"[/、,，;；]", theirs[1])}
            if compact(part) in parts and compact(name) in names:
                return "joint", theirs
            return "other", theirs
        return "none", ("", "")

    def list_deadlines(self, ctx: RuleContext) -> list[tuple[date, str]]:
        """Each "临时偏差至<date>" of the list's remark column, with the deviation item it belongs to."""
        sheet = next(sheet for sheet in Table(ctx).find_sheets("偏差认可汇总", on_missing=Decision.IGNORE) if self.located(sheet, "偏差区") is not None)
        section = self.section(sheet, "偏差区")
        found = []
        current = ""
        for row in section.rows("偏差项", "UAES评估确认", "是否需要偏差", "偏差备注"):
            current = str(row.value("偏差项") or "").strip() or current
            for day in temporary(str(row.value("偏差备注") or "")):
                if day is None:
                    raise unable(f"偏差认可汇总清单第{row.row_index}行“临时偏差至”后的日期未能读取，需人工复核。")
                self.cite(sheet, row.row_index, row.columns["偏差备注"].header_col)
                found.append((day, current or f"第{row.row_index}行"))
        return found
