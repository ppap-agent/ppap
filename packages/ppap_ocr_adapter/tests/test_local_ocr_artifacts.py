"""PDF fallback is Responses multimodal and returns independent caller workbooks."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, current_thread

import fitz
import openpyxl
from openpyxl import load_workbook
from ppap_core.settings import get_settings
from ppap_ocr_adapter import LocalOcrAdapter
from ppap_ocr_adapter.doc_cache import ocr_service, vision_service


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
        vision_service,
        "call_llm",
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


def test_racing_cache_publish_never_exposes_partial_workbook(tmp_path, monkeypatch):
    """A second producer may write while a third caller reads an earlier hit."""
    source = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(source)
    monkeypatch.setenv("OCR_LOCAL_OSS_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("OCR_LOCAL_SQLITE_DB", str(tmp_path / "cache" / "cache.db"))
    get_settings.cache_clear()
    # DDL initialization is not the concurrency behavior under test.
    ocr_service._ensure_db()
    two_producers = Barrier(2, timeout=10)
    begin_second_write = Event()
    second_write_opened = Event()
    finish_second_write = Event()
    original_save = openpyxl.Workbook.save

    def analyze_page(self, pdf_path):
        two_producers.wait()
        return [{"tables": [{"rows": [[current_thread().name]], "merges": []}], "paragraphs": [], "seals": []}]

    def staggered_save(self, filename):
        if current_thread().name == "ocr-B":
            if not begin_second_write.wait(10):
                raise TimeoutError("First producer did not publish")
            # Simulate a writer exposing a truncated destination before save.
            Path(filename).write_bytes(b"unfinished xlsx")
            second_write_opened.set()
            if not finish_second_write.wait(10):
                raise TimeoutError("Cache reader did not finish")
        return original_save(self, filename)

    def produce(label):
        current_thread().name = label
        return ocr_service.process_local_pdf(str(source))

    monkeypatch.setattr(ocr_service.VisionLayoutService, "analyze_full_pdf", analyze_page)
    monkeypatch.setattr(openpyxl.Workbook, "save", staggered_save)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(produce, "ocr-A")
            second_future = pool.submit(produce, "ocr-B")
            try:
                first = first_future.result(timeout=10)
                before = Path(first["excel_path"]).read_bytes()
                begin_second_write.set()
                assert second_write_opened.wait(10)
                cached = ocr_service.process_local_pdf(str(source))
                assert cached["excel_path"] == first["excel_path"]
                book = load_workbook(cached["excel_path"])
                try:
                    assert book.active["A1"].value == "ocr-A"
                finally:
                    book.close()
            finally:
                begin_second_write.set()
                finish_second_write.set()
            second = second_future.result(timeout=10)
        assert first["excel_path"] != second["excel_path"]
        assert Path(first["excel_path"]).read_bytes() == before
        for result, expected in ((first, "ocr-A"), (second, "ocr-B")):
            book = load_workbook(result["excel_path"])
            try:
                assert book.active["A1"].value == expected
            finally:
                book.close()
    finally:
        begin_second_write.set()
        finish_second_write.set()
        get_settings.cache_clear()
