"""P3 checks real file contents without matching rules or starting an audit."""

from io import BytesIO
from pathlib import Path
from struct import pack_into
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import fitz
import openpyxl
import pytest
from docx import Document
from openpyxl import Workbook
from ppap_application import validate_file


def test_readable_excel_with_content_is_valid() -> None:
    workbook = Workbook()
    workbook.active["A1"] = "零件号 P-204"
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    assert validate_file("控制计划.xlsx", content.getvalue()) == {"state": "valid", "reasons": []}


def test_invalid_excel_content_returns_a_safe_fixed_reason() -> None:
    assert validate_file("客户私密文件.xlsx", b"not-an-excel-workbook private-detail") == {
        "state": "invalid",
        "reasons": ["文件加密或异常，无法识别"],
    }


@pytest.mark.parametrize("value", [None, "   "])
def test_excel_without_readable_cell_content_is_invalid(value) -> None:
    workbook = Workbook()
    workbook.active["A1"] = value
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    assert validate_file("空表.xlsx", content.getvalue()) == {"state": "invalid", "reasons": ["文件中没有可读取的内容"]}


@pytest.mark.parametrize("value", [0, False, "=1+1"])
def test_excel_zero_false_and_uncached_formulas_are_content(value) -> None:
    workbook = Workbook()
    workbook.active["A1"] = value
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    assert validate_file("数据.xlsm", content.getvalue()) == {"state": "valid", "reasons": []}


@pytest.mark.parametrize("paragraph, cell", [("可读段落", ""), ("", "表格资料"), ("", "")])
def test_word_checks_real_paragraph_and_table_content(paragraph, cell) -> None:
    document = Document()
    document.add_paragraph(paragraph)
    document.add_table(rows=1, cols=1).cell(0, 0).text = cell
    content = BytesIO()
    document.save(content)

    expected = {"state": "valid", "reasons": []} if paragraph or cell else {"state": "invalid", "reasons": ["文件中没有可读取的内容"]}
    assert validate_file("资料.docx", content.getvalue()) == expected


@pytest.mark.parametrize("page_texts", [["可读内容"], ["", "Readable text"], ["", ""]])
def test_pdf_checks_every_page_for_real_text(page_texts) -> None:
    with fitz.open() as document:
        for text in page_texts:
            page = document.new_page()
            if text:
                page.insert_text((40, 40), text)
        content = document.tobytes()

    expected = {"state": "valid", "reasons": []} if any(page_texts) else {"state": "invalid", "reasons": ["PDF 中没有可读取的文字，请上传含文字的文件"]}
    assert validate_file("资料.pdf", content) == expected


def test_encrypted_pdf_is_invalid_without_password_attempts() -> None:
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "Secret text")
        content = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="password")

    assert validate_file("加密.pdf", content) == {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}


@pytest.mark.parametrize("filename", ["损坏.pdf", "损坏.docx"])
def test_corrupt_documents_return_a_safe_reason(filename) -> None:
    assert validate_file(filename, b"private broken content") == {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}


def test_unsupported_filename_does_not_pass_even_with_readable_excel_content() -> None:
    workbook = Workbook()
    workbook.active["A1"] = "data"
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    assert validate_file("不支持.csv", content.getvalue()) == {"state": "invalid", "reasons": ["非审核支持格式"]}


def test_zip_entry_count_limit_is_an_error_not_a_bad_file() -> None:
    content = BytesIO()
    with ZipFile(content, "w") as archive:
        for number in range(4097):
            archive.writestr(f"entry-{number}", b"")

    assert validate_file("过多条目.docx", content.getvalue()) == {"state": "error", "reasons": ["文件检查超出安全限制，暂时无法完成检查"]}


def test_zip_declared_expansion_limit_is_an_error() -> None:
    content = BytesIO()
    with ZipFile(content, "w") as archive:
        archive.writestr("document.xml", b"small")
    data = bytearray(content.getvalue())
    directory = data.index(b"PK\x01\x02")
    pack_into("<I", data, directory + 24, 128 * 1024 * 1024 + 1)

    assert validate_file("过大展开.docx", bytes(data)) == {"state": "error", "reasons": ["文件检查超出安全限制，暂时无法完成检查"]}


def test_zip_duplicates_are_invalid_even_when_the_last_copy_is_readable() -> None:
    document = Document()
    document.add_paragraph("data")
    content = BytesIO()
    document.save(content)
    data = BytesIO(content.getvalue())
    with ZipFile(data, "a") as archive:
        archive.writestr("word/document.xml", archive.read("word/document.xml"))

    assert validate_file("重复内容.docx", data.getvalue()) == {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}


def test_crc_on_unreferenced_archive_entries_is_checked() -> None:
    document = Document()
    document.add_paragraph("data")
    content = BytesIO()
    document.save(content)
    data = BytesIO(content.getvalue())
    with ZipFile(data, "a") as archive:
        archive.writestr("unreferenced.bin", b"private payload", compress_type=ZIP_STORED)
        entry = archive.getinfo("unreferenced.bin")
    damaged = bytearray(data.getvalue())
    offset = entry.header_offset + 30 + len(entry.filename.encode()) + len(entry.extra)
    damaged[offset] ^= 1

    assert validate_file("损坏附件.docx", bytes(damaged)) == {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}


