"""UAES simple records on the shared rule engine; textual labels never prove signatures or seals.

Spreadsheets and PDFs are read as workbooks (a PDF also by its text layer). A DOCX original is
read only by the snapshot reader, so each record keeps that reader as its DOCX carrier
(``Uaes...DocxAgent``); the leaves bind it to ``.docx``.
"""

from __future__ import annotations

import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.rule_engine import Cell, Decision, RuleContext, Sheet, Table

from .excel import UaesExcelAgent, compact, unable
from .native import MissingFact, UaesSourceAgent, UnknownFact
from .resources import combine
from .resources import records as resources

# A leading area number: "1. 基本信息", "2、分供方追溯", "(3)生产过程追溯".
_NUMBERING = re.compile(r"^[\(（]?\d+[\.．、\)）]?")


def _chosen(text: str, positive: str, negative: str) -> bool | None:
    """The one option a mark selects in `text`; None when neither or both are marked."""
    text = "".join(str(text).split())
    marks = f"[{resources.SELECTED}]"
    yes = bool(re.search(f"(?:{marks}{positive}|{positive}{marks})", text))
    no = bool(re.search(f"(?:{marks}{negative}|{negative}{marks})", text))
    if yes and not no:
        return True
    if no and not yes:
        return False
    return None


class UaesRecordAgent(UaesExcelAgent):
    """A UAES record read from its own original; every cell it relies on is evidence of that original."""

    # Leaves bind each clause to its method by the clause's own ID, never by position.
    SOURCE_CLAUSE_IDS = True
    DOCUMENT_MODULE: Any = resources.TEXT
    EXTRA_RESOURCE = combine(resources.NO_TABLE)

    def precheck(self):
        """Preparation facts still come from the snapshot reader."""
        from .precheck import structural_facts

        return structural_facts(UaesSourceAgent(self.audit_file, self.rule, group_context=self.group_context))

    def _finalize_audit_result(self, result: Any, *, rule_no: int, phase: Any, ctx: RuleContext | None = None) -> Any:
        result = super()._finalize_audit_result(result, rule_no=rule_no, phase=phase, ctx=ctx)
        if result.evidence is not None:
            return result
        from audit.contracts import AuditEvidence, EvidenceDiagnostic

        suffix = Path(str(self.audit_file.file_url)).suffix.lower().lstrip(".")
        # Nothing was read: an explicit empty evidence, so no reader can mistake it for a read.
        evidence = AuditEvidence(
            required="structure_or_visual",
            used="none",
            source_format=suffix,
            prepared_format=suffix,
            diagnostics=(EvidenceDiagnostic(code="uaes_source_fact", stage="evaluation", severity="info", message="客户原始条款的实际执行结果；未知事实未转为通过。"),),
        )
        return result.model_copy(update={"evidence": evidence})

    @staticmethod
    def sheets(ctx: RuleContext) -> list[Sheet]:
        return [Sheet(ctx, ws) for ws in ctx.workbook.worksheets]

    @classmethod
    def cells(cls, sheet: Sheet, *, row_min: int = 1, row_max: int | None = None) -> list[Cell]:
        """Filled cells (merged areas by their anchor), without counting them as read."""
        rows, columns = cls.extent(sheet)
        return sheet.scan_non_empty(row_min=row_min, row_max=min(row_max or rows, rows), col_min=1, col_max=columns)

    @staticmethod
    def text(cell: Cell) -> str:
        """A cell's comparable text, without counting it as read; `cell.value()` records the read."""
        return compact(cell.ws.cell(cell.row_index, cell.col_index).value)

    def find(self, ctx: RuleContext, phrase: str, *, row_max: int | None = None) -> Cell | None:
        """The first cell whose text contains `phrase`; it becomes evidence."""
        wanted = compact(phrase)
        for sheet in self.sheets(ctx):
            for cell in self.cells(sheet, row_max=row_max):
                if wanted in self.text(cell):
                    cell.value()
                    return cell
        return None

    def mentioned(self, ctx: RuleContext, phrase: str) -> bool:
        """Whether a cell or the PDF text layer of the original writes `phrase`."""
        return self.find(ctx, phrase) is not None or self.pdf_mentions(phrase)

    def unseen(self) -> str | None:
        """What of the original the reader cannot see as text, if anything."""
        path = str(self.audit_file.file_url)
        suffix = Path(path).suffix.lower()
        if suffix == ".pdf":
            import fitz

            with fitz.open(path) as document:
                blank = [str(page.number + 1) for page in document if not page.get_text().strip()]
                pictured = [str(page.number + 1) for page in document if page.get_images()]
            if blank:
                return f"原件第{'、'.join(blank)}页没有文字层"
            if pictured:
                return f"原件第{'、'.join(pictured)}页含图片"
            return None
        if suffix == ".xls":
            return "xls原件中的图片和文本框未能读取"
        if suffix in {".xlsx", ".xlsm"}:
            with zipfile.ZipFile(path) as archive:
                if any(name.startswith(("xl/drawings/", "xl/media/")) for name in archive.namelist()):
                    return "原件含图片或文本框，其中的文字未能读取"
        return None

    def absent(self, what: str) -> tuple[str, str]:
        """NG for a missing fact when the whole original was read as text; else a reading gap."""
        if reason := self.unseen():
            raise unable(f"原件文字中未读到{what}；{reason}，需人工复核。")
        return "NG", f"原件中没有{what}。"

    def choice(self, ctx: RuleContext, label: str) -> str | None:
        """The text written after `label`: in its own cell after the label, and in the cells right of it."""
        wanted = compact(label)
        for sheet in self.sheets(ctx):
            for cell in self.cells(sheet):
                own = self.text(cell)
                if not own.startswith(wanted):
                    continue
                cell.value()
                right = [other for other in self.cells(sheet, row_min=cell.row_index, row_max=cell.row_index) if other.col_index > cell.col_index]
                for other in right:
                    other.value()
                return own[len(wanted) :] + "".join(self.text(other) for other in right)
        return None


