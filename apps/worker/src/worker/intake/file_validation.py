"""Read-only P3 file checks. No model, rule matching or audit creation."""

from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import fitz
import xlrd
from documents.excel.loader import ExcelLoader, ExcelLoadError, ExcelLoadOptions
from documents.word.legacy import LegacyDocConversionError, convert_legacy_doc_to_docx
from docx import Document
from lxml.etree import XMLSyntaxError, iterparse

_MAX_ARCHIVE_ENTRIES = 4096
_MAX_EXPANDED_BYTES = 128 * 1024 * 1024
_MAX_VISITS = 1_000_000
_MAX_TEXT_BYTES = 16 * 1024 * 1024


class _CheckLimitError(Exception):
    """Checking stopped at a safety budget; file validity is still unknown."""


def _within(value: int, limit: int) -> None:
    if value > limit:
        raise _CheckLimitError


def _check_archive(content: bytes) -> None:
    with ZipFile(BytesIO(content)) as archive:
        entries = archive.infolist()
        _within(len(entries), _MAX_ARCHIVE_ENTRIES)
        _within(sum(entry.file_size for entry in entries), _MAX_EXPANDED_BYTES)
        if len({entry.filename for entry in entries}) != len(entries):
            raise ValueError("duplicate archive entry")
        if any(entry.flag_bits & 1 for entry in entries):
            raise ValueError("encrypted archive entry")
        expanded = 0
        for entry in entries:
            with archive.open(entry) as stream:
                while chunk := stream.read(64 * 1024):
                    expanded += len(chunk)
                    _within(expanded, _MAX_EXPANDED_BYTES)
        visits = text_bytes = 0
        for entry in entries:
            if not entry.filename.lower().endswith((".xml", ".rels")):
                continue
            with archive.open(entry) as stream:
                parser = iterparse(stream, events=("end",), resolve_entities=False, no_network=True)
                for _, element in parser:
                    visits += 1
                    _within(visits, _MAX_VISITS)
                    text_bytes += sum(len(value.encode("utf-8")) for value in (element.text or "", element.tail or "", *element.attrib.values()))
                    _within(text_bytes, _MAX_TEXT_BYTES)
                    element.clear()
                    while element.getprevious() is not None:
                        del element.getparent()[0]
                if parser.root.getroottree().docinfo.doctype:
                    raise ValueError("document type declarations are not supported")


def _excel_has_content(content: bytes, suffix: str) -> bool:
    if suffix == ".xls":
        # Bound the legacy loader's cell materialization before conversion.
        with xlrd.open_workbook(file_contents=content, formatting_info=True, on_demand=True) as source:
            _within(source.nsheets, 256)
            visits = 0
            for index in range(source.nsheets):
                sheet = source.sheet_by_index(index)
                visits += sheet.nrows * sheet.ncols
                _within(visits, _MAX_VISITS)
                source.unload_sheet(index)
    workbook = ExcelLoader.load_workbook(BytesIO(content), ExcelLoadOptions(read_only=True, data_only=False))
    has_content = False
    try:
        _within(len(workbook.worksheets), 256)
        declared_visits = actual_visits = 0
        for sheet in workbook.worksheets:
            declared_visits += (sheet.max_row or 0) * (sheet.max_column or 0)
            _within(declared_visits, _MAX_VISITS)
            if workbook.read_only:
                # Producer dimensions can be absent or smaller than actual cells.
                sheet.reset_dimensions()
            for row in sheet.iter_rows(values_only=True):
                actual_visits += len(row)
                _within(actual_visits, _MAX_VISITS)
                has_content |= any(value is not None and str(value).strip() for value in row)
    finally:
        workbook.close()
    return has_content


def validate_file(filename: str, content: bytes) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".xlsx", ".xls", ".xlsm", ".docx", ".doc", ".pdf"}:
        return {"state": "invalid", "reasons": ["非审核支持格式"]}
    try:
        if suffix == ".doc":
            # Keep the converter's 25 MiB input limit and fail-closed sandbox.
            content = convert_legacy_doc_to_docx(content, filename)
            suffix = ".docx"
        if suffix in {".xlsx", ".xlsm", ".docx"}:
            _check_archive(content)
        if suffix == ".docx":
            document = Document(BytesIO(content))
            has_content = any(node.text and node.text.strip() for node in document.element.body.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        elif suffix == ".pdf":
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.needs_pass or document.is_encrypted or document.is_repaired or not document.page_count:
                    return {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}
                _within(document.page_count, 1000)
                has_content = False
                text_bytes = 0
                for page in document:
                    text = page.get_text()
                    text_bytes += len(text.encode("utf-8"))
                    _within(text_bytes, _MAX_TEXT_BYTES)
                    has_content |= bool(text.strip())
                if document.is_repaired:
                    return {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}
            if not has_content:
                return {"state": "invalid", "reasons": ["PDF 中没有可读取的文字，请上传含文字的文件"]}
        else:
            has_content = _excel_has_content(content, suffix)
    except LegacyDocConversionError as exc:
        if exc.code == "input_too_large":
            return {"state": "error", "reasons": ["旧版 Word 文件超出安全转换限制，请另存为 DOCX 后再试"]}
        if exc.code == "invalid_legacy_doc":
            return {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}
        return {"state": "error", "reasons": ["文件检查暂不可用，请稍后重试"]}
    except _CheckLimitError:
        return {"state": "error", "reasons": ["文件检查超出安全限制，暂时无法完成检查"]}
    except ExcelLoadError as exc:
        if isinstance(exc.__cause__ or exc.__context__, (MemoryError, OSError, RuntimeError)):
            return {"state": "error", "reasons": ["文件检查暂不可用，请稍后重试"]}
        return {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}
    except XMLSyntaxError as exc:
        if any(error.type_name == "ERR_RESOURCE_LIMIT" for error in exc.error_log):
            return {"state": "error", "reasons": ["文件检查超出安全限制，暂时无法完成检查"]}
        return {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}
    except (BadZipFile, ValueError, KeyError, fitz.FileDataError, xlrd.XLRDError):
        return {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}
    except Exception:
        # Internal failures do not prove that the customer's file is invalid.
        return {"state": "error", "reasons": ["文件检查暂不可用，请稍后重试"]}
    return {"state": "valid", "reasons": []} if has_content else {"state": "invalid", "reasons": ["文件中没有可读取的内容"]}
