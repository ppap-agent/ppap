"""Isolated historical PaddleOCR capability and safe local artifact contract."""

from __future__ import annotations

import base64
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import fitz
import httpx
import pytest
from documents.ocr import paddle
from documents.ocr.paddle import export
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
                {
                    "prunedResult": {
                        "table_res_list": [
                            {
                                "pred_html": (
                                    '<table><tr><th colspan="2">标题</th><th>金额</th></tr><tr><td rowspan="2">=2+2</td><td>A&amp;B</td><td>10</td></tr><tr><td>第二行</td><td>20</td></tr></table>'
                                )
                            }
                        ],
                        "parsing_res_list": [
                            {"block_label": "text", "block_content": '=HYPERLINK("bad")', "block_bbox": [10, 10, 300, 50]},
                            {"block_label": "table", "block_content": "table-duplicate"},
                        ],
                        "seal_res_list": [{"rec_texts": ["签章", "已盖章"]}],
                    }
                }
                for _ in range(pages)
            ],
        }
    }


def test_client_uses_exact_historical_paddle_parameters(pdf, monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((origin + self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))), self.headers))
            body = json.dumps(response()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: pytest.fail("Paddle HTTP must not start a process"))
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        origin = f"http://127.0.0.1:{server.server_port}"
        endpoint = origin + "/layout-parsing"
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        service = paddle.PaddleStructureService(endpoint, token="secret")
        try:
            result = service.analyze_full_pdf(pdf)
        finally:
            server.shutdown()
            thread.join(timeout=1)
    assert result == response()["result"]
    url, payload, headers = calls.pop()
    assert not calls
    assert url == endpoint
    assert base64.b64decode(payload["file"]) == pdf.read_bytes()
    assert payload["fileType"] == 0
    assert payload["useSealRecognition"] is True
    assert payload["useTableRecognition"] is True
    assert payload["useChartRecognition"] is False
    assert headers["Authorization"] == "token secret"
    assert service.timeout == 20
    assert not thread.is_alive()


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
    assert stats.status == "complete"
    assert not (stats.output_dir / "tables.xlsx").exists()
    saved = json.loads((stats.output_dir / "evidence.json").read_text())
    assert saved["pages"][0]["seals"][0]["text_status"] == "unreadable"


def test_multiple_seals_match_text_by_position_without_array_order(pdf):
    result = response()["result"]
    pruned = result["layoutParsingResults"][0]["prunedResult"]
    pruned["layout_det_res"] = {
        "boxes": [
            {"label": "seal", "coordinate": [30, 30, 200, 230]},
            {"label": "seal", "coordinate": [250, 250, 450, 470]},
        ]
    }
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
    assert [(item["index"], item["type"]) for item in page["texts"]] == [(1, "paragraph_title"), (2, "text"), (0, "footer")]
    assert page["regions"][0]["bbox"] == [5, 15, 300, 55]


def test_multiple_tables_use_spatial_matching_not_array_position():
    result = response()["result"]
    pruned = result["layoutParsingResults"][0]["prunedResult"]
    pruned["layout_det_res"] = {
        "boxes": [
            {"label": "table", "coordinate": [30, 30, 180, 180]},
            {"label": "table", "coordinate": [280, 280, 490, 490]},
        ]
    }
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
def test_invalid_table_html_preserves_independent_evidence(pdf, tmp_path, markup):
    result = response()
    result["result"]["layoutParsingResults"][0]["prunedResult"]["table_res_list"][0]["pred_html"] = markup
    target = tmp_path / "invalid"
    stats = paddle.evaluate_pdf(pdf, target, response=result)
    assert stats.status == "partial"
    assert stats.tables == 0
    assert (stats.output_dir / "response.json").exists()
    saved = json.loads((stats.output_dir / "evidence.json").read_text())
    assert saved["diagnostics"]
    assert saved["pages"][0]["seals"]
    assert not (stats.output_dir / "tables.xlsx").exists()


def test_incomplete_page_response_rejects_before_publishing(tmp_path):
    source = make_pdf(tmp_path, pages=2)
    stats = paddle.evaluate_pdf(source, tmp_path / "missing-page", response=response())
    assert stats.status == "error"
    saved = json.loads((stats.output_dir / "evidence.json").read_text())
    assert saved["pages"] == []
    assert saved["diagnostics"][0]["code"] == "paddle.response.invalid"
    assert not (stats.output_dir / "tables.xlsx").exists()


