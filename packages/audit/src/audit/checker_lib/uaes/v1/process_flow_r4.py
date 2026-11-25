"""UAES revision 4 recognition of explicitly equivalent integrated test names."""

import re
import tempfile
from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl.utils.cell import get_column_letter

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.flowchart import placed_drawings
from audit.checker_lib.uaes.identity import pair_identity
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Table

from .process_flow import UaesProcessFlowRevision3, compact, unable

_AIRTIGHT_NAMES = frozenset({"气密", "气密性检测", "气检"})
_PAGE_READS = 6  # Maximum rendered flow-chart pages read for a disposition branch, not an assumed page count.
# A drawing layer or rendering that could not be read or converted: the chart is then not completely read.
_UNREAD = (OSError, ValueError, KeyError, BadZipFile, ParseError, RuntimeError)
_UNCONFIRMED = object()  # A branch the page reading located but its local reading did not confirm.


_DISPOSITION = re.compile(r"(?:(?:不合格|不良)(?:品|件))?(?:隔离|返工(?:返修)?|返修|报废|偏差(?:处理|放行))|不(?:合格|良)(?:品|件)(?:处理|处置)|rework|scrap|quarantine", re.I)
_FAILED_BRANCH = re.compile(r"(?:(?:检测|检验|测试|判定)(?:结果)?[:：]?)?(?:不合格|不良|未通过)(?:品|件)?|(?:(?:test|inspection)(?:result)?[:：]?)?(?:ng|nok|fail(?:ed)?)", re.I)
_BOX = {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 1000}, "minItems": 4, "maxItems": 4}
_LOCATE = {"type": "object", "properties": {"box": {"anyOf": [_BOX, {"type": "null"}]}}, "required": ["box"], "additionalProperties": False}
_BRANCH_SCHEMA = {
    "type": "object",
    "properties": {"condition": {"type": "string"}, "destination": {"type": "string"}, "arrow": {"type": "boolean"}},
    "required": ["condition", "destination", "arrow"],
    "additionalProperties": False,
}


def disposition_text(observation: dict) -> str | None:
    condition, destination = observation.get("condition"), observation.get("destination")
    if not isinstance(condition, str) or not isinstance(destination, str) or observation.get("arrow") is not True:
        return None
    if len(condition) > 80 or len(destination) > 80 or not _FAILED_BRANCH.fullmatch(compact(condition)) or not _DISPOSITION.fullmatch(compact(destination)):
        return None
    return f"“{condition}” → “{destination}”"


