"""UAES C sample release report: reject marks, the primary remark, sign-off dates and deviation deadlines."""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace
from typing import Any

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table

from .excel import UaesExcelAgent, compact, unable
from .materials import drawing_anchors, related_by_content
from .resources import approval as resources
from .resources import combine

_DATE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
_MARK = re.compile(r"[×xX☑✓√✔■●]")
_FULL = "完全认可"
_CONDITIONAL = ("偏差认可", "条件认可", "超差", "不合格", "整改")
_DEVIATIONS = SimpleNamespace(DOCUMENT=resources.DEVIATIONS, PDF_SPECS=resources.PDF_SPECS)


def _day(year: str, month: str, day: str) -> date | None:
    """A Chinese-order date; a two-digit year (26.10.15) is of this century."""
    try:
        return date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day))
    except ValueError:
        return None


def deadlines(text: str) -> list[date | None]:
    """Promised latest dates: "截止<date>", "临时偏差至<date>", "<date>前完成"; never an expected rectification time."""
    found = re.findall(r"(?:截止|临时偏差至)\s*(\d{2,4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", text)
    found += re.findall(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?\s*前完成", text)
    return [_day(*parts) for parts in found]


class UaesApprovalAgent(UaesExcelAgent):
    DOCUMENT_MODULE = resources
    EXTRA_RESOURCE = combine(resources.DOCUMENT, resources.DEVIATIONS)
    ROLES = resources.ROLES
    # The English halves of the bilingual role and date labels on the cover.
    LABEL_WORDS = {"日期", "date", "team", "leader", "checked", "approved", "by"}

    def precheck(self):
        """Preparation facts still come from the snapshot reader."""
        from .native import UaesSourceAgent
        from .precheck import structural_facts

        return structural_facts(UaesSourceAgent(self.audit_file, self.rule, group_context=self.group_context))

    def pdf(self) -> bool:
        return str(self.audit_file.file_url).lower().endswith(".pdf")

    def witness(self, page: int, bounds: Any, text: str) -> None:
        """A fact read from the PDF text layer, at its place on the page."""
        self.legacy().witness({"kind": "pdf_region", "page": page, "bbox": [round(float(value), 2) for value in bounds], "coordinate_system": "pdf_points_unrotated"}, text)

    def cover(self, ctx: RuleContext) -> tuple[Sheet, Any]:
        found = [(sheet, section) for sheet in Table(ctx).find_sheets("认可封面", on_missing=Decision.IGNORE) for section in [self.located(sheet, "认可区")] if section]
        if len(found) != 1:
            if not found and (self.untabled() or self.unread_pages()):
                raise unable("PDF原件未能读出表格，检查程序未能读取认可报告封面，需人工复核。")
            raise unable(f"检查程序{'未能定位' if not found else f'定位到{len(found)}处'}封面“报告内容/批准/带条件批准/否决”表格，需人工复核。")
        return found[0]

    @staticmethod
    def located(sheet: Sheet, name: str):
        try:
            return sheet.section(name)
        except GuardStop:
            return None

    def cover_signatures(self):
        """Role -> (typed name, date) read beside each cover role label; None when only an image could tell."""
        import fitz

        if not self.pdf():
            return None
        with fitz.open(str(self.audit_file.file_url)) as document:
            page = document[0]
            words = [(fitz.Rect(word[:4]), word[4]) for word in page.get_text("words")]
            images = [fitz.Rect(image["bbox"]) for image in page.get_image_info()]
        labels = {}
        for role in self.ROLES:
            found = [rect for rect, text in words if text.rstrip(":：") == role and text[-1:] in ":："]
            if len(found) != 1:
                return None
            labels[role] = found[0]
        signed = {}
        for role, label in labels.items():
            # A role's entry is on its label line, or just under it, up to the next label on that line.
            bottom = label.y1 + label.height
            right = min((other.x0 for other in labels.values() if other.x0 > label.x0 and other.y0 < bottom and other.y1 > label.y0), default=float("inf"))
            entry = [(rect, text) for rect, text in words if rect.x0 > label.x1 - 1 and rect.x1 <= right + 1 and rect.y0 < bottom and rect.y1 > label.y0]
            day = next((normalize_excel_date(text) for _rect, text in entry if _DATE.fullmatch(text)), None)
            name = " ".join(text for _rect, text in entry if not _DATE.fullmatch(text) and text.rstrip(":：").casefold() not in self.LABEL_WORDS)
            if (not name or day is None) and any(image.intersects(fitz.Rect(label.x0, label.y0, min(right, 1e6), bottom)) for image in images):
                return None  # A handwritten signature image may fill the entry.
            if entry:
                area = fitz.Rect(label)
                for rect, _text in entry:
                    area |= rect
                self.witness(1, area, f"{role}：{name} {day or ''}".strip())
            signed[role] = (name, day)
        return signed

    def rejected(self, ctx: RuleContext, sheet: Sheet, section: Any) -> str | None:
        """The category whose 否决 cell carries a mark; "" when none does; None when a drawn mark cannot be excluded."""
        rows = {}
        for row in section.rows("报告内容"):
            name = str(row.value("报告内容") or "")
            category = next((item for item in resources.CATEGORIES if item in name), None)
            if category:
                rows[category] = row
        if set(rows) != set(resources.CATEGORIES):
            missing = "、".join(item for item in resources.CATEGORIES if item not in rows)
            raise unable(f"封面“报告内容”下未能读取{missing}行，检查程序未能确认否决列，需人工复核。")
        column = section.column("否决").header_col
        for category, row in rows.items():
            mark = str(sheet.cell(row.row_index, column).value() or "").strip()
            if _MARK.search(mark):
                return category
            if mark:
                return None
        if self.pdf():
            return self.drawn_reject()
        anchors = drawing_anchors(str(self.audit_file.file_url), sheet.title) if sheet.ws.parent is self._workbook_cache else None
        first, last = min(row.row_index for row in rows.values()), max(row.row_index for row in rows.values())
        if anchors is not None and not any(top <= last and bottom >= first and left <= column <= right for top, left, bottom, right in anchors):
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
        heading = heading[0]
        under = max(rect.y1 for rect, text in words if text in {"否决", "Reject"} and abs(rect.x0 - heading.x0) < 40)
        vertical = [item["rect"] for item in drawings if item["rect"].width <= 3 and item["rect"].height > 40 and item["rect"].y0 <= heading.y0]
        horizontal = [item["rect"] for item in drawings if item["rect"].height <= 3 and item["rect"].width > 40]
        left = max((rect.x1 for rect in vertical if rect.x1 <= heading.x0), default=None)
        right = min((rect.x0 for rect in vertical if rect.x0 >= heading.x1), default=None)
        top = min((rect.y1 for rect in horizontal if rect.y0 >= under and rect.x0 < heading.x0 and rect.x1 > heading.x1), default=None)
        bottom = min((rect.y0 for rect in horizontal if rect.y0 >= max(rect.y1 for rect in tryout) and rect.x0 < heading.x0 and rect.x1 > heading.x1), default=None)
        if left is None or right is None or top is None or bottom is None:
            return None
        cells = fitz.Rect(left + 1.5, top + 1.5, right - 1.5, bottom - 1.5)

        def stroke(item) -> bool:
            # Table rules are axis-aligned lines and rectangles; a tick or cross is a slanted line or a curve.
            return any(part[0] == "c" or part[0] == "l" and abs(part[1].x - part[2].x) > 1 and abs(part[1].y - part[2].y) > 1 for part in item["items"])

        marks = [item["rect"] for item in drawings if stroke(item) and item["rect"].intersects(cells) and (item["rect"] & cells).get_area() > 0]
        self.witness(1, cells, f"封面否决列：{len(marks)}处图形标记")
        if marks:
            return "否决列"
        if any(rect.intersects(cells) for rect in images) or any(rect.intersects(cells) for rect, _text in words):
            return None
        return ""

    def remark(self, sheet: Sheet) -> tuple[str, Any]:
        rows, columns = self.extent(sheet)
        cells = [cell for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns) if compact(resources.REMARK) in compact(cell.value())]
        if len(cells) != 1:
            raise unable(f"封面{'未能定位' if not cells else '有多处'}“UAES 批准的前提/备注”栏，检查程序未能读取，需人工复核。")
        text = str(cells[0].value())
        # The remark follows its bilingual heading and ends where the team leader signs.
        text = re.split(r"remark", text, maxsplit=1, flags=re.I)[-1]
        return re.split(r"组长\s*[:：]", text, maxsplit=1)[0].strip(), cells[0]

    def identity(self, ctx: RuleContext, sheet: Sheet) -> tuple[str, str]:
        values = []
        for name, aliases in (("零件号", resources.PART_NUMBER), ("零件名称", resources.PART_NAME)):
            wanted = {compact(alias) for alias in aliases}
            rows, columns = self.extent(sheet)
            labels = [cell for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns) if compact(cell.value()) in wanted]
            if len(labels) != 1:
                raise unable(f"“{sheet.title}”中{'未能定位' if not labels else '有多处'}{name}字段，检查程序未能读取，需人工复核。")
            merged = ctx.ops.get_detailed_merge_info(sheet.ws, labels[0].row_index, labels[0].col_index)
            values.append(str(sheet.cell(labels[0].row_index, (merged.max_col if merged else labels[0].col_index) + 1).value() or "").strip())
        return values[0], values[1]

    @rule("UAES封面首要备注与实际否决签批及偏差期限")
    def rule_1(self, ctx: RuleContext):
        sheet, section = self.cover(ctx)
        rejected = self.rejected(ctx, sheet, section)
        if rejected is None:
            observed = self._from_legacy(lambda agent: agent.visual_pdf({"reject_marked": "封面“否决 Reject”列中，尺寸、材料、性能、试装任一行有实际的勾或叉标记；空框为false。"}))
            if observed.get("reject_marked") is True:
                return "NG", "封面否决列有实际标记，为否决件。"
            if observed.get("reject_marked") is not False:
                raise unable("封面否决列有未能识别的内容，勾选尚未核实，需人工复核。")
        elif rejected:
            return "NG", f"封面否决列{rejected}有实际标记，为否决件。"
        remarks, _cell = self.remark(sheet)
        full = _FULL in remarks
        conditional = [term for term in _CONDITIONAL if term in remarks]
        if full == bool(conditional):
            raise unable(f"封面备注“{remarks}”{'同时含完全认可与偏差/整改表述' if full else '无法归类为完全认可或偏差认可'}，需人工复核。")
        if full:
            return self.full_approval()
        return self.conditional_approval(ctx, sheet, remarks)

    def full_approval(self) -> tuple[str, str]:
        signed = self.cover_signatures()
        if signed is None:
            return self.visual(
                {"signed_dates": "封面组长Team Leader、审核Checked by、批准Approved by三处实际签字及各自日期全部齐全，且组长日期≤审核日期≤批准日期。"}, "完全认可的三方实际签字及递增日期"
            )
        missing = [role for role, (name, day) in signed.items() if not name or day is None]
        if missing:
            return "NG", "封面完全认可，但" + "、".join(missing) + "缺少签名或日期。"
        days = [day for _name, day in signed.values()]
        listed = "，".join(f"{role}{name} {day}" for role, (name, day) in signed.items())
        if days != sorted(days):
            return "NG", "封面签字日期不是组长≤审核≤批准：" + listed + "。"
        return "PASS", "封面完全认可，" + listed + "，日期递增。"

    def conditional_approval(self, ctx: RuleContext, sheet: Sheet, remarks: str) -> tuple[str, str]:
        promised = deadlines(remarks)
        if not promised or any(item is None for item in promised):
            raise unable(f"封面备注“{remarks}”中未能读取承诺最晚期限（…前完成/截止…），预计整改时间不能代替承诺，需人工复核。")
        deadline, today = max(item for item in promised if item is not None), date.today()
        if deadline <= today:
            return "NG", f"封面条件认可时间({deadline})早于或等于当前({today})，请确认整改是否逾期或已闭环。"
        part, name = self.identity(ctx, sheet)
        lists = self.deviation_lists(ctx, part, name)
        if not lists:
            return "NG", f"文件及同批次没有零件号“{part}”、零件名称“{name}”的偏差认可汇总清单。"
        if len(lists) > 1:
            raise unable(f"文件及同批次有{len(lists)}份同零件的偏差认可汇总清单，尚不能确定按哪一份核对，需人工复核。")
        item = lists[0]
        context = self.reading(ctx, item, _DEVIATIONS) if item is not None else _Here(ctx)
        with context:
            return self.reconcile(ctx, deadline, today)

    def deviation_lists(self, ctx: RuleContext, part: str, name: str) -> list:
        """The lists (None for this file) whose header names this part number and part name among its own."""
        found: list[Any] = []
        unknown: list[Any] = []
        if self.list_matches(ctx, part, name):
            found.append(None)
        for item in related_by_content(self, ("偏差认可汇总", "偏差")).items:
            with self.reading(ctx, item, _DEVIATIONS):
                matched = self.list_matches(ctx, part, name)
                untabled = str(item.file_url) in self.__dict__.get("_untabled", set())
            if matched:
                found.append(item)
            elif matched is None or untabled:
                unknown.append(str((item.file_metadata or {}).get("original_filename") or item.file_url))
        if not found and unknown:
            raise unable(f"同批次原件（{'、'.join(unknown)}）未能读取，尚不能确认是否为同零件的偏差认可汇总清单，需人工复核。")
        return found

    def list_matches(self, ctx: RuleContext, part: str, name: str) -> bool | None:
        """Whether the workbook being read holds a deviation list of this part; None when its identity is unreadable."""
        for sheet in Table(ctx).find_sheets("偏差认可汇总", on_missing=Decision.IGNORE):
            if self.located(sheet, "偏差区") is None:
                continue
            try:
                theirs = self.identity(ctx, sheet)
            except GuardStop:
                return None
            # A list may cover several parts: "F01RD0YC50/F01RC0Y796", "机加工壳体 / 毛坯件壳体".
            parts = {compact(value) for value in re.split(r"[/、,，;；]", theirs[0])}
            names = {compact(value) for value in re.split(r"[/、,，;；]", theirs[1])}
            return compact(part) in parts and compact(name) in names
        return False

    def reconcile(self, ctx: RuleContext, deadline: date, today: date) -> tuple[str, str]:
        sheet = next(sheet for sheet in Table(ctx).find_sheets("偏差认可汇总", on_missing=Decision.IGNORE) if self.located(sheet, "偏差区") is not None)
        section = self.section(sheet, "偏差区")
        rows = list(section.rows("偏差项", "UAES评估确认", "是否需要偏差", "偏差备注"))
        if not rows:
            return "NG", "偏差认可汇总清单没有偏差行。"
        expired, unequal = [], []
        for row in rows:
            item = str(row.value("偏差项") or "").strip() or f"第{row.row_index}行"
            if not row.is_present("UAES评估确认") or not row.is_present("是否需要偏差"):
                return "NG", f"偏差认可汇总清单{item}的UAES评估确认或是否需要偏差为空。"
            found = deadlines(str(row.value("偏差备注") or ""))
            if len(found) != 1 or found[0] is None:
                raise unable(f"偏差认可汇总清单{item}的备注未能唯一读取临时偏差有效期，需人工复核。")
            if found[0] <= today:
                expired.append((found[0], item))
            elif found[0] != deadline:
                unequal.append((found[0], item))
        if expired:
            day, item = min(expired)
            return "NG", f"{item}偏差项有效期({day})已过期，请确认已更新批准或整改闭环。"
        if unequal:
            day, item = unequal[0]
            return "NG", f"封面条件认可时间({deadline})与偏差汇总认可清单承诺时间({day}，{item})不一致，请复核。"
        return "PASS", f"条件认可封面承诺期限({deadline})与全部偏差行有效期一致且未过期。"


class _Here:
    """The deviation list is in this file: read it where it is."""

    def __init__(self, ctx: RuleContext) -> None:
        self.ctx = ctx

    def __enter__(self) -> RuleContext:
        return self.ctx

    def __exit__(self, *exc: object) -> None:
        return None
