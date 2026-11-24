"""UAES U10 candidate: material specification, UAES v1.0 revision 3 (MSPEC:1).

A leaf carries only checker.py and resources.py and may not import the UAES implementation. resources.py
declares the UAES controlled-document reader as the carrier; at run time `Audit` extends the declared reader
class with the revision 3 clause method below, reusing its controlled-stamp reading. Verdicts are decided in
code; the image model only reads the stamp, and transcribes a PDF drawing's material field when its text layer
does not carry it.
"""

from __future__ import annotations

import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext
from resources import RESOURCE

DRAWING = ("图纸", "drawing")
ISIR = ("初始样品检验报告", "ISIR", "材料报告", "检验报告")
# The label a drawing or an ISIR material report writes its material (grade and standard) after.
MATERIAL_LABELS = ("材料牌号", "材料", "材质", "Material", "MATERIAL")
MATERIAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["material"],
    "properties": {"material": {"type": ["string", "null"]}},
}
MATERIAL_PROMPT = (
    "这是一份零件图纸右下角标题栏区域的图像。请按 JSON Schema 转写，只转写，不判断。\n"
    "material：“材料 / MATERIAL”栏的内容（牌号与标准号照原样转写，保留空格）；栏位空白写空字符串；图中没有该栏或看不清写 null。"
)


def compact(value: Any) -> str:
    """Comparable text: width-folded, spaces removed, case-folded, trailing colon dropped (as the UAES reader)."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def name_of(item: Any) -> str:
    return str((getattr(item, "file_metadata", None) or {}).get("original_filename") or Path(str(item.file_url)).name)


def file_lines(path: str) -> list[str]:
    """The text of an original by lines: a PDF's text layer, a spreadsheet's cells, a Word document's paragraphs."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import fitz

        with fitz.open(path) as document:
            return "\n".join(page.get_text() for page in document).splitlines()
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        from audit.checker_lib.excel import load_workbook

        book = load_workbook(path)
        try:
            lines = []
            for sheet in book.worksheets:
                for row in sheet.iter_rows():
                    values = [str(cell.value).strip() for cell in row if cell.value is not None and str(cell.value).strip()]
                    # A label cell and the value right of it read as one "label: value" line.
                    lines.extend(f"{values[index]}：{values[index + 1]}" for index in range(len(values) - 1))
                    lines.extend(values)
            return lines
        finally:
            book.close()
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "ignore")
        return [re.sub(r"<[^>]+>", "", paragraph) for paragraph in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)]
    return []


def labelled_material(lines: list[str]) -> list[str]:
    """Values written after a material label ("材料：AlSi12(Fe) DIN EN 1706")."""
    labels = "|".join(re.escape(label) for label in sorted(MATERIAL_LABELS, key=len, reverse=True))
    found = []
    for line in lines:
        match = re.match(rf"\s*(?:{labels})\s*(?:/\s*[A-Za-z ]+)?\s*[:：]\s*(.+)", unicodedata.normalize("NFKC", line))
        if match and match.group(1).strip():
            found.append(match.group(1).strip())
    return list(dict.fromkeys(found))


def kind_matches(kind: Any, terms: tuple[str, ...]) -> bool:
    from audit.kinds import PpapDocumentType
    from audit.presets import customer_presets

    names = [str(kind)]
    resolved = PpapDocumentType.from_string(str(kind))
    if resolved != PpapDocumentType.UNKNOWN:
        names.append(resolved.get_type_name())
    names.extend(preset.title for preset in customer_presets() if preset.customer == "UAES" and preset.source_type == kind)
    return any(compact(term) in compact(name) for term in terms for name in names)


