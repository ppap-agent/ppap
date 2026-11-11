"""Cross-owner checker, task-view and API contract regression tests."""

import hashlib
import json
from pathlib import Path

from agent.protocol import ExecuteRequest, FrozenFile
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.contracts import AuditInput, RuleInput
from audit.engine.rule_engine import RuleContext
from audit.engine.runtime import bind_runtime
from audit.kinds import PpapDocumentType
from documents.contracts import ConversionArtifact
from documents.pdf import PdfSpec
from openpyxl import Workbook


class PdfAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("转换结果",)
    PDF_SPEC = PdfSpec()

    @rule("转换结果", clause_id="P1")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.record_execution()
        return "PASS", "表格已读取"


def save_workbook(path: Path, builder) -> None:
    workbook = builder()
    workbook.save(path)
    workbook.close()


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


def _sheet(title: str, header: list[str], row: list[str]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.append(header)
    sheet.append(row)
    return workbook


def _frozen_target(raw: bytes, clauses: list[dict]) -> "FrozenFile":
    from agent.protocol import FrozenFile

    return FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256=hashlib.sha256(raw).hexdigest(),
        rule_version_id="same-rule",
        rule_body={
            "rule_profile": "v3",
            "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
            "rules": clauses,
        },
    )


def request() -> ExecuteRequest:
    return ExecuteRequest(protocol_revision="test", round_id="round-1", files=[], executor="ppap-deep", executor_revision=REVISION, job_id="job-1")


REVISION = "checker-test-revision"


