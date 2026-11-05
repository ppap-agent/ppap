import asyncio
import gc
from pathlib import Path

import pytest
from audit.checkers.v3.pdf_excel_delegator_agent import ControlPlanPdfExcelDelegatorAgent, ProcessFlowChartPdfExcelDelegatorAgent
from audit.contracts import (
    AuditInput,
    GroupContextInput,
    RuleInput,
)
from audit.engine.runtime import bind_runtime
from documents.contracts import (
    ConversionArtifact,
    FileRef,
)
from openpyxl import Workbook, load_workbook


class ConversionRuntime:
    def __init__(self, artifact: ConversionArtifact):
        self.artifact = artifact
        self.converted_sources: list[str] = []
        self.loaded_paths: list[str] = []

    def convert_pdf_to_excel(self, file_ref: FileRef, *, enable_cache: bool = True, doc_type=None) -> ConversionArtifact:
        assert enable_cache is True
        self.converted_sources.append(file_ref.location)
        return self.artifact

    def load_workbook_auto(self, file_ref: FileRef) -> Workbook:
        self.loaded_paths.append(file_ref.location)
        return load_workbook(file_ref.location)

    def render_pdf_images(self, file_url: str, *, render_all_pages: bool = True) -> list[str]:
        return []

    def run_image_prompt(self, **kwargs) -> str:
        raise AssertionError("These deterministic rules do not require a model response")


def rules(count: int) -> RuleInput:
    return RuleInput(
        rule_id="confirmed-rules",
        rule_profile="v3",
        type="控制计划",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, count + 1)],
    )


def converted_control_plan(tmp_path: Path) -> Path:
    path = tmp_path / "converted.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "控制计划"
    sheet.append(["零件/工序号", "工序名称", "产品", "过程", "特殊特性分类", "控制方法", "频率"])
    sheet.append(["OP15", "装配", "外观", None, "SC", "作业指导书", "连续/自检"])
    workbook.save(path)
    workbook.close()
    return path


@pytest.mark.parametrize(("rule_count", "expected_count"), [(3, 3), (14, 11)])
def test_successful_conversion_runs_only_confirmed_available_rules(tmp_path, rule_count, expected_count):
    converted = converted_control_plan(tmp_path)
    runtime = ConversionRuntime(ConversionArtifact(success=True, xlsx_path=str(converted)))
    with bind_runtime(runtime):
        agent = ControlPlanPdfExcelDelegatorAgent(AuditInput(audit_id="plan", file_url="plan.pdf"), rules(rule_count))

        results = asyncio.run(agent.run())

    assert len(results) == expected_count
    assert all(result.status in {"PASS", "NG"} for result in results)
    assert runtime.converted_sources == ["plan.pdf"]
    assert runtime.loaded_paths == [str(converted)]


def test_failed_conversion_returns_an_error_for_each_confirmed_rule():
    runtime = ConversionRuntime(ConversionArtifact(success=False))
    with bind_runtime(runtime):
        agent = ControlPlanPdfExcelDelegatorAgent(AuditInput(audit_id="plan", file_url="plan.pdf"), rules(4))

        results = asyncio.run(agent.run())

    assert len(results) == 4
    assert all(result.status == "ERR" and "conversion failed" in result.suggestions for result in results)
    assert runtime.loaded_paths == []


def test_converted_file_and_auxiliary_artifact_are_cleaned_up(tmp_path):
    converted = converted_control_plan(tmp_path)
    auxiliary = tmp_path / "conversion.html"
    auxiliary.touch()
    runtime = ConversionRuntime(ConversionArtifact(success=True, xlsx_path=str(converted), temp_files=[str(auxiliary)]))
    with bind_runtime(runtime):
        agent = ControlPlanPdfExcelDelegatorAgent(AuditInput(audit_id="plan", file_url="plan.pdf"), rules(1))
        assert asyncio.run(agent.run())
        assert converted.exists()
        assert auxiliary.exists()

        del agent
        gc.collect()

    assert not converted.exists()
    assert not auxiliary.exists()


@pytest.mark.parametrize(("operation", "expected_status"), [("OP15", "PASS"), ("OP99", "NG")])
def test_pdf_cross_file_check_uses_the_submitted_group(tmp_path, operation, expected_status):
    converted = converted_control_plan(tmp_path)
    flow_path = tmp_path / "flow.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序号", "工序名称"])
    sheet.append([operation, "装配"])
    workbook.save(flow_path)
    workbook.close()
    flow = AuditInput(audit_id="flow", file_url=str(flow_path))
    group = GroupContextInput(task_group_id="submitted-group", by_doc_type={"过程流程图": [flow]}, by_audit_id={"flow": flow})
    runtime = ConversionRuntime(ConversionArtifact(success=True, xlsx_path=str(converted)))
    with bind_runtime(runtime):
        agent = ControlPlanPdfExcelDelegatorAgent(AuditInput(audit_id="plan", file_url="plan.pdf"), rules(11), group_context=group)

        result = agent.check(1)

    assert result is not None
    assert result.status == expected_status
    assert str(flow_path) in runtime.loaded_paths


def test_process_flow_pdf_keeps_the_original_page_on_the_delegated_result(tmp_path):
    path = tmp_path / "converted.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序名称"])
    sheet.append(["焊接"])
    workbook.save(path)
    workbook.close()
    runtime = ConversionRuntime(
        ConversionArtifact(
            success=True,
            xlsx_path=str(path),
            diagnostics={"pdf_v2_diagnostics": {"original_pages": [{"page": 4, "success": True, "region": [1, 2, 3, 4], "error": None}]}},
        )
    )
    with bind_runtime(runtime):
        agent = ProcessFlowChartPdfExcelDelegatorAgent(
            AuditInput(audit_id="flow", file_url="flow.pdf", file_metadata={"file_version_id": "pdf-version"}),
            RuleInput(
                rule_id="flow-rule",
                rule_profile="v3",
                type="过程流程图",
                rules=[{"id": "1", "category": "表头字段完整性检查", "rule_to_check": "表头字段要完整"}],
            ),
        )
        result = agent.check(0)

    assert result is not None
    assert result.evidence is not None
    assert result.evidence.sources[0].locator == "file:pdf-version;page:4;region:[1, 2, 3, 4]"
    assert result.status in {"NG", "UNABLE", "PASS"}
    assert agent.checks
    bound = agent.checks[0].check()
    assert bound.clause_id == "1"
    assert bound.evidence is not None
    assert bound.evidence.sources[0].locator == "file:pdf-version;page:4;region:[1, 2, 3, 4]"
    ran = asyncio.run(agent.run())
    assert [item.clause_id for item in ran] == [item.rule_item.id for item in agent.checks]
    assert ran[0].evidence is not None
    assert ran[0].evidence.sources[0].locator == "file:pdf-version;page:4;region:[1, 2, 3, 4]"
    last = agent.check(len(agent.checks) - 1)
    assert last is not None
    assert last.clause_id == agent.checks[-1].rule_item.id
    assert last.evidence is not None
    assert last.evidence.sources[0].locator == "file:pdf-version;page:4;region:[1, 2, 3, 4]"
