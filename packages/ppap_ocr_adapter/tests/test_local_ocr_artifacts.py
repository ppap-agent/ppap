"""PDF fallback is Responses multimodal and returns independent caller workbooks."""

from pathlib import Path

import fitz
from openpyxl import load_workbook
from ppap_core.settings import get_settings
from ppap_ocr_adapter import LocalOcrAdapter
from ppap_ocr_adapter.doc_cache import vision_service


def test_multimodal_workbook_cache_and_html_are_independent(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(source)
    monkeypatch.setenv("OCR_MODEL_NAME", "vision-test-model")
    monkeypatch.setenv("OCR_LOCAL_OSS_DIR", str(tmp_path / "vision-cache"))
    monkeypatch.setenv("OCR_LOCAL_SQLITE_DB", str(tmp_path / "vision-cache" / "cache.db"))
    get_settings.cache_clear()
    calls = []

    def respond(*, image_paths, prompt, response_schema, json_mode, retry_count):
        assert image_paths.startswith(b"\x89PNG\r\n")
        assert "table" in prompt.lower()
        assert response_schema["strict"]
        assert json_mode is True and retry_count == 1
        calls.append(1)
        return {
            "tables": [{"rows": [["ocr"], ["fallback"]], "merges": []}],
            "paragraphs": ["<private text>"],
            "seals": ["<signed & stamped>"],
        }

    monkeypatch.setattr(vision_service, "call_llm", respond)
    copies: list[Path] = []
    try:
        adapter = LocalOcrAdapter()
        first = adapter.ocr_pdf_to_excel(str(source), enable_cache=True)
        assert first.provider == "local_ocr_doc_cache"
        assert first.excel_path is not None
        first_copy = Path(first.excel_path)
        copies.append(first_copy)
        assert first.raw_meta["vision_protocol"] == "responses_multimodal_v2"
        provider_path = Path(first.raw_meta["excel_path"])
        original_bytes = provider_path.read_bytes()
        assert first_copy != provider_path
        loaded = load_workbook(first_copy)
        try:
            assert list(loaded.active.values) == [("ocr",), ("fallback",)]
            loaded.active["A2"] = "caller edit"
            loaded.save(first_copy)
        finally:
            loaded.close()
        html = Path(first.raw_meta["html_path"]).read_text()
        assert "&lt;private text&gt;" in html
        assert "&lt;signed &amp; stamped&gt;" in html

        second = adapter.ocr_pdf_to_excel(str(source), enable_cache=True)
        assert second.excel_path is not None
        copies.append(Path(second.excel_path))
        assert copies[1] not in {copies[0], provider_path}
        loaded = load_workbook(second.excel_path)
        try:
            assert list(loaded.active.values) == [("ocr",), ("fallback",)]
        finally:
            loaded.close()
        assert provider_path.read_bytes() == original_bytes
        assert len(calls) == 1  # reading cached provider result makes no second model call
    finally:
        for copy in copies:
            copy.unlink(missing_ok=True)
        get_settings.cache_clear()


def test_multimodal_failure_never_caches_an_excel_artifact(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(source)
    monkeypatch.setenv("OCR_LOCAL_OSS_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("OCR_LOCAL_SQLITE_DB", str(tmp_path / "cache" / "cache.db"))
    get_settings.cache_clear()
    monkeypatch.setattr(vision_service, "call_llm", lambda **kwargs: {"tables": [], "paragraphs": [], "seals": []})
    try:
        artifact = LocalOcrAdapter().ocr_pdf_to_excel(str(source))
        assert artifact.excel_path is None
        assert artifact.raw_meta["error"] == "vision_empty_result"
        assert not list((tmp_path / "cache" / "results").glob("*.xlsx"))
    finally:
        get_settings.cache_clear()


def test_multimodal_merged_product_evidence_survives_workbook_conversion(tmp_path, monkeypatch):
    """A visually merged product column must not become an empty rule-evidence row."""
    source = tmp_path / "merged.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(source)
    monkeypatch.setenv("OCR_LOCAL_OSS_DIR", str(tmp_path / "merge-cache"))
    monkeypatch.setenv("OCR_LOCAL_SQLITE_DB", str(tmp_path / "merge-cache" / "cache.db"))
    get_settings.cache_clear()
    model_table = {
        "rows": [
            ["特性", "产品", "过程", "特殊特性分类"],
            ["", "外观", "", ""],
            ["", "", "", "SC"],
        ],
        "merges": [{"start_row": 1, "start_col": 1, "end_row": 2, "end_col": 1}],
    }
    monkeypatch.setattr(
        vision_service, "call_llm",
        lambda **kwargs: {"tables": [model_table], "paragraphs": [], "seals": []},
    )
    artifact = None
    try:
        artifact = LocalOcrAdapter().ocr_pdf_to_excel(str(source))
        assert artifact.excel_path is not None
        book = load_workbook(artifact.excel_path)
        try:
            sheet = book.active
            assert str(sheet["B2"].value) == "外观"
            assert "B2:B3" in {str(merge) for merge in sheet.merged_cells.ranges}
            assert sheet["D3"].value == "SC"
        finally:
            book.close()
    finally:
        if artifact and artifact.excel_path:
            Path(artifact.excel_path).unlink(missing_ok=True)
        get_settings.cache_clear()
