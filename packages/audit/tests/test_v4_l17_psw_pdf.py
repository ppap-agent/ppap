"""v4 L17 PDF owns its host and can check a real converted workbook."""

import inspect
from pathlib import Path
from types import SimpleNamespace

import fitz
from audit.checkers.v2.base_agent import BaseAgent
from audit.checkers.v2.psw_pdf_agent import PswPDFAgent as V2PswPDFAgent
from audit.checkers.v3.psw_excel_agent import PswExcelAgent
from audit.checkers.v3.psw_pdf_agent import PswPDFAgent
from audit.checkers.v4.li_auto.bindings import BINDINGS
from audit.checkers.v4.li_auto.psw import LiAutoPswAgent
from audit.checkers.v4.li_auto.psw_pdf import LiAutoPswPdfAgent
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.engine.checkers.rule_declarations import discover_declared_rules
from audit.engine.runtime import bind_runtime
from audit.presets import preset_rule_body
from audit.standard_library import check_clause
from audit.standard_suites import standard_suites
from documents.contracts import ConversionArtifact, FileRef
from openpyxl import Workbook, load_workbook


def _pdf(tmp_path: Path) -> Path:
    path = tmp_path / "l17.pdf"
    with fitz.open() as document:
        document.new_page(width=595, height=842)
        document.save(path)
    return path


def _suite():
    suite = next(item for item in standard_suites() if item.suite_key == "理想:L17")
    module: dict = {}
    exec(suite.files["checker.py"], module)
    return suite, module["RESOURCES"]


def _context(pdf: Path):
    audit = AuditInput(audit_id="frozen-l17", file_url=str(pdf))
    rule = RuleInput(rule_id="standard-revision", **preset_rule_body("理想", "L17"))
    closeables = []
    context = SimpleNamespace(
        audit_file=audit,
        rule=rule,
        group_context=GroupContextInput(by_audit_id={audit.audit_id: audit}),
        cache={},
        on_close=closeables.append,
        clause_id="L17:1",
    )
    return context, closeables


class _Export:
    def __init__(self, destination: Path, rows: list[list[object | None]]):
        self.destination = destination
        self.rows = rows
        self.calls = []

    def convert_pdf_to_excel(self, file_ref, **kwargs):
        self.calls.append(str(file_ref.location))
        book = Workbook()
        book.active.title = "生产件批准交付物文件清单"
        for row in self.rows:
            book.active.append(row)
        book.save(self.destination)
        book.close()
        return ConversionArtifact(
            success=True,
            xlsx_path=str(self.destination),
            strategy_used="controlled_xlsx",
            diagnostics={"pdf_v2_diagnostics": {"original_pages": [{"page": 1}]}},
            temp_files=[str(self.destination)],
        )

    def load_workbook_auto(self, file_ref: FileRef):
        return load_workbook(file_ref.location)


class _Failed:
    def convert_pdf_to_excel(self, file_ref, **kwargs):
        return ConversionArtifact(success=False, diagnostics={"error_code": "controlled_missing_grid"})


def test_v4_l17_pdf_host_does_not_inherit_a_versioned_base():
    module_source = inspect.getsource(inspect.getmodule(LiAutoPswPdfAgent))
    assert "audit.checkers.v2" not in module_source
    assert "audit.checkers.v3" not in module_source
    assert BaseAgent not in LiAutoPswPdfAgent.__mro__
    assert {BaseAgent, V2PswPDFAgent, PswPDFAgent, PswExcelAgent}.isdisjoint(LiAutoPswPdfAgent.__mro__)
    assert LiAutoPswPdfAgent.TARGET_EXCEL_AGENT_CLASS is LiAutoPswAgent
    assert LiAutoPswAgent.rule_1 is not PswExcelAgent.rule_1


def test_successful_conversion_runs_the_v4_clause_on_the_exported_workbook(tmp_path, monkeypatch):
    calls = []
    original = LiAutoPswAgent.rule_1

    def spy(self, ctx):
        calls.append(type(self))
        return original(self, ctx)

    spy.__rule_description__ = original.__rule_description__
    monkeypatch.setattr(LiAutoPswAgent, "rule_1", spy)

    def reject_v3(self, ctx):
        raise AssertionError("v3 PSW rule ran")

    monkeypatch.setattr(PswExcelAgent, "rule_1", reject_v3)

    pdf = _pdf(tmp_path)
    exported = tmp_path / "l17-converted.xlsx"
    runtime = _Export(exported, [["项目", "附件"], ["尺寸报告", "报告.pdf"], ["零件认证相关", None]])
    suite, resources = _suite()
    context, closeables = _context(pdf)
    names = [name for name, _description in discover_declared_rules(LiAutoPswAgent)]
    assert resources["carriers"][".pdf"].endswith(":LiAutoPswPdfAgent")
    assert resources["methods"]["L17:1"] == BINDINGS["v4"]["L17:1"] == "rule_1"
    assert names.index("rule_1") == 0
    try:
        with bind_runtime(runtime):
            result = check_clause(context, resources)
        checker = context.cache[("standard-checker", "frozen-l17", resources["carriers"][".pdf"])]
        assert type(checker) is LiAutoPswPdfAgent
        assert type(checker.excel_agent) is LiAutoPswAgent
        assert calls == [LiAutoPswAgent]
        assert result.clause_id == "L17:1"
        assert result.status == "PASS"
        assert result.suggestions == "已核实交付物附件。"
        assert result.evidence is not None
        assert runtime.calls == [str(pdf)]
        assert checker.conversion_artifact.success is True
        saved = load_workbook(checker.conversion_artifact.xlsx_path, read_only=True, data_only=False)
        try:
            assert saved.active.title == "生产件批准交付物文件清单"
            assert saved.active["A2"].value == "尺寸报告"
            assert saved.active["B2"].value == "报告.pdf"
        finally:
            saved.close()
        assert "L17:1" in suite.entrypoints
    finally:
        for checker in closeables:
            close = getattr(checker, "close", None)
            if close:
                close()


def test_failed_conversion_stays_unable_or_error_without_running_the_workbook_clause(tmp_path, monkeypatch):
    calls = []
    original = LiAutoPswAgent.rule_1

    def spy(self, ctx):
        calls.append(type(self))
        return original(self, ctx)

    spy.__rule_description__ = original.__rule_description__
    monkeypatch.setattr(LiAutoPswAgent, "rule_1", spy)
    pdf = _pdf(tmp_path)
    _suite_ignored, resources = _suite()
    context, closeables = _context(pdf)
    try:
        with bind_runtime(_Failed()):
            result = check_clause(context, resources)
        checker = context.cache[("standard-checker", "frozen-l17", resources["carriers"][".pdf"])]
        assert type(checker) is LiAutoPswPdfAgent
        assert checker.excel_agent is None
        assert calls == []
        assert result.clause_id == "L17:1"
        assert result.status in {"UNABLE", "ERR"}
        assert result.status != "PASS"
    finally:
        for checker in closeables:
            close = getattr(checker, "close", None)
            if close:
                close()
