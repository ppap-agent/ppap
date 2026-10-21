"""Local OCR returns a caller-owned workbook, not the shared provider artifact."""

import base64
from pathlib import Path
from types import SimpleNamespace

import fitz
from openpyxl import load_workbook
from ppap_core.settings import get_settings
from ppap_ocr_adapter import LocalOcrAdapter


def test_local_ocr_returns_independent_readable_workbooks_without_modifying_the_provider_artifact(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(source)
    original_pdf = source.read_bytes()
    monkeypatch.setenv("PADDLE_OCR_URL", "https://ocr.example.test/layout")
    monkeypatch.setenv("OCR_LOCAL_OSS_DIR", str(tmp_path / "ocr"))
    monkeypatch.setenv("OCR_LOCAL_SQLITE_DB", str(tmp_path / "ocr" / "cache.db"))
    get_settings.cache_clear()

    def post(url, *, json, headers, timeout):
        assert url == "https://ocr.example.test/layout"
        assert base64.b64decode(json["file"]) == original_pdf
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "result": {
                    "dataInfo": {"pages": [{"width": 595, "height": 842}]},
                    "layoutParsingResults": [
                        {
                            "prunedResult": {
                                "layout_det_res": {"boxes": [{"label": "table", "coordinate": [0, 0, 595, 842]}]},
                                "table_res_list": [{"pred_html": "<table><tr><td>ocr</td></tr><tr><td>fallback</td></tr></table>"}],
                            }
                        }
                    ],
                }
            },
        )

    # Only Paddle's HTTP response is controlled. The adapter, PDF processing,
    # HTML table parsing, pandas, cache and workbook copy all run unchanged.
    monkeypatch.setattr("requests.post", post)
    copies = []
    try:
        adapter = LocalOcrAdapter()
        first = adapter.ocr_pdf_to_excel(str(source), enable_cache=True)
        assert first.provider == "local_ocr_doc_cache"
        assert first.excel_path is not None
        first_copy = Path(first.excel_path)
        copies.append(first_copy)
        provider_path = Path(first.raw_meta["excel_path"])
        provider_bytes = provider_path.read_bytes()
        assert first_copy != provider_path
        loaded = load_workbook(first_copy)
        try:
            assert list(loaded.active.values) == [("ocr",), ("fallback",)]
            loaded.active["A2"] = "caller edit"
            loaded.save(first_copy)
        finally:
            loaded.close()

        second = adapter.ocr_pdf_to_excel(str(source), enable_cache=True)
        assert second.excel_path is not None
        second_copy = Path(second.excel_path)
        copies.append(second_copy)
        assert second_copy not in {first_copy, provider_path}
        loaded = load_workbook(second_copy)
        try:
            assert list(loaded.active.values) == [("ocr",), ("fallback",)]
        finally:
            loaded.close()
        assert provider_path.read_bytes() == provider_bytes
        assert source.read_bytes() == original_pdf
    finally:
        for copy in copies:
            copy.unlink(missing_ok=True)
        get_settings.cache_clear()
