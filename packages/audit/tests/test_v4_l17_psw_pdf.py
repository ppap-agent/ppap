"""v4 L17 PDF owns its host and can check a real converted workbook."""

import inspect
from pathlib import Path
from types import SimpleNamespace

import fitz
from audit.checkers.v2.base_agent import BaseAgent
from audit.checkers.v2.excel_agent import ExcelAgent as V2ExcelAgent
from audit.checkers.v2.psw_pdf_agent import PswPDFAgent as V2PswPDFAgent
from audit.checkers.v3.excel_agent import ExcelAgent as V3ExcelAgent
from audit.checkers.v3.psw_excel_agent import PswExcelAgent
from audit.checkers.v3.psw_pdf_agent import PswPDFAgent
from audit.checkers.v4.li_auto.bindings import BINDINGS
from audit.checkers.v4.li_auto.psw import LiAutoPswAgent
from audit.checkers.v4.li_auto.psw_pdf import LiAutoPswPdfAgent
from audit.checkers.v4.li_auto.psw_pdf_workbook import LiAutoPswPdfWorkbookAgent
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


def _context(pdf: Path, clause_id: str = "L17:1"):
    audit = AuditInput(audit_id="frozen-l17", file_url=str(pdf))
    rule = RuleInput(rule_id="standard-revision", **preset_rule_body("理想", "L17"))
    closeables = []
    context = SimpleNamespace(
        audit_file=audit,
        rule=rule,
        group_context=GroupContextInput(by_audit_id={audit.audit_id: audit}),
        cache={},
        on_close=closeables.append,
        clause_id=clause_id,
    )
    return context, closeables


def _versioned_business(cls: type) -> bool:
    return any(getattr(base, "__module__", "").startswith(("audit.checkers.v2", "audit.checkers.v3")) for base in cls.__mro__)