class UaesProcessFlowRevision4(UaesProcessFlowRevision3):
    def station_rows(self, sheet, label, step, flow_steps):
        """A PFMEA row whose operation name is blank is named by its failure-mode field when that field holds one of the
        confirmed integrated-test names (product owner, 2026-11-24: 气密、气密性检测、气检 are one operation)."""
        rows = []
        for name, param, method, proof, loc, part, target, mode in super().station_rows(sheet, label, step, flow_steps):
            if label == "PFMEA" and not name and mode:
                name, loc = mode, f"{loc}（工序名栏空白，取失效模式栏的工序名）"
            rows.append((name, param, method, proof, loc, part, target, mode))
        return rows

    @rule("四类核心控制节点")
    def rule_4(self, ctx: RuleContext):
        """A node class missing after every operation, cell, drawing-layer note and rendered page was read is absent:
        the product owner decided (2026-11-24) that a complete reading which finds nothing judges the clause."""
        self.__dict__.pop("_node_reading", None)
        self.__dict__.pop("_drawings_unread", None)
        try:
            return super().rule_4(ctx)
        except GuardStop as stop:
            reading = self.__dict__.pop("_node_reading", None)
            if stop.decision != Decision.UNABLE or not reading or not reading["complete"] or not reading["missing"]:
                raise
            ctx.record_execution()
            looked = "（原图目视识读，请对照原件核实）" if reading["visual"] else ""
            return "NG", f"{reading['scope']}，未见{'、'.join(reading['missing'])}的对应工序、标注或书面说明{looked}。"

    def pdf_stamp(self) -> tuple[str, str]:
        """A page looked at whole is "not found" when the reading saw no imprint there, even if it could not vouch for
        every corner: the product owner decided (2026-11-24) that a completely read original without the stamp fails."""
        import fitz

        from .process_flow import CROP_PROMPT, CROP_SCHEMA, STAMP_PROMPT, STAMP_SCHEMA, _text

        unread = set(self.unread_pages())
        unknown, unclear, negatives = [], [], []
        version = str((getattr(self.audit_file, "file_metadata", None) or {}).get("file_version_id") or self.audit_file.audit_id)
        with fitz.open(str(self.audit_file.file_url)) as doc:
            for page in doc:
                n, rect = page.number + 1, page.rect
                if n in unread:
                    unknown.append(f"第{n}页为扫描页，未作图像识读")
                    continue
                fact = self.transcribe(page.get_pixmap(dpi=150).tobytes("png"), STAMP_PROMPT, STAMP_SCHEMA, f"第{n}页的受控章")
                seal, controlled, box = fact.get("seal_present"), fact.get("controlled_stamp"), fact.get("bbox")
                locator = {"kind": "pdf_region", "page": n, "engine": "ocr_llm", "fact": "controlled_stamp"}
                if seal is False and controlled is not True and box in ([], None):
                    negatives.append(n)
                    self.legacy().witness({**locator, "bbox": list(rect)}, "整页未见印迹", version, visual=True)
                    continue
                if seal is not True or not isinstance(box, list) or len(box) != 4 or not all(type(v) is int for v in box) or not (0 <= box[0] < box[2] <= 1000 and 0 <= box[1] < box[3] <= 1000):
                    unknown.append(f"第{n}页未能定位或确认印迹")
                    continue
                clip = fitz.Rect(*(offset + v * size / 1000 for offset, v, size in zip((rect.x0, rect.y0) * 2, box, (rect.width, rect.height) * 2)))
                if abs(clip) >= 0.9 * abs(rect):
                    unknown.append(f"第{n}页未提供可复核的局部印迹位置")
                    continue
                crop = self.transcribe(page.get_pixmap(clip=clip, dpi=220).tobytes("png"), CROP_PROMPT, CROP_SCHEMA, f"第{n}页印迹局部")
                text = _text(crop.get("text"))
                if crop.get("seal_present") is True:
                    local = {**locator, "bbox": list(clip)}
                    controlled_text = bool(re.search(r"受控|\bcontrolled\b", text, re.I)) and not re.search(r"非受控|不受控|未受控|not\s+controlled|uncontrolled", text, re.I)
                    if controlled is True and crop.get("legible") is True and controlled_text:
                        self.legacy().witness(local, text, version, visual=True)
                        return "PASS", f"原件第{n}页的印迹内可读“{text[:80]}”（全页定位后读印迹局部，请对照原件核实）。"
                    if controlled is None and crop.get("legible") is False and not controlled_text:
                        self.legacy().witness(local, "疑似印章图形，章内受控标识不可辨", version, visual=True)
                        unclear.append(n)
                        continue
                    if controlled is False and crop.get("legible") is True and text and not controlled_text:
                        self.legacy().witness(local, text, version, visual=True)
                        negatives.append(n)
                        continue
                unknown.append(f"第{n}页整页与局部读数未能共同确认印迹性质")
        if unknown:
            detail = "；".join(unknown[:6])
            prior = f"另有第{'、'.join(map(str, unclear))}页疑似印迹不可辨；" if unclear else ""
            raise unable(f"{prior}{detail}；不能据此认定整份没有受控章，需人工复核。")
        if unclear:
            return "NG", f"第{'、'.join(map(str, unclear))}页已定位疑似印章并核对局部，章内文字或受控标识无法辨认，请人工复核。"
        if not negatives:
            raise unable("没有取得可核实的页面观察，需人工复核。")
        return "NG", f"已逐页查看原件全部{len(negatives)}页，未见受控章（原件图像目视识读，请对照原件核实）。"

    @staticmethod
    def station_name_key(name: str) -> str:
        # Whole names only: do not erase the tested object from a qualified name.
        if compact(name) in _AIRTIGHT_NAMES:
            return "气密"
        return UaesProcessFlowRevision3.station_name_key(name)

    def station_mode_hint(self, mode: str) -> str:
        return mode if compact(mode) in _AIRTIGHT_NAMES else ""

    def station_target_header(self, label: str) -> bool:
        return label in {"测试对象", "检测对象", "被测部位", "testobject", "testedlocation"}

    def station_target(self, chart, op) -> str:
        columns = [c for c in range(1, chart.sheet.ws.max_column + 1) if self.station_target_header(compact(chart.text(chart.header, c)))]
        if len(columns) != 1:
            return ""
        col = columns[0]
        cells = {chart.anchor(row, col) for row in op.rows}
        observed = {chart.text(row, column) for row, column in cells} - {""}
        if len(observed) == 1:
            for row, column in cells:
                self.cite(chart.sheet, row, column)
            return next(iter(observed))
        return ""

    def station_target_matches(self, source_name: str, target_name: str, source_object: str, target_object: str) -> bool:
        # 气密、气密性检测、气检 are one operation (product owner, 2026-11-24): treated as the same name.
        same = compact(source_name) == compact(target_name) or {compact(source_name), compact(target_name)} <= _AIRTIGHT_NAMES
        if same:
            return not (source_object and target_object) or compact(source_object) == compact(target_object)
        # Different names may identify the same type of test without identifying the same object.
        source, target = pair_identity(source_object), pair_identity(target_object)
        return source is not None and source == target and self.station_name_key(source_name) == self.station_name_key(target_name)

    def disposition_node(self, clause, operation):
        text = compact(clause)
        # A non-operation annotation must name the affected parts or explicitly connect failure to disposition.
        if _DISPOSITION.fullmatch(text):
            return operation or text.startswith(("不合格", "不良"))
        branch = re.split(r"→|->", text)
        return len(branch) == 2 and bool(_FAILED_BRANCH.fullmatch(branch[0]) and _DISPOSITION.fullmatch(branch[1]))

    def printable(self) -> bytes:
        """The workbook as the renderer prints it: an .xlsx/.xlsm original itself, an .xls original converted by the same
        LibreOffice the renderer uses. Cell values are still read from the original; only drawings and pictures come from here."""
        from documents.excel.visual import renderable_xlsx

        return renderable_xlsx(Path(str(self.audit_file.file_url)).read_bytes())

    def node_annotations(self, ctx: RuleContext):
        """Text written in the drawing layer of each flow chart sheet. A drawing layer that cannot be read leaves the chart
        not completely read, so a missing node then stays open."""
        path = Path(str(self.audit_file.file_url))
        suffix = path.suffix.lower()
        if suffix not in {".xlsx", ".xlsm", ".xls"}:
            return
        found = []
        try:
            with tempfile.TemporaryDirectory() as folder:
                source = str(path)
                if suffix == ".xls":
                    # Old binary drawings keep their text in records the shape reader does not parse; their converted
                    # copy carries the same shapes and text on the same cells.
                    source = str(Path(folder) / "drawings.xlsx")
                    Path(source).write_bytes(self.printable())
                for sheet in Table(ctx).find_sheets("过程流程图", on_missing=Decision.IGNORE):
                    for drawing in placed_drawings(source, sheet.title):
                        if drawing.place is None or not drawing.text.strip():
                            continue
                        row, col, _, _ = drawing.place
                        note = drawing.text.strip()
                        disposition = bool(re.fullmatch(r"不(?:合格|良)(?:品|件)(?:处理|处置|隔离|返工|返修|报废)", compact(note)))
                        found.append((f"“{sheet.title}”{get_column_letter(col)}{row}处流程图标注", note, (sheet, row, col), disposition))
        except _UNREAD:
            self.__dict__["_drawings_unread"] = True
        yield from found

    def visual_node_evidence(self, ctx: RuleContext, missing: list[str]) -> dict[str, str]:
        """An explicit legible branch proves presence. Absence is recorded as read completely only when the drawing layer was
        read and every rendered page with graphics was looked at for a branch (or the chart has no graphics, its text then
        being all of it). A branch the page reading located but its local reading did not confirm keeps the chart open."""
        reading = self.__dict__["_node_reading"] = {"complete": False, "missing": missing, "scope": "", "visual": False}
        path = Path(str(self.audit_file.file_url))
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self.pdf_node_evidence(path, missing, reading)
        if suffix not in {".xlsx", ".xlsm", ".xls"} or self.__dict__.get("_drawings_unread"):
            return {}
        import fitz
        from documents.excel.visual import has_region_graphics, render_xlsx_pdf

        pages: list[tuple[str, int, bytes]] = []
        try:
            source = self.printable()
            for sheet in Table(ctx).find_sheets("过程流程图", on_missing=Decision.IGNORE):
                if not has_region_graphics(source, sheet_name=sheet.title, cell_range=None):
                    continue
                pdf = render_xlsx_pdf(source, sheet_name=sheet.title, cell_range=None)
                with fitz.open(stream=pdf, filetype="pdf") as document:
                    if len(pages) + len(document) > _PAGE_READS:
                        return {}  # More pages than the read budget: not completely read.
                    for page in document:
                        dpi = min(180, max(36, int(2400 * 72 / max(page.rect.width, page.rect.height))))
                        pages.append((sheet.title, page.number + 1, page.get_pixmap(dpi=dpi).tobytes("png")))
        except _UNREAD:
            return {}
        # The rendered pages are read for a disposition branch only; other node classes rely on the read text.
        if pages and set(missing) != {"异常处理"}:
            return {}
        version = self.version()
        for title, page_number, image in pages:
            found = self.read_disposition(image)
            if found is _UNCONFIRMED:
                return {}
            if found:
                trace, box = found
                self.legacy().witness(
                    {"kind": "workbook_render_region", "sheet": title, "page": page_number, "bbox": box, "coordinate_system": "render_image_0_to_1000", "engine": "ocr_llm"},
                    trace,
                    version,
                    visual=True,
                )
                return {"异常处理": f"异常处理（{title}第{page_number}页原图局部）{trace}"}
        for title, page_number, _image in pages:
            self.legacy().witness(
                {"kind": "workbook_render_region", "sheet": title, "page": page_number, "bbox": [0, 0, 1000, 1000], "coordinate_system": "render_image_0_to_1000", "engine": "ocr_llm"},
                "整页看过，未见不合格分支到异常处置的箭头",
                version,
                visual=True,
            )
        if pages:
            reading.update(complete=True, visual=True, scope=f"已读完流程图全部工序、单元格与绘图层标注，并逐页看过流程图原图（共{len(pages)}页）")
        else:
            reading.update(complete=True, scope="已读完流程图全部工序、单元格与绘图层（流程图页没有图形）")
        return {}

    def pdf_node_evidence(self, path: Path, missing: list[str], reading: dict) -> dict[str, str]:
        """Every page of a PDF flow chart looked at for a disposition branch. Its text layer and tables were already read
        (scanned pages stop the clause before this). The page images are read for a branch only, so only a missing
        disposition class can be completed here; its location is given in the page's own unrotated PDF points."""
        if set(missing) != {"异常处理"}:
            return {}
        import fitz

        pages = []
        with fitz.open(str(path)) as document:
            if document.needs_pass or not 1 <= len(document) <= _PAGE_READS:
                return {}  # Unreadable, or more pages than the read budget: not completely read.
            for page in document:
                dpi = min(180, max(36, int(2400 * 72 / max(page.rect.width, page.rect.height))))
                pages.append((page.number + 1, page.get_pixmap(dpi=dpi).tobytes("png"), fitz.Rect(page.rect), fitz.Matrix(page.derotation_matrix)))
        version = self.version()
        for number, image, rect, derotate in pages:
            found = self.read_disposition(image)
            if found is _UNCONFIRMED:
                return {}
            if found:
                trace, box = found
                area = fitz.Rect(*(origin + value * size / 1000 for origin, value, size in zip((rect.x0, rect.y0) * 2, box, (rect.width, rect.height) * 2))) * derotate
                self.legacy().witness(
                    {"kind": "pdf_region", "page": number, "bbox": [round(v, 2) for v in area], "coordinate_system": "pdf_points_unrotated", "engine": "ocr_llm", "fact": "disposition_branch"},
                    trace,
                    version,
                    visual=True,
                )
                return {"异常处理": f"异常处理（第{number}页原图局部）{trace}"}
        for number, _image, rect, derotate in pages:
            self.legacy().witness(
                {"kind": "pdf_region", "page": number, "bbox": [round(v, 2) for v in rect * derotate], "coordinate_system": "pdf_points_unrotated", "engine": "ocr_llm", "fact": "disposition_branch"},
                "整页看过，未见不合格分支到异常处置的箭头",
                version,
                visual=True,
            )
        reading.update(complete=True, visual=True, scope=f"已读完原件全部{len(pages)}页的工序与文字，并逐页看过原图（共{len(pages)}页）")
        return {}

    def version(self) -> str:
        return str((self.audit_file.file_metadata or {}).get("file_version_id") or self.audit_file.audit_id)

    def read_disposition(self, image: bytes):
        """(branch, its 0–1000 box on the image) when the image shows a confirmed failed branch into a disposition; None when
        the reading located none, or the arrow it located reads locally as an ordinary step (its condition is no failure);
        _UNCONFIRMED when it located one it could not place, or a failed or unreadable condition whose destination is not
        confirmed as a disposition."""
        from PIL import Image

        found = self.transcribe(
            image,
            "只定位实际工艺路线中清晰可见的不合格分支到异常处置动作的箭头，忽略图例、示例和说明性插图。用0至1000坐标圈住分支条件、箭头全长、目标文字；没有读到就返回null。只读图，不做审核。",
            _LOCATE,
            "异常分支原图定位",
        )
        box = found.get("box")
        if box is None:
            return None
        if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int or not 0 <= v <= 1000 for v in box):
            return _UNCONFIRMED
        left, top, right, bottom = box
        if left >= right or top >= bottom or (right - left) * (bottom - top) > 600_000:
            return _UNCONFIRMED
        img = Image.open(BytesIO(image))
        selected = img.crop((left * img.width // 1000, top * img.height // 1000, (right * img.width + 999) // 1000, (bottom * img.height + 999) // 1000))
        out = BytesIO()
        selected.save(out, format="PNG")
        read = self.transcribe(
            out.getvalue(),
            "只抄此图中同一完整箭头的起点条件和所指目标文字，两个字段逐字写。arrow只在从条件到目标的箭头可完整跟踪时为true。空白或不可读写空字符串。不解释、不判定。",
            _BRANCH_SCHEMA,
            "异常分支局部核读",
        )
        if trace := disposition_text(read):
            return trace, box
        condition = read.get("condition")
        if isinstance(condition, str) and compact(condition) and not _FAILED_BRANCH.fullmatch(compact(condition)):
            return None
        return _UNCONFIRMED
