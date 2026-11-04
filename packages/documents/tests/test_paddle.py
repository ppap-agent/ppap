"""Historical PaddleOCR HTTP capability is explicit, isolated and testable offline."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from documents.ocr import paddle
from documents.ocr.paddle import cli, client
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

    monkeypatch.setattr(client.requests, "post", post)
    result = paddle.PaddleStructureService("http://127.0.0.1:8080/layout-parsing", token="top-secret").analyze_full_pdf(source)
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

    monkeypatch.setattr(client.requests, "post", no_network)
    xlsx = tmp_path / "recognized.xlsx"
    html = tmp_path / "recognized.html"
    stats = paddle.evaluate_pdf(source, xlsx_path=xlsx, html_path=html, response=_result())
    assert stats == paddle.PaddleOCRStats(pages=1, tables=1, text_blocks=1, seals=1)
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
    with pytest.raises(paddle.PaddleOCRFormatError, match="incomplete PDF page set"):
        paddle.evaluate_pdf(source, xlsx_path=output, response=_result())
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
    with pytest.raises(paddle.PaddleOCRFormatError):
        paddle.evaluate_pdf(source, xlsx_path=destination, response=response)
    assert not destination.exists()


def test_empty_or_incorrect_api_response_fails_without_publish(tmp_path, monkeypatch):
    source = _pdf(tmp_path)
    output = tmp_path / "no.xlsx"
    returned = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"result": {"layoutParsingResults": []}})
    monkeypatch.setattr(client.requests, "post", lambda *args, **kwargs: returned)
    with pytest.raises(paddle.PaddleOCRFormatError):
        paddle.evaluate_pdf(source, xlsx_path=output, endpoint="https://paddle.example/layout-parsing")
    assert not output.exists()


def test_text_only_page_creates_excel_without_mislabeling_it_as_a_table(tmp_path):
    source = _pdf(tmp_path)
    response = _result()
    page = response["result"]["layoutParsingResults"][0]["prunedResult"]
    page["table_res_list"] = []
    stats = paddle.evaluate_pdf(source, xlsx_path=tmp_path / "text.xlsx", response=response)
    assert stats.tables == 0
    book = load_workbook(tmp_path / "text.xlsx")
    try:
        assert book.sheetnames == ["OCR_Text"]
    finally:
        book.close()


def test_bad_endpoint_never_sends_credentials():
    for value in ("", "ftp://server/layout-parsing", "https://name:password@server/layout-parsing", "https://server/#fragment"):
        with pytest.raises(ValueError):
            paddle.PaddleStructureService(value)


def test_cli_offline_json_replay(tmp_path, monkeypatch, capsys):
    source = _pdf(tmp_path)
    payload = tmp_path / "result.json"
    payload.write_text(json.dumps(_result(), ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "cli.xlsx"
    monkeypatch.delenv("PADDLE_OCR_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["paddle", "--pdf", str(source), "--response-json", str(payload), "--xlsx", str(output)])
    monkeypatch.setattr(client.requests, "post", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("offline means no network")))
    cli.main()
    assert output.exists()
    assert "tables=1" in capsys.readouterr().out


def test_detected_but_missing_recognized_table_fails_closed(tmp_path):
    source = _pdf(tmp_path)
    response = _result()
    page = response["result"]["layoutParsingResults"][0]["prunedResult"]
    page["table_res_list"] = []
    page["layout_det_res"] = {"boxes": [{"label": "table", "coordinate": [0, 0, 10, 10]}]}
    with pytest.raises(paddle.PaddleOCRFormatError, match="omitted its recognition"):
        paddle.evaluate_pdf(source, xlsx_path=tmp_path / "missing.xlsx", response=response)
    assert not (tmp_path / "missing.xlsx").exists()


def test_detected_unreadable_seal_keeps_region_and_crops(tmp_path):
    from documents.ocr.paddle.evidence import build_evidence

    source = _pdf(tmp_path)
    data = _result()["result"]
    page = data["layoutParsingResults"][0]["prunedResult"]
    page["seal_res_list"] = []
    page["layout_det_res"] = {"boxes": [{"label": "seal", "coordinate": [100, 100, 380, 380]}]}
    evidence = build_evidence(data, page_count=1, pdf_path=source)
    assert evidence["pages"][0]["seals"] == [{
        "page": 1, "detected": True, "bbox": [100.0, 100.0, 380.0, 380.0],
        "text_status": "unreadable", "recognized_text": [], "source": "paddle_layout", "region_index": 0, "recognition_index": None,
    }]
    directory = tmp_path / "seal-crops"
    paddle.evaluate_pdf(source, response=data, xlsx_path=tmp_path / "unreadable.xlsx", html_path=tmp_path / "unreadable.html", seal_crop_dir=directory)
    assert list(directory.glob("*.png"))
    assert "检测到印章" in (tmp_path / "unreadable.html").read_text(encoding="utf-8")


def test_multiple_seals_keep_location_and_spatial_text_assignment():
    from documents.ocr.paddle.evidence import build_evidence

    data = _result()["result"]
    page = data["layoutParsingResults"][0]["prunedResult"]
    page["layout_det_res"] = {"boxes": [
        {"label": "seal", "coordinate": [50, 50, 200, 210]},
        {"label": "seal", "coordinate": [400, 420, 600, 610]},
    ]}
    page["seal_res_list"] = [
        {"rec_texts": ["右章"], "bbox": [450, 460, 560, 550]},
        {"rec_texts": ["左章"], "bbox": [80, 80, 170, 160]},
    ]
    seals = build_evidence(data, page_count=1)["pages"][0]["seals"]
    assert [s["recognized_text"] for s in seals] == [["左章"], ["右章"]]
    assert [s["recognition_index"] for s in seals] == [1, 0]


def test_page_layout_preserves_indices_and_semantic_types():
    from documents.ocr.paddle.evidence import build_evidence

    data = _result()["result"]
    page = data["layoutParsingResults"][0]["prunedResult"]
    page["layout_det_res"] = {"boxes": [
        {"label": "paragraph_title", "coordinate": [40, 10, 500, 80]},
        {"label": "header", "coordinate": [40, 1, 500, 9]},
        {"label": "footer", "coordinate": [40, 400, 500, 500]},
    ]}
    page["parsing_res_list"] = [
        {"block_label": "footer", "block_content": "底部", "block_bbox": [5, 450, 200, 480]},
        {"block_label": "paragraph_title", "block_content": "标题", "block_bbox": [5, 20, 200, 40]},
        {"block_label": "text", "block_content": "正文", "block_bbox": [5, 90, 200, 120]},
    ]
    evidence = build_evidence(data, page_count=1)
    assert [item["type"] for item in evidence["pages"][0]["texts"]] == ["paragraph_title", "text", "footer"]
    assert [item["index"] for item in evidence["pages"][0]["texts"]] == [1, 2, 0]
    assert [item["label"] for item in evidence["pages"][0]["regions"]] == ["paragraph_title", "header", "footer"]


def test_multiple_tables_spatial_matching_ignores_provider_array_order():
    from documents.ocr.paddle.evidence import build_evidence

    data = _result()["result"]
    p = data["layoutParsingResults"][0]["prunedResult"]
    p["layout_det_res"] = {"boxes": [
        {"label": "table", "coordinate": [50, 50, 400, 400]},
        {"label": "table", "coordinate": [600, 600, 1000, 1000]},
    ]}
    p["table_res_list"] = [
        {"pred_html": "<table><tr><td>右表</td></tr></table>", "cell_box_list": [[700, 700, 900, 900]]},
        {"pred_html": "<table><tr><td>左表</td></tr></table>", "cell_box_list": [[100, 100, 300, 300]]},
    ]
    tables = build_evidence(data, page_count=1)["pages"][0]["tables"]
    assert [table["rows"][0][0] for table in tables] == ["左表", "右表"]
    assert [table["recognition_index"] for table in tables] == [1, 0]


def test_native_pdf_table_fallback_on_missing_paddle_html(tmp_path):
    from documents.ocr.paddle.evidence import build_evidence

    path = tmp_path / "ruled.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=300)
        for x in (20, 140, 300):
            page.draw_line((x, 20), (x, 180))
        for y in (20, 100, 180):
            page.draw_line((20, y), (300, y))
        page.insert_text((30, 70), "ALPHA", fontsize=12)
        page.insert_text((150, 70), "BETA", fontsize=12)
        page.insert_text((30, 150), "GAMMA", fontsize=12)
        page.insert_text((150, 150), "DELTA", fontsize=12)
        document.save(path)
    with fitz.open(path) as pdf:
        assert len(pdf[0].find_tables().tables) == 1
    result = _result()["result"]
    result["dataInfo"]["pages"] = [{"width": 400, "height": 300}]
    page = result["layoutParsingResults"][0]["prunedResult"]
    page["table_res_list"] = []
    page["layout_det_res"] = {"boxes": [{"label": "table", "coordinate": [20, 20, 300, 180]}]}
    evidence = build_evidence(result, page_count=1, pdf_path=path)
    assert evidence["pages"][0]["tables"][0]["source"] == "native_pdf"
    assert evidence["pages"][0]["tables"][0]["rows"] == [["ALPHA", "BETA"], ["GAMMA", "DELTA"]]


def test_continuation_is_opt_in_without_overwriting_source_evidence(tmp_path):
    from documents.ocr.paddle.evidence import build_evidence

    source = _pdf(tmp_path, pages=2)
    raw = _result(pages=2)["result"]
    for page in raw["layoutParsingResults"]:
        page["prunedResult"]["table_res_list"] = [{"pred_html": "<table><tr><td>序号</td><td>值</td></tr><tr><td>1</td><td>ok</td></tr></table>"}]
    evidence = build_evidence(raw, page_count=2)
    assert evidence["continuation_candidates"] == [{
        "from_page": 1, "to_page": 2, "from_table": 1, "to_table": 2, "reason": "repeated_header", "proposed": True,
    }]
    paddle.evaluate_pdf(source, xlsx_path=tmp_path / "separate.xlsx", response=raw)
    paddle.evaluate_pdf(source, xlsx_path=tmp_path / "joined.xlsx", response=raw, join_continuations=True)
    original = load_workbook(tmp_path / "separate.xlsx")
    joined = load_workbook(tmp_path / "joined.xlsx")
    try:
        assert len(original.sheetnames) == 3
        assert len(joined.sheetnames) == 2
        assert list(joined["Table_1"].values) == [("序号", "值"), ("1", "ok"), ("1", "ok")]
    finally:
        original.close()
        joined.close()


def test_structured_json_and_raw_replay_do_not_call_network(tmp_path, monkeypatch):
    source = _pdf(tmp_path)
    response = _result()
    input_json = tmp_path / "offline.json"
    input_json.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    out_json = tmp_path / "evidence.json"
    raw_json = tmp_path / "raw.json"
    monkeypatch.setattr(client.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No network in offline replay")))
    monkeypatch.setattr("sys.argv", [
        "paddle", "--pdf", str(source), "--xlsx", str(tmp_path / "result.xlsx"),
        "--response-json", str(input_json), "--evidence-json", str(out_json),
        "--raw-json", str(raw_json),
    ])
    cli.main()
    result = json.loads(out_json.read_text(encoding="utf-8"))
    assert result["engine"] == "paddleocr_ppstructure_v3"
    assert len(result["source_sha256"]) == 64
    assert len(result["pages"]) == 1
    assert json.loads(raw_json.read_text(encoding="utf-8")) == response["result"]


def test_pdf_experiment_never_overwrites_source(tmp_path):
    source = _pdf(tmp_path)
    expected = source.read_bytes()
    with pytest.raises(ValueError):
        paddle.evaluate_pdf(source, xlsx_path=source, response=_result())
    assert source.read_bytes() == expected


def test_non_table_preview_reconstructs_untabled_content(tmp_path):
    source = _pdf(tmp_path)
    data = _result()["result"]
    data["dataInfo"]["pages"] = [{"width": 595, "height": 842}]
    page = data["layoutParsingResults"][0]["prunedResult"]
    page["layout_det_res"] = {"boxes": [
        {"label": "paragraph_title", "coordinate": [20, 10, 500, 50]},
        {"label": "table", "coordinate": [20, 150, 580, 650]},
    ]}
    output = tmp_path / "non-table"
    paddle.evaluate_pdf(source, xlsx_path=tmp_path / "result.xlsx", response=data, non_table_dir=output)
    from PIL import Image

    files = list(output.glob("*.png"))
    assert len(files) == 1
    with Image.open(files[0]) as image:
        assert image.width > 0 and 0 < image.height < 842


def test_remote_url_stream_bound_and_cleanup(tmp_path, monkeypatch):
    from documents.ocr.paddle.client import downloaded_pdf

    source = _pdf(tmp_path)
    pdf_bytes = source.read_bytes()
    visited = []

    class Response:
        status_code = 200
        headers = {"Content-Length": str(len(pdf_bytes))}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield pdf_bytes[:20]
            yield pdf_bytes[20:]

    def get(url, **kwargs):
        visited.append((url, kwargs))
        return Response()

    monkeypatch.setattr(client.requests, "get", get)
    with client.downloaded_pdf("https://example.test/file.pdf") as local:
        assert local.read_bytes() == pdf_bytes
        downloaded_path = local
    assert not downloaded_path.exists()
    assert len(visited) == 1
    assert visited[0][1]["allow_redirects"] is False
    monkeypatch.setattr("documents.ocr.paddle.client.parse_file_size", lambda _: 1)
    with pytest.raises(paddle.PaddleOCRFormatError):
        with client.downloaded_pdf("https://example.test/file.pdf"):
            pass