class SpecClauses:
    """MSPEC:1 over the declared UAES controlled-document reader (`READER` is its module)."""

    READER: Any = None

    def related(self, terms: tuple[str, ...]) -> list[Any]:
        group = self.group_context
        if group is None:
            return []
        own = self.audit_file.audit_id
        return [item for kind, items in group.by_doc_type.items() if kind_matches(kind, terms) for item in items if item.audit_id != own]

    def drawing_material(self, item: Any) -> list[str]:
        path = str(item.file_url)
        values = labelled_material(file_lines(path))
        if values or not path.lower().endswith(".pdf"):
            return values
        # A PDF drawing's title block is often lettered outside the text layer: transcribe its material field.
        import fitz

        with fitz.open(path) as document:
            page = document[0]
            area = fitz.Rect(page.rect.x1 * 0.55, page.rect.y1 * 0.7, page.rect.x1, page.rect.y1)
            image = page.get_pixmap(dpi=min(200, int(2400 * 72 / area.width)), clip=area).tobytes("png")
        material = self.transcribe(image, MATERIAL_PROMPT, MATERIAL_SCHEMA, f"图纸{name_of(item)}的材料栏").get("material")
        return [material] if material else []

    def grade(self) -> tuple[str, str]:
        drawings, reports = self.related(DRAWING), self.related(ISIR)
        if not drawings and not reports:
            return "NA", "同批未提交图纸，也未提交ISIR材料报告，“牌号一致”一项不适用（依据：条文“两者均缺时……记‘不适用’”），不记作通过"
        own = file_lines(str(self.audit_file.file_url))
        text = compact("".join(own))
        if not text:
            return "UNABLE", "材料规范原件没有可读的文字层，检查程序未能读出所载牌号"
        basis: list[tuple[str, str]] = []
        unread: list[str] = []
        for item in drawings:
            values = self.drawing_material(item)
            basis.extend((f"图纸{name_of(item)}", value) for value in values)
            if not values:
                unread.append(f"图纸{name_of(item)}")
        for item in reports:
            values = labelled_material(file_lines(str(item.file_url)))
            basis.extend((f"ISIR材料报告{name_of(item)}", value) for value in values)
            if not values:
                unread.append(f"ISIR材料报告{name_of(item)}")
        sources = "、".join(dict.fromkeys(source for source, _value in basis)) or "无"
        differ, partial = [], []
        for source, value in basis:
            tokens = [compact(token) for token in re.split(r"[\s,，;；]+", value) if compact(token)]
            present = [token in text for token in tokens]
            if not any(present):
                differ.append(f"{source}记载“{value}”，材料规范中没有")
            elif not all(present):
                partial.append(f"{source}记载“{value}”，材料规范中只见其中部分（牌号或执行标准号）")
        if differ:
            return "NG", "牌号不一致：" + "；".join(differ) + f"（取值来源：{sources}）"
        if partial or unread:
            notes = [*partial, *(f"{source}的材料牌号未能读出" for source in unread)]
            return "UNABLE", "；".join(notes) + "，牌号与执行标准是否一致需人工核对" + f"（取值来源：{sources}）"
        return "PASS", f"所载牌号与执行标准与{sources}记载一致"

    @rule("材料规范受控章与同批图纸及ISIR材料报告牌号一致")
    def mspec_1(self, ctx: RuleContext):
        try:
            stamp_status, stamp = self.stamp(fails_unidentified=True)
        except GuardStop as stop:
            stamp_status, stamp = stop.status, stop.message
        try:
            grade_status, grade = self.grade()
        except GuardStop as stop:
            grade_status, grade = stop.status, stop.message
        parts = f"受控章：{stamp.rstrip('。')}；牌号：{grade.rstrip('。')}。"
        if "NG" in (stamp_status, grade_status):
            return "NG", "材料规范" + parts
        if "ERR" in (stamp_status, grade_status):
            raise GuardStop(Decision.ERR, "材料规范" + parts)
        if "UNABLE" in (stamp_status, grade_status):
            raise unable("材料规范" + parts)
        if grade_status == "NA":
            return "NA", "材料规范" + parts
        return "PASS", "材料规范" + parts


class Audit(AgentAudit):
    """Runs the declared UAES reader extended with this leaf's clause method (see the module docstring)."""

    CLAUSES: dict[str, type] = {"UaesControlledDocumentAgent": SpecClauses}

    def _implementation(self) -> Any:
        if self._agent is None and self._failure is None:
            reader = super()._implementation()
            if reader is None:
                return None
            base = type(reader)
            close = getattr(reader, "close", None)
            if callable(close):
                close()
            try:
                extended = type(base.__name__ + "R3", (self.CLAUSES[base.__name__], base), {"READER": sys.modules[base.__module__]})
                self._agent = extended(self.audit_file, self._implementation_rule(), group_context=self.group_context)
            except BaseException as exc:
                self._agent, self._failure = None, exc
                raise
        return super()._implementation()


def create_audit(audit_file, rule, group_context=None):
    return Audit(audit_file, rule, group_context, resource=RESOURCE)
