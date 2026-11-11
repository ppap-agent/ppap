"""Standard source identity/coverage, separate from human corpus correctness."""

import importlib
import json
from collections import Counter
from types import SimpleNamespace

import pytest
from audit.checker_lib.agents import AgentAudit
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.engine.checkers.rule_declarations import discover_declared_rules
from audit.presets import customer_presets, preset_rule_body
from audit.standard_suites import library_revision, predefined_directory_suites

COMPANY_MODULES = {"li": "audit.checker_lib.li", "nio": "audit.checker_lib.nio"}


def resources(suite):
    module = {}
    exec(suite.program_files["resources.py"], module)
    return module["RESOURCE"]


def leaf(key):
    return next(suite for suite in predefined_directory_suites() if suite.suite_key == key)


def check_clause(context, resource):
    """Run one clause through the leaf adapter; a prepared implementation may be injected through the cache."""
    audit = AgentAudit(context.audit_file, getattr(context, "rule", None), getattr(context, "group_context", None), resource=resource)
    path = resource["carriers"].get("." + context.audit_file.file_url.rsplit(".", 1)[-1].lower())
    injected = getattr(context, "cache", {}).get(("standard-checker", context.audit_file.audit_id, path))
    if injected is not None:
        audit._agent = injected
    return audit.evaluate(context.clause_id)


def test_every_company_leaf_binds_each_clause_to_a_declared_implementation_method():
    suites = predefined_directory_suites()
    assert Counter(s.customer for s in suites) == {"UAES": 19, "理想": 17, "蔚来": 17}
    assert {s.suite_key for s in suites} == {f"{p.customer}:{p.entry_id}" for p in customer_presets()}
    for suite in suites:
        rules = json.loads(suite.files["rules.json"])
        source = resources(suite)
        assert set(source["methods"]) == set(suite.entrypoints) == {rule["id"] for rule in rules["rules"]}
        for class_path in set(source["carriers"].values()):
            module, name = class_path.split(":")
            cls = getattr(importlib.import_module(module), name)
            declared = cls.TARGET_EXCEL_AGENT_CLASS if getattr(cls, "TARGET_EXCEL_AGENT_CLASS", None) else cls
            assert set(source["methods"].values()) <= dict(discover_declared_rules(declared)).keys()
        assert rules["checker_binding"] == dict(suite.entrypoints)


def test_standard_content_and_shared_library_have_stable_identity():
    assert [dict(suite.files) for suite in predefined_directory_suites()] == [dict(suite.files) for suite in predefined_directory_suites()]
    revision = library_revision()
    assert len(revision) == 64 and revision == library_revision()
    suite = predefined_directory_suites()[0]
    try:
        suite.files["checker.py"] = b"changed"
    except TypeError:
        pass
    else:
        raise AssertionError("mutable standard source")


def test_unsupported_carrier_never_falls_back_to_legacy_checker():
    suite = leaf("理想:L11")
    ctx = SimpleNamespace(clause_id=next(iter(suite.entrypoints)), audit_file=AuditInput(audit_id="frozen", file_url="/not/read/document.txt"))
    result = check_clause(ctx, resources(suite))
    assert result.status == "UNABLE"


def test_editable_u12_l11_n03_starters_execute_source_clauses(tmp_path):
    from openpyxl import Workbook

    for key, rows, title in [
        ("UAES:U12", [["特殊特性清单"], ["编号", "特性名称", "特性类别"], ["1", "扭矩", "重要特性"]], "特殊特性清单"),
        ("理想:L11", [["过程流程图"], ["工序号", "工序名称", "特殊特性分类"], ["10", "装配", "NA"]], "过程流程图"),
        ("蔚来:N03", [["控制计划", "☑生产"], ["工序名称/操作描述", "特殊特性分类", "产品", "过程"], ["装配", "NA", "扭矩", "装配力"]], "控制计划"),
    ]:
        suite = leaf(key)
        workbook = Workbook()
        workbook.active.title = title
        for row in rows:
            workbook.active.append(row)
        path = tmp_path / f"{suite.source_entry}.xlsx"
        workbook.save(path)
        workbook.close()
        audit = AuditInput(audit_id="frozen-version", file_url=str(path))
        rule = RuleInput(rule_id="standard-revision", **preset_rule_body(suite.customer, suite.source_entry))
        closeables = []
        context = SimpleNamespace(audit_file=audit, rule=rule, group_context=GroupContextInput(by_audit_id={audit.audit_id: audit}), cache={}, on_close=closeables.append)
        try:
            for clause in suite.entrypoints:
                context.clause_id = clause
                item = check_clause(context, resources(suite))
                assert item.clause_id == clause
                assert item.status in {"PASS", "NG", "UNABLE", "ERR", "NA"}
                # The uaes implementation locates evidence for every PASS; li/nio report as their baseline did.
                assert item.status != "PASS" or item.evidence is not None or suite.customer != "UAES"
        finally:
            for checker in closeables:
                close = getattr(checker, "close", None)
                if close:
                    close()