def test_sparse_excel_cell_visit_limit_is_an_error() -> None:
    workbook = Workbook()
    workbook.active["A1000001"] = "data"
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    assert validate_file("稀疏工作簿.xlsx", content.getvalue()) == {"state": "error", "reasons": ["文件检查超出安全限制，暂时无法完成检查"]}


def test_legacy_doc_keeps_the_existing_25_mib_converter_limit() -> None:
    assert validate_file("大型旧文档.doc", b"0" * (25 * 1024 * 1024 + 1)) == {
        "state": "error",
        "reasons": ["旧版 Word 文件超出安全转换限制，请另存为 DOCX 后再试"],
    }


def test_real_legacy_doc_without_converter_is_unknown_not_invalid(monkeypatch) -> None:
    monkeypatch.setenv("LEGACY_DOC_CONVERTER_EXECUTABLE", "/not-installed/ppap-test-libreoffice")
    fixture = Path(__file__).resolve().parents[3] / "tests/corpus/fixtures/legacy_doc/psw.doc"

    assert validate_file("真实旧文档.doc", fixture.read_bytes()) == {
        "state": "error",
        "reasons": ["文件检查暂不可用，请稍后重试"],
    }


def test_fake_legacy_doc_is_invalid_before_conversion() -> None:
    assert validate_file("伪装.doc", b"not a binary Word document") == {
        "state": "invalid",
        "reasons": ["文件加密或异常，无法识别"],
    }


def test_pdf_page_limit_is_unknown_not_invalid() -> None:
    with fitz.open() as document:
        for _ in range(1001):
            document.new_page()
        content = document.tobytes()

    assert validate_file("过多页面.pdf", content) == {"state": "error", "reasons": ["文件检查超出安全限制，暂时无法完成检查"]}


def test_pdf_with_damaged_xref_is_not_valid_just_because_text_survives() -> None:
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "Readable text")
        content = document.tobytes()
    damaged = content.rsplit(b"startxref\n", 1)[0] + b"startxref\n0\n%%EOF\n"
    with fitz.open(stream=damaged, filetype="pdf") as recovered:
        assert recovered.is_repaired
        assert recovered[0].get_text().strip()

    assert validate_file("局部损坏.pdf", damaged) == {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}


def test_unknown_parser_runtime_failure_is_error_not_invalid(monkeypatch) -> None:
    def unavailable(*args, **kwargs):
        raise RuntimeError("private environment detail")

    monkeypatch.setattr(fitz, "open", unavailable)
    assert validate_file("资料.pdf", b"content") == {"state": "error", "reasons": ["文件检查暂不可用，请稍后重试"]}


def test_xml_node_access_limit_is_an_error_before_office_parsing() -> None:
    document = Document()
    document.add_paragraph("readable")
    content = BytesIO()
    document.save(content)
    data = BytesIO(content.getvalue())
    with ZipFile(data, "a") as archive:
        archive.writestr("customXml/extra.xml", b"<root>" + b"<node/>" * 1_000_001 + b"</root>", compress_type=ZIP_DEFLATED)

    assert validate_file("过多节点.docx", data.getvalue()) == {"state": "error", "reasons": ["文件检查超出安全限制，暂时无法完成检查"]}


def test_understated_excel_dimensions_do_not_hide_real_content() -> None:
    workbook = Workbook()
    workbook.active["B2"] = "readable"
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    data = BytesIO()
    with ZipFile(content) as source, ZipFile(data, "w", compression=ZIP_DEFLATED) as output:
        for entry in source.infolist():
            body = source.read(entry)
            if entry.filename == "xl/worksheets/sheet1.xml":
                body = body.replace(b'<dimension ref="B2:B2"/>', b'<dimension ref="A1:A1"/>')
            output.writestr(entry, body)

    assert validate_file("错误维度.xlsx", data.getvalue()) == {"state": "valid", "reasons": []}


def test_excel_loader_wrapped_runtime_failure_is_error_not_invalid(monkeypatch) -> None:
    workbook = Workbook()
    workbook.active["A1"] = "readable"
    content = BytesIO()
    workbook.save(content)
    workbook.close()

    def unavailable(*args, **kwargs):
        raise RuntimeError("private environment detail")

    monkeypatch.setattr(openpyxl, "load_workbook", unavailable)
    assert validate_file("资料.xlsx", content.getvalue()) == {"state": "error", "reasons": ["文件检查暂不可用，请稍后重试"]}


def test_real_legacy_excel_is_checked_through_the_shared_loader() -> None:
    fixture = Path(__file__).resolve().parents[3] / "assets/v2.0plus/fallback/5-模具验收报告-上海亚大.xls"

    assert validate_file("真实旧工作簿.xls", fixture.read_bytes()) == {"state": "valid", "reasons": []}


def test_zip_password_flag_is_invalid_not_a_service_failure() -> None:
    content = BytesIO()
    with ZipFile(content, "w") as archive:
        archive.writestr("document.xml", b"private content")
    data = bytearray(content.getvalue())
    pack_into("<H", data, 6, 1)
    pack_into("<H", data, data.index(b"PK\x01\x02") + 8, 1)

    assert validate_file("加密包.docx", bytes(data)) == {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}
