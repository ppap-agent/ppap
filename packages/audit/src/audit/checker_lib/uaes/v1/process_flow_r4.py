"""UAES revision 4 recognition of explicitly equivalent integrated test names."""

import re
from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl.utils.cell import get_column_letter

from audit.checker_lib.uaes.flowchart import placed_drawings
from audit.engine.rule_engine import Decision, RuleContext, Table

from .process_flow import UaesProcessFlowRevision3, compact

_AIRTIGHT_NAMES = frozenset({"气密", "气密性检测", "气检"})


_DISPOSITION = re.compile(r"(?:(?:不合格|不良)(?:品|件))?(?:隔离|返工|返修|报废|偏差(?:处理|放行))|rework|scrap|quarantine", re.I)
_FAILED_BRANCH = re.compile(r"不合格|不良|\bNG\b|\bfail(?:ed)?\b", re.I)
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
    if len(condition) > 80 or len(destination) > 80 or not _FAILED_BRANCH.search(condition) or not _DISPOSITION.fullmatch(compact(destination)):
        return None
    return f"“{condition}” → “{destination}”"


class UaesProcessFlowRevision4(UaesProcessFlowRevision3):
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
        if compact(source_name) == compact(target_name):
            return not (source_object and target_object) or compact(source_object) == compact(target_object)
        # Different names may identify the same type of test without identifying the same object.
        return bool(source_object and target_object and compact(source_object) == compact(target_object))

    def node_annotations(self, ctx: RuleContext):
        path = str(self.audit_file.file_url)
        if Path(path).suffix.lower() not in {".xlsx", ".xlsm"}:
            return
        for sheet in Table(ctx).find_sheets("过程流程图", on_missing=Decision.IGNORE):
            try:
                drawings = placed_drawings(path, sheet.title)
            except (OSError, ValueError, KeyError, BadZipFile, ParseError):
                continue
            for drawing in drawings:
                if drawing.place is None or not drawing.text.strip():
                    continue
                row, col, _, _ = drawing.place
                note = drawing.text.strip()
                disposition = bool(re.fullmatch(r"不(?:合格|良)(?:品|件)(?:处理|处置|隔离|返工|返修|报废)", compact(note)))
                yield f"“{sheet.title}”{get_column_letter(col)}{row}处流程图标注", note, (sheet, row, col), disposition

    def visual_node_evidence(self, ctx: RuleContext, missing: list[str]) -> dict[str, str]:
        """An explicit legible branch can prove presence; an OCR miss never proves absence."""
        if "异常处理" not in missing:
            return {}
        path = Path(str(self.audit_file.file_url))
        if path.suffix.lower() not in {".xlsx", ".xlsm"}:
            return {}
        import fitz
        from documents.excel.visual import render_xlsx_pdf
        from PIL import Image

        source = path.read_bytes()
        # Bound work per rule. A sheet not read here remains outside any absence claim.
        for sheet in list(Table(ctx).find_sheets("过程流程图", on_missing=Decision.IGNORE))[:2]:
            try:
                if not any(d.picture or d.place is None for d in placed_drawings(str(path), sheet.title)):
                    continue
                pdf = render_xlsx_pdf(source, sheet_name=sheet.title, cell_range=None)
                with fitz.open(stream=pdf, filetype="pdf") as document:
                    if len(document) != 1:
                        continue
                    page = document[0]
                    image = page.get_pixmap(dpi=min(180, max(36, int(2400 * 72 / max(page.rect.width, page.rect.height))))).tobytes("png")
            except (OSError, ValueError, KeyError, BadZipFile, ParseError, RuntimeError):
                continue
            found = self.transcribe(
                image,
                "只定位原图中清晰可见的不合格分支到异常处置动作的箭头。用0至1000坐标圈住分支条件、箭头全长、目标文字；没有读到就返回null。只读图，不做审核。",
                _LOCATE,
                "异常分支原图定位",
            )
            box = found.get("box")
            if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int or not 0 <= v <= 1000 for v in box):
                continue
            left, top, right, bottom = box
            if left >= right or top >= bottom or (right - left) * (bottom - top) > 600_000:
                continue
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
            if not (trace := disposition_text(read)):
                continue
            meta = self.audit_file.file_metadata or {}
            self.legacy().witness(
                {"kind": "workbook_render_region", "sheet": sheet.title, "page": 1, "bbox": box, "coordinate_system": "render_image_0_to_1000", "engine": "ocr_llm"},
                trace,
                str(meta.get("file_version_id") or self.audit_file.audit_id),
                visual=True,
            )
            return {"异常处理": f"异常处理（{sheet.title}原图局部）{trace}"}
        return {}