def test_authored_factory_results_bind_by_ids_without_historical_class():
    from audit.contracts import AuditResultItem
    from audit.standard_library import check_factory

    calls = []

    def result(clause):
        calls.append(clause)
        return AuditResultItem(classification="x", item=clause, priority="P0", status="NG", suggestions="", clause_id=clause)

    def factory(audit, rule):
        return SimpleNamespace(checks=[SimpleNamespace(check=lambda: result("b")), SimpleNamespace(check=lambda: result("a"))])

    context = SimpleNamespace(
        audit_file=SimpleNamespace(audit_id="target"),
        target=SimpleNamespace(binding=SimpleNamespace(checker_digest="fixed", entrypoints={"a": "checker:create_audit", "b": "checker:create_audit"})),
        rule=None,
        group_context=None,
        cache={},
        on_close=lambda item: None,
        clause_id="a",
    )
    assert check_factory(context, factory).clause_id == "a"
    context.clause_id = "b"
    assert check_factory(context, factory).clause_id == "b"
    assert calls == ["b", "a"]


def test_authored_factory_failure_is_not_repeated_for_later_clauses():
    import pytest
    from audit.standard_library import check_factory

    calls = []

    def factory(*args):
        calls.append("external")
        raise RuntimeError("unknown provider outcome")

    context = SimpleNamespace(
        audit_file=SimpleNamespace(audit_id="target"),
        target=SimpleNamespace(binding=SimpleNamespace(checker_digest="fixed")),
        rule=None,
        group_context=None,
        cache={},
        on_close=lambda item: None,
        clause_id="a",
    )
    for clause in ("a", "b"):
        context.clause_id = clause
        with pytest.raises(RuntimeError, match="unknown provider"):
            check_factory(context, factory)
    assert calls == ["external"]


def test_authored_collective_failure_is_not_repeated_for_later_clauses():
    import pytest
    from audit.standard_library import check_factory

    calls = []

    def fail():
        calls.append("external")
        raise RuntimeError("unknown provider outcome")

    context = SimpleNamespace(
        audit_file=SimpleNamespace(audit_id="target"),
        target=SimpleNamespace(binding=SimpleNamespace(checker_digest="fixed", entrypoints={"a": "checker:create_audit", "b": "checker:create_audit"})),
        rule=None,
        group_context=None,
        cache={},
        on_close=lambda item: None,
        clause_id="a",
    )
    for clause in ("a", "b"):
        context.clause_id = clause
        with pytest.raises(RuntimeError, match="unknown provider"):
            check_factory(context, lambda *args: SimpleNamespace(checks=[SimpleNamespace(check=fail)]))
    assert calls == ["external"]


@pytest.mark.parametrize("module", ["audit.checker_lib.li.psw_pdf_agent", "audit.checker_lib.nio.psw_pdf_agent"])
def test_pdf_suite_keeps_original_page_visual_policy(module, monkeypatch):
    """Dispatch regression only: a converted grid cannot stand in for a PDF stamp."""
    import importlib

    from audit.contracts import AuditResultItem
    from audit.engine.checkers.rule_declarations import discover_declared_rules

    cls = importlib.import_module(module).PswPDFAgent
    checker = object.__new__(cls)
    checker.temp_files = []
    checker.audit_file = AuditInput(audit_id="frozen", file_url="/frozen/original.pdf")
    checker.excel_agent = SimpleNamespace(_execute_rule_check=lambda *_: pytest.fail("PDF visuals must not use the converted workbook"))
    checker.log_audit_start = checker.log_audit_result = lambda *args: None
    calls = []

    def original(ith, rule_no, **kwargs):
        calls.append((ith, rule_no))
        return AuditResultItem(classification="stamp", item="original PDF", priority="P0", status="UNABLE", suggestions="Original fact is unconfirmed")

    checker._run_fact_rule = original
    from audit.engine.checkers import paddle_evidence

    monkeypatch.setattr(paddle_evidence, "observe_psw_shadow", lambda *args: None)
    suite = leaf("理想:L17" if ".li." in module else "蔚来:N17")
    context = SimpleNamespace(audit_file=checker.audit_file, clause_id="3", cache={("standard-checker", "frozen", f"{module}:PswPDFAgent"): checker})
    methods = discover_declared_rules(cls.TARGET_EXCEL_AGENT_CLASS)
    result = check_clause(context, {"carriers": {".pdf": f"{module}:PswPDFAgent"}, "methods": {"3": methods[2][0]}})
    assert resources(suite)["methods"][suite.source_entry + ":3"] == methods[2][0]
    assert calls == [(2, 3)]
    assert result.status == "UNABLE" and result.clause_id == "3"


