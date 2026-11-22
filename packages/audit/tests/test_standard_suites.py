"""Standard source identity/coverage, separate from human corpus correctness."""

import importlib
from types import SimpleNamespace

import pytest
from audit.checker_lib.agents import AgentAudit
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.presets import preset_rule_body
from audit.standard_suites import predefined_directory_suites

COMPANY_MODULES = {"li": "audit.checker_lib.li", "nio": "audit.checker_lib.nio"}


def resources(suite):
    module = {}
    exec(suite.program_files["resources.py"], module)
    return module["RESOURCE"]


def leaf(key):
    return next(suite for suite in predefined_directory_suites() if suite.suite_key == key)


def test_standard_content_has_stable_immutable_identity():
    assert [dict(suite.files) for suite in predefined_directory_suites()] == [dict(suite.files) for suite in predefined_directory_suites()]
    suite = predefined_directory_suites()[0]
    try:
        suite.files["checker.py"] = b"changed"
    except TypeError:
        pass
    else:
        raise AssertionError("mutable standard source")


def test_unsupported_carrier_is_unable():
    suite = leaf("理想:L11")
    audit = AgentAudit(AuditInput(audit_id="frozen", file_url="/not/read/document.txt"), None, resource=resources(suite))
    assert audit.evaluate(next(iter(suite.entrypoints))).status == "UNABLE"


def test_u12_l11_n03_leaves_execute_every_source_clause(tmp_path):
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
        rule = RuleInput.of(preset_rule_body(suite.customer, suite.source_entry), rule_id="standard-revision")
        checker = AgentAudit(audit, rule, GroupContextInput(by_audit_id={audit.audit_id: audit}), resource=resources(suite))
        try:
            for clause in suite.entrypoints:
                item = checker.evaluate(clause)
                assert item.clause_id == clause
                assert item.status in {"PASS", "NG", "UNABLE", "ERR", "NA"}
                # The uaes implementation locates evidence for every PASS; li/nio report as their baseline did.
                assert item.status != "PASS" or item.evidence is not None or suite.customer != "UAES"
        finally:
            checker.close()


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
