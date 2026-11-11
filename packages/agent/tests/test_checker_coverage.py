import hashlib
import json
from pathlib import Path

from agent.checker_spec import CheckerArtifact
from agent.execution.checker_guest import execute_fixed_checker
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.engine.rule_engine import RuleContext
from audit.engine.runtime import bind_runtime
from audit.kinds import PpapDocumentType
from documents.contracts import ConversionArtifact
from documents.pdf import PdfSpec
from openpyxl import Workbook

PROGRAM = """
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine import RuleContext, Table
from audit.kinds import PpapDocumentType
from documents.excel import load_excel_workbook


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("工序范围", "关联资料")

    @rule("工序范围", clause_id="A")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
        columns = section.columns("工序名称", optional=("工序号", "操作描述"))
        names = [str(row.value("工序名称")).strip() for row in section.rows_from(columns) if row.is_present("工序名称")]
        if names != ["父工序", "子工序", "重复工序"]:
            return "NG", "工序被错误过滤: " + "、".join(names)
        return "PASS", "已保留父工序、子工序和重复工序"

    @rule("关联资料", clause_id="B")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        related = self.get_related_files(PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST)
        if len(related) != 1:
            return "UNABLE", "关联文件没有进入本次检查"
        workbook = load_excel_workbook(related[0].file_url)
        try:
            if "特殊特性清单" not in workbook.sheetnames:
                return "UNABLE", "关联文件缺少特殊特性清单"
        finally:
            workbook.close()
        return "NG", f"关联文件 {related[0].audit_id} 仍有未核对项"


def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""


def save_workbook(path: Path, builder) -> None:
    workbook = builder()
    workbook.save(path)
    workbook.close()


def flow_workbook() -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序号", "工序名称", "操作描述", "产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别", "制造", "运输", "存储", "检验", "返工", "报废"])
    sheet.append(["10", "父工序", "父操作", None, None, None, None, "制造", None, None, None, None, None])
    sheet.append(["10.1", "子工序", "子操作", None, None, None, None, None, None, None, "检验", None, None])
    sheet.append(["10.1", "重复工序", "重复操作", None, None, None, None, "制造", None, None, None, None, None])
    return workbook


def test_fixed_program_keeps_distinct_clauses_hierarchy_and_related_file(tmp_path: Path) -> None:
    target = tmp_path / "flow.xlsx"
    related_path = tmp_path / "list.xlsx"
    save_workbook(target, flow_workbook)
    save_workbook(
        related_path,
        lambda: _sheet("特殊特性清单", ["特性描述"], ["长度"]),
    )
    related = AuditInput(audit_id="related-file", file_url=str(related_path), file_metadata={"file_version_id": "related-version"})
    group = GroupContextInput(
        by_doc_type={PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name(): [related]},
        by_audit_id={related.audit_id: related},
    )
    raw = target.read_bytes()
    audit_file = AuditInput(audit_id="target-file", file_url=str(target), file_metadata={"file_version_id": "target-version"})
    rule = RuleInput(
        rule_id="flow-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "A", "category": "工序范围", "rule_to_check": "父工序、子工序和重复工序都要检查"},
            {"id": "B", "category": "关联资料", "rule_to_check": "特殊特性清单要参与核对"},
        ],
    )
    artifact = CheckerArtifact(
        input_digest=hashlib.sha256(raw).hexdigest(),
        executor_revision="coverage",
        entrypoint="checker:create_audit",
        files={"checker.py": PROGRAM.encode()},
    )

    results = execute_fixed_checker(artifact, audit_file=audit_file, rule=rule, group_context=group, work=tmp_path / "work", digest=artifact.input_digest, executor_revision="coverage")

    assert [(item.clause_id, item.status) for item in results] == [("A", "PASS"), ("B", "NG")]
    assert "子工序" in results[0].suggestions
    assert "related-version" in results[1].suggestions


def _sheet(title: str, header: list[str], row: list[str]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.append(header)
    sheet.append(row)
    return workbook


class PdfAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("转换结果",)
    PDF_SPEC = PdfSpec()

    @rule("转换结果", clause_id="P1")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.record_execution()
        return "PASS", "表格已读取"


class RecordingRuntime:
    def __init__(self, artifact: ConversionArtifact) -> None:
        self.artifact = artifact
        self.spec = None
        self.doc_type = None

    def convert_pdf_to_excel(self, file_ref, *, enable_cache: bool = True, spec=None, doc_type=None) -> ConversionArtifact:
        del file_ref, enable_cache
        self.spec = spec
        self.doc_type = doc_type
        return self.artifact

    def load_workbook_auto(self, file_ref):
        raise AssertionError(file_ref.location)


def pdf_agent(tmp_path: Path, artifact: ConversionArtifact) -> tuple[PdfAudit, RecordingRuntime]:
    runtime = RecordingRuntime(artifact)
    agent = PdfAudit(
        AuditInput(audit_id="pdf-file", file_url=str(tmp_path / "source.pdf"), file_metadata={"file_version_id": "pdf-version"}),
        RuleInput(
            rule_id="pdf-rule",
            rule_profile="v3",
            type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
            rules=[{"id": "P1", "category": "转换结果", "rule_to_check": "转换后的表格"}],
        ),
    )
    return agent, runtime


def test_pdf_conversion_failure_keeps_the_gap_and_is_not_pass(tmp_path: Path) -> None:
    agent, runtime = pdf_agent(
        tmp_path,
        ConversionArtifact(
            success=False,
            strategy_used="manual_linegrid",
            diagnostics={
                "error_code": "manual_linegrid_failed",
                "error": "没有表格",
                "conversion_attempts": [{"backend": "manual_linegrid", "success": False}],
            },
        ),
    )
    with bind_runtime(runtime):
        result = agent.check(0)
    assert runtime.spec is PdfAudit.PDF_SPEC
    assert runtime.doc_type == PpapDocumentType.PROCESS_FLOW_CHART
    assert result is not None
    assert result.status == "ERR"
    assert result.clause_id == "P1"
    assert result.evidence is not None
    assert result.evidence.used == "none"
    assert result.evidence.sources[0].locator == "file:pdf-version"
    assert result.evidence.diagnostics[0].code == "manual_linegrid_failed"
    assert "manual_linegrid" in result.evidence.conversion_strategies


def test_pdf_result_keeps_original_page_and_blocks_pass_when_a_page_is_unlocated(tmp_path: Path) -> None:
    workbook_path = tmp_path / "converted.xlsx"
    save_workbook(workbook_path, lambda: _sheet("过程流程图", ["工序名称"], ["焊接"]))
    located = ConversionArtifact(
        success=True,
        xlsx_path=str(workbook_path),
        strategy_used="manual_linegrid",
        diagnostics={
            "pdf_v2_diagnostics": {"original_pages": [{"page": 3, "success": True, "region": [0, 12, 400, 700], "error": None}]},
            "conversion_attempts": [{"backend": "manual_linegrid", "success": True}],
        },
    )
    agent, runtime = pdf_agent(tmp_path, located)
    with bind_runtime(runtime):
        result = agent.check(0)
    assert result is not None
    assert result.status == "PASS"
    assert result.evidence is not None
    assert result.evidence.sources[0].locator == "file:pdf-version;page:3;region:[0, 12, 400, 700]"

    partial = located.model_copy(
        update={
            "diagnostics": {
                "pdf_v2_diagnostics": {"original_pages": [{"page": 4, "success": True, "region": [None, 10.5, None, 80.25], "error": None}]},
                "conversion_attempts": [{"backend": "manual_linegrid", "success": True}],
            }
        }
    )
    agent._conversion_artifact = None
    agent._workbook_cache = None
    runtime.artifact = partial
    with bind_runtime(runtime):
        located_page = agent.check(0)
    assert located_page is not None
    assert located_page.evidence is not None
    assert located_page.evidence.sources[0].locator == "file:pdf-version;page:4;region:[null, 10.5, null, 80.25]"

    gap = located.model_copy(update={"diagnostics": {"pdf_v2_diagnostics": {"original_pages": [{"page": 3, "success": False, "region": None, "error": "未找到表格区域"}]}}})
    agent._conversion_artifact = None
    agent._workbook_cache = None
    runtime.artifact = gap
    with bind_runtime(runtime):
        blocked = agent.check(0)
    assert blocked is not None
    assert blocked.status == "UNABLE"
    assert blocked.evidence is not None
    assert blocked.evidence.diagnostics[0].code == "page_location_failed"


def test_pdf_unresolvable_region_keeps_the_page_and_does_not_pass(tmp_path: Path) -> None:
    workbook_path = tmp_path / "converted.xlsx"
    save_workbook(workbook_path, lambda: _sheet("过程流程图", ["工序名称"], ["焊接"]))
    artifact = ConversionArtifact(
        success=True,
        xlsx_path=str(workbook_path),
        strategy_used="manual_linegrid",
        diagnostics={
            "pdf_v2_diagnostics": {
                "original_pages": [
                    {"page": 3, "success": True, "region": [float("nan"), 0, 1, 1]},
                    {"page": 4, "success": True, "region": [0, float("inf"), 1, 1]},
                ]
            }
        },
    )
    agent, runtime = pdf_agent(tmp_path, artifact)
    with bind_runtime(runtime):
        result = agent.check(0)
    assert result is not None
    assert result.status == "UNABLE"
    assert "不能判为通过" in result.suggestions
    assert result.evidence is not None
    assert [source.locator for source in result.evidence.sources] == [
        "file:pdf-version;page:3",
        "file:pdf-version;page:4",
    ]
    assert [item.code for item in result.evidence.diagnostics] == ["region_unresolved", "region_unresolved"]

    bad_page = artifact.model_copy(update={"diagnostics": {"pdf_v2_diagnostics": {"original_pages": [{"page": "３", "success": True, "region": [0, 0, 1, 1]}]}}})
    agent._conversion_artifact = None
    agent._workbook_cache = None
    runtime.artifact = bad_page
    with bind_runtime(runtime):
        dropped = agent.check(0)
    assert dropped is not None
    assert dropped.status == "UNABLE"
    assert dropped.evidence is not None
    assert all(";page:" not in source.locator for source in dropped.evidence.sources)
    assert dropped.evidence.diagnostics[0].code == "page_location_failed"


def test_pdf_page_without_success_does_not_pass(tmp_path: Path) -> None:
    workbook_path = tmp_path / "converted.xlsx"
    save_workbook(workbook_path, lambda: _sheet("过程流程图", ["工序名称"], ["焊接"]))
    artifact = ConversionArtifact(
        success=True,
        xlsx_path=str(workbook_path),
        strategy_used="manual_linegrid",
        diagnostics={"pdf_v2_diagnostics": {"original_pages": [{"page": 3, "region": [0, 12, 400, 700]}]}},
    )
    agent, runtime = pdf_agent(tmp_path, artifact)
    with bind_runtime(runtime):
        result = agent.check(0)
    assert result is not None
    assert result.status == "UNABLE"
    assert "不能判为通过" in result.suggestions
    assert result.evidence is not None
    assert result.evidence.diagnostics[0].code == "page_location_failed"


def test_pdf_budget_exhaustion_does_not_pass(tmp_path: Path) -> None:
    workbook_path = tmp_path / "converted.xlsx"
    save_workbook(workbook_path, lambda: _sheet("过程流程图", ["工序名称"], ["焊接"]))
    artifact = ConversionArtifact(
        success=True,
        xlsx_path=str(workbook_path),
        strategy_used="manual_linegrid",
        diagnostics={
            "budget_exhausted": True,
            "pdf_v2_diagnostics": {"original_pages": [{"page": 2, "success": True, "region": [0, 0, 100, 100], "error": None}]},
        },
    )
    agent, runtime = pdf_agent(tmp_path, artifact)
    with bind_runtime(runtime):
        result = agent.check(0)
    assert result is not None
    assert result.status == "UNABLE"
    assert result.clause_id == "P1"
    assert "预算用尽" in result.suggestions
    assert result.evidence is not None
    assert result.evidence.diagnostics[-1].code == "budget_exhausted"
    assert result.evidence.sources[0].locator == "file:pdf-version;page:2;region:[0, 0, 100, 100]"


def test_pdf_page_keeps_a_cell_read_on_the_converted_sheet(tmp_path: Path) -> None:
    from audit.engine.rule_engine import Table

    class PdfWithCell(PdfAudit):
        @rule("转换结果", clause_id="P1")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_execution()
            Table(ctx).sheet("过程流程图").cell(2, 1).value()
            return "PASS", "已读取转换表单元格"

    workbook_path = tmp_path / "converted.xlsx"
    save_workbook(workbook_path, lambda: _sheet("过程流程图", ["工序名称"], ["焊接"]))
    artifact = ConversionArtifact(
        success=True,
        xlsx_path=str(workbook_path),
        diagnostics={"pdf_v2_diagnostics": {"original_pages": [{"page": 3, "success": True, "region": [0, 12, 400, 700], "error": None}]}},
    )
    runtime = RecordingRuntime(artifact)
    agent = PdfWithCell(
        AuditInput(audit_id="pdf-file", file_url=str(tmp_path / "source.pdf"), file_metadata={"file_version_id": "pdf-version"}),
        RuleInput(
            rule_id="pdf-rule",
            rule_profile="v3",
            type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
            rules=[{"id": "P1", "category": "转换结果", "rule_to_check": "转换后的表格"}],
        ),
    )
    with bind_runtime(runtime):
        result = agent.check(0)

    assert result is not None
    assert result.status == "PASS"
    assert result.evidence is not None
    locators = [source.locator for source in result.evidence.sources]
    assert locators[0] == "file:pdf-version;page:3;region:[0, 12, 400, 700]"
    cell = json.loads(locators[1])
    assert cell["kind"] == "xlsx_cell"
    assert cell["sheet"] == "过程流程图"
    assert cell["cell"] == "A2"
    assert cell["file_version_id"] == "pdf-version"


def test_task_pdf_spec_keeps_original_page_map(monkeypatch, tmp_path: Path) -> None:
    from documents.conversion import pipeline

    spec = PdfSpec()
    seen: list[PdfSpec] = []

    def attempt(source: str, output_path: str, *, spec: PdfSpec):
        del source
        seen.append(spec)
        workbook = Workbook()
        workbook.save(output_path)
        workbook.close()
        return None, {"original_pages": [{"page": 2, "success": True, "region": [1, 2, 3, 4], "error": None}]}

    monkeypatch.setattr(pipeline, "_attempt_manual_linegrid", attempt)
    artifact = pipeline.convert_pdf_to_excel_auto(tmp_path / "source.pdf", spec=spec)

    assert seen == [spec]
    assert artifact.success is True
    assert artifact.diagnostics["task_spec"] is True
    assert artifact.diagnostics["pdf_v2_diagnostics"]["original_pages"][0]["page"] == 2


def test_related_pdf_cache_keeps_file_identity_and_resets_per_check(tmp_path: Path, monkeypatch) -> None:
    from audit.engine.rule_engine import Table

    target = tmp_path / "target.xlsx"
    workbook = Workbook()
    workbook.active.title = "过程流程图"
    workbook.active["A1"] = "target"
    workbook.save(target)
    workbook.close()
    related = tmp_path / "related.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "related"
    workbook.save(related)
    workbook.close()
    artifact = ConversionArtifact(
        success=True,
        xlsx_path=str(related),
        strategy_used="manual_linegrid",
        diagnostics={"pdf_v2_diagnostics": {"original_pages": [{"page": 2, "success": False, "error": "未读取完"}]}},
    )
    runtime = RecordingRuntime(artifact)

    class Audit(ExcelAgent):
        CLAUSE_BOUND_CHECKS = True
        PDF_SPECS = {"related-version": PdfSpec()}

        @rule("关联文件", clause_id="A")
        def check_related(self, ctx):
            book = self.load_related_pdf("related-version")
            ctx.bind_workbook(book, "related-version")
            ctx.note_cell(book.active.title, 1, 1, file_version_id="related-version")
            ctx.record_execution()
            assert book.active["A1"].value == "related"
            return "PASS", "关联单元格已读"

        @rule("本文件", clause_id="B")
        def check_target(self, ctx):
            assert Table(ctx).sheet("过程流程图").cell(1, 1).value() == "target"
            return "PASS", "本文件已读"

    reference = AuditInput(audit_id="ref", file_url=str(tmp_path / "ref.pdf"), file_metadata={"file_version_id": "related-version"})
    agent = Audit(
        AuditInput(audit_id="target", file_url=str(target), file_metadata={"file_version_id": "target-version"}),
        RuleInput(
            rule_id="rule",
            rule_profile="v3",
            type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
            rules=[
                {"id": "A", "category": "关联文件", "rule_to_check": "关联文件"},
                {"id": "B", "category": "本文件", "rule_to_check": "本文件"},
            ],
        ),
        GroupContextInput(by_audit_id={"ref": reference}),
    )
    # The controlled boundary is conversion only; both checks use the real context and finalizer.
    from documents.excel import load_excel_workbook

    runtime.load_workbook_auto = lambda ref: load_excel_workbook(ref.location)
    with bind_runtime(runtime):
        first = agent.checks[0].check()
        cached = agent._related_pdf_cache["related-version"][1]
        second = agent.checks[1].check()
        again = agent.checks[0].check()
    assert first.status == again.status == "UNABLE"
    assert any(source.locator.startswith("file:related-version;page:2") for source in first.evidence.sources)
    assert not any(source.locator.startswith("file:target-version;page:") for source in first.evidence.sources)
    assert second.status == "PASS"
    assert "related-version" not in str(second.evidence.sources)
    assert agent._related_pdf_cache["related-version"][1] is cached
    closed = []
    monkeypatch.setattr(cached, "close", lambda: closed.append(True))
    agent.close()
    assert closed == [True]
    assert not agent._related_pdf_cache
