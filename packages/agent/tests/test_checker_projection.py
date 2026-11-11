"""Real checker results retain their scope and evidence through Agent projection."""

from pathlib import Path

from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.contracts import AuditInput, RuleInput
from audit.engine.rule_engine.context import RuleContext
from audit.kinds import PpapDocumentType
from openpyxl import Workbook


def rule_input(*items: tuple[str, str]) -> RuleInput:
    return RuleInput(
        rule_id="confirmed-clauses",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[{"id": clause_id, "category": category, "rule_to_check": category} for clause_id, category in items],
    )


def test_truncated_cell_reads_remain_visible_but_are_not_a_complete_location(tmp_path: Path) -> None:
    from agent.mapping import checker_response
    from agent.protocol import ExecuteRequest, FrozenFile
    from audit.engine.rule_engine.context import MAX_CELL_READS

    class WideAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True
        EXPECTED_RULE_CATEGORIES = ("范围",)

        @rule("范围", clause_id="A")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_execution()
            for row in range(1, MAX_CELL_READS + 3):
                ctx.note_cell("过程流程图", row, 1, file_version_id="version-1")
            return "PASS", "已检查可见行"

    path = tmp_path / "target.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    for row in range(1, MAX_CELL_READS + 3):
        sheet.cell(row, 1, f"r{row}")
    workbook.save(path)
    workbook.close()
    agent = WideAudit(
        AuditInput(audit_id="version-1", file_url=str(path), file_metadata={"file_version_id": "version-1"}),
        rule_input(("A", "范围")),
    )
    try:
        result = agent.check(0)
    finally:
        agent.close()

    assert result is not None
    assert result.status == "PASS"
    assert result.evidence is not None
    assert len(result.evidence.sources) == MAX_CELL_READS
    assert result.evidence.diagnostics[0].severity == "warning"
    assert result.evidence.diagnostics[0].code == "read_locations_truncated"
    body = {"rule_profile": "v3", "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(), "rules": [{"id": "A", "category": "范围", "rule_to_check": "范围"}]}
    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256="a" * 64,
        rule_version_id="rule-1",
        rule_body=body,
    )
    response = checker_response(
        ExecuteRequest(protocol_revision="test", round_id="round-1", files=[frozen], executor="ppap-deep", executor_revision="rev", job_id="job-1"),
        [(frozen, [result])],
    )

    assert response.coverage.verification is None
    assert response.coverage.locator_gap is True
    assert response.findings[0].status == "PASS"
    assert response.findings[0].locator["semantic"]["evidence_incomplete"] is True
    assert len(response.findings[0].locator["semantic"]["sources"]) == MAX_CELL_READS


def test_a_finished_scan_with_no_rows_does_not_pass_without_an_explicit_empty_scope(tmp_path: Path) -> None:
    from audit.engine.rule_engine.fluent import Table

    class EmptyScanAudit(ExcelAgent):
        DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
        CLAUSE_BOUND_CHECKS = True

        @rule("空表", clause_id="EMPTY")
        def empty(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_execution()
            section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
            rows = list(section.rows_from(section.columns("工序名称")))
            assert rows == []
            return "PASS", "没有工序也通过"

        @rule("写明空范围", clause_id="STATED")
        def stated(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_execution()
            ctx.record_empty_scope("NA", "规则写明没有工序时不适用")
            section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
            list(section.rows_from(section.columns("工序名称")))
            return "PASS", "方法末尾通过"

    path = tmp_path / "empty.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序号", "工序名称", "操作描述"])
    workbook.save(path)
    workbook.close()
    agent = EmptyScanAudit(
        AuditInput(audit_id="target", file_url=str(path)),
        rule_input(("EMPTY", "空表"), ("STATED", "写明空范围")),
    )
    try:
        results = [check.check() for check in agent.checks or ()]
    finally:
        agent.close()

    by_id = {result.clause_id: result for result in results}
    assert by_id["EMPTY"].status == "ERR"
    assert by_id["EMPTY"].empty_scope_basis is None
    assert "空范围不能判为通过" in by_id["EMPTY"].suggestions
    assert (by_id["STATED"].status, by_id["STATED"].suggestions) == ("NA", "规则写明没有工序时不适用")
    assert by_id["STATED"].empty_scope_basis == "规则写明没有工序时不适用"

    from agent.mapping import checker_response
    from agent.protocol import ExecuteRequest, FrozenFile

    body = {"rules": [{"id": "STATED", "category": "写明空范围", "rule_to_check": "规则写明没有工序时不适用"}]}
    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="empty.xlsx",
        sha256="a" * 64,
        rule_version_id="rule-1",
        rule_body=body,
    )
    response = checker_response(
        ExecuteRequest(protocol_revision="test", round_id="round-1", files=[frozen], executor="ppap-deep", executor_revision="rev", job_id="job-1"),
        [(frozen, [by_id["STATED"]])],
    )
    assert response.findings[0].status == "NA"
    assert response.findings[0].locator["semantic"]["empty_scope_basis"] == "规则写明没有工序时不适用"
    assert response.findings[0].locator["semantic"]["scope"] == {"total": 0, "checked": 0, "unknown": 0}
    assert response.coverage.verification is None
    counted = by_id["STATED"].model_copy(update={"checks_performed": 4, "scope": None})
    counted_response = checker_response(
        ExecuteRequest(protocol_revision="test", round_id="round-1", files=[frozen], executor="ppap-deep", executor_revision="rev", job_id="job-1"),
        [(frozen, [counted])],
    )
    assert "scope" not in counted_response.findings[0].locator["semantic"]
