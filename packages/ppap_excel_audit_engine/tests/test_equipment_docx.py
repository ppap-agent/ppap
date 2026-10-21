"""Equipment Word tables are audited without rendering or conversion."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from docx import Document
from ppap_core.contracts import AuditInput, RuleInput
from ppap_core.types import PpapDocumentType
from ppap_excel_audit_engine import run_audit


class NoConversionRuntime:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        raise AssertionError(f"Structured DOCX must not call an external conversion or model: {name}")


def equipment_document(*, supplier=True, auxiliary=False, empty_template=False, duplicate=False):
    document = Document()
    document.add_paragraph("量检具、检测试验设备清单")
    if auxiliary:
        document.add_table(rows=1, cols=1).cell(0, 0).text = "产线/工序、设备开发、设备验收、设备、验收结果"

    def add_table(data_rows, filled):
        table = document.add_table(rows=3 + data_rows, cols=11)
        table.cell(0, 0).merge(table.cell(0, 10)).text = "设备开发计划、验收清单"
        table.cell(1, 0).merge(table.cell(2, 0)).text = "产线/工序"
        table.cell(1, 1).merge(table.cell(1, 7)).text = "设备开发"
        table.cell(1, 8).merge(table.cell(1, 10)).text = "设备验收"
        headers = ("设备", "供应商" if supplier else "", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间", "验收结果", "整改措施", "整改计划")
        for column, header in enumerate(headers, 1):
            table.cell(2, column).text = header
        if filled:
            values = ("焊接工序", "量检具", "供应商A", "EQ-01", "0", "1", "专用", "2026-09-01", "通过", "", "")
            for column, value in enumerate(values):
                table.cell(3, column).text = value
            table.cell(3, 1).add_paragraph("检测试验设备")

    if empty_template:
        add_table(8, False)
    add_table(1, True)
    if duplicate:
        add_table(1, True)
    document.add_paragraph("表后说明")
    return document


@pytest.fixture(params=["v2", "v3"])
def audit(request):
    def check(location):
        runtime = NoConversionRuntime()
        results = run_audit(
            AuditInput(audit_id="equipment-word", file_url=str(location)),
            RuleInput(rule_id="confirmed-equipment-rules", rule_profile=request.param, type=PpapDocumentType.EQUIPMENT_LIST.get_type_name(), rules=[{"id": "1"}, {"id": "2"}]),
            runtime=runtime,
        )
        assert runtime.calls == []
        return results

    return check


@pytest.fixture
def http_source(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    servers = []

    def serve(content, status=200):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                self.send_response(status)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}/equipment.docx?sig=test", calls

    yield serve
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join()


def test_structured_docx_passes_without_conversion_or_rendering(tmp_path, audit):
    path = tmp_path / "equipment.docx"
    equipment_document().save(path)

    results = audit(path)

    assert [result.status for result in results] == ["PASS", "PASS"]


@pytest.mark.parametrize("extra_table", ["auxiliary", "empty-template"])
def test_complete_equipment_table_is_not_displaced_by_notes_or_an_empty_template(tmp_path, audit, extra_table):
    path = tmp_path / "equipment.docx"
    equipment_document(auxiliary=extra_table == "auxiliary", empty_template=extra_table == "empty-template").save(path)

    results = audit(path)

    assert [result.status for result in results] == ["PASS", "PASS"]


def test_equally_valid_primary_tables_report_ambiguity(tmp_path, audit):
    path = tmp_path / "equipment.docx"
    equipment_document(duplicate=True).save(path)

    results = audit(path)

    assert [result.status for result in results] == ["NG", "NG"]
    assert all("歧义" in result.suggestions for result in results)


def test_missing_supplier_column_is_a_document_failure_not_a_runtime_failure(tmp_path, audit):
    path = tmp_path / "equipment.docx"
    equipment_document(supplier=False).save(path)

    results = audit(path)

    assert results[0].status == "NG"
    assert "供应商" in results[0].suggestions


@pytest.mark.parametrize(("source", "expected"), [("missing-table", "NG"), ("corrupt-package", "ERR")])
def test_readable_missing_table_and_corrupt_package_have_distinct_results(tmp_path, audit, source, expected):
    path = tmp_path / "equipment.docx"
    if source == "missing-table":
        document = Document()
        document.add_paragraph("量检具清单没有表格")
        document.save(path)
    else:
        path.write_bytes(b"not-a-docx")

    results = audit(path)

    assert [result.status for result in results] == [expected, expected]


def test_remote_docx_is_downloaded_once_and_parsed_as_a_real_word_grid(tmp_path, audit, http_source):
    path = tmp_path / "equipment.docx"
    equipment_document().save(path)
    url, calls = http_source(path.read_bytes())

    results = audit(url)

    assert [result.status for result in results] == ["PASS", "PASS"]
    assert calls == ["/equipment.docx?sig=test"]


@pytest.mark.parametrize("failure", ["http-error", "corrupt-package"])
def test_remote_failure_is_not_downloaded_again_for_each_rule(audit, http_source, failure):
    url, calls = http_source(b"not-a-docx", status=503 if failure == "http-error" else 200)

    results = audit(url)

    assert [result.status for result in results] == ["ERR", "ERR"]
    assert calls == ["/equipment.docx?sig=test"]