def test_pdf_page_keeps_a_related_file_cell(tmp_path: Path) -> None:
    class PdfWithRelated(PdfAudit):
        @rule("转换结果", clause_id="P1")
        def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
            ctx.record_execution()
            from openpyxl import load_workbook

            opened = load_workbook(related_path)
            try:
                ctx.bind_workbook(opened, "related-version")
                ctx.note_cell("特殊特性清单", 2, 2, file_version_id="related-version", excerpt="伪造摘录")
            finally:
                opened.close()
            return "PASS", "已读取原件和关联清单"

    related_path = tmp_path / "list.xlsx"
    save_workbook(related_path, lambda: _sheet("特殊特性清单", ["特性描述", "特性值"], ["长度", "10mm"]))
    workbook_path = tmp_path / "converted.xlsx"
    save_workbook(workbook_path, lambda: _sheet("过程流程图", ["工序名称"], ["焊接"]))
    artifact = ConversionArtifact(
        success=True,
        xlsx_path=str(workbook_path),
        diagnostics={"pdf_v2_diagnostics": {"original_pages": [{"page": 3, "success": True, "region": [0, 12, 400, 700], "error": None}]}},
    )
    runtime = RecordingRuntime(artifact)
    agent = PdfWithRelated(
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
    related = json.loads(locators[1])
    assert related == {"kind": "xlsx_cell", "sheet": "特殊特性清单", "cell": "B2", "file_version_id": "related-version"}

    from agent.mapping import checker_response, product_result
    from agent.protocol import ExecuteRequest, FrozenFile
    from audit.audits import RoundInput, RoundWork
    from product.store.task_view import task_round_view

    body = {"rules": [{"id": "P1", "category": "转换结果", "rule_to_check": "转换后的表格"}]}
    frozen = FrozenFile(
        file_version_id="pdf-version",
        file_id="file-pdf",
        role="target",
        filename="source.pdf",
        sha256="a" * 64,
        rule_version_id="pdf-rule",
        rule_body=body,
    )
    reference = FrozenFile(
        file_version_id="related-version",
        file_id="file-related",
        role="reference",
        filename="list.xlsx",
        sha256="b" * 64,
        rule_version_id="related-rule",
        rule_body={"type": "过程特殊特性清单"},
    )
    spec = ExecuteRequest(
        protocol_revision="test",
        round_id="round-1",
        files=[frozen, reference],
        executor="ppap-deep",
        executor_revision="rev",
        job_id="job-1",
    )
    response = checker_response(spec, [(frozen, [result])])
    stored = product_result(
        response,
        RoundWork(
            round_id=spec.round_id,
            tenant_id=spec.tenant_id,
            inputs=[
                RoundInput(
                    role="target",
                    file_id=frozen.file_id,
                    file_version_id=frozen.file_version_id,
                    filename=frozen.filename,
                    status="ready",
                    sha256=frozen.sha256,
                    rule_version_id=frozen.rule_version_id,
                    rule_body=body,
                ),
                RoundInput(
                    role="reference",
                    file_id=reference.file_id,
                    file_version_id=reference.file_version_id,
                    filename=reference.filename,
                    status="ready",
                    sha256=reference.sha256,
                    rule_version_id=reference.rule_version_id,
                    rule_body=reference.rule_body,
                ),
            ],
        ),
    )
    viewed = task_round_view(
        {"id": "task-1", "files": [{"file": {"id": "pdf-version"}, "selection": "selected"}]},
        {
            "id": spec.round_id,
            "execution": "completed",
            "result_attempt": 1,
            "analysis_mode": "deep",
            "executor": "ppap-deep",
            "executor_revision": "rev",
            "result_job_id": "job-1",
            "coverage": stored["coverage"],
            "findings": stored["findings"],
            "inputs": [
                {
                    "role": "target",
                    "file_version_id": "pdf-version",
                    "rule": {"id": "pdf-rule", "title": "规则", "body": body},
                },
                {"role": "reference", "file_version_id": "related-version", "rule": None},
            ],
        },
    )
    check = viewed["files"][0]["audit"]["checks"][0]

    assert stored["coverage"]["verification"] is None
    assert check["verification_scope"] == "checker_execution"
    assert check["evidence_verified"] is False
    assert [(item["file_version_id"], item["locator"], item["location_gap"]) for item in check["evidence"]] == [
        ("pdf-version", {"kind": "pdf_page", "page": 3, "region": [0, 12, 400, 700]}, None),
        ("related-version", {"kind": "xlsx_cell", "sheet": "特殊特性清单", "cell": "B2"}, None),
    ]


def test_an_unfrozen_source_does_not_invalidate_the_clause_or_become_pass() -> None:
    from agent.mapping import checker_response
    from agent.protocol import FrozenFile
    from api.web_contract import Finding
    from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource
    from pydantic import TypeAdapter

    body = {"rules": [{"id": "R9", "category": "跨文件", "rule_to_check": "特性在两份文件中一致"}]}
    target = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256="a" * 64,
        rule_version_id="same-rule",
        rule_body=body,
    )

    def response_for(status: str, locator: str):
        result = AuditResultItem(
            classification="过程流程图",
            item="跨文件",
            priority="P1",
            status=status,
            suggestions="已比较",
            clause_id="R9",
            checks_performed=1,
            evidence=AuditEvidence(
                required="structure",
                used="structure",
                source_format="xlsx",
                prepared_format="xlsx",
                sources=(EvidenceSource(kind="structure", locator=locator),),
            ),
        )
        return checker_response(request().model_copy(update={"files": [target]}), [(target, [result])])

    outside = response_for("NG", '{"kind":"xlsx_cell","sheet":"特殊特性清单","cell":"B2","file_version_id":"outside"}')
    finding = outside.findings[0]
    assert finding.status == "NG"
    assert finding.suggestions == "已比较"
    assert finding.locator["semantic"]["sources"] == []
    assert finding.locator["semantic"]["evidence_incomplete"] is True

    mixed = AuditResultItem(
        classification="过程流程图",
        item="跨文件",
        priority="P1",
        status="PASS",
        suggestions="已比较",
        clause_id="R9",
        checks_performed=1,
        evidence=AuditEvidence(
            required="structure",
            used="structure",
            source_format="xlsx",
            prepared_format="xlsx",
            sources=(
                EvidenceSource(kind="structure", locator='{"kind":"xlsx_cell","sheet":"过程流程图","cell":"A2","file_version_id":"version-1"}'),
                EvidenceSource(kind="structure", locator='{"kind":"xlsx_cell","sheet":"特殊特性清单","cell":"B2","file_version_id":"outside"}'),
            ),
        ),
    )
    mixed_finding = checker_response(request().model_copy(update={"files": [target]}), [(target, [mixed])]).findings[0]
    assert mixed_finding.status == "UNABLE"
    assert "原件定位不在本次冻结文件中" in mixed_finding.suggestions
    assert mixed_finding.locator["semantic"]["sources"] == [
        {
            "file_version_id": "version-1",
            "filename": "target.xlsx",
            "role": "target",
            "kind": "structure",
            "locator": {"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A2"},
            "excerpt": "",
        }
    ]
    assert mixed_finding.locator["semantic"]["evidence_incomplete"] is True
    TypeAdapter(Finding).validate_python({**finding.model_dump(), "review": None})

    unread = response_for("PASS", "not-a-locator")
    blocked = unread.findings[0]
    assert blocked.status == "UNABLE"
    assert "原件定位不在本次冻结文件中" in blocked.suggestions
    assert blocked.locator["semantic"]["sources"] == []
    TypeAdapter(Finding).validate_python({**blocked.model_dump(), "review": None})

    for raw in (
        '{"kind":"xlsx_cell","sheet":"过程流程图","cell":"A0"}',
        '{"kind":"xlsx_range","sheet":"过程","start":"a1","end":"B2"}',
        '{"kind":"docx_paragraph","paragraph":0}',
        '{"kind":"pdf_block","page":1,"block":0}',
        '{"kind":"docx_cell","table":0,"row":1,"column":1}',
    ):
        dropped = response_for("PASS", raw).findings[0]
        assert dropped.status == "UNABLE"
        assert dropped.locator["semantic"]["sources"] == []
        assert "原件定位不在本次冻结文件中" in dropped.suggestions

    kept = {
        '{"kind":"docx_cell","table":1,"row":2,"column":3}': {"kind": "docx_cell", "table": 1, "row": 2, "column": 3},
        '{"kind":"xlsx_range","sheet":"过程","start":"A1","end":"B2"}': {"kind": "xlsx_range", "sheet": "过程", "start": "A1", "end": "B2"},
        '{"kind":"pdf_cell","page":2,"row":1,"column":4}': {"kind": "pdf_cell", "page": 2, "row": 1, "column": 4},
        '{"kind":"docx_paragraph","paragraph":5}': {"kind": "docx_paragraph", "paragraph": 5},
    }
    for raw, expected in kept.items():
        found = response_for("NG", raw).findings[0]
        assert found.status == "NG"
        assert found.locator["semantic"]["sources"][0]["locator"] == expected
        assert found.locator["semantic"]["sources"][0]["filename"] == "target.xlsx"
        TypeAdapter(Finding).validate_python({**found.model_dump(), "review": None})


