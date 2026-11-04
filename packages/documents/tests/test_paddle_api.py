"""Historical PaddleOCR HTTP capability is explicit, isolated and testable offline."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from documents.ocr import paddle_api
from openpyxl import load_workbook


def _pdf(tmp_path: Path, pages: int = 1) -> Path:
    path = tmp_path / "source.pdf"
    with fitz.open() as pdf:
        for _ in range(pages):
            pdf.new_page()
        pdf.save(path)
    return path


def _result(*, pages: int = 1) -> dict:
    return {
        "result": {
            "dataInfo": {"pages": [{"width": 1654, "height": 2339}] * pages},
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "table_res_list": [
                            {
                                "pred_html": '<html><body><table><tr><th colspan="2">标题</th><th>金额</th></tr>'
                                '<tr><td rowspan="2">=2+2</td><td>A&amp;B</td><td>10</td></tr>'
                                '<tr><td>第二行</td><td>20</td></tr></table></body></html>'
                            }
                        ],
                        "parsing_res_list": [
                            {"block_label": "text", "block_content": "<private & source>"},
                            {"block_label": "table", "block_content": "ignore table duplicate"},
                        ],
                        "seal_res_list": [{"rec_texts": ["签章", "已盖章"]}],
                    }
                }
                for _ in range(pages)
            ],
        }
    }


def test_client_sends_historical_ppstructure_contract_with_token(tmp_path, monkeypatch):
    source = _pdf(tmp_path)
    sent = []

    def post(url, *, json, headers, timeout):
        sent.append((url, json, headers, timeout))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: _result())

    monkeypatch.setattr(paddle_api.requests, "post", post)
    result = paddle_api.PaddleStructureService("http://127.0.0.1:8080/layout-parsing", token="top-secret").analyze_full_pdf(source)
    assert result == _result()["result"]
    assert len(sent) == 1
    url, payload, headers, timeout = sent[0]
    assert url == "http://127.0.0.1:8080/layout-parsing"
    assert base64.b64decode(payload["file"]) == source.read_bytes()
    assert payload["fileType"] == 0
    assert payload["useTableRecognition"] is True
    assert payload["useSealRecognition"] is True
    assert headers["Authorization"] == "token top-secret"
    assert timeout == 300


def test_offline_export_keeps_spans_unicode_text_and_prevents_excel_formulas(tmp_path, monkeypatch):
    source = _pdf(tmp_path)

    def no_network(*args, **kwargs):
        raise AssertionError("Offline replay must not call network")

    monkeypatch.setattr(paddle_api.requests, "post", no_network)
    xlsx = tmp_path / "recognized.xlsx"
    html = tmp_path / "recognized.html"
    stats = paddle_api.convert_pdf_with_paddle(source, xlsx_path=xlsx, html_path=html, response=_result())
    assert stats == paddle_api.PaddleOCRStats(pages=1, tables=1, text_blocks=1, seals=1)
    wb = load_workbook(xlsx)
    try:
        assert wb.sheetnames == ["Table_1", "OCR_Text"]
        sheet = wb["Table_1"]
        assert {str(merge) for merge in sheet.merged_cells.ranges} == {"A1:B1", "A2:A3"}
        assert sheet["C1"].value == "金额"
        assert sheet["B2"].value == "A&B"
        assert sheet["B3"].value == "第二行"
        assert sheet["A2"].value == "=2+2"
        assert sheet["A2"].data_type == "s"
        assert list(wb["OCR_Text"].values) == [
            ("Page", "Type", "Content"),
            (1, "text", "<private & source>"),
            (1, "seal", "签章 已盖章"),
        ]
    finally:
        wb.close()
    assert "&lt;private &amp; source&gt;" in html.read_text(encoding="utf-8")
    assert "ignore table duplicate" not in html.read_text(encoding="utf-8")


def test_pdf_page_truncation_fails_closed_before_writing(tmp_path):
    source = _pdf(tmp_path, pages=2)
    output = tmp_path / "unpublished.xlsx"
    with pytest.raises(paddle_api.PaddleOCRFormatError, match="incomplete PDF page set"):
        paddle_api.convert_pdf_with_paddle(source, xlsx_path=output, response=_result())
    assert not output.exists()


@pytest.mark.parametrize(
    "markup",
    [
        "",
        "<table><tr><td colspan='0'>x</td></tr></table>",
        "<table><tr><td rowspan='2'>x</td></tr></table>",
        "<table><tr><td>x</td></tr>",
        "<table><tr><td>x</td></tr></table><table><tr><td>y</td></tr></table>",
    ],
)
def test_invalid_table_html_never_publishes_workbook(tmp_path, markup):
    source = _pdf(tmp_path)
    response = _result()
    response["result"]["layoutParsingResults"][0]["prunedResult"]["table_res_list"][0]["pred_html"] = markup
    destination = tmp_path / "bad.xlsx"
    with pytest.raises(paddle_api.PaddleOCRFormatError):
        paddle_api.convert_pdf_with_paddle(source, xlsx_path=destination, response=response)
    assert not destination.exists()


def test_empty_or_incorrect_api_response_fails_without_publish(tmp_path, monkeypatch):
    source = _pdf(tmp_path)
    output = tmp_path / "no.xlsx"
    returned = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"result": {"layoutParsingResults": []}})
    monkeypatch.setattr(paddle_api.requests, "post", lambda *args, **kwargs: returned)
    with pytest.raises(paddle_api.PaddleOCRFormatError):
        paddle_api.convert_pdf_with_paddle(source, xlsx_path=output, endpoint="https://paddle.example/layout-parsing")
    assert not output.exists()


def test_text_only_page_creates_excel_without_mislabeling_it_as_a_table(tmp_path):
    source = _pdf(tmp_path)
    response = _result()
    page = response["result"]["layoutParsingResults"][0]["prunedResult"]
    page["table_res_list"] = []
    stats = paddle_api.convert_pdf_with_paddle(source, xlsx_path=tmp_path / "text.xlsx", response=response)
    assert stats.tables == 0
    book = load_workbook(tmp_path / "text.xlsx")
    try:
        assert book.sheetnames == ["OCR_Text"]
    finally:
        book.close()


def test_bad_endpoint_never_sends_credentials():
    for value in ("", "ftp://server/layout-parsing", "https://name:password@server/layout-parsing", "https://server/#fragment"):
        with pytest.raises(ValueError):
            paddle_api.PaddleStructureService(value)


def test_cli_offline_json_replay(tmp_path, monkeypatch, capsys):
    source = _pdf(tmp_path)
    payload = tmp_path / "result.json"
    payload.write_text(json.dumps(_result(), ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "cli.xlsx"
    monkeypatch.delenv("PADDLE_OCR_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["paddle_api", "--pdf", str(source), "--response-json", str(payload), "--xlsx", str(output)])
    monkeypatch.setattr(paddle_api.requests, "post", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("offline means no network")))
    paddle_api.main()
    assert output.exists()
    assert "tables=1" in capsys.readouterr().out


def test_detected_but_missing_recognized_table_fails_closed(tmp_path):
    source = _pdf(tmp_path)
    response = _result()
    page = response["result"]["layoutParsingResults"][0]["prunedResult"]
    page["table_res_list"] = []
    page["layout_det_res"] = {"boxes": [{"label": "table", "coordinate": [0, 0, 10, 10]}]}
    with pytest.raises(paddle_api.PaddleOCRFormatError, match="omitted its recognition"):
        paddle_api.convert_pdf_with_paddle(source, xlsx_path=tmp_path / "missing.xlsx", response=response)
    assert not (tmp_path / "missing.xlsx").exists()