class UaesPackagingAgent(UaesRecordAgent):
    PRECHECK_METHODS = {"U09:1": "rule_1"}

    def titled(self, ctx: RuleContext) -> bool:
        """ "包装方案" as a title: a sheet name, a cell of a sheet's first rows, or the opening text of a PDF."""
        wanted = compact(resources.PACKAGING_TITLE)
        # The 包装方案信息 block repeats the title words; it is not the title.
        block = compact("包装方案信息")
        for sheet in self.sheets(ctx):
            if wanted in compact(sheet.title).replace(block, ""):
                return True
            for cell in self.cells(sheet, row_max=8):
                if wanted in self.text(cell).replace(block, ""):
                    cell.value()
                    return True
        if str(self.audit_file.file_url).lower().endswith(".pdf"):
            import fitz

            with fitz.open(str(self.audit_file.file_url)) as document:
                opening = "".join(block[4] for block in document[0].get_text("blocks")[:3]) if len(document) else ""
            return wanted in compact(opening).replace(block, "")
        return False

    @rule("包装方案标题与六个内容块")
    def rule_1(self, ctx: RuleContext):
        if not self.titled(ctx):
            if self.mentioned(ctx, resources.PACKAGING_TITLE):
                raise unable("原件提到“包装方案”，但标题位置（工作表名、各表前8行或PDF首页开头）未见；正文提及不能代替文件标题，需人工复核。")
            return self.absent("“包装方案”标题")
        missing = [name for name in resources.PACKAGING_BLOCKS if not self.mentioned(ctx, name)]
        if missing:
            return self.absent("、".join(f"“{name}”" for name in missing) + "内容块")
        return "PASS", "原件标题含“包装方案”，并有" + "、".join(f"“{name}”" for name in resources.PACKAGING_BLOCKS) + "六个内容块。"


class UaesControlledDocumentAgent(UaesRecordAgent):
    """A controlled stamp is an image fact: no table of the original is read."""

    def _load_workbook(self) -> Any:
        from openpyxl import Workbook

        if self._workbook_cache is None:
            self._workbook_cache = Workbook()
        return self._workbook_cache

    @rule("原件实际受控章")
    def rule_1(self, ctx: RuleContext):
        return self.stamp()


