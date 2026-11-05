"""A selected unreadable cell is unknown, not a nonempty business value."""

import pytest
from audit.checkers.v2.excel_agent import ExcelAgent as V2Excel
from audit.checkers.v2.psw_excel_agent import PswExcelAgent as V2Psw
from audit.checkers.v3.excel_agent import ExcelAgent as V3Excel
from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.psw_excel_agent import PswExcelAgent as V3Psw
from audit.contracts import AuditInput, RuleInput
from audit.engine.rule_engine import Table
from audit.kinds import PpapDocumentType
from openpyxl import Workbook


def saved(tmp_path):
    path = tmp_path / "source.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet["A1"] = "known"
    sheet["A2"] = "#N/A"
    assert sheet["A2"].data_type == "e"
    workbook.save(path)
    workbook.close()
    return path


@pytest.mark.parametrize("base", [V2Excel, V3Excel], ids=["v2", "v3"])
@pytest.mark.parametrize("read", ["value", "has_data", "missing", "unrelated", "collected_ng", "explicit_unknown"])
def test_only_selected_error_cells_block_conclusions(tmp_path, base, read):
    class Probe(base):
        DOC_TYPE = PpapDocumentType.PSW

        @rule("受控读取")
        def rule_1(self, ctx):
            sheet = Table(ctx).sheet("PSW主表")
            if read == "explicit_unknown":
                from audit.engine.rule_engine.fluent import _observe_cell

                assert _observe_cell(ctx, sheet.ws, sheet.ws["A2"]).state == "unresolved"
                return "PASS", "不能把未知当通过"
            if read == "unrelated":
                return ("PASS", "已知见证") if sheet.cell(1, 1).value() == "known" else ("NG", "值错误")
            if read == "collected_ng":
                ctx.record_ng("独立已知缺陷")
            source = sheet.cell(2, 1)
            if read == "has_data":
                return ("PASS", "非空") if source.has_data() else ("NG", "空值")
            value = source.value()
            if read == "missing":
                return "NG", f"不能据不可读值判定缺失: {value}"
            return "PASS", f"不能把错误当原文: {value}"

    probe = Probe(AuditInput(audit_id="frozen", file_url=str(saved(tmp_path))), RuleInput(rule_id="confirmed", type="零件提交保证书", rules=[]))
    try:
        result = probe.check(0)
    finally:
        probe.close()
    assert result.status == ("PASS" if read == "unrelated" else "NG" if read == "collected_ng" else "UNABLE")
    assert "#N/A" not in result.suggestions
    if read == "collected_ng":
        assert "独立已知缺陷" in result.suggestions


@pytest.mark.parametrize("cls", [V2Psw, V3Psw], ids=["v2", "v3"])
def test_psw_supplier_error_does_not_satisfy_name_slash_code_format(tmp_path, cls):
    path = tmp_path / "supplier.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet["A1"] = "供应商名称/供应商代码"
    sheet["A2"] = "#N/A"
    workbook.save(path)
    workbook.close()
    agent = cls(AuditInput(audit_id="frozen", file_url=str(path)), RuleInput(rule_id="confirmed", type="零件提交保证书", rules=[]))
    try:
        result = agent.check(3)
    finally:
        agent.close()
    assert result.status == "UNABLE"


@pytest.mark.parametrize("base", [V2Excel, V3Excel], ids=["v2", "v3"])
@pytest.mark.parametrize(
    "case,expected",
    [
        ("hidden_label", "UNABLE"),
        ("known_label_with_other_unknown", "PASS"),
        ("readable_missing", "NG"),
        ("unknown_outside_search", "NG"),
    ],
)
def test_field_absence_requires_readable_bounded_label_search(tmp_path, base, case, expected):
    class Probe(base):
        DOC_TYPE = PpapDocumentType.PSW

        @rule("字段存在检查")
        def rule_1(self, ctx):
            fields = Table(ctx).field_block("PSW主表字段区")
            fields.expect("重量字段")
            return "PASS", "存在且已填写"

    path = tmp_path / "field-search.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet["A1"] = "重量" if case == "known_label_with_other_unknown" else "#N/A" if case == "hidden_label" else "其他字段"
    sheet["B1"] = "1.2345"
    if case == "known_label_with_other_unknown":
        sheet["D2"] = "#N/A"
    elif case == "unknown_outside_search":
        sheet["AZ100"] = "#N/A"
    workbook.save(path)
    workbook.close()
    agent = Probe(AuditInput(audit_id="frozen", file_url=str(path)), RuleInput(rule_id="confirmed", type="零件提交保证书", rules=[]))
    try:
        result = agent.check(0)
    finally:
        agent.close()
    assert result.status == expected