def _reject_inherited_business(monkeypatch) -> None:
    """The v4 PDF path must not dispatch these versioned business methods."""

    def reject(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("v3 PSW business method ran")

    for cls, names in (
        (
            PswExcelAgent,
            (
                "rule_1",
                "rule_2",
                "rule_4",
                "rule_6",
                "rule_7",
                "rule_8",
                "rule_10",
                "_extract_supplier_name_code",
                "_is_effective_business_value",
                "_build_label_tokens",
                "_compact_token",
                "_compact_label_token",
                "_checked_controls",
                "_resolve_warrant_disposition",
                "_extract_comments_value",
                "_extract_conditional_approval_date",
                "_classify_disposition_option",
                "_is_submission_result_all_checked",
                "_parse_date_from_text",
                "_evidence_from_reads",
                "_finalize_audit_result",
            ),
        ),
        (LiAutoPswAgent, ("rule_1", "rule_2", "rule_3", "rule_5", "rule_6", "rule_9")),
        (V3ExcelAgent, ("check", "_execute_rule_check", "_load_workbook", "_finalize_rule_result")),
        (V2ExcelAgent, ("check",)),
        (BaseAgent, ("check",)),
    ):
        for name in names:
            monkeypatch.setattr(cls, name, reject)


class _Export:
    def __init__(self, destination: Path, rows: list[list[object | None]], title: str = "生产件批准交付物文件清单"):
        self.destination = destination
        self.rows = rows
        self.title = title
        self.calls = []

    def convert_pdf_to_excel(self, file_ref, **kwargs):
        self.calls.append(str(file_ref.location))
        book = Workbook()
        book.active.title = self.title
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
    for cls in (LiAutoPswPdfAgent, LiAutoPswPdfWorkbookAgent):
        module_source = inspect.getsource(inspect.getmodule(cls))
        assert "audit.checkers.v2" not in module_source
        assert "audit.checkers.v3" not in module_source
        assert not _versioned_business(cls)
    assert {BaseAgent, V2ExcelAgent, V3ExcelAgent, V2PswPDFAgent, PswPDFAgent, PswExcelAgent}.isdisjoint(LiAutoPswPdfAgent.__mro__)
    assert {BaseAgent, V2ExcelAgent, V3ExcelAgent, V2PswPDFAgent, PswPDFAgent, PswExcelAgent, LiAutoPswAgent}.isdisjoint(LiAutoPswPdfWorkbookAgent.__mro__)
    assert LiAutoPswPdfAgent.TARGET_EXCEL_AGENT_CLASS is LiAutoPswPdfWorkbookAgent
    assert LiAutoPswPdfWorkbookAgent.rule_1 is not LiAutoPswAgent.rule_1
    assert LiAutoPswPdfWorkbookAgent.rule_1 is not PswExcelAgent.rule_1
    assert LiAutoPswPdfWorkbookAgent.rule_2 is not PswExcelAgent.rule_2
    assert LiAutoPswPdfWorkbookAgent.rule_6 is not PswExcelAgent.rule_6
    assert LiAutoPswAgent.rule_1 is not PswExcelAgent.rule_1


def test_successful_conversion_runs_the_v4_clause_on_the_exported_workbook(tmp_path, monkeypatch):
    calls = []
    original = LiAutoPswPdfWorkbookAgent.rule_1

    def spy(self, ctx):
        calls.append(type(self))
        return original(self, ctx)

    spy.__rule_description__ = original.__rule_description__
    monkeypatch.setattr(LiAutoPswPdfWorkbookAgent, "rule_1", spy)
    _reject_inherited_business(monkeypatch)

    pdf = _pdf(tmp_path)
    exported = tmp_path / "l17-converted.xlsx"
    runtime = _Export(exported, [["项目", "附件"], ["尺寸报告", "报告.pdf"], ["零件认证相关", None]])
    suite, resources = _suite()
    context, closeables = _context(pdf)
    names = [name for name, _description in discover_declared_rules(LiAutoPswPdfWorkbookAgent)]
    assert resources["carriers"][".pdf"].endswith(":LiAutoPswPdfAgent")
    assert resources["methods"]["L17:1"] == BINDINGS["v4"]["L17:1"] == "rule_1"
    assert names == [f"rule_{number}" for number in range(1, 11)]
    assert names.index("rule_1") == 0
    try:
        with bind_runtime(runtime):
            result = check_clause(context, resources)
        checker = context.cache[("standard-checker", "frozen-l17", resources["carriers"][".pdf"])]
        assert type(checker) is LiAutoPswPdfAgent
        assert type(checker.excel_agent) is LiAutoPswPdfWorkbookAgent
        assert not _versioned_business(type(checker.excel_agent))
        assert calls == [LiAutoPswPdfWorkbookAgent]
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
    original = LiAutoPswPdfWorkbookAgent.rule_1

    def spy(self, ctx):
        calls.append(type(self))
        return original(self, ctx)

    spy.__rule_description__ = original.__rule_description__
    monkeypatch.setattr(LiAutoPswPdfWorkbookAgent, "rule_1", spy)
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


class _FormulaExport(_Export):
    def convert_pdf_to_excel(self, file_ref, **kwargs):
        self.calls.append(str(file_ref.location))
        book = Workbook()
        sheet = book.active
        sheet.title = "零件提交保证书"
        sheet["A1"] = "重量(kg)"
        sheet["B1"] = "=1+1"
        book.save(self.destination)
        book.close()
        return ConversionArtifact(
            success=True,
            xlsx_path=str(self.destination),
            strategy_used="controlled_xlsx",
            diagnostics={"pdf_v2_diagnostics": {"original_pages": [{"page": 1}]}},
            temp_files=[str(self.destination)],
        )


def test_converted_l17_weight_and_required_fields_do_not_call_v3(tmp_path, monkeypatch):
    calls = []

    def watch(name):
        original = getattr(LiAutoPswPdfWorkbookAgent, name)

        def spy(self, ctx, original=original, name=name):
            calls.append((name, type(self)))
            return original(self, ctx)

        spy.__rule_description__ = original.__rule_description__
        monkeypatch.setattr(LiAutoPswPdfWorkbookAgent, name, spy)

    watch("rule_2")
    watch("rule_6")
    _reject_inherited_business(monkeypatch)
    suite, resources = _suite()
    names = [name for name, _description in discover_declared_rules(LiAutoPswPdfWorkbookAgent)]
    assert resources["carriers"][".pdf"].endswith(":LiAutoPswPdfAgent")
    assert resources["methods"]["L17:2"] == BINDINGS["v4"]["L17:2"] == "rule_2"
    assert resources["methods"]["L17:6"] == BINDINGS["v4"]["L17:6"] == "rule_6"
    assert names.index("rule_2") == 1
    assert names.index("rule_6") == 5
    assert "L17:2" in suite.entrypoints
    assert "L17:6" in suite.entrypoints

    def run(runtime, clause_id):
        context, closeables = _context(_pdf(tmp_path), clause_id)
        try:
            with bind_runtime(runtime):
                return check_clause(context, resources)
        finally:
            for checker in closeables:
                close = getattr(checker, "close", None)
                if close:
                    close()

    weight_context, weight_closeables = _context(_pdf(tmp_path), "L17:2")
    try:
        with bind_runtime(_Export(tmp_path / "weight.xlsx", [["重量(kg)", "1.2345"]], title="零件提交保证书")):
            passed = check_clause(weight_context, resources)
            weight_context.clause_id = "L17:6"
            missing = check_clause(weight_context, resources)
        checker = weight_context.cache[("standard-checker", "frozen-l17", resources["carriers"][".pdf"])]
        assert type(checker) is LiAutoPswPdfAgent
        assert type(checker.excel_agent) is LiAutoPswPdfWorkbookAgent
        assert not _versioned_business(type(checker.excel_agent))
        assert calls == [("rule_2", LiAutoPswPdfWorkbookAgent), ("rule_6", LiAutoPswPdfWorkbookAgent)]
        assert passed.clause_id == "L17:2"
        assert passed.status == "PASS"
        assert passed.suggestions == ""
        assert passed.evidence is not None and passed.evidence.sources
        assert missing.clause_id == "L17:6"
        assert missing.status == "NG"
        assert missing.status != "PASS"
        assert missing.suggestions
        assert missing.evidence is not None
    finally:
        for checker in weight_closeables:
            close = getattr(checker, "close", None)
            if close:
                close()

    rejected = run(_Export(tmp_path / "short.xlsx", [["重量(kg)", "1.2"]], title="零件提交保证书"), "L17:2")
    assert rejected.clause_id == "L17:2"
    assert rejected.status == "NG"
    assert "小数" in rejected.suggestions
    assert rejected.evidence is not None

    unreadable = run(_FormulaExport(tmp_path / "formula.xlsx", []), "L17:2")
    assert unreadable.clause_id == "L17:2"
    assert unreadable.status == "UNABLE"
    assert unreadable.status != "PASS"
    assert unreadable.suggestions
    assert unreadable.evidence is not None