class UaesEngineeringHistoryAgent(UaesRecordAgent):
    PRECHECK_METHODS = {"U11:1": "rule_1"}
    DOCUMENT_MODULE = resources.ENGINEERING_HISTORY
    EXTRA_RESOURCE = combine(resources.ENGINEERING_HISTORY.DOCUMENT)
    FIELDS = ("项目", "零件号", "变更内容", "变更原因", "时间")

    @rule("工程更改一览表及五类字段")
    def rule_1(self, ctx: RuleContext):
        sheets = Table(ctx).find_sheets("工程更改一览表", on_missing=Decision.IGNORE)
        title = self.find(ctx, resources.HISTORY_TITLE)
        if not sheets:
            if title is not None:
                raise unable(f"工作表“{title.ws.title}”有“工程更改一览表”标题，但未能定位其表头（项目、零件号、变更内容简述、变更原因、时间中至少三项），检查程序未能读取，需人工复核。")
            return self.absent("工程更改一览表")
        if title is None and not any(compact(resources.HISTORY_TITLE) in compact(sheet.title) for sheet in sheets):
            # The clause names the 工程更改一览表; a change table under another title is not judged here.
            raise unable(f"工作表“{sheets[0].title}”有更改记录表头，但未见“工程更改一览表”标题，尚不能确认是否为工程更改一览表，需人工复核。")
        found = []
        for sheet in sheets:
            section = self.section(sheet, "工程更改区")
            columns = section.columns(optional=self.FIELDS)
            missing = [name for name in self.FIELDS if name not in columns]
            row = section.resolved.header_row
            if missing:
                headers = "、".join(str(cell.value()).strip() for cell in self.cells(sheet, row_min=row, row_max=row))
                # Meaning-alike names pass by the clause; a name not declared here is not proof of absence.
                raise unable(f"工程更改一览表“{sheet.title}”第{row}行表头为“{headers}”，未能对应{'、'.join(missing)}列，意思相近的列名需人工确认。")
            for name in self.FIELDS:
                section.header_cell(name).value()
            found.append(f"“{sheet.title}”第{row}行")
        return "PASS", f"工程更改一览表{'、'.join(found)}含项目、零件号、变更内容简述、变更原因、时间各列。"


class UaesTraceabilityAgent(UaesRecordAgent):
    PRECHECK_METHODS = {"U18:1": "rule_1"}
    DOCUMENT_MODULE = resources.TRACEABILITY

    @staticmethod
    def substance(text: str, labels: tuple[str, ...]) -> str:
        """What a cell writes beyond the template's labels; a numbered label stub is no content."""
        rest, stripped = text, False
        for label in sorted((compact(label) for label in labels), key=len, reverse=True):
            if label and label in rest:
                rest, stripped = rest.replace(label, ""), True
        rest = re.sub(r"[\W_]+", "", rest)
        return "" if stripped and rest.isdigit() else rest

    def areas(self, sheet: Sheet) -> tuple[str, str]:
        """Each of the five areas of one titled sheet, by its heading, holds content."""
        headings: dict[str, Cell] = {}
        for cell in self.cells(sheet):
            name = _NUMBERING.sub("", self.text(cell))
            for area in resources.TRACEABILITY_AREAS:
                if name == compact(area) and area not in headings:
                    headings[area] = cell
        missing = [area for area in resources.TRACEABILITY_AREAS if area not in headings]
        if missing:
            return "NG", f"“{sheet.title}”中没有{'、'.join(f'“{area}”' for area in missing)}区域"
        order = sorted(headings.items(), key=lambda item: item[1].row_index)
        pictured = {anchor._from.row + 1 for image in getattr(sheet.ws, "_images", []) if (anchor := getattr(image, "anchor", None)) is not None and hasattr(anchor, "_from")}
        for index, (area, heading) in enumerate(order):
            heading.value()
            last = order[index + 1][1].row_index - 1 if index + 1 < len(order) else self.extent(sheet)[0]
            content = [
                cell
                for cell in self.cells(sheet, row_min=heading.row_index, row_max=last)
                if (cell.row_index, cell.col_index) != (heading.row_index, heading.col_index) and self.substance(self.text(cell), resources.TRACEABILITY_AREAS[area])
            ]
            if content:
                content[0].value()
                continue
            where = f"“{sheet.title}”第{heading.row_index}至{last}行"
            if any(heading.row_index <= row <= last for row in pictured):
                return "UNABLE", f"“{area}”区域（{where}）只有图片，没有文字内容，图片是否为追溯内容需人工确认"
            if Path(str(self.audit_file.file_url)).suffix.lower() in {".xls", ".pdf"} and (reason := self.unseen()):
                return "UNABLE", f"“{area}”区域（{where}）没有读到文字内容；{reason}，需人工复核"
            return "NG", f"“{area}”区域（{where}）只有模板标签，没有填写内容"
        return "PASS", f"“{sheet.title}”的" + "、".join(f"“{area}”（第{cell.row_index}行起）" for area, cell in order) + "五个区域均有内容"

    @rule("供应商零件追溯五个区域须有实际内容")
    def rule_1(self, ctx: RuleContext):
        wanted = compact(resources.TRACEABILITY_TITLE)
        titled = [sheet for sheet in self.sheets(ctx) if wanted in compact(sheet.title) or any(wanted in self.text(cell) for cell in self.cells(sheet, row_max=5))]
        if not titled:
            return self.absent("“供应商零件追溯”表")
        outcomes = [self.areas(sheet) for sheet in titled]
        for status, message in outcomes:
            if status == "PASS":
                return "PASS", f"供应商零件追溯表{message}。"
        for status, message in outcomes:
            if status == "UNABLE":
                raise unable(f"供应商零件追溯表{message}。")
        return "NG", f"供应商零件追溯表{outcomes[0][1]}。"


