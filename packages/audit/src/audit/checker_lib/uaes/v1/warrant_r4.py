"""UAES U17 decision methods for signed v1.0 revision 4."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.v1.reader import PLACEHOLDERS, compact, original_name, unable
from audit.checker_lib.uaes.v1.warrant import (
    _CHECKBOX,
    _MARK,
    _NO_POINTER,
    _NO_VALUE,
    _PICTURE,
    _TEXT,
    CONTACT,
    EXPLANATION,
    IDENTITY,
    ORDER,
    PICTURE_SCHEMA,
    SAFETY,
    SIGNATURE,
    SIGNED_DATE,
    TOOLING,
    WEIGHT,
    WarrantR3,
    _object,
    _Reading,
    _ScanFacts,
    _Shape,
)
from audit.checker_lib.uaes.v1.warrant_pdf_r4 import DocumentPage, WarrantDocument
from audit.engine.rule_engine import Decision, GuardStop, RuleContext


def version_of(item: Any) -> str:
    return str((getattr(item, "file_metadata", None) or {}).get("file_version_id") or item.audit_id)


_IMAGE_PAGE = object()

# An embedded picture of an Excel warrant is read for two separate things it shows: a seal face and handwriting. Asking
# for one category that also said whether it was an actual ink impression made the same electronic seal graphic read as
# imprint, printed logo, digital signature or uncertain from run to run. A seal counts as its impression or its image
# (公章、专用章等的印迹或其图像), as the other UAES picture readings count it; who stamped it is not asked.
EMBEDDED_FACTS = _object(seal=_MARK, seal_text=_TEXT, handwriting=_MARK, detail=_TEXT)
EMBEDDED_READ = (
    "这是零件提交保证书工作表中嵌入的一张图片。只描述图片本身，不判断是否合格，不推断是谁签字盖章、是否在本文件上亲笔签署或当场盖章。\n"
    "seal：图中有单位印章的印面图形为 true，即公章、合同章、专用章、受控章等：有圆形、椭圆或方形边框，沿框或框内排着单位名称、章名等文字印文；"
    "不论它是盖出的印迹、扫描图还是电子章图形。商标或表头标志、打印文字或表格、二维码、没有文字印文的几何符号或图标、手写、勾选框、空白为 false。"
    "图形残缺或模糊到不能确定为 null。\n"
    "seal_text：照录印章上的文字印文；读不出或不是印章写 null。\n"
    "handwriting：图中有手写笔迹（签名或手写字）为 true，没有为 false，看不清为 null。\n"
    "detail：写出图中具体可见的内容。"
)
_EMBEDDED_CORRECTION = "仅输出 JSON 对象，包含 seal、seal_text、handwriting、detail 四个字段；seal 和 handwriting 只能是 true、false 或 null。不加解释或代码块。"


def submission_detail(text: str) -> bool:
    """Positive witnesses only, not an exhaustive definition of substantive Chinese or English prose."""
    if re.search(r"(?:[.][a-z]{2,6}\b|附件名|文件名|示例|上批|仅供参考|其他零件|上次提交|example)", text, re.I) or re.match(r"\s*(?:[=]|(?:详见|参见|见|see\b|refer to\b|附件|附页|附表))", text, re.I):
        return False
    return bool(
        re.search(r"(?:生产节拍|节拍|cycle time)\s*[:=：]?\s*\d+(?:[.]\d+)?\s*(?:秒|分|s\b|min\b)", text, re.I)
        or re.search(r"(?:[一二三四五六七八九十两\d]+模[一二三四五六七八九十两\d]+穴|(?:模腔数|工位数|cavities|stations)\s*[:=：]?\s*[1-9]\d*(?![\d.]))", text, re.I)
        or re.search(r"(?:基准版本|试装批次)\s*[:=：]\s*[A-Za-z0-9-]*\d[A-Za-z0-9-]*", text)
        or re.search(r"检具(?:状态)?\s*[:=：]?\s*已(?:检定|校准|停用|更换)", text)
    )


class WarrantR4(WarrantR3):
    """Reader for signed UAES v1.0 revision 4."""

    def _transcribe(self) -> Any:
        path = Path(str(self.audit_file.file_url))
        if path.suffix.lower() != ".pdf":
            return super()._transcribe()
        with path.open("rb") as source:
            self._digest = hashlib.file_digest(source, "sha256").hexdigest()
        expected = (self.audit_file.file_metadata or {}).get("source_sha256")
        if expected and expected != self._digest:
            raise unable("保证书与冻结原件不符，不能核对。")
        return WarrantDocument(self, path)

    def note(self, fact: str, value: str) -> None:
        document = self.transcription()
        if isinstance(document, WarrantDocument):
            if page := document.selected.get(fact):
                page.cite(fact, value)
        else:
            super().note(fact, value)

    def _scan_note(self, scan, image, box, fact, detail) -> None:
        if not isinstance(scan, DocumentPage):
            return super()._scan_note(scan, image, box, fact, detail)
        sx, sy = scan.page_size[0] / image.width, scan.page_size[1] / image.height
        scan.bounds[fact] = [round(v * k, 3) for v, k in zip(box, (sx, sy, sx, sy), strict=True)]
        scan.cite(fact, detail)

    def signature(self) -> dict[str, Any]:
        document = self.transcription()
        if isinstance(document, WarrantDocument):
            return document.signature()
        if isinstance(document, _ScanFacts) or self._signature_cell is None:
            return super().signature()
        # An embedded picture of handwriting on the signature line is the handwritten signature (product owner, 2026-11-24).
        field = dict(document.get(SIGNATURE[0]) or {})
        row, column = self._signature_cell
        boundary = min((c for r, c in self._sheet.labels if r == row and c > column), default=self._sheet.sheet.ncols + 1)
        values = []
        for shape in self._sheet.shapes:
            top, left, bottom, right = shape.anchor
            if shape.kind in {_CHECKBOX, 0x19} or not (top <= row <= bottom and right > column and left < boundary):
                continue
            mark = self._picture(shape)["handwritten_signature"] if shape.kind == _PICTURE else None
            if mark is True:
                self.witness(self._range(shape), "供方签署栏有嵌入的手写签名图片", visual=True)
            values.append(mark)
        field["handwritten"] = self._any(values)
        return field

    @rule("供方代表手写签字印章与联系信息")
    def rule_3(self, ctx: RuleContext):
        """Revision 3's PSW:3, except that a Title read in full with no job title written in it is an incomplete contact
        detail (product owner, 2026-11-24); a reading of the role that does not match the original stays open."""
        reading = _Reading(self)
        name, label = SIGNATURE
        if (reading.facts.get(name) or {}).get("present") is False:
            self.note(name, "未见供方代表签字栏")
            return "NG", "零件提交保证书中缺少供方代表签字字段。"
        contact, invalid = [], []
        for field, words in CONTACT.items():
            value = reading.field(field, words)
            if value is None:
                continue
            text = unicodedata.normalize("NFKC", value)
            if field == "title" and isinstance(reading.facts, _ScanFacts):
                role = reading.facts[field].get("role_as_written")
                if role is None:
                    invalid.append(f"职务（Title）只写“{value}”，未见职务")
                elif not isinstance(role, str) or not role.strip() or compact(role) not in compact(value):
                    reading.unknown.append(f"Title 原文“{value}”中可辨识的职务")
            if field == "phone" and len(re.sub(r"\D", "", text)) < 7:
                invalid.append(f"电话号码“{value}”格式不合法")
            elif field == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", text.strip()):
                invalid.append(f"电子邮箱“{value}”格式不合法")
            elif field == "signed_date" and not SIGNED_DATE.search(text):
                invalid.append(f"签署日期“{value}”不是日期")
            contact.append(f"{words.split('（')[0]}{value}")
        missing = [failure for failure in reading.failed if failure.startswith("缺少") or failure.endswith("为空")]
        if missing or invalid:
            reading.failed[:] = [failure for failure in reading.failed if failure not in missing]
            reading.failed.insert(0, "联系信息不完整：" + "、".join([*missing, *invalid]))
        if reading.present(name, label):
            try:
                signature = self.signature()
            except GuardStop as stop:
                if stop.decision != Decision.UNABLE:
                    raise
                signature = {"handwritten": None}
            signed = signature.get("handwritten")
            if signed is False:
                if not isinstance(reading.facts, _ScanFacts):
                    self.note(name, signature.get("value") or "签署线未见手写笔迹")
                reading.failed.append(f"{label}没有手写签名")
            elif signed is None:
                reading.unknown.append(f"{label}是否手写")
        try:
            seen = self.seal()
        except GuardStop as stop:
            if stop.decision != Decision.UNABLE:
                raise
            seen = None
        if seen is False:
            reading.failed.append("原件上未找到印章")
        elif seen is None:
            reading.unknown.append("原件上是否有印章")
        if reading.failed and reading.unknown:
            return "NG", "零件提交保证书" + "；".join(reading.failed) + "。另未核实：" + "、".join(reading.unknown) + "。"
        return reading.decided(f"零件提交保证书有供方代表手写签字和印章，签署区{'、'.join(contact)}。")

    def seal(self) -> bool | None:
        document = self.transcription()
        return document.seal() if isinstance(document, WarrantDocument) else super().seal()

    def _picture(self, shape: _Shape) -> dict[str, Any]:
        """One embedded picture, read once per file (at most six): whether it shows a seal face and whether handwriting."""
        cache = self.__dict__.setdefault("_pictures", {})
        if shape not in cache:
            unknown = {"handwritten_signature": None, "ink_seal": None, "detail": ""}
            if shape.picture is None or len(cache) >= 6:
                return unknown
            from PIL import Image

            output = BytesIO()
            try:
                with Image.open(BytesIO(shape.picture)) as image:
                    image.convert("RGB").save(output, format="PNG")
            except (ValueError, OSError):
                cache[shape] = unknown
                return unknown
            local = self.transcribe(output.getvalue(), EMBEDDED_READ, EMBEDDED_FACTS, "零件提交保证书上的图片", output_correction=_EMBEDDED_CORRECTION)
            seal, legend, written, detail = (local.get(key) for key in ("seal", "seal_text", "handwriting", "detail"))
            legend = legend.strip() if isinstance(legend, str) else ""
            cache[shape] = unknown
            if isinstance(detail, str) and detail.strip():
                # A seal face is witnessed by its legend: a seal-like figure whose legend cannot be read is not certified.
                found = (True if legend else None) if seal is True else seal if seal is False else None
                cache[shape] = {
                    "handwritten_signature": written if isinstance(written, bool) else None,
                    "ink_seal": found,
                    "detail": f"印文“{legend}”；{detail.strip()}" if found else detail.strip(),
                }
        return cache[shape]

    def transcribe(self, image: bytes, prompt: str, schema: dict[str, Any], subject: str, *, output_correction: str | None = None) -> dict[str, Any]:
        if schema is PICTURE_SCHEMA:
            output_correction = "仅输出 JSON 对象，包含 kind 和 detail 两个字段。kind 必须是允许的字符串；无法判断时 kind 写 uncertain，detail 写 null。不加解释或代码块。"
        # The picture, signature and seal prompts all have string choices. The strict structured reader
        # requires the primitive type alongside the enum, even though enum alone is valid JSON Schema.
        fields = schema["properties"]
        if any("enum" in value and "type" not in value for value in fields.values()):
            schema = {**schema, "properties": {key: {"type": "string", **value} if "enum" in value else value for key, value in fields.items()}}
        return super().transcribe(image, prompt, schema, subject, output_correction=output_correction)

    def check_explanation(self, audit: _Reading, value: str) -> None:
        """A reference is not an observation. Resolve only bounded, unambiguous locations in frozen originals."""
        text = unicodedata.normalize("NFKC", value.strip())
        if compact(text) in PLACEHOLDERS:
            audit.failed.append(f"说明/解释/备注栏只填占位词“{value}”，没有实质内容")
            return
        if compact(text) in _NO_POINTER or re.fullmatch(r"[■█⬛]+", text):
            audit.failed.append(f"说明/解释/备注栏“{value}”未提供具体内容")
            return
        # Stop at actual, independently readable submission data. A number in a filename or a page is not data.
        if submission_detail(text):
            return
        if not re.search(r"见|参[见照阅]|附[页件表录]|详述于|参考|attachment|appendix|annex|\bsee\b|[.]pdf|[.]xlsx?|!", text, re.I):
            audit.unknown.append("说明栏有文字但未核实具体提交信息：“" + value + "”")
            return

        # A workbook cell, optionally qualified by a named attachment; or a numbered page in a named PDF.
        # No probing of paths taken from the form: exact match against the original names in this frozen batch.
        ref = re.sub(r"^(?:详见|参见|见|see\s+|refer to\s+)", "", text, count=1, flags=re.I).strip()
        refs = list(self.group_context.by_audit_id.values()) if self.group_context is not None else []
        external = re.fullmatch(r"[《\[]([^》\]]+)[》\]]\s*(.*)", ref)
        item = self.audit_file
        if external:
            name, ref = external.groups()
            matching = [f for f in refs if f.audit_id != self.audit_file.audit_id and original_name(f) == name]
            if len(matching) != 1:
                if not matching and (self.group_context is None or self.group_context.unavailable_files):
                    audit.unknown.append(f"同批资料未能核全，无法确认说明栏所引《{name}》是否随附")
                elif matching:
                    audit.unknown.append(f"引用的《{name}》有多个同名原件")
                else:
                    audit.failed.append(f"说明栏引用的《{name}》未随附")
                return
            item = matching[0]
        path = Path(str(item.file_url))
        cell = re.fullmatch(r"'?([^'!]+?)'?\s*!\s*[$]?([A-Za-z]{1,3})[$]?([1-9][0-9]{0,6})", ref)
        page = re.fullmatch(r"(?:第\s*([1-9][0-9]*)\s*页|p(?:age)?[.]?\s*([1-9][0-9]*))", ref, re.I)
        if (not cell or path.suffix.lower() not in {".xls", ".xlsx", ".xlsm"}) and (not page or path.suffix.lower() != ".pdf"):
            audit.unknown.append(f"说明栏的引用“{value}”未核到随附页的具体数据，不能仅凭名称认定")
            return
        expected = (getattr(item, "file_metadata", None) or {}).get("source_sha256")
        if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise unable("随附资料与冻结原件不符，不能核对说明。")
        if cell and item is self.audit_file and getattr(getattr(self, "_sheet", None), "sheet", None) is not None and cell[1] == self._sheet.sheet.name:
            audit.unknown.append("说明栏只指向本表，未核到随附的附页")
            return
        if page and item is self.audit_file and (not isinstance(doc := self.transcription(), WarrantDocument) or int(next(n for n in page.groups() if n)) not in doc.annex):
            audit.unknown.append("所引页未被识别为随附附页，不能用保证书自身说明替代")
            return
        try:
            actual, locator, location = self.annex_location(path, cell, page)
        except (OSError, ValueError, KeyError) as exc:
            audit.unknown.append(f"未能读取说明所引附页（{type(exc).__name__}）")
            return
        if actual is None:
            audit.failed.append("说明栏所指" + location + "不存在")
            return
        if actual is _IMAGE_PAGE:
            audit.unknown.append(f"所引{location}含图像，未核实具体数据")
            return
        fact = next((line.strip() for line in actual.splitlines() if submission_detail(line)), None)
        if fact:
            self.witness(locator, fact, version_of(item))
        elif not actual.strip() or compact(actual) in PLACEHOLDERS:
            self.witness(locator, actual or "空白", version_of(item))
            audit.failed.append(f"说明栏所指{location}为空或只有占位词，没有具体数据")
        else:
            audit.unknown.append(f"说明栏所指{location}未能确认本次提交的具体数据")

    def annex_location(self, path: Path, cell, page):
        """Read only the cited cell/page. Absence in a valid original is distinct from unreadable bytes."""
        if cell:
            from openpyxl.utils import column_index_from_string

            sheet, column, row_s = cell.groups()
            row, col = int(row_s), column_index_from_string(column)
            address = column.upper() + row_s
            locator = {"kind": "xlsx_cell", "sheet": sheet, "cell": address}
            location = f"“{sheet}”!{address}"
            if row > 1048576 or col > 16384:
                return None, locator, location
            if path.suffix.lower() == ".xls":
                import xlrd

                try:
                    book = xlrd.open_workbook(str(path), on_demand=True)
                except xlrd.XLRDError as exc:
                    raise ValueError("invalid workbook") from exc
                with book:
                    if sheet not in book.sheet_names():
                        return None, locator, location
                    ws = book.sheet_by_name(sheet)
                    actual = str(ws.cell_value(row - 1, col - 1)) if row <= ws.nrows and col <= ws.ncols else ""
            else:
                from zipfile import BadZipFile

                from openpyxl import load_workbook

                try:
                    book = load_workbook(path, read_only=True, data_only=False)
                except BadZipFile as exc:
                    raise ValueError("invalid workbook") from exc
                try:
                    if sheet not in book.sheetnames:
                        return None, locator, location
                    value = book[sheet][address].value
                    actual = str(value) if value is not None else ""
                finally:
                    book.close()
            return actual, locator, location
        import fitz

        number = int(next(n for n in page.groups() if n))
        locator, location = {"kind": "pdf_page", "page": number}, f"第{number}页"
        try:
            with fitz.open(path) as doc:
                if number > len(doc):
                    return None, locator, location
                pdf_page = doc[number - 1]
                if not pdf_page.get_text().strip() or pdf_page.get_images():
                    return _IMAGE_PAGE, locator, location
                return pdf_page.get_text(), locator, location
        except fitz.FileDataError as exc:
            raise ValueError("invalid PDF") from exc

    @rule("PSW基础字段两个日期法规唯一勾选净重与说明栏")
    def rule_1(self, ctx: RuleContext):
        reading = _Reading(self)
        exempt = self.first_submission()
        for name, label in IDENTITY.items():
            if name == "additional_engineering_changes" and exempt:
                # 全新开发首次提交且确无附加设变: N/A, /, - in 其他工程更改 is no missing item.
                reading.field(name, label)
                continue
            field = reading.field(name, label, empty=_NO_VALUE)
            if name == "engineering_change_level" and compact(field) in {compact("待定"), "tbd"}:
                reading.unknown.append("工程更改级别只写待定，是否构成有效值尚待澄清")
        reading.one_of(*SAFETY)
        weight = reading.field(*WEIGHT)
        if weight is not None:
            text = unicodedata.normalize("NFKC", weight)
            if not re.search(r"\d", text):
                reading.failed.append(f"零件净重“{weight}”没有数值")
            elif "kg" not in text.casefold():
                reading.failed.append(f"零件净重“{weight}”没有单位kg")
        for name, label in (ORDER, TOOLING):
            if (self.transcription().get(name) or {}).get("present") is True:
                reading.field(name, label)
        if (self.transcription().get(EXPLANATION[0]) or {}).get("present") is not False:
            explanation = reading.field(*EXPLANATION)
            if explanation is not None:
                self.check_explanation(reading, explanation)
        exemption = "；首次提交，其他工程更改按豁免处理" if exempt else ""
        return reading.decided(f"零件身份、工程更改级别及两个日期、零件净重、说明栏均已填写，安全及政府条例恰好勾选一项{exemption}。")