def test_cli_replay_requires_no_model_or_network(pdf, tmp_path, monkeypatch, capsys):
    saved = tmp_path / "saved.json"
    saved.write_text(json.dumps(response(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.delenv("PPAP_PADDLE_URL", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("network forbidden")))
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
    stats = paddle.evaluate_pdf(pdf, tmp_path / "empty", response=result)
    assert stats.status == "error"
    assert not (stats.output_dir / "tables.xlsx").exists()
    assert (stats.output_dir / "evidence.json").exists()


@pytest.mark.parametrize(
    "markup",
    [
        "<table><tr><td>A<td>B<tr><td>C<td>D</table>",
        "<table><tbody><tr><td>A<td>B</tbody><tbody><tr><td>C<td>D</tbody></table>",
    ],
)
def test_standard_omitted_cell_and_row_end_tags_preserve_grid(markup):
    from documents.ocr.paddle.tables import parse_table

    assert parse_table(markup) == ([["A", "B"], ["C", "D"]], [])


def test_bad_table_does_not_discard_other_table_or_detected_seal():
    from documents.ocr.paddle.tables import recognize_tables

    result = response()["result"]
    result["layoutParsingResults"][0]["prunedResult"]["table_res_list"] = [
        {"pred_html": "<table><tr><td>A</td></tr></table>"},
        {"pred_html": '<table><tr><td colspan="2"</table>'},
    ]
    diagnostics = []
    tables = recognize_tables(None, result, [{"regions": []}], diagnostics=diagnostics)
    assert [table["rows"] for table in tables] == [[["A"]]]
    assert len(diagnostics) == 1
    assert diagnostics[0]["recognition_index"] == 1
    assert diagnostics[0]["page"] == 1


def test_partial_export_saves_known_facts_and_bad_table_diagnostic(pdf, tmp_path):
    result = response()
    result["result"]["layoutParsingResults"][0]["prunedResult"]["table_res_list"].append({"pred_html": "<table><tr><td unfinished"})
    stats = paddle.evaluate_pdf(pdf, tmp_path / "partial", response=result)
    assert stats.status == "partial"
    assert stats.tables == 1
    assert set(stats.artifacts) == {"response.json", "evidence.json", "tables.xlsx"}
    evidence = json.loads((stats.output_dir / "evidence.json").read_text())
    assert evidence["pages"][0]["seals"][0]["recognized_text"] == ["签章", "已盖章"]
    assert evidence["diagnostics"][0]["recognition_index"] == 1


def test_text_only_response_exports_evidence_without_fake_workbook(pdf, tmp_path):
    result = response()
    result["result"]["layoutParsingResults"][0]["prunedResult"]["table_res_list"] = []
    stats = paddle.evaluate_pdf(pdf, tmp_path / "text-only", response=result)
    assert stats.tables == 0
    assert set(stats.artifacts) == {"response.json", "evidence.json"}
    assert not (stats.output_dir / "tables.xlsx").exists()
    assert (stats.output_dir / "response.json").is_file()


def test_failed_recognition_keeps_original_index_for_following_valid_table():
    from documents.ocr.paddle.tables import recognize_tables

    result = response()["result"]
    result["layoutParsingResults"][0]["prunedResult"]["table_res_list"] = [None, {"pred_html": "<table><tr><td>Good</td></tr></table>"}]
    diagnostics = []
    tables = recognize_tables(None, result, [{"regions": []}], diagnostics=diagnostics, source_pages=[7])
    assert tables[0]["recognition_index"] == 1
    assert tables[0]["page"] == 7
    assert diagnostics[0]["recognition_index"] == 0


def test_ambiguous_table_geometry_is_not_assigned_by_iteration_order():
    from documents.ocr.paddle.tables import recognize_tables

    result = response()["result"]
    result["layoutParsingResults"][0]["prunedResult"]["table_res_list"] = [{"pred_html": "<table><tr><td>Unknown region</td></tr></table>", "cell_box_list": [[20, 20, 80, 80]]}]
    layouts = [
        {
            "regions": [
                {"index": 0, "label": "table", "bbox": [10, 10, 90, 90]},
                {"index": 1, "label": "table", "bbox": [10, 10, 90, 90]},
            ]
        }
    ]
    diagnostics = []
    assert recognize_tables(None, result, layouts, diagnostics=diagnostics) == []
    assert diagnostics


def test_cli_live_requires_selected_page_before_any_request(pdf, tmp_path, monkeypatch):
    monkeypatch.setenv("PPAP_PADDLE_URL", "https://paddle.example/layout-parsing")
    monkeypatch.setattr(httpx.Client, "post", lambda *_a, **_kw: pytest.fail("network must not be attempted"))
    monkeypatch.setattr("sys.argv", ["paddle", "--pdf", str(pdf), "--out-root", str(tmp_path / "no-page")])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    assert not (tmp_path / "no-page").exists()


def test_live_export_requires_page_instead_of_sending_full_document(tmp_path, monkeypatch):
    source = make_pdf(tmp_path, pages=2)
    monkeypatch.setattr(httpx.Client, "post", lambda *_a, **_kw: pytest.fail("network must not be attempted"))
    with pytest.raises(ValueError, match="explicit page_number"):
        paddle.evaluate_pdf(source, tmp_path / "no-page", endpoint="https://paddle.example/layout-parsing")
    assert not (tmp_path / "no-page").exists()


@pytest.mark.parametrize("content", ["x" * 32768, "known\x01text", "known\ud800text", "known\ufffftext"], ids=["too-long", "illegal-control", "unpaired-surrogate", "xml-noncharacter"])
def test_unexportable_text_keeps_raw_evidence_and_valid_table(pdf, tmp_path, content):
    result = response()
    result["result"]["layoutParsingResults"][0]["prunedResult"]["parsing_res_list"][0]["block_content"] = content
    stats = paddle.evaluate_pdf(pdf, tmp_path / "export-limits", response=result)
    assert stats.status == "partial"
    assert set(stats.artifacts) == {"response.json", "evidence.json", "tables.xlsx"}
    raw = json.loads((stats.output_dir / "response.json").read_text())
    assert raw["layoutParsingResults"][0]["prunedResult"]["parsing_res_list"][0]["block_content"] == content
    evidence = json.loads((stats.output_dir / "evidence.json").read_text())
    assert evidence["pages"][0]["texts"][0]["text"] == content
    diagnostics = [d for d in evidence["diagnostics"] if d["code"] == "paddle.export.cell_unavailable"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["page"] == 1 and diagnostics[0]["element"] == "text" and diagnostics[0]["index"] == 0
    assert diagnostics[0] in evidence["pages"][0]["diagnostics"]
    assert content not in diagnostics[0]["message"]
    workbook = load_workbook(stats.output_dir / "tables.xlsx")
    try:
        assert workbook["Table_1"]["B2"].value == "A&B"
        assert content not in [c.value for row in workbook["OCR_Text"] for c in row]
    finally:
        workbook.close()


def test_illegal_table_text_does_not_discard_another_valid_table(pdf, tmp_path):
    result = response()
    result["result"]["layoutParsingResults"][0]["prunedResult"]["table_res_list"].append({"pred_html": "<table><tr><td>bad\x01text</td></tr></table>"})
    stats = paddle.evaluate_pdf(pdf, tmp_path / "bad-cell", response=result)
    assert stats.status == "partial"
    evidence = json.loads((stats.output_dir / "evidence.json").read_text())
    assert evidence["pages"][0]["tables"][1]["rows"] == [["bad\x01text"]]
    assert any(d["element"] == "table" and d["index"] == 1 for d in evidence["diagnostics"])
    workbook = load_workbook(stats.output_dir / "tables.xlsx")
    try:
        assert workbook.sheetnames == ["Table_1", "OCR_Text"]
        assert workbook["Table_1"]["B2"].value == "A&B"
    finally:
        workbook.close()


def test_unexportable_only_table_still_publishes_raw_and_json(pdf, tmp_path):
    result = response()
    result["result"]["layoutParsingResults"][0]["prunedResult"]["table_res_list"] = [{"pred_html": "<table><tr><td>bad\x01text</td></tr></table>"}]
    stats = paddle.evaluate_pdf(pdf, tmp_path / "json-only", response=result)
    assert stats.status == "partial"
    assert set(stats.artifacts) == {"response.json", "evidence.json"}
    evidence = json.loads((stats.output_dir / "evidence.json").read_text())
    assert evidence["pages"][0]["tables"][0]["rows"] == [["bad\x01text"]]
    assert evidence["pages"][0]["seals"]
    assert not (stats.output_dir / "tables.xlsx").exists()