class UaesTrialAgent(UaesRecordAgent):
    PRECHECK_METHODS = {"U15:1": "rule_1"}

    @rule("试装流转记录和两类实际勾选结论")
    def rule_1(self, ctx: RuleContext):
        missing = [area for area in resources.TRIAL_AREAS if not self.mentioned(ctx, area)]
        if missing:
            return self.absent("、".join(f"“{area}”" for area in missing) + "区域")
        missing = [state for state in resources.TRIAL_FLOW if not self.mentioned(ctx, state)]
        if missing:
            return self.absent("流转记录中的" + "、".join(f"“{state}”" for state in missing))
        unknown = []
        for label, positive, negative in resources.TRIAL_CHOICES:
            written = self.choice(ctx, label)
            selected = None if written is None else _chosen(written, compact(positive), compact(negative))
            if selected is False:
                return "NG", f"{label}勾选的是“{negative}”。"
            if selected is None:
                unknown.append(f"{label}（读到“{written}”）" if written else f"{label}（未读到选项）")
        if unknown:
            raise unable("尚不能唯一确认" + "、".join(unknown) + "的实际勾选；文字标签不代表已选择，需人工复核。")
        return "PASS", "原件有流转记录、试装结论、检验结论区域及“流程结束”“流程完成”，试装结论勾选“通过”，检验结论勾选“合格”。"


class UaesLabQualificationAgent(UaesRecordAgent):
    def deadlines(self, ctx: RuleContext) -> set[date]:
        """The certificate deadlines written beside their labels; a PDF also by its text layer."""
        found: set[date] = set()
        unreadable = []
        for sheet in self.sheets(ctx):
            for value in self.labelled(sheet, resources.LAB_DEADLINE):
                deadline = normalize_excel_date(value)
                if deadline is None:
                    unreadable.append(value)
                else:
                    found.add(deadline)
        if not found and str(self.audit_file.file_url).lower().endswith(".pdf"):
            import fitz

            with fitz.open(str(self.audit_file.file_url)) as document:
                text = "".join(page.get_text() for page in document)
            labels = "|".join(map(re.escape, sorted(resources.LAB_DEADLINE, key=len, reverse=True)))
            for match in re.finditer(rf"(?:{labels})\s*[:：]?\s*(\d{{4}}\s*[-/.年]\s*\d{{1,2}}\s*[-/.月]\s*\d{{1,2}})", text):
                if (deadline := normalize_excel_date(re.sub(r"\s", "", match.group(1)))) is not None:
                    found.add(deadline)
        if not found:
            read = f"（读到“{'、'.join(map(str, unreadable))}”）" if unreadable else ""
            raise unable(f"认可证书的截止日期未能可靠识别{read}，需人工复核。")
        return found

    @rule("实验室认可标识和真实截止日期")
    def rule_1(self, ctx: RuleContext):
        missing = [phrase for phrase in (resources.LAB_BODY, resources.LAB_MARK) if not self.mentioned(ctx, phrase)]
        if missing:
            return self.absent("、".join(f"“{phrase}”" for phrase in missing))
        deadlines = self.deadlines(ctx)
        if len(deadlines) > 1:
            raise unable(f"原件写有多个截止日期（{'、'.join(sorted(map(str, deadlines)))}），尚不能确定证书截止日期，需人工复核。")
        deadline = next(iter(deadlines))
        if deadline <= date.today():
            return "NG", f"实验室资质截止日期{deadline}不晚于当前日期，资质已到期。"
        message = f"资质截止日期{deadline}晚于当前日期；CNAS标识文字及认可委员会名称已读取。"
        if self.group_context and any(any(term in str(item.file_metadata.get("original_filename", "")) for term in resources.LAB_REPORT_NAMES) for item in self.group_context.by_audit_id.values()):
            message += "同批有试验报告，需人工比对测试机构与资质实验室是否一致。"
        status, visual = self.visual({"cnas_mark": "资质证书存在实际CNAS认可标识，不能仅有提及CNAS或空标识占位。"}, "证书实际CNAS认可标识")
        return status, message + visual