def test_checker_results_keep_clause_status_in_the_task_view_and_public_contract() -> None:
    import json

    from agent.mapping import checker_response, product_result
    from api.web_contract import AuditClauseEvaluation, AuditFileReview, Coverage, Finding, Round
    from audit.audits import RoundInput, RoundWork
    from audit.contracts import AuditEvidence, AuditResultItem, EvidenceDiagnostic, EvidenceSource
    from product.store.task_view import task_round_view
    from pydantic import TypeAdapter

    clauses = [
        {"id": "A", "category": "表头", "priority": "P1", "rule_to_check": "表头含义需要确认", "type_of_rule": "客户规则"},
        {"id": "B", "category": "比较", "priority": "P1", "rule_to_check": "比较已有反例", "type_of_rule": "客户规则"},
        {"id": "C", "category": "漏项", "priority": "P1", "rule_to_check": "这项没有结果", "type_of_rule": "客户规则"},
    ]
    frozen = _frozen_target(b"target-bytes", clauses)
    unable = AuditResultItem(
        classification="客户规则",
        item="表头含义需要确认",
        priority="P1",
        status="UNABLE",
        suggestions="含义未确认",
        clause_id="A",
        checks_performed=1,
        clarifications=[
            {
                "id": "header-meaning",
                "question": "表头按什么理解？",
                "clause_id": "A",
                "candidates": [
                    {"id": "left", "label": "左列", "explanation": "按左列表头理解"},
                    {"id": "right", "label": "右列", "explanation": "按右列表头理解"},
                ],
                "sources": [],
            }
        ],
        evidence=AuditEvidence(
            required="structure",
            used="structure",
            source_format="xlsx",
            prepared_format="xlsx",
            sources=(EvidenceSource(kind="structure", locator=json.dumps({"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A1", "file_version_id": "version-1"}, ensure_ascii=False)),),
            diagnostics=(EvidenceDiagnostic(code="read_gap", stage="evaluation", severity="warning", message="表头有两种读法"),),
        ),
    )
    duplicate = AuditResultItem(classification="客户规则", item="第二次", priority="P1", status="NG", suggestions="重复结果", clause_id="A")
    compared = AuditResultItem(classification="客户规则", item="比较已有反例", priority="P1", status="NG", suggestions="已比较", clause_id="B", checks_performed=1)
    response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [unable, duplicate, compared])])
    stored = product_result(
        response,
        RoundWork(
            round_id="round-1",
            tenant_id="",
            inputs=[
                RoundInput(
                    role="target",
                    file_id="file-1",
                    file_version_id="version-1",
                    filename="target.xlsx",
                    status="ready",
                    sha256=hashlib.sha256(b"target-bytes").hexdigest(),
                    rule_version_id="same-rule",
                    rule_body=frozen.rule_body,
                )
            ],
        ),
    )
    for finding in stored["findings"]:
        finding["review"] = None
    source = next(item for item in stored["findings"] if item["locator"]["semantic"]["clause_id"] == "A" and item["locator"]["semantic"].get("binding") != "duplicate")
    assert source["locator"]["semantic"]["sources"][0]["filename"] == "target.xlsx"
    assert source["locator"]["semantic"]["sources"][0]["role"] == "target"
    assert source["locator"]["semantic"]["sources"][0]["excerpt"] == ""
    assert "proof_kind" not in source["locator"]["semantic"]
    assert "scope" not in source["locator"]["semantic"]
    assert stored["coverage"]["verification"] is None
    assert stored["coverage"]["clarifications"][0]["id"] == "header-meaning"

    public_findings = [TypeAdapter(Finding).validate_python(item) for item in stored["findings"]]
    public_coverage = TypeAdapter(Coverage).validate_python(stored["coverage"])
    assert [item["status"] for item in public_findings] == ["UNABLE", "NG", "ERR", "NG"]
    assert public_coverage["clarifications"][0]["rule_ids"] == ["A"]
    assert public_coverage["rule_items"][0]["verification_scope"] == "checker_execution"
    assert public_coverage["verification"] is None
    TypeAdapter(Round).validate_python(
        {
            "id": "round-1",
            "execution": "completed",
            "coverage": stored["coverage"],
            "predecessor_id": None,
            "source_draft_id": None,
            "result_attempt": 1,
            "created_at": "2026-11-01T00:00:00Z",
            "analysis_mode": "deep",
            "writeback": {"available": False},
            "progress": None,
            "failure": None,
            "inputs": [
                {
                    "file_id": "file-1",
                    "file_version_id": "version-1",
                    "original_filename": "target.xlsx",
                    "role": "target",
                    "rule_version_id": "same-rule",
                    "rule": None,
                    "status": "ready",
                }
            ],
            "findings": stored["findings"],
        }
    )

    viewed = task_round_view(
        {"id": "task-1", "files": [{"file": {"id": "version-1"}, "selection": "selected"}]},
        {
            "id": "round-1",
            "execution": "completed",
            "result_attempt": 1,
            "failure": None,
            "coverage": stored["coverage"],
            "findings": stored["findings"],
            "inputs": [
                {
                    "role": "target",
                    "file_version_id": "version-1",
                    "rule": {
                        "id": "same-rule",
                        "title": "客户规则",
                        "body": {
                            "kind": "engine",
                            "type": "过程流程图",
                            "rule_profile": "v3",
                            "customers": [],
                            "models": [],
                            "parts": [],
                            "rules": clauses,
                        },
                    },
                }
            ],
        },
    )
    audit = viewed["files"][0]["audit"]
    by_id = {check["rule_item"]["id"]: check for check in audit["checks"]}
    assert by_id["A"]["finding"]["status"] == "UNABLE"
    assert by_id["A"]["evidence_verified"] is False
    assert by_id["A"]["verification_scope"] == "checker_execution"
    assert by_id["A"]["scope_notice"] == "这是固定程序的执行结果，不是 trace 证明。已执行 1 次断言。诊断：read_gap：表头有两种读法。"
    assert by_id["B"]["finding"]["status"] == "NG"
    assert by_id["C"]["finding"]["status"] == "ERR"
    assert "本项未执行" in by_id["C"]["finding"]["suggestions"]
    assert [(item["status"], item["locator"]["rule_item_id"]) for item in audit["unmapped_findings"]] == [("NG", "A")]
    for check in audit["checks"]:
        TypeAdapter(AuditClauseEvaluation).validate_python(check)
    TypeAdapter(AuditFileReview).validate_python(audit)
