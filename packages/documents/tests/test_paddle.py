"""Isolated historical PaddleOCR capability and safe local artifact contract."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from documents.ocr import paddle
from documents.ocr.paddle import client, export
from documents.ocr.paddle.__main__ import main
from openpyxl import load_workbook


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path)


def make_pdf(directory: Path, pages: int = 1) -> Path:
    source = directory / "source.pdf"
    with fitz.open() as document:
        for _ in range(pages):
            document.new_page()
        document.save(source)
    return source


def response(pages: int = 1) -> dict:
    return {
        "result": {
            "dataInfo": {"pages": [{"width": 595, "height": 842} for _ in range(pages)]},
            "layoutParsingResults": [
                {"prunedResult": {
                    "table_res_list": [{"pred_html": (
                        '<table><tr><th colspan="2">标题</th><th>金额</th></tr>'
                        '<tr><td rowspan="2">=2+2</td><td>A&amp;B</td><td>10</td></tr>'
                        '<tr><td>第二行</td><td>20</td></tr></table>'
                    )}],
                    "parsing_res_list": [
                        {"block_label": "text", "block_content": "=HYPERLINK(\"bad\")", "block_bbox": [10, 10, 300, 50]},
                        {"block_label": "table", "block_content": "table-duplicate"},
                    ],
                    "seal_res_list": [{"rec_texts": ["签章", "已盖章"]}],
                }} for _ in range(pages)
            ],
        }
    }


def test_client_uses_exact_historical_paddle_parameters(pdf, monkeypatch):
    calls = []

    def post(url, *, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: response())

    monkeypatch.setattr(client.requests, "post", post)
    result = paddle.PaddleStructureService("https://paddle.example/layout-parsing", token="secret").analyze_full_pdf(pdf)
    assert result == response()["result"]
    url, payload, headers, timeout = calls.pop()
    assert not calls
    assert url == "https://paddle.example/layout-parsing"
    assert base64.b64decode(payload["file"]) == pdf.read_bytes()
    assert payload["fileType"] == 0
    assert payload["useSealRecognition"] is True
    assert payload["useTableRecognition"] is True
    assert payload["useChartRecognition"] is False
    assert headers["Authorization"] == "token secret"
    assert timeout == 300


@pytest.mark.parametrize("endpoint", ["", "ftp://example.test", "https://user:password@example.test", "https://example.test/#fragment"])
def test_client_rejects_unsafe_endpoint(endpoint):
    with pytest.raises(ValueError):
        paddle.PaddleStructureService(endpoint)


def test_workbook_preserves_spans_and_never_executes_ocr_formulas(pdf, tmp_path):
    root = tmp_path / "evaluations"
    old_bytes = pdf.read_bytes()
    stats = paddle.evaluate_pdf(pdf, root, response=response())
    directory = stats.output_dir
    assert directory.parent == root.resolve()
    assert directory.name.startswith("paddle-")
    assert (stats.pages, stats.tables, stats.text_blocks, stats.seals) == (1, 1, 1, 1)
    assert pdf.read_bytes() == old_bytes
    assert {item.name for item in directory.iterdir()} == {"tables.xlsx", "evidence.json", "response.json"}
    workbook = load_workbook(directory / "tables.xlsx")
    try:
        assert workbook.sheetnames == ["Table_1", "OCR_Text"]
        table = workbook["Table_1"]
        assert {str(span) for span in table.merged_cells.ranges} == {"A1:B1", "A2:A3"}
        assert table["A2"].value == "=2+2"
        assert table["A2"].data_type == "s"
        assert table["B2"].value == "A&B"
        rows = list(workbook["OCR_Text"].values)
        assert rows == [
            ("Page", "Type", "Content"),
            (1, "text", '=HYPERLINK("bad")'),
            (1, "seal", "签章 已盖章"),
        ]
        assert workbook["OCR_Text"]["C2"].data_type == "s"
    finally:
        workbook.close()
    evidence = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["engine"] == "paddleocr_ppstructure_v3"
    assert len(evidence["source_sha256"]) == 64
    assert evidence["pages"][0]["tables"][0]["source"] == "paddle_html_unlocated"
    assert json.loads((directory / "response.json").read_text(encoding="utf-8")) == response()["result"]


def test_layout_detection_preserves_unreadable_seal_without_ocr_text(pdf, tmp_path):
    result = response()["result"]
    pruned = result["layoutParsingResults"][0]["prunedResult"]
    pruned["table_res_list"] = []
    pruned["parsing_res_list"] = []
    pruned["seal_res_list"] = []
    pruned["layout_det_res"] = {"boxes": [{"label": "seal", "coordinate": [20, 30, 200, 230]}]}
    evidence = paddle.build_evidence(result, page_count=1, pdf_path=pdf)
    assert evidence["pages"][0]["seals"][0]["text_status"] == "unreadable"
    assert evidence["pages"][0]["seals"][0]["bbox"] == [20, 30, 200, 230]
    stats = paddle.evaluate_pdf(pdf, tmp_path / "seal-only", response=result)
    assert (stats.pages, stats.tables, stats.text_blocks, stats.seals) == (1, 0, 0, 1)
    workbook = load_workbook(stats.output_dir / "tables.xlsx")
    try:
        assert list(workbook["OCR_Text"].values) == [("Page", "Type", "Content"), (1, "seal", "检测到印章（内容未识别）")]
    finally:
        workbook.close()


def test_multiple_seals_match_text_by_position_without_array_order(pdf):
    result = response()["result"]
    pruned = result["layoutParsingResults"][0]["prunedResult"]
    pruned["layout_det_res"] = {"boxes": [
        {"label": "seal", "coordinate": [30, 30, 200, 230]},
        {"label": "seal", "coordinate": [250, 250, 450, 470]},
    ]}
    pruned["seal_res_list"] = [
        {"rec_texts": ["右侧"], "bbox": [300, 300, 420, 420]},
        {"rec_texts": ["左侧"], "bbox": [60, 60, 160, 170]},
    ]
    seals = paddle.build_evidence(result, page_count=1, pdf_path=pdf)["pages"][0]["seals"]
    assert [item["recognized_text"] for item in seals] == [["左侧"], ["右侧"]]
    assert [item["recognition_index"] for item in seals] == [1, 0]


def test_page_layout_preserves_labels_coordinates_and_source_indexes():
    result = response()["result"]
    pruned = result["layoutParsingResults"][0]["prunedResult"]
    pruned["layout_det_res"] = {"boxes": [{"label": "paragraph_title", "coordinate": [5, 15, 300, 55]}]}
    pruned["parsing_res_list"] = [
        {"block_label": "footer", "block_content": "底部", "block_bbox": [5, 600, 300, 620]},
        {"block_label": "paragraph_title", "block_content": "标题", "block_bbox": [5, 15, 300, 55]},
        {"block_label": "text", "block_content": "正文", "block_bbox": [5, 100, 300, 140]},
    ]
    page = paddle.build_evidence(result, page_count=1)["pages"][0]
    assert [(item["index"], item["type"]) for item in page["texts"]] == [
        (1, "paragraph_title"), (2, "text"), (0, "footer")
    ]
    assert page["regions"][0]["bbox"] == [5, 15, 300, 55]


def test_multiple_tables_use_spatial_matching_not_array_position():
    result = response()["result"]
    pruned = result["layoutParsingResults"][0]["prunedResult"]
    pruned["layout_det_res"] = {"boxes": [
        {"label": "table", "coordinate": [30, 30, 180, 180]},
        {"label": "table", "coordinate": [280, 280, 490, 490]},
    ]}
    pruned["table_res_list"] = [
        {"pred_html": "<table><tr><td>右</td></tr></table>", "cell_box_list": [[320, 330, 450, 450]]},
        {"pred_html": "<table><tr><td>左</td></tr></table>", "cell_box_list": [[60, 60, 160, 160]]},
    ]
    tables = paddle.build_evidence(result, page_count=1)["pages"][0]["tables"]
    assert [table["rows"][0][0] for table in tables] == ["左", "右"]
    assert [table["recognition_index"] for table in tables] == [1, 0]


def test_native_pdf_table_fallback(tmp_path):
    source = tmp_path / "ruled.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=300)
        for x in (20, 140, 300):
            page.draw_line((x, 20), (x, 180))
        for y in (20, 100, 180):
            page.draw_line((20, y), (300, y))
        for x, y, value in ((30, 70, "ALPHA"), (150, 70, "BETA"), (30, 150, "GAMMA"), (150, 150, "DELTA")):
            page.insert_text((x, y), value, fontsize=12)
        document.save(source)
    result = response()["result"]
    result["dataInfo"]["pages"] = [{"width": 400, "height": 300}]
    pruned = result["layoutParsingResults"][0]["prunedResult"]
    pruned["table_res_list"] = []
    pruned["layout_det_res"] = {"boxes": [{"label": "table", "coordinate": [20, 20, 300, 180]}]}
    table = paddle.build_evidence(result, page_count=1, pdf_path=source)["pages"][0]["tables"][0]
    assert table["source"] == "native_pdf"
    assert table["rows"] == [["ALPHA", "BETA"], ["GAMMA", "DELTA"]]


def test_cross_page_tables_remain_separate_without_guessed_join(tmp_path):
    source = make_pdf(tmp_path, pages=2)
    result = response(pages=2)
    for page in result["result"]["layoutParsingResults"]:
        page["prunedResult"]["table_res_list"] = [{"pred_html": "<table><tr><td>序号</td><td>值</td></tr><tr><td>1</td><td>OK</td></tr></table>"}]
    stats = paddle.evaluate_pdf(source, tmp_path / "cross-page", response=result)
    assert stats.tables == 2
    workbook = load_workbook(stats.output_dir / "tables.xlsx")
    try:
        assert workbook.sheetnames == ["Table_1", "Table_2", "OCR_Text"]
        assert list(workbook["Table_1"].values) == list(workbook["Table_2"].values)
    finally:
        workbook.close()


@pytest.mark.parametrize("markup", [
    "", "<table><tr><td colspan='0'>x</td></tr></table>",
    "<table><tr><td rowspan='2'>x</td></tr></table>",
    "<table><tr><td>x</td></tr>",
    "<table><tr><td>x</td></tr></table><table><tr><td>y</td></tr></table>",
])
def test_invalid_table_html_never_publishes_partial_output(pdf, tmp_path, markup):
    result = response()
    result["result"]["layoutParsingResults"][0]["prunedResult"]["table_res_list"][0]["pred_html"] = markup
    target = tmp_path / "invalid"
    with pytest.raises(paddle.PaddleOCRFormatError):
        paddle.evaluate_pdf(pdf, target, response=result)
    assert not target.exists()


def test_incomplete_page_response_rejects_before_publishing(tmp_path):
    source = make_pdf(tmp_path, pages=2)
    with pytest.raises(paddle.PaddleOCRFormatError, match="incomplete PDF page set"):
        paddle.evaluate_pdf(source, tmp_path / "missing-page", response=response())
    assert not (tmp_path / "missing-page").exists()


def test_cli_replay_requires_no_model_or_network(pdf, tmp_path, monkeypatch, capsys):
    saved = tmp_path / "saved.json"
    saved.write_text(json.dumps(response(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.delenv("PADDLE_OCR_URL", raising=False)
    monkeypatch.setattr(client.requests, "post", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("network forbidden")))
    root = tmp_path / "replays"
    monkeypatch.setattr("sys.argv", ["paddle", "--pdf", str(pdf), "--out-root", str(root), "--response-json", str(saved)])
    main()
    outputs = list(root.glob("paddle-*/tables.xlsx"))
    assert len(outputs) == 1
    output = capsys.readouterr().out
    assert "tables=1" in output
    assert f"output_dir={outputs[0].parent}" in output


def test_existing_output_root_is_reused_and_failed_export_is_cleaned(pdf, tmp_path, monkeypatch):
    root = tmp_path / "results"
    root.mkdir()
    (root / "keep.txt").write_text("original")
    first = paddle.evaluate_pdf(pdf, root, response=response())
    assert first.output_dir.is_dir()
    assert (root / "keep.txt").read_text() == "original"

    def fail_save(*_args, **_kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(export.Workbook, "save", fail_save)
    with pytest.raises(OSError, match="simulated"):
        paddle.evaluate_pdf(pdf, root, response=response())
    assert {item.name for item in root.iterdir()} == {"keep.txt", first.output_dir.name}
    assert list(root.glob(".paddle-*")) == []


def test_parallel_runs_share_output_root_without_collisions(pdf, tmp_path):
    root = tmp_path / "concurrent"
    original = pdf.read_bytes()

    def run(_):
        return paddle.evaluate_pdf(pdf, root, response=response()).output_dir

    with ThreadPoolExecutor(max_workers=8) as pool:
        outputs = list(pool.map(run, range(16)))

    assert len(set(outputs)) == 16
    assert all(path.parent == root.resolve() and path.name.startswith("paddle-") for path in outputs)
    assert all({item.name for item in path.iterdir()} == {"tables.xlsx", "evidence.json", "response.json"} for path in outputs)
    assert list(root.glob(".paddle-*")) == []
    assert pdf.read_bytes() == original


def test_reserved_run_name_collision_retries_without_overwrite(pdf, tmp_path, monkeypatch):
    root = tmp_path / "results"
    existing = root / ("paddle-" + "a" * 32)
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("untouched")
    candidates = iter(["a" * 32, "b" * 32])
    monkeypatch.setattr(export.secrets, "token_hex", lambda _count: next(candidates))

    result = paddle.evaluate_pdf(pdf, root, response=response())
    assert result.output_dir == root / ("paddle-" + "b" * 32)
    assert (existing / "keep.txt").read_text() == "untouched"
    assert (result.output_dir / "tables.xlsx").exists()
    assert list(root.glob(".paddle-*")) == []


def test_publish_failure_does_not_leave_a_reserved_directory(pdf, tmp_path, monkeypatch):
    root = tmp_path / "results"

    def fail_publish(*_args, **_kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(export.os, "rename", fail_publish)
    with pytest.raises(OSError, match="simulated rename failure"):
        paddle.evaluate_pdf(pdf, root, response=response())
    assert not list(root.iterdir())


def test_empty_evidence_is_not_exported(pdf, tmp_path):
    result = response()
    pruned = result["result"]["layoutParsingResults"][0]["prunedResult"]
    pruned["table_res_list"] = []
    pruned["parsing_res_list"] = []
    pruned["seal_res_list"] = []
    with pytest.raises(paddle.PaddleOCRFormatError, match="no recognized evidence"):
        paddle.evaluate_pdf(pdf, tmp_path / "empty", response=result)
    assert not (tmp_path / "empty").exists()