class UaesMeasurementComparisonAgent(UaesRecordAgent):
    def precheck(self):
        from .precheck_l1 import precheck_measurement

        return precheck_measurement(UaesSourceAgent(self.audit_file, self.rule, group_context=self.group_context))

    def sign_off(self, ctx: RuleContext) -> list[Cell] | None:
        """The last row writing all four sign-off labels: the report's foot."""
        labels = [compact(label) for label in resources.COMPARISON_SIGNERS]
        found = None
        for sheet in self.sheets(ctx):
            rows: dict[int, dict[str, Cell]] = {}
            for cell in self.cells(sheet):
                for label in labels:
                    if self.text(cell).startswith(label):
                        rows.setdefault(cell.row_index, {}).setdefault(label, cell)
            complete = [row for row, hits in rows.items() if len(hits) == len(labels)]
            if complete:
                found = list(rows[max(complete)].values())
        return found

    @rule("测量比较报告四方原件签字")
    def rule_1(self, ctx: RuleContext):
        if not self.mentioned(ctx, resources.COMPARISON_TITLE):
            return self.absent("测量比较报告")
        cells = self.sign_off(ctx)
        if cells is not None:
            for cell in cells:
                cell.value()
        elif not all(self.pdf_mentions(label) for label in resources.COMPARISON_SIGNERS):
            return self.absent("同一行的“供应商”“UAES技术中心”“UAES测量部门”“UAES供应商质量开发工程师”签字栏")
        return self.visual(
            {"four_signatures": "测量比较报告底部供应商、UAES技术中心、UAES测量部门、UAES供应商质量开发工程师四处对应区域均有真实签字。不能用预印姓名或其他位置签字代替。"},
            "测量比较报告底部四方签字",
        )


class UaesPhotoAgent(UaesSourceAgent):
    PRECHECK_METHODS = {"U13:1": "rule_1"}

    SUPPORTED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".pdf"})

    @rule("可读取照片载体与人工确认提示")
    def rule_1(self):
        from documents.photo_inputs import validate_photo_input

        path = Path(self.audit_file.file_url)
        result = validate_photo_input(path.name, path.read_bytes())
        message = "需人工确认样品照片是否符合要求；可读取图片不代表照片内容正确。"
        if result["state"] == "valid":
            self.witness({"kind": "file_version"}, f"已安全解码{result['image_count']}幅图像。")
            return "PASS", message
        return ("ERR" if result["state"] == "error" else "UNABLE"), "；".join(result["reasons"]) + "；" + message


# DOCX originals: only the snapshot reader reads Word paragraphs, so these carriers keep it.


class UaesPackagingDocxAgent(UaesSourceAgent):
    PRECHECK_METHODS = {"U09:1": "rule_1"}

    @rule("包装方案标题与六个内容块")
    def rule_1(self):
        facts = self.native()
        facts.require_title(resources.PACKAGING_TITLE)
        for phrase in resources.PACKAGING_BLOCKS:
            facts.require_text(phrase)
        return "PASS", "已读取包装方案标题及原规则要求的六个内容块。"