def test_pdf_suite_keeps_conversion_page_evidence():
    from audit.checker_lib.nio.pdf_excel_delegator_agent import ProcessFlowChartPdfExcelDelegatorAgent
    from audit.checker_lib.nio.process_flow_chart_excel_agent import ProcessFlowChartExcelAgent
    from audit.contracts import AuditResultItem
    from audit.engine.checkers.rule_declarations import discover_declared_rules

    cls = ProcessFlowChartPdfExcelDelegatorAgent
    checker = object.__new__(cls)
    checker.temp_files = []
    checker.audit_file = AuditInput(audit_id="frozen", file_url="/frozen/original.pdf")
    checker.excel_agent = SimpleNamespace(_execute_rule_check=lambda _: AuditResultItem(classification="field", item="field", priority="P0", status="PASS", suggestions="grid value"))
    checker._apply_pdf_evidence = lambda result: result.model_copy(update={"status": "UNABLE", "suggestions": "original page was incomplete"})
    implementation = f"{cls.__module__}:{cls.__name__}"
    method = discover_declared_rules(ProcessFlowChartExcelAgent)[0][0]
    context = SimpleNamespace(audit_file=checker.audit_file, clause_id="1", cache={("standard-checker", "frozen", implementation): checker})
    result = check_clause(context, {"carriers": {".pdf": implementation}, "methods": {"1": method}})
    assert result.status == "UNABLE"
    assert result.suggestions == "original page was incomplete"


@pytest.mark.parametrize("version", ["li", "nio"])
def test_word_suite_uses_original_visual_hook_instead_of_excel(version):
    from audit.contracts import AuditResultItem

    module = f"{COMPANY_MODULES[version]}.psw_docx_agent"
    cls = importlib.import_module(module).PswDocxAgent
    checker = object.__new__(cls)
    checker.temp_files = []
    checker.audit_file = AuditInput(audit_id="frozen", file_url="/frozen/original.docx")
    checker._execute_rule_check = lambda *_: pytest.fail("Word visual clauses must not read an XLSX carrier")
    calls = []
    checker.check_source_rule = lambda ith: calls.append(ith) or AuditResultItem(classification="stamp", item="stamp", priority="P0", status="UNABLE", suggestions="unconfirmed")
    implementation = f"{module}:PswDocxAgent"
    context = SimpleNamespace(audit_file=checker.audit_file, clause_id="3", cache={("standard-checker", "frozen", implementation): checker})
    result = check_clause(context, {"carriers": {".docx": implementation}, "methods": {"3": discover_declared_rules(cls)[2][0]}})
    assert calls == [2]
    assert result.status == "UNABLE" and result.clause_id == "3"


@pytest.mark.parametrize("version", ["li", "nio"])
@pytest.mark.parametrize("performed", [False, True])
def test_native_adapter_publishes_only_actual_predicate_count(tmp_path, version, performed):
    from audit.kinds import PpapDocumentType
    from openpyxl import Workbook

    module = importlib.import_module(f"{COMPANY_MODULES[version]}.excel_agent")

    class NativeChecker(module.ExcelAgent):
        DOC_TYPE = PpapDocumentType.PSW

        @module.rule("actual equality")
        def rule_1(self, ctx):
            if performed:
                value = ctx.workbook.active["A1"].value
                ctx.note_cell(ctx.workbook.active.title, 1, 1)
                ctx.record_execution()
                return ("PASS", "equal") if value == "known" else ("NG", "different")
            return "PASS", "no predicate ran"

    workbook = Workbook()
    workbook.active["A1"] = "known"
    path = tmp_path / "original.xlsx"
    workbook.save(path)
    workbook.close()
    checker = NativeChecker(AuditInput(audit_id="frozen", file_url=str(path)), RuleInput(rule_id="r", type="零件提交保证书", rules=[{"id": "1"}]))
    try:
        result = checker._execute_rule_check(1)
        assert result.checks_performed == int(performed)
    finally:
        checker.close()


def test_removed_root_reexports_stay_absent_and_version_packages_remain():
    """The 17 root shims had no caller; the old version packages are gone and company implementations import."""
    import importlib.util

    removed = (
        "annual_test",
        "assembly_issue",
        "checking_fixture",
        "control_plan",
        "dimension_measurement",
        "dpvr",
        "dvp_r",
        "equipment_list",
        "initial_process_capacity",
        "msa",
        "pfmea",
        "plant_capacity",
        "process_flow",
        "psw",
        "special_characteristic",
        "sub_supplier",
        "tooling",
    )
    for name in removed:
        assert importlib.util.find_spec(f"audit.checkers.{name}") is None
    for version in ("v2", "v3", "v4"):
        assert importlib.util.find_spec(f"audit.checkers.{version}") is None
    for company in ("li", "nio", "uaes"):
        importlib.import_module(f"audit.checker_lib.{company}")
