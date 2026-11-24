"""UAES U17 decision methods for signed v1.0 revision 4."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.v1.reader import PLACEHOLDERS, compact, original_name, unable
from audit.checker_lib.uaes.v1.warrant import (
    _NO_POINTER,
    _NO_VALUE,
    EXPLANATION,
    IDENTITY,
    ORDER,
    PICTURE_SCHEMA,
    SAFETY,
    TOOLING,
    WEIGHT,
    WarrantR3,
    _Reading,
)
from audit.engine.rule_engine import RuleContext


def version_of(item: Any) -> str:
    return str((getattr(item, "file_metadata", None) or {}).get("file_version_id") or item.audit_id)


_IMAGE_PAGE = object()


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

    def transcribe(self, image: bytes, prompt: str, schema: dict[str, Any], subject: str, *, output_correction: str | None = None) -> dict[str, Any]:
        if schema is PICTURE_SCHEMA:
            properties = schema["properties"]
            schema = {**schema, "properties": {**properties, "kind": {"type": "string", **properties["kind"]}}}
            output_correction = "仅输出 JSON 对象，包含 kind 和 detail 两个字段。kind 必须是允许的字符串；无法判断时 kind 写 uncertain，detail 写 null。不加解释或代码块。"
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
                if matching:
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
        if page and item is self.audit_file:
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