class UaesEngineeringHistoryDocxAgent(UaesSourceAgent):
    PRECHECK_METHODS = {"U11:1": "rule_1"}

    @rule("工程更改一览表及五类字段")
    def rule_1(self):
        facts = self.native()
        facts.require_text(resources.HISTORY_TITLE)
        aliases = {column.canonical_name: column.aliases for column in resources.ENGINEERING_HISTORY.DOCUMENT[2]}
        table = facts.table(("工程更改",))
        try:
            facts.columns(table, aliases)
        except MissingFact as exc:
            # The source explicitly permits semantically equivalent aliases.
            # An unrecognized spelling is not proof that the field is absent.
            raise UnknownFact("尚不能完整确认工程更改表字段及合法别名，需人工核对。") from exc
        return "PASS", "工程更改表及项目、零件号、变更内容、原因、时间已核实。"


class UaesTraceabilityDocxAgent(UaesSourceAgent):
    PRECHECK_METHODS = {"U18:1": "rule_1"}

    @rule("供应商零件追溯五个区域须有实际内容")
    def rule_1(self):
        facts = self.native()
        facts.require_text(resources.TRACEABILITY_TITLE)
        for name in resources.TRACEABILITY_AREAS:
            facts.field((name,), title=(resources.TRACEABILITY_TITLE,))
        return "PASS", "已读取供应商零件追溯五个区域的对应内容。"


class UaesTrialDocxAgent(UaesSourceAgent):
    @rule("试装流转记录和两类实际勾选结论")
    def rule_1(self):
        facts = self.native()
        for phrase in (*resources.TRIAL_AREAS, *resources.TRIAL_FLOW):
            facts.require_text(phrase)
        results = [_chosen(facts.field((label,)), positive, negative) for label, positive, negative in resources.TRIAL_CHOICES]
        if False in results:
            return "NG", "试装或检验结论选中了未通过/不合格。"
        if None in results:
            return "UNABLE", "尚不能唯一确认两个结论的实际勾选状态，文字标签不代表已选择。"
        return "PASS", "已核实流程结束/完成和两类通过结论的实际勾选。"


class UaesLabQualificationDocxAgent(UaesSourceAgent):
    @rule("实验室认可标识和真实截止日期")
    def rule_1(self):
        facts = self.native()
        facts.require_text(resources.LAB_BODY)
        facts.require_text(resources.LAB_MARK)
        deadline = normalize_excel_date(facts.field(resources.LAB_DEADLINE))
        if deadline is None:
            raise UnknownFact("认可证书的截止日期未能可靠识别，需人工复核。")
        if deadline <= date.today():
            return "NG", "实验室资质已到期，截止日期必须晚于当前日期。"
        message = "资质截止日期晚于当前日期；CNAS标识及认可委员会名称已读取。"
        if self.group_context and any(any(term in str(file.file_metadata.get("original_filename", "")) for term in resources.LAB_REPORT_NAMES) for file in self.group_context.by_audit_id.values()):
            message += "同批有试验报告，需人工比对测试机构与资质实验室是否一致。"
        status, visual_message = self.visual_result({"cnas_mark": "资质证书存在实际CNAS认可标识，不能仅有提及CNAS或空标识占位。"}, "证书实际CNAS认可标识")
        return status, message + visual_message


class UaesMeasurementComparisonDocxAgent(UaesSourceAgent):
    def precheck(self):
        from .precheck_l1 import precheck_measurement

        return precheck_measurement(self)

    @rule("测量比较报告四方原件签字")
    def rule_1(self):
        facts = self.native()
        facts.require_text(resources.COMPARISON_TITLE)
        for phrase in resources.COMPARISON_SIGNERS:
            facts.require_text(phrase)
        observed = self.visual_pdf({"four_signatures": "测量比较报告底部供应商、UAES技术中心、UAES测量部门、UAES供应商质量开发工程师四处对应区域均有真实签字。不能用预印姓名或其他位置签字代替。"})
        fact = observed["four_signatures"]
        return ("PASS", "四方实际签字已从原件核实。") if fact is True else ("NG", "四方签字不完整。") if fact is False else ("UNABLE", "四方签字尚未核实，需人工复核。")
