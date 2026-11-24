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


def file_lines(path: str, locators=None) -> list[str]:
    """The text of an original by lines: a PDF's text layer, a spreadsheet's cells, a Word document's paragraphs."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import fitz

        lines = []
        with fitz.open(path) as document:
            for page in document:
                lines.append("<PAGE>")
                if locators is not None:
                    locators.append(None)
                for block in page.get_text("dict", flags=0)["blocks"]:
                    for line in block.get("lines", []):
                        lines.append("".join(span["text"] for span in line["spans"]).strip())
                        if locators is not None:
                            locators.append({"kind": "pdf_region", "page": page.number + 1, "bbox": list(line["bbox"])})
        return lines
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        from audit.checker_lib.excel import load_workbook

        book = load_workbook(path)
        try:
            lines = []
            for sheet in book.worksheets:
                lines.append("<SHEET>")
                if locators is not None:
                    locators.append(None)
                for row in sheet.iter_rows():
                    values = [str(c.value).strip() if c.value is not None else "" for c in row]
                    i = 0
                    if not any(values):
                        lines.append("<ROW>")
                        if locators is not None:
                            locators.append(None)
                    while i < len(values):
                        text = values[i]
                        if text and i + 1 < len(values) and values[i + 1] and (field_kind(text) or text.rstrip(":：") in MATERIAL_LABELS):
                            lines.append(f"{text.rstrip(':：')}：{values[i + 1]}")
                            if locators is not None:
                                locators.append({"kind": "cell", "sheet": sheet.title, "cell": row[i + 1].coordinate})
                            i += 2
                        else:
                            if text:
                                lines.append(text)
                                if locators is not None:
                                    locators.append({"kind": "cell", "sheet": sheet.title, "cell": row[i].coordinate})
                            i += 1
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


# An explicit grade field and its execution-standard field in the same uninterrupted record.
# In particular the real Bosch spec's "Material number" is not its "Material abbreviation".
GRADE_FIELDS = {"材料牌号", "牌号", "grade", "materialgrade", "materialabbreviation"}
STANDARD_FIELDS = {"执行标准", "执行标准号", "标准号", "qualitystandard", "materialstandard", "standardnumber"}


def field_kind(label: str) -> str:
    key = compact(label)
    return "grade" if key in GRADE_FIELDS else "standard" if key in STANDARD_FIELDS else ""


def exact(value: str) -> str:
    return " ".join(value.split())


def grade_records(lines: list[str], witness=None) -> list[tuple[str, str]]:
    records = []
    row = {}
    index = 0
    while index < len(lines):
        text = lines[index].strip()
        index += 1
        label, *tail = re.split("[:：]", text, maxsplit=1)
        kind = field_kind(label)
        if not kind:
            row.clear()
            continue
        if kind in row:
            row.clear()
        value = tail[0].strip() if tail else ""
        if not value and index < len(lines) and not field_kind(re.split("[:：]", lines[index], maxsplit=1)[0]) and not lines[index].strip().startswith("<"):
            value = lines[index].strip()
            index += 1
        if not value:
            row.clear()
            continue
        row[kind] = (exact(value), index - 1)
        if row.keys() >= {"grade", "standard"}:
            records.append((row["grade"][0], row["standard"][0]))
            if witness:
                for key, (raw, at) in row.items():
                    witness(key, raw, at)
            row.clear()
    return list(dict.fromkeys(records))


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
            if not len(document) or not document[0].get_text().strip():
                return []  # Scanned drawing pages are not an OCR source.
            page = document[0]
            area = fitz.Rect(page.rect.x1 * 0.55, page.rect.y1 * 0.7, page.rect.x1, page.rect.y1)
            image = page.get_pixmap(dpi=min(200, int(2400 * 72 / area.width)), clip=area).tobytes("png")
        material = self.transcribe(image, MATERIAL_PROMPT, MATERIAL_SCHEMA, f"图纸{name_of(item)}的材料栏").get("material")
        return [material] if material else []

    def pairs(self, item):
        locations = []
        lines = file_lines(str(item.file_url), locations)
        version = str((item.file_metadata or {}).get("file_version_id") or item.audit_id)

        def witness(key, raw, at):
            if at < len(locations) and locations[at]:
                self.legacy().witness({**locations[at], "fact": key}, raw, version)

        return lines, grade_records(lines, witness)

    def grade(self) -> tuple[str, str]:
        drawings, reports = self.related(DRAWING), self.related(ISIR)
        if not drawings and not reports:
            return "NA", "同批未提交图纸，也未提交ISIR材料报告，“牌号一致”一项不适用（依据：条文“两者均缺时……记‘不适用’”），不记作通过"
        _, own = self.pairs(self.audit_file)
        if len(own) != 1:
            return "UNABLE", "材料规范中未定位到唯一明确成组的材料牌号与执行标准字段，不能用全文词片段代替"
        grade, standard = own[0]
        matched, unread = [], []
        for typ, items in (("图纸", drawings), ("ISIR材料报告", reports)):
            for item in items:
                source = typ + name_of(item)
                lines, pairs = self.pairs(item)
                if len(pairs) == 1:
                    ref_grade, ref_standard = pairs[0]
                    if (grade, standard) != pairs[0]:
                        return "NG", f"材料规范牌号“{grade}”、执行标准“{standard}”，与{source}牌号“{ref_grade}”、执行标准“{ref_standard}”不一致"
                elif not pairs:
                    raw = self.drawing_material(item) if typ == "图纸" else labelled_material(lines)
                    # A single combined material field can establish equality only in its entirety;
                    # do not guess a grade/standard boundary on a different unstructured value.
                    if len(raw) != 1 or exact(raw[0]) != f"{grade} {standard}":
                        unread.append(source)
                        continue
                else:
                    unread.append(source)
                    continue
                matched.append(source)
        if unread:
            return "UNABLE", f"材料规范牌号“{grade}”、执行标准“{standard}”；{'、'.join(unread)}未定位到可唯一对照的牌号与标准字段，不能按全文包含判相同"
        return "PASS", f"材料规范牌号“{grade}”、执行标准“{standard}”与{'、'.join(matched)}的材料字段一致"

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
