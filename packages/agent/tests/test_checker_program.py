import hashlib
import json
import time
from pathlib import Path

import pytest
from agent.checker_program import adapt, execute, input_digest
from agent.checker_spec import CheckerArtifact
from agent.client import DeepError
from agent.execution.guest_model import ModelProtocolError
from agent.protocol import ExecuteRequest, FrozenFile
from audit.contracts import AuditInput, RuleInput
from audit.kinds import PpapDocumentType
from openpyxl import Workbook

REVISION = "checker-test-revision"
VALID_PROGRAM = """
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.kinds import PpapDocumentType


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("工序名称",)

    @rule("工序名称", clause_id="R4")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        return "UNABLE", "未确定工序范围"


def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""
RESOURCE_FILE = """
from types import MappingProxyType

from audit.engine.rule_engine.contracts import ColumnModel, DomainTokensModel, RuleResource

COLUMN = ColumnModel(
    canonical_name="任务列",
    section_name="过程流程主数据区",
    aliases=("任务别名",),
)
RESOURCE = RuleResource(
    sheets=MappingProxyType({}),
    sections=MappingProxyType({}),
    columns=MappingProxyType({COLUMN.canonical_name: COLUMN}),
    field_blocks=MappingProxyType({}),
    fields=MappingProxyType({}),
    tokens=DomainTokensModel(),
)
"""
RESOURCE_CHECKER = """
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.kinds import PpapDocumentType


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("工序名称",)

    @rule("工序名称", clause_id="R4")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        columns = ctx.resource.columns
        task = columns["任务列"]
        registry = columns["工序名称"]
        same_context = "过程流程主数据区" in ctx.resource.sections
        text = f"registry={same_context};registry_alias={registry.aliases[0]};column={task.canonical_name};alias={task.aliases[0]};section={task.section_name}"
        return "UNABLE", text


def create_audit(audit_file, rule, group_context=None):
    from resources import RESOURCE

    return TaskAudit(audit_file, rule, group_context, resource=RESOURCE)
"""


def workbook_bytes(path: Path) -> bytes:
    workbook = Workbook()
    workbook.save(path)
    workbook.close()
    return path.read_bytes()


def rules() -> RuleInput:
    return RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    )


def audit_file(path: Path) -> AuditInput:
    return AuditInput(audit_id="file-1", file_url=str(path), file_metadata={"file_version_id": "version-1"})


class ScriptedDeveloper:
    def __init__(self, drafts: list[dict]) -> None:
        self.drafts = list(drafts)
        self.prompts: list[str] = []
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.last: dict | None = None

    def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
        self.calls += 1
        self.prompts.append(prompt)
        if not self.drafts:
            if self.last is not None and "这些结果只说明程序跑完，不说明业务判断正确。" in prompt:
                return schema.model_validate(self.last)
            raise ModelProtocolError("no checker draft")
        self.last = self.drafts.pop(0)
        return schema.model_validate(self.last)


class Checker:
    """Develop then execute one fixed program through checker_program.adapt/execute."""

    def __init__(self, developer, root: Path) -> None:
        self.developer, self.root, self.runs = developer, root, 0

    def _args(self, raw: bytes, role: str) -> dict:
        self.runs += 1
        return dict(file_bytes=raw, file_sha256=hashlib.sha256(raw).hexdigest(), role=role, executor_revision=REVISION)

    def adapt_checker(self, target: AuditInput, rule: RuleInput, raw: bytes, *, role: str = "target", group_context=None, prior=None) -> CheckerArtifact:
        args = self._args(raw, role)
        return adapt(target, rule, group_context, runner=self.developer, work=self.root / f"adapt-{self.runs}", deadline=time.monotonic() + 30, prior=prior, **args)

    def execute_checker(self, artifact: CheckerArtifact, target: AuditInput, rule: RuleInput, raw: bytes, *, role: str = "target", group_context=None):
        args = self._args(raw, role)
        return execute(artifact, target, rule, group_context, work=self.root / f"execute-{self.runs}", **args)


def request() -> ExecuteRequest:
    return ExecuteRequest(protocol_revision="test", round_id="round-1", files=[], executor="ppap-deep", executor_revision=REVISION, job_id="job-1")


def broken_draft() -> dict:
    return {"entrypoint": "checker:create_audit", "files": {"checker.py": "def unused():\n    return None\n"}}


def valid_draft() -> dict:
    return {"entrypoint": "checker:create_audit", "files": {"checker.py": VALID_PROGRAM}}


def test_input_digest_includes_file_version_and_role_not_only_the_rule_id(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    target = audit_file(path)
    rule = rules().model_copy(update={"appendix": [{"id": "note-1", "text": "空值不是通过"}]})
    base = input_digest(target, rule, file_sha256=hashlib.sha256(raw).hexdigest(), role="target")
    other_file = input_digest(target, rule, file_sha256=hashlib.sha256(b"other-bytes").hexdigest(), role="target")
    other_role = input_digest(target, rule, file_sha256=hashlib.sha256(raw).hexdigest(), role="reference")
    assert base != other_file
    assert base != other_role
    assert rule.rule_id == "same-rule"


def test_rule_appendix_content_and_removal_change_the_input_identity(tmp_path: Path) -> None:
    target = audit_file(tmp_path / "target.xlsx")
    original = rules()
    confirmed = original.model_copy(update={"appendix": [{"id": "note-1", "text": "空值不是通过"}]})
    edited = confirmed.model_copy(update={"appendix": [{"id": "note-1", "text": "只检查指定工序"}]})
    removed = confirmed.model_copy(update={"appendix": []})

    def digest(rule: RuleInput) -> str:
        return input_digest(target, rule, file_sha256="a" * 64, role="target")

    assert digest(confirmed) != digest(original)
    assert digest(edited) != digest(confirmed)
    assert digest(removed) != digest(confirmed)
    assert digest(removed) == digest(original)


def test_deep_adapts_a_checker_then_executes_only_the_fixed_program(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper([broken_draft(), valid_draft()])
    host = Checker(developer, tmp_path)
    prior = CheckerArtifact(input_digest="a" * 64, executor_revision="old", entrypoint="checker:create_audit", files={"checker.py": b"# PRIOR-START\n"})
    artifact = host.adapt_checker(audit_file(path), rules(), raw, prior=prior)

    assert developer.calls == 3
    assert "缺少 create_audit" in developer.prompts[1]
    assert "这些结果只说明程序跑完，不说明业务判断正确。" in developer.prompts[2]
    assert "R4|UNABLE" in developer.prompts[2]
    assert "PRIOR-START" in developer.prompts[0]
    assert artifact.entrypoint == "checker:create_audit"

    calls_after_adapt = developer.calls
    results = host.execute_checker(artifact, audit_file(path), rules(), raw)
    assert developer.calls == calls_after_adapt
    assert [(item.clause_id, item.status) for item in results] == [("R4", "UNABLE")]
    assert "未确定工序范围" in results[0].suggestions


def test_execute_checker_rejects_identity_changes_before_running(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    host = Checker(ScriptedDeveloper([valid_draft()]), tmp_path)
    artifact = host.adapt_checker(audit_file(path), rules(), raw)
    monkeypatch.setattr("agent.checker_program.execute_fixed_checker", lambda *args, **kwargs: pytest.fail("fixed program ran"))

    with pytest.raises(DeepError) as caught:
        host.execute_checker(artifact, audit_file(path), rules(), raw, role="reference")
    assert caught.value.code == "deep_output_identity_mismatch"

    changed = rules()
    changed.rules[0]["rule_to_check"] = "另一项要求"
    with pytest.raises(DeepError) as caught:
        host.execute_checker(artifact, audit_file(path), changed, raw)
    assert caught.value.code == "deep_output_identity_mismatch"

    with pytest.raises(DeepError) as caught:
        host.execute_checker(artifact, audit_file(path), rules(), b"different-bytes")
    assert caught.value.code == "deep_output_identity_mismatch"

    moved = artifact.model_copy(update={"executor_revision": "other-revision"})
    with pytest.raises(DeepError) as caught:
        host.execute_checker(moved, audit_file(path), rules(), raw)
    assert caught.value.code == "deep_output_identity_mismatch"


def test_a_swapped_program_does_not_execute_under_the_same_input_digest(tmp_path: Path) -> None:
    from agent.checker_spec import program_digest
    from agent.execution.checker_guest import CheckerProgramError, execute_fixed_checker
    from pydantic import ValidationError

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    artifact = Checker(ScriptedDeveloper([valid_draft()]), tmp_path).adapt_checker(audit_file(path), rules(), raw)
    assert artifact.program_digest == program_digest(dict(artifact.files))
    swapped = artifact.model_copy(update={"files": {"checker.py": b"def create_audit(audit_file, rule, group_context=None):\n    return None\n"}})
    with pytest.raises(CheckerProgramError, match="程序摘要"):
        execute_fixed_checker(
            swapped,
            audit_file=audit_file(path),
            rule=rules(),
            group_context=None,
            work=tmp_path / "run",
            digest=artifact.input_digest,
            executor_revision=artifact.executor_revision,
        )
    with pytest.raises(ValidationError, match="program digest"):
        CheckerArtifact(
            input_digest=artifact.input_digest,
            executor_revision=artifact.executor_revision,
            entrypoint="checker:create_audit",
            files=dict(artifact.files),
            program_digest="b" * 64,
        )


def test_zero_assertions_stay_err_even_when_a_source_is_present() -> None:
    from agent.mapping import checker_response
    from agent.protocol import FrozenFile
    from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256="a" * 64,
        rule_version_id="same-rule",
        rule_body={"rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}]},
    )
    result = AuditResultItem(
        classification="规则审核",
        item="工序名称",
        priority="P1",
        status="PASS",
        suggestions="只附了一个来源",
        clause_id="R4",
        checks_performed=0,
        evidence=AuditEvidence(
            required="structure",
            used="structure",
            source_format="xlsx",
            prepared_format="xlsx",
            sources=(EvidenceSource(kind="structure", locator='{"kind":"xlsx_cell","sheet":"过程流程图","cell":"A2","file_version_id":"version-1"}'),),
        ),
    )
    response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [result])])

    assert response.findings[0].status == "ERR"
    assert "零断言不能判为通过" in response.findings[0].suggestions
    assert response.coverage.verification is None
    empty = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [result.model_copy(update={"suggestions": "", "evidence": None})])])
    assert empty.findings[0].status == "ERR"
    assert "空结果不能判为通过" in empty.findings[0].suggestions


def test_a_pending_question_cannot_reach_the_product_as_pass_or_not_applicable() -> None:
    from agent.mapping import checker_response
    from agent.protocol import FrozenFile
    from audit.contracts import AuditResultItem

    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256="a" * 64,
        rule_version_id="same-rule",
        rule_body={"rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}]},
    )
    question = {
        "id": "stamp",
        "question": "印章按哪种理解？",
        "clause_id": "R4",
        "candidates": [
            {"id": "round", "label": "圆章", "explanation": "按圆形印章理解"},
            {"id": "square", "label": "方章", "explanation": "按方形印章理解"},
        ],
        "sources": [
            {"document_id": "version-1", "locator": {"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A2"}, "excerpt": "焊接"},
            {"document_id": "other-file", "locator": {"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A2"}, "excerpt": "不属于本轮"},
            {"document_id": "version-1", "locator": {"kind": "note", "text": "不是定位"}, "excerpt": "无效"},
        ],
    }

    def mapped(status: str, *, basis: str | None = None):
        result = AuditResultItem(
            classification="规则审核",
            item="工序名称",
            priority="P1",
            status=status,
            suggestions="方法给出了结论",
            clause_id="R4",
            checks_performed=1,
            empty_scope_basis=basis,
            clarifications=[question],
        )
        return checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [result])])

    passed = mapped("PASS", basis="规则写明没有工序即通过")
    assert passed.findings[0].status == "UNABLE"
    assert "含义未确认，不能判为通过" in passed.findings[0].suggestions
    assert passed.coverage.clarifications[0]["id"] == "stamp"
    assert passed.coverage.clarifications[0]["sources"] == [{"document_id": "version-1", "locator": {"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A2"}, "excerpt": "焊接"}]

    skipped = mapped("NA")
    assert skipped.findings[0].status == "UNABLE"
    assert "含义未确认，不能判为不适用" in skipped.findings[0].suggestions
    assert skipped.coverage.clarifications[0]["id"] == "stamp"


def test_related_file_identity_ignores_the_host_path(tmp_path: Path) -> None:
    from agent.checker_program import input_digest
    from audit.contracts import GroupContextInput

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    target = audit_file(path)
    rule = rules()
    host_path = tmp_path / "HOST-ONLY-PATH.xlsx"
    other_path = tmp_path / "other-host.xlsx"
    metadata = {"file_version_id": "related-1", "sha256": "a" * 64, "role": "reference"}
    first = GroupContextInput(by_audit_id={"related-1": AuditInput(audit_id="related-1", file_url=str(host_path), file_metadata=metadata)})
    second = GroupContextInput(by_audit_id={"related-1": AuditInput(audit_id="related-1", file_url=str(other_path), file_metadata=metadata)})
    changed = GroupContextInput(by_audit_id={"related-1": AuditInput(audit_id="related-1", file_url=str(host_path), file_metadata={**metadata, "sha256": "b" * 64})})
    base = input_digest(target, rule, file_sha256=hashlib.sha256(raw).hexdigest(), role="target", group_context=first)
    assert base == input_digest(target, rule, file_sha256=hashlib.sha256(raw).hexdigest(), role="target", group_context=second)
    assert base != input_digest(target, rule, file_sha256=hashlib.sha256(raw).hexdigest(), role="target", group_context=changed)
    assert str(host_path) not in base


def test_reference_rules_enter_the_checker_without_replacing_the_target_rule(tmp_path: Path) -> None:
    from audit.contracts import GroupContextInput

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    related = tmp_path / "special.xlsx"
    related.write_bytes(b"related-rules")
    target = AuditInput(audit_id="version-1", file_url=str(path), file_metadata={"file_version_id": "version-1", "sha256": hashlib.sha256(raw).hexdigest(), "role": "target"})
    reference = AuditInput(audit_id="related-1", file_url=str(related), file_metadata={"file_version_id": "related-1", "sha256": hashlib.sha256(b"related-rules").hexdigest(), "role": "reference"})
    reference_rule = RuleInput(
        rule_id="related-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name(),
        rules=[{"id": "S1", "category": "特性", "rule_to_check": "长度必须标注单位"}],
    )
    group = GroupContextInput(by_audit_id={"version-1": target, "related-1": reference}, rules_by_id={"version-1": rules(), "related-1": reference_rule})
    program = VALID_PROGRAM.replace(
        'return "UNABLE", "未确定工序范围"',
        'return "UNABLE", self.group_context.rules_by_id["related-1"].rules[0]["rule_to_check"]',
    )
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": program}}])
    host = Checker(developer, tmp_path)
    artifact = host.adapt_checker(target, rules(), raw, group_context=group)
    assert "长度必须标注单位" in developer.prompts[0]
    assert "工序名称不为空时要有操作描述" in developer.prompts[0]
    results = host.execute_checker(artifact, target, rules(), raw, group_context=group)
    assert results[0].status == "UNABLE"
    assert results[0].suggestions == "长度必须标注单位"


def test_appendix_for_another_file_or_clause_does_not_enter_this_checker(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    appendix = [
        {
            "id": "mine",
            "content": "本文件口径",
            "scope": {"document_ids": ["version-1"], "rule_ids": ["R4"], "rule_targets": [{"document_id": "version-1", "rule_id": "R4"}]},
        },
        {
            "id": "other-file",
            "content": "别的文件口径",
            "scope": {"document_ids": ["version-other"], "rule_ids": ["R4"], "rule_targets": [{"document_id": "version-other", "rule_id": "R4"}]},
        },
        {
            "id": "other-clause",
            "content": "其他条款口径",
            "scope": {"document_ids": ["version-1"], "rule_ids": ["R9"], "rule_targets": [{"document_id": "version-1", "rule_id": "R9"}]},
        },
        {
            "id": "document-only-other",
            "content": "只限制了别的文件",
            "scope": {"document_ids": ["version-other"]},
        },
        {
            "id": "clause-only-other",
            "content": "只限制了别的条款",
            "scope": {"rule_ids": ["R9"]},
        },
        {
            "id": "empty-targets",
            "content": "空目标列表",
            "scope": {"rule_targets": [], "document_ids": ["version-1"], "rule_ids": ["R4"]},
        },
    ]
    program = VALID_PROGRAM.replace(
        'return "UNABLE", "未确定工序范围"',
        'return "UNABLE", "|".join(item["id"] for item in self.rule.appendix)',
    )
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": program}}])
    host = Checker(developer, tmp_path)
    rule = rules().model_copy(update={"appendix": appendix})
    artifact = host.adapt_checker(audit_file(path), rule, raw)

    assert "本文件口径" in developer.prompts[0]
    assert "别的文件口径" not in developer.prompts[0]
    assert "其他条款口径" not in developer.prompts[0]
    assert "只限制了别的文件" not in developer.prompts[0]
    assert "只限制了别的条款" not in developer.prompts[0]
    assert "空目标列表" not in developer.prompts[0]
    results = host.execute_checker(artifact, audit_file(path), rule, raw)
    assert results[0].suggestions == "mine"
    scoped = rule.model_copy(update={"appendix": [appendix[0]]})
    assert artifact.input_digest == input_digest(audit_file(path), scoped, file_sha256=hashlib.sha256(raw).hexdigest(), role="target")
    assert rule.appendix == appendix


def test_a_related_pdf_page_keeps_its_file_and_an_unknown_file_cannot_pass() -> None:
    from agent.mapping import checker_response
    from agent.protocol import FrozenFile
    from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

    target = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="flow.xlsx",
        sha256="a" * 64,
        rule_version_id="same-rule",
        rule_body={"rules": [{"id": "R9", "category": "跨文件", "rule_to_check": "图纸区域与流程一致"}]},
    )
    related = FrozenFile(
        file_version_id="related-version",
        file_id="file-2",
        role="reference",
        filename="drawing.pdf",
        sha256="b" * 64,
        rule_version_id="related-rule",
        rule_body={"rules": []},
    )

    def mapped(locator: str):
        result = AuditResultItem(
            classification="规则审核",
            item="跨文件",
            priority="P1",
            status="PASS",
            suggestions="已核对图纸",
            clause_id="R9",
            checks_performed=1,
            evidence=AuditEvidence(
                required="structure",
                used="structure",
                source_format="pdf",
                prepared_format="pdf",
                sources=(EvidenceSource(kind="structure", locator=locator),),
            ),
        )
        return checker_response(request().model_copy(update={"files": [target, related]}), [(target, [result])])

    kept = mapped("file:related-version;page:2;region:[0, 12, 400, 700]")
    source = kept.findings[0].locator["semantic"]["sources"][0]
    assert kept.findings[0].status == "PASS"
    assert source["file_version_id"] == "related-version"
    assert source["filename"] == "drawing.pdf"
    assert source["role"] == "reference"
    assert source["locator"] == {"kind": "pdf_page", "page": 2, "region": [0, 12, 400, 700]}

    blocked = mapped("file:outside;page:2;region:[0, 12, 400, 700]")
    assert blocked.findings[0].status == "UNABLE"
    assert blocked.findings[0].locator["semantic"]["sources"] == []
    assert "原件定位不在本次冻结文件中" in blocked.findings[0].suggestions


def test_pdf_page_and_region_reach_the_product_finding_without_trace_verification() -> None:
    from agent.mapping import checker_response
    from agent.protocol import FrozenFile
    from audit.contracts import AuditEvidence, AuditResultItem, EvidenceDiagnostic, EvidenceSource

    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="source.pdf",
        sha256="a" * 64,
        rule_version_id="same-rule",
        rule_body={"rules": [{"id": "P1", "category": "转换结果", "rule_to_check": "保留原件位置"}]},
    )
    evidence = AuditEvidence(
        required="structure",
        used="structure",
        source_format="pdf",
        prepared_format="xlsx",
        sources=(EvidenceSource(kind="structure", locator="file:version-1;page:3;region:[0, 12, 400, 700]"),),
        diagnostics=(EvidenceDiagnostic(code="page_location_failed", stage="structure", severity="error", message="第3页未能定位表格"),),
    )
    result = AuditResultItem(
        classification="规则审核",
        item="转换结果",
        priority="P1",
        status="UNABLE",
        suggestions="原件定位或转换未完成，不能判为通过",
        clause_id="P1",
        evidence=evidence,
    )
    response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [result])])
    source = response.findings[0].locator["semantic"]["sources"][0]
    assert source["file_version_id"] == "version-1"
    assert source["locator"] == {"kind": "pdf_page", "page": 3, "region": [0, 12, 400, 700]}
    partial = evidence.model_copy(update={"sources": (EvidenceSource(kind="structure", locator="file:version-1;page:4;region:[null, 10.5, null, 80.25]"),), "diagnostics": ()})
    partial_result = result.model_copy(update={"evidence": partial, "suggestions": "已定位原件区域"})
    partial_response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [partial_result])])
    assert partial_response.findings[0].locator["semantic"]["sources"][0]["locator"] == {
        "kind": "pdf_page",
        "page": 4,
        "region": [None, 10.5, None, 80.25],
    }
    assert response.findings[0].status == "UNABLE"
    assert response.findings[0].locator["semantic"]["diagnostics"][0]["code"] == "page_location_failed"
    assert response.findings[0].locator["semantic"]["sources"][0]["excerpt"] == ""
    assert response.coverage.verification is None
    valued = evidence.model_copy(update={"sources": (EvidenceSource(kind="structure", locator="file:version-1;page:3;region:[0, 12, 400, 700]", excerpt="0"),)})
    valued_result = result.model_copy(update={"evidence": valued, "status": "NG", "suggestions": "原值为 0", "checks_performed": 1})
    valued_response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [valued_result])])
    assert valued_response.findings[0].locator["semantic"]["sources"][0]["excerpt"] == "0"
    assert valued_response.findings[0].status == "NG"

    passed = result.model_copy(update={"status": "PASS", "suggestions": "表格已读取", "checks_performed": 1})
    blocked = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [passed])])
    assert blocked.findings[0].status == "UNABLE"
    assert "不能判为通过" in blocked.findings[0].suggestions
    assert blocked.findings[0].locator["semantic"]["sources"][0]["locator"]["page"] == 3
    assert blocked.coverage.verification is None

    for locator in (
        "file:version-1;page:5;region:not-json",
        "file:version-1;page:5;region:[true, 0, 1, 1]",
        "file:version-1;page:5;region:[NaN, 0, 1, 1]",
        "file:version-1;page:٣",
    ):
        bad = evidence.model_copy(update={"sources": (EvidenceSource(kind="structure", locator=locator),), "diagnostics": ()})
        bad_result = result.model_copy(update={"status": "PASS", "suggestions": "表格已读取", "checks_performed": 1, "evidence": bad})
        bad_response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [bad_result])])
        assert bad_response.findings[0].status == "UNABLE"
        assert bad_response.findings[0].locator["semantic"]["sources"] == []
        assert "原件定位不在本次冻结文件中" in bad_response.findings[0].suggestions


def test_a_pdf_without_text_does_not_start_the_model(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    raw = b"%PDF-1.4\n"
    path.write_bytes(raw)
    developer = ScriptedDeveloper([valid_draft()])
    host = Checker(developer, tmp_path)
    target = audit_file(path)
    artifact = host.adapt_checker(target, rules(), raw)

    assert developer.calls == 0
    results = host.execute_checker(artifact, target, rules(), raw)
    assert results[0].status == "UNABLE"
    assert "没有可用文字网格" in results[0].suggestions
    assert str(path) not in results[0].suggestions


def test_a_preset_is_known_only_from_its_own_markers() -> None:
    from agent.execution.checker_guest import _known_preset

    assert _known_preset("过程流程图") is None
    assert _known_preset("控制计划 零件号 工序") is None
    matched = _known_preset("全功能检验报告")
    assert matched is not None
    assert matched[0] == "PPAP_1_DIMENSION_MEASUREMENT_REPORT_SPEC"


def test_a_text_pdf_is_not_assigned_a_preset_by_document_type(tmp_path: Path) -> None:
    import fitz

    path = tmp_path / "plan.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Control plan layout is not a preset")
    document.save(path)
    document.close()
    raw = path.read_bytes()

    class Stop:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.input_tokens = 0
            self.output_tokens = 0

        def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
            del deadline, reasoning_effort
            self.prompts.append(prompt)
            assert "entrypoint" in schema.model_fields
            candidate = (work / "template-pdf-spec.py").read_text()
            assert "PdfSpec()" in candidate
            assert "PPAP_" not in candidate
            raise ModelProtocolError("stop")

    developer = Stop()
    host = Checker(developer, tmp_path)
    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rules(), raw)

    assert caught.value.code == "deep_model_protocol"
    prompt = developer.prompts[0]
    assert "不要按文档类型或文件名选择预设" in prompt
    assert "PPAP_" not in prompt
    assert "Control plan layout is not a preset" in prompt


def test_confirmed_appendix_enters_development_and_execution(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    appendix = [{"id": "note-1", "text": "空值不是通过"}]
    rule = rules().model_copy(update={"appendix": appendix})
    program = VALID_PROGRAM.replace(
        'return "UNABLE", "未确定工序范围"',
        'return "UNABLE", self.rule.appendix[0]["text"]',
    )
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": program}}])
    host = Checker(developer, tmp_path)
    artifact = host.adapt_checker(audit_file(path), rule, raw)

    assert "空值不是通过" in developer.prompts[0]
    results = host.execute_checker(artifact, audit_file(path), rule, raw)
    assert results[0].status == "UNABLE"
    assert results[0].suggestions == "空值不是通过"
    for changed_appendix in ([{"id": "note-1", "text": "只检查指定工序"}], []):
        changed = rule.model_copy(update={"appendix": changed_appendix})
        with pytest.raises(DeepError) as caught:
            host.execute_checker(artifact, audit_file(path), changed, raw)
        assert caught.value.code == "deep_output_identity_mismatch"


def test_development_prompt_previews_the_sheet_without_making_a_verdict(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序名称", "操作描述"])
    sheet.append(["下料", None])
    workbook.save(path)
    workbook.close()

    class Stop:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.input_tokens = 0
            self.output_tokens = 0

        def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
            del work, schema, deadline, reasoning_effort
            self.prompts.append(prompt)
            raise ModelProtocolError("stop")

    developer = Stop()
    host = Checker(developer, tmp_path)
    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rules(), path.read_bytes())

    assert caught.value.code == "deep_model_protocol"
    prompt = developer.prompts[0]
    assert "工作表 过程流程图" in prompt
    assert "工序名称 | 操作描述" in prompt
    assert "下料 | 空白" in prompt
    assert "不是完整扫描" in prompt
    assert "也不能当作判定" in prompt
    assert "目标文件 version-1" in prompt


def test_development_prompt_previews_a_related_workbook_and_a_pdf(tmp_path: Path) -> None:
    from audit.contracts import GroupContextInput

    target = tmp_path / "target.xlsx"
    related = tmp_path / "related.xlsx"
    pdf = tmp_path / "drawing.pdf"
    for path, title, header in (
        (target, "过程流程图", ("工序名称", "操作描述")),
        (related, "特殊特性", ("特性名称", "规格")),
    ):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = title
        sheet.append(list(header))
        sheet.append(["示例", "值"])
        workbook.save(path)
        workbook.close()
    pdf.write_bytes(b"%PDF-1.4\n")
    related_raw = related.read_bytes()
    pdf_raw = pdf.read_bytes()

    def reference(audit_id: str, path: Path, raw: bytes) -> AuditInput:
        return AuditInput(
            audit_id=audit_id,
            file_url=str(path),
            file_metadata={
                "file_version_id": audit_id,
                "role": "reference",
                "original_filename": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )

    group = GroupContextInput(
        by_audit_id={
            "related-xlsx": reference("related-xlsx", related, related_raw),
            "related-pdf": reference("related-pdf", pdf, pdf_raw),
        }
    )

    class Stop:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.input_tokens = 0
            self.output_tokens = 0

        def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
            del schema, deadline, reasoning_effort
            self.prompts.append(prompt)
            template = (work / "template-pdf-spec.py").read_text(encoding="utf-8")
            assert "related-pdf" in template
            assert "没有可用文字网格" in template
            assert "PPAP_" not in template
            raise ModelProtocolError("stop")

    developer = Stop()
    host = Checker(developer, tmp_path)
    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(target), rules(), target.read_bytes(), group_context=group)

    assert caught.value.code == "deep_model_protocol"
    prompt = developer.prompts[0]
    assert "目标文件 version-1" in prompt
    assert "关联文件 related-xlsx，角色 reference" in prompt
    assert "特性名称 | 规格" in prompt
    assert "不要把这里的单元格记到目标文件" in prompt
    assert "关联文件 related-pdf，角色 reference" in prompt
    assert "这是 PDF。这里没有单元格预览。" in prompt
    assert "references/pdf-reading.md" in prompt
    assert "references/cross-file.md" in prompt
    assert "template-pdf-spec.py" in prompt


def test_related_pdf_requires_the_spec_for_its_file_version(tmp_path: Path) -> None:
    from audit.checker_lib.nio.excel_agent import ExcelAgent
    from audit.contracts import GroupContextInput
    from documents.pdf import PdfSpec, StitchSpec

    path = tmp_path / "target.xlsx"
    workbook_bytes(path)
    group = GroupContextInput(
        by_audit_id={
            "cp": AuditInput(audit_id="cp", file_url=str(tmp_path / "cp.pdf"), file_metadata={"file_version_id": "cp-1"}),
        }
    )
    missing = ExcelAgent(audit_file(path), rules(), group)
    with pytest.raises(ValueError, match=r"PDF_SPECS\[cp-1\]"):
        missing.load_related_pdf("cp-1")

    class Bad(ExcelAgent):
        PDF_SPECS = {"cp-1": PdfSpec(stitch=StitchSpec(large_y_gap_ratio=2))}

    with pytest.raises(ValueError, match="large_y_gap_ratio"):
        Bad(audit_file(path), rules(), group).load_related_pdf("cp-1")


def test_a_pdf_target_must_declare_its_own_spec(tmp_path: Path) -> None:
    from agent.execution.checker_guest import CheckerTrialError, run_program

    source = "\n".join(
        [
            "from audit.checker_lib.nio.excel_agent import ExcelAgent, rule",
            "class TaskAudit(ExcelAgent):",
            "    CLAUSE_BOUND_CHECKS = True",
            "    @rule('标题', clause_id='1')",
            "    def check_clause(self, ctx):",
            "        del ctx",
            "        return 'PASS', 'ok'",
            "def create_audit(audit_file, rule, group_context=None):",
            "    return TaskAudit(audit_file, rule, group_context)",
        ]
    )
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    audit = AuditInput(audit_id="plan.pdf", file_url=str(pdf), file_metadata={"file_version_id": "plan-1"})
    rule = RuleInput(rule_id="plan", rules=[{"id": "1", "category": "标题", "rule_to_check": "有标题"}])
    with pytest.raises(CheckerTrialError, match="PDF_SPEC"):
        run_program({"checker.py": source.encode()}, tmp_path / "program", audit, rule, None)


def test_a_related_pdf_gap_cannot_remain_pass(tmp_path: Path) -> None:
    from audit.checker_lib.nio.excel_agent import ExcelAgent
    from audit.contracts import AuditResultItem
    from documents.contracts import ConversionArtifact

    path = tmp_path / "target.xlsx"
    workbook_bytes(path)
    agent = ExcelAgent(audit_file(path), rules(), None)
    agent._related_conversion_artifacts["related-1"] = ConversionArtifact(success=False)
    updated = agent._with_source_evidence(AuditResultItem(classification="规则审核", item="条款", priority="P0", status="PASS", suggestions="看起来通过"))
    assert updated.status == "UNABLE"


def test_an_unchanged_template_is_not_fixed(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)

    class SubmitTemplate:
        def __init__(self) -> None:
            self.calls = 0
            self.input_tokens = 0
            self.output_tokens = 0
            self.prompts: list[str] = []

        def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
            del deadline, reasoning_effort
            self.calls += 1
            self.prompts.append(prompt)
            template = work / "template-checker.py"
            assert template.is_file()
            assert not (work / "template-pdf-spec.py").exists()
            assert not (work / "checker.py").exists()
            if self.calls == 1:
                return schema.model_validate({"entrypoint": "checker:create_audit", "files": {"checker.py": template.read_text(encoding="utf-8")}})
            raise ModelProtocolError("stop")

    developer = SubmitTemplate()
    host = Checker(developer, tmp_path)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rules(), raw)

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 2
    assert "PDF 的观察与表格建模" not in developer.prompts[0]
    assert "模板尚未实现本次条款" in developer.prompts[1]


def test_a_missing_clause_result_is_reported_without_dropping_the_other_clause(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    program = """
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class NullCheck:
    def __init__(self, clause_id):
        self.rule_item = Item(clause_id)
    def check(self):
        return None

class KeptCheck:
    def __init__(self, clause_id):
        self.rule_item = Item(clause_id)
    def check(self):
        clause_id = self.rule_item.id
        return AuditResultItem(classification="规则审核", item=clause_id, priority="P1", status="UNABLE", suggestions="保留", clause_id=clause_id)

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (NullCheck("N1"), KeptCheck("N2"))
        def close(self):
            return None
    return Audit()
"""
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "N1", "category": "N1", "rule_to_check": "第一项"},
            {"id": "N2", "category": "N2", "rule_to_check": "第二项"},
        ],
    )
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": program}}])
    host = Checker(developer, tmp_path)
    artifact = host.adapt_checker(audit_file(path), rule, raw)
    results = host.execute_checker(artifact, audit_file(path), rule, raw)

    assert [(item.clause_id, item.status) for item in results] == [("N1", "ERR"), ("N2", "UNABLE")]
    assert "没有返回结果" in results[0].suggestions
    assert developer.calls == 2


def test_trial_feedback_includes_the_failed_clause_and_completed_result(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    broken = """
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class Done:
    def __init__(self):
        self.rule_item = Item("B2")
    def check(self):
        return AuditResultItem(classification="规则审核", item="B2", priority="P1", status="UNABLE", suggestions="已看过", clause_id="B2")

class Boom:
    def __init__(self):
        self.rule_item = Item("B1")
    def check(self):
        raise RuntimeError("缺原件位置 page:4")

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Done(), Boom())
        def close(self):
            return None
    return Audit()
"""
    fixed = """
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class Check:
    def __init__(self, clause_id):
        self.rule_item = Item(clause_id)
    def check(self):
        return AuditResultItem(classification="规则审核", item=self.rule_item.id, priority="P1", status="UNABLE", suggestions="已修订", clause_id=self.rule_item.id)

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Check("B1"), Check("B2"))
        def close(self):
            return None
    return Audit()
"""
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "B1", "category": "B1", "rule_to_check": "第一项"},
            {"id": "B2", "category": "B2", "rule_to_check": "第二项"},
        ],
    )
    developer = ScriptedDeveloper(
        [
            {"entrypoint": "checker:create_audit", "files": {"checker.py": broken}},
            {"entrypoint": "checker:create_audit", "files": {"checker.py": fixed}},
        ]
    )
    host = Checker(developer, tmp_path)
    host.adapt_checker(audit_file(path), rule, raw)

    assert developer.calls == 3
    assert "条款 B1 试跑失败" in developer.prompts[1]
    assert "缺原件位置 page:4" not in developer.prompts[1]
    assert "checker_failure:RuntimeError" in developer.prompts[1]
    assert "B2|UNABLE" in developer.prompts[1]
    assert "修复读取或实现错误后，按同一规则重新判断" in developer.prompts[1]
    assert "这些结果只说明程序跑完，不说明业务判断正确。" in developer.prompts[2]
    assert "B1|UNABLE" in developer.prompts[2]
    assert "B2|UNABLE" in developer.prompts[2]


def test_review_can_read_the_current_program_before_confirming(tmp_path: Path) -> None:
    from agent.execution.checker_guest import develop_checker

    path = tmp_path / "target.xlsx"
    workbook_bytes(path)

    class ReviewDeveloper:
        def __init__(self) -> None:
            self.calls = 0
            self.input_tokens = 0
            self.output_tokens = 0
            self.prompt = ""
            self.current = ""

        def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
            del deadline, reasoning_effort
            self.calls += 1
            if self.calls == 1:
                return schema.model_validate(valid_draft())
            self.prompt = prompt
            self.current = (work / "current-checker.py").read_text(encoding="utf-8")
            assert not (work / "checker.py").exists()
            return schema.model_validate(valid_draft())

    developer = ReviewDeveloper()
    artifact = develop_checker(
        audit_file=audit_file(path),
        rule=rules(),
        group_context=None,
        digest="c" * 64,
        executor_revision=REVISION,
        runner=developer,
        work=tmp_path / "adapt",
        deadline=10**12,
    )

    assert developer.calls == 2
    assert developer.current == VALID_PROGRAM
    assert "current-checker.py" in developer.prompt
    assert "R4|UNABLE" in developer.prompt
    assert artifact.files["checker.py"] == VALID_PROGRAM.encode()


def test_a_failed_revision_does_not_fix_the_unconfirmed_program(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    revised = VALID_PROGRAM.replace("def create_audit", "def missing_entry", 1)
    developer = ScriptedDeveloper([valid_draft(), {"entrypoint": "checker:create_audit", "files": {"checker.py": revised}}, *[broken_draft() for _ in range(4)]])
    host = Checker(developer, tmp_path)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rules(), raw)

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 6
    assert "这些结果只说明程序跑完，不说明业务判断正确。" in developer.prompts[1]
    assert "程序缺少 create_audit" in developer.prompts[2]


def test_a_confirmation_turn_without_a_program_does_not_fix_the_candidate(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)

    class DroppedConfirmation:
        def __init__(self) -> None:
            self.calls = 0
            self.input_tokens = 0
            self.output_tokens = 0

        def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
            del work, prompt, deadline, reasoning_effort
            self.calls += 1
            if self.calls == 1:
                return schema.model_validate(valid_draft())
            raise ModelProtocolError("confirmation dropped")

    developer = DroppedConfirmation()
    host = Checker(developer, tmp_path)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rules(), raw)

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 2


def test_the_sixth_trial_cannot_fix_the_first_full_success(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper([*[broken_draft() for _ in range(5)], valid_draft()])
    host = Checker(developer, tmp_path)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rules(), raw)

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 6


def test_a_partial_trial_does_not_fix_or_run_the_other_clause(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    partial_program = """
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class Check:
    def __init__(self, clause_id):
        self.rule_item = Item(clause_id)
    def check(self):
        if self.rule_item.id == "B":
            raise RuntimeError("B should wait")
        return AuditResultItem(classification="规则审核", item="A", priority="P1", status="UNABLE", suggestions="只看了A", clause_id="A")

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Check("A"), Check("B"))
        def close(self):
            return None
    return Audit()
"""
    fixed = partial_program.replace(
        'raise RuntimeError("B should wait")',
        'return AuditResultItem(classification="规则审核", item="B", priority="P1", status="NA", suggestions="B不适用", clause_id="B")',
    )
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "A", "category": "A", "rule_to_check": "第一项"},
            {"id": "B", "category": "B", "rule_to_check": "第二项"},
        ],
    )
    partial = {"entrypoint": "checker:create_audit", "files": {"checker.py": partial_program}, "clauses": ["A"]}
    complete = {"entrypoint": "checker:create_audit", "files": {"checker.py": fixed}}
    developer = ScriptedDeveloper([partial, complete, complete])
    host = Checker(developer, tmp_path)

    artifact = host.adapt_checker(audit_file(path), rule, raw)

    assert developer.calls == 3
    assert "本次已有结果的条款" in developer.prompts[1]
    assert "本次已有结果的条款：A" in developer.prompts[1]
    assert "B should wait" not in developer.prompts[1]
    assert "A|UNABLE" in developer.prompts[1]
    assert "这些结果只说明程序跑完，不说明业务判断正确。" in developer.prompts[2]
    assert artifact.files["checker.py"] == fixed.encode()


def test_a_resource_change_runs_every_clause_even_when_the_receipt_names_one(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    program = """
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class Check:
    def __init__(self, clause_id):
        self.rule_item = Item(clause_id)
    def check(self):
        if self.rule_item.id == "B":
            raise RuntimeError("B should wait")
        return AuditResultItem(classification="规则审核", item="A", priority="P1", status="UNABLE", suggestions="只看了A", clause_id="A")

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Check("A"), Check("B"))
        def close(self):
            return None
    return Audit()
"""
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "A", "category": "A", "rule_to_check": "第一项"},
            {"id": "B", "category": "B", "rule_to_check": "第二项"},
        ],
    )
    named = {"entrypoint": "checker:create_audit", "files": {"checker.py": program, "resources.py": "RESOURCE = None\n"}, "clauses": ["A"]}
    developer = ScriptedDeveloper([named for _ in range(6)])
    host = Checker(developer, tmp_path)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rule, raw)

    assert caught.value.code == "deep_output_invalid"
    assert "这是局部试跑" not in developer.prompts[1]
    assert "B|ERR||checker_failure:RuntimeError" in developer.prompts[1]


def test_removing_resources_runs_every_clause_even_when_the_receipt_names_one(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    shared = """
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class Check:
    def __init__(self, clause_id):
        self.rule_item = Item(clause_id)
    def check(self):
        return AuditResultItem(classification="规则审核", item=self.rule_item.id, priority="P1", status="UNABLE", suggestions="已看过", clause_id=self.rule_item.id)

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Check("A"), Check("B"))
        def close(self):
            return None
    return Audit()
"""
    narrowed = shared.replace(
        '        return AuditResultItem(classification="规则审核", item=self.rule_item.id, priority="P1", status="UNABLE", suggestions="已看过", clause_id=self.rule_item.id)',
        '        if self.rule_item.id == "B":\n            raise RuntimeError("B should wait")\n'
        '        return AuditResultItem(classification="规则审核", item="A", priority="P1", status="UNABLE", suggestions="只看了A", clause_id="A")',
    )
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "A", "category": "A", "rule_to_check": "第一项"},
            {"id": "B", "category": "B", "rule_to_check": "第二项"},
        ],
    )
    developer = ScriptedDeveloper(
        [
            {"entrypoint": "checker:create_audit", "files": {"checker.py": shared, "resources.py": "RESOURCE = None\n"}},
            {"entrypoint": "checker:create_audit", "files": {"checker.py": narrowed}, "clauses": ["A"]},
        ]
    )
    host = Checker(developer, tmp_path)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rule, raw)

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 3
    assert "这是局部试跑" not in developer.prompts[2]
    assert "B|ERR||checker_failure:RuntimeError" in developer.prompts[2]


def test_a_shared_function_change_runs_every_clause_even_when_the_receipt_names_one(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    program = """
from audit.contracts import AuditResultItem

def helper(clause_id):
    return "UNABLE"

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class Check:
    def __init__(self, clause_id):
        self.rule_item = Item(clause_id)
    def check(self):
        status = helper(self.rule_item.id)
        return AuditResultItem(classification="规则审核", item=self.rule_item.id, priority="P1", status=status, suggestions="已看过", clause_id=self.rule_item.id)

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Check("A"), Check("B"))
        def close(self):
            return None
    return Audit()
"""
    changed = program.replace(
        'def helper(clause_id):\n    return "UNABLE"\n',
        'def helper(clause_id):\n    if clause_id == "B":\n        raise RuntimeError("B should wait")\n    return "NG"\n',
    )
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "A", "category": "A", "rule_to_check": "第一项"},
            {"id": "B", "category": "B", "rule_to_check": "第二项"},
        ],
    )
    developer = ScriptedDeveloper(
        [
            {"entrypoint": "checker:create_audit", "files": {"checker.py": program}},
            {"entrypoint": "checker:create_audit", "files": {"checker.py": changed}, "clauses": ["A"]},
        ]
    )
    host = Checker(developer, tmp_path)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rule, raw)

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 3
    assert "这是局部试跑" not in developer.prompts[2]
    assert "B|ERR||checker_failure:RuntimeError" in developer.prompts[2]


def test_a_clause_method_change_can_still_name_one_clause(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    program = """
from audit.contracts import AuditResultItem

def rule(*_args, **_kwargs):
    def decorate(fn):
        return fn
    return decorate

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class Check:
    def __init__(self, clause_id, method):
        self.rule_item = Item(clause_id)
        self.method = method
    def check(self):
        return self.method()

class Task:
    @rule("第一项", clause_id="A")
    def check_a(self):
        return AuditResultItem(classification="规则审核", item="A", priority="P1", status="UNABLE", suggestions="初稿", clause_id="A")

    @rule("第二项", clause_id="B")
    def check_b(self):
        return AuditResultItem(classification="规则审核", item="B", priority="P1", status="UNABLE", suggestions="初稿", clause_id="B")

def create_audit(audit_file, rule, group_context=None):
    task = Task()
    class Audit:
        checks = (Check("A", task.check_a), Check("B", task.check_b))
        def close(self):
            return None
    return Audit()
"""
    revised = program.replace('suggestions="初稿", clause_id="A"', 'suggestions="只看了A", clause_id="A"').replace(
        'return AuditResultItem(classification="规则审核", item="B", priority="P1", status="UNABLE", suggestions="初稿", clause_id="B")',
        'raise RuntimeError("B should wait")',
    )
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "A", "category": "A", "rule_to_check": "第一项"},
            {"id": "B", "category": "B", "rule_to_check": "第二项"},
        ],
    )
    developer = ScriptedDeveloper(
        [
            {"entrypoint": "checker:create_audit", "files": {"checker.py": program}},
            {"entrypoint": "checker:create_audit", "files": {"checker.py": revised}, "clauses": ["A"]},
        ]
    )
    host = Checker(developer, tmp_path)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rule, raw)

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 3
    assert "本次已有结果的条款" in developer.prompts[2]
    assert "本次已有结果的条款：A" in developer.prompts[2]
    assert "B should wait" not in developer.prompts[2]


def test_a_shared_check_method_change_runs_every_clause_even_when_the_receipt_names_one(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    first = """
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class Check:
    def __init__(self, clause_id):
        self.rule_item = Item(clause_id)
    def check(self):
        return AuditResultItem(classification="规则审核", item=self.rule_item.id, priority="P1", status="UNABLE", suggestions="初稿", clause_id=self.rule_item.id)

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Check("A"), Check("B"))
        def close(self):
            return None
    return Audit()
"""
    revised = first.replace("初稿", "修订后仍只试跑 A").replace('raise RuntimeError("B")', 'raise RuntimeError("B")')
    revised = revised.replace(
        'return AuditResultItem(classification="规则审核", item=self.rule_item.id, priority="P1", status="UNABLE", suggestions="修订后仍只试跑 A", clause_id=self.rule_item.id)',
        'if self.rule_item.id == "B":\n            raise RuntimeError("B should wait")\n'
        '        return AuditResultItem(classification="规则审核", item="A", priority="P1", status="NG", suggestions="修订后仍只试跑 A", clause_id="A")',
    )
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "A", "category": "A", "rule_to_check": "第一项"},
            {"id": "B", "category": "B", "rule_to_check": "第二项"},
        ],
    )
    developer = ScriptedDeveloper(
        [
            {"entrypoint": "checker:create_audit", "files": {"checker.py": first}},
            {"entrypoint": "checker:create_audit", "files": {"checker.py": revised}, "clauses": ["A"]},
        ]
    )
    host = Checker(developer, tmp_path)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rule, raw)

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 3
    assert "这些结果只说明程序跑完，不说明业务判断正确。" in developer.prompts[1]
    assert "这是局部试跑" not in developer.prompts[2]
    assert "B|ERR||checker_failure:RuntimeError" in developer.prompts[2]
    assert "A|NG" in developer.prompts[2]


def test_a_fixed_program_reports_one_clause_fault_without_dropping_the_other(tmp_path: Path) -> None:
    from agent.checker_spec import CheckerArtifact

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    program = """
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class Ok:
    def __init__(self):
        self.rule_item = Item("C1")
    def check(self):
        return AuditResultItem(classification="规则审核", item="C1", priority="P1", status="UNABLE", suggestions="保留", clause_id="C1")

class Boom:
    def __init__(self):
        self.rule_item = Item("C2")
    def check(self):
        raise RuntimeError("读失败")

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Ok(), Boom())
        def close(self):
            return None
    return Audit()
"""
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "C1", "category": "C1", "rule_to_check": "第一项"},
            {"id": "C2", "category": "C2", "rule_to_check": "第二项"},
        ],
    )
    audit = audit_file(path)
    digest = input_digest(audit, rule, file_sha256=hashlib.sha256(raw).hexdigest(), role="target")
    artifact = CheckerArtifact(input_digest=digest, executor_revision=REVISION, entrypoint="checker:create_audit", files={"checker.py": program.encode()})
    host = Checker(ScriptedDeveloper([]), tmp_path)
    results = host.execute_checker(artifact, audit, rule, raw)

    assert [(item.clause_id, item.status) for item in results] == [("C1", "UNABLE"), ("C2", "ERR")]
    assert "技术失败" in results[1].suggestions
    assert "PASS" not in {item.status for item in results}


def test_a_fixed_program_keeps_later_clauses_when_one_result_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    program = """
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class BadShape:
    def __init__(self):
        self.rule_item = Item("C1")
    def check(self):
        return object()

class Ok:
    def __init__(self):
        self.rule_item = Item("C2")
    def check(self):
        return AuditResultItem(classification="规则审核", item="C2", priority="P1", status="UNABLE", suggestions="保留", clause_id="C2")

class BadValue:
    def __init__(self):
        self.rule_item = Item("C3")
    def check(self):
        class Broken:
            def model_dump(self):
                return {}
        return Broken()

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (BadShape(), Ok(), BadValue())
        def close(self):
            return None
    return Audit()
"""
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "C1", "category": "C1", "rule_to_check": "第一项"},
            {"id": "C2", "category": "C2", "rule_to_check": "第二项"},
            {"id": "C3", "category": "C3", "rule_to_check": "第三项"},
        ],
    )
    audit = audit_file(path)
    digest = input_digest(audit, rule, file_sha256=hashlib.sha256(raw).hexdigest(), role="target")
    artifact = CheckerArtifact(input_digest=digest, executor_revision=REVISION, entrypoint="checker:create_audit", files={"checker.py": program.encode()})
    host = Checker(ScriptedDeveloper([]), tmp_path)
    results = host.execute_checker(artifact, audit, rule, raw)

    assert [(item.clause_id, item.status) for item in results] == [("C1", "ERR"), ("C2", "UNABLE"), ("C3", "ERR")]
    assert "检查结果无效" in results[0].suggestions
    assert "检查结果无效" in results[2].suggestions
    assert "PASS" not in {item.status for item in results}


def test_an_invalid_trial_result_stays_a_revision_and_names_the_clause(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    program = """
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id
        self.category = clause_id
        self.rule_to_check = clause_id
        self.priority = "P1"

class Ok:
    def __init__(self):
        self.rule_item = Item("C1")
    def check(self):
        return AuditResultItem(classification="规则审核", item="C1", priority="P1", status="UNABLE", suggestions="保留", clause_id="C1")

class BadShape:
    def __init__(self):
        self.rule_item = Item("C2")
    def check(self):
        return object()

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Ok(), BadShape())
        def close(self):
            return None
    return Audit()
"""
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "C1", "category": "C1", "rule_to_check": "第一项"},
            {"id": "C2", "category": "C2", "rule_to_check": "第二项"},
        ],
    )
    draft = {"entrypoint": "checker:create_audit", "files": {"checker.py": program}}
    developer = ScriptedDeveloper([draft for _ in range(6)])
    host = Checker(developer, tmp_path)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rule, raw)

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 6
    assert "条款 C2 检查结果无效" in developer.prompts[1]
    assert "authored_checker_invalid_result" in developer.prompts[1]
    assert "C1|UNABLE" in developer.prompts[1]


def test_task_resource_file_joins_the_registry_resource_in_one_context(tmp_path: Path) -> None:
    import sys

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": RESOURCE_CHECKER, "resources.py": RESOURCE_FILE}}])
    host = Checker(developer, tmp_path)
    before = list(sys.path)
    artifact = host.adapt_checker(audit_file(path), rules(), raw)

    assert set(artifact.files) == {"checker.py", "resources.py"}
    assert list(sys.path) == before
    assert "resources" not in sys.modules
    results = host.execute_checker(artifact, audit_file(path), rules(), raw)

    assert developer.calls == 2
    assert [(item.clause_id, item.status) for item in results] == [("R4", "UNABLE")]
    text = results[0].suggestions
    assert "registry=True" in text
    assert "registry_alias=工序名称" in text
    assert "column=任务列" in text
    assert "alias=任务别名" in text
    assert "section=过程流程主数据区" in text
    assert list(sys.path) == before
    assert "resources" not in sys.modules


def test_a_program_without_clause_binding_is_revised_before_it_is_fixed(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    unbound = VALID_PROGRAM.replace("    CLAUSE_BOUND_CHECKS = True\n", "", 1)
    developer = ScriptedDeveloper(
        [
            {"entrypoint": "checker:create_audit", "files": {"checker.py": unbound}},
            valid_draft(),
        ]
    )
    host = Checker(developer, tmp_path)

    artifact = host.adapt_checker(audit_file(path), rules(), raw)

    assert developer.calls == 3
    assert artifact.files["checker.py"] == VALID_PROGRAM.encode()


CELL_PROGRAM = """
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.engine.rule_engine.fluent import Table
from audit.kinds import PpapDocumentType


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("工序名称",)

    @rule("工序名称", clause_id="R4")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        section = Table(ctx).sheet("过程流程图").section("过程流程主数据区")
        columns = section.columns("工序名称", "操作描述")
        for row in section.rows_from(columns):
            if row.is_present("工序名称") and not row.is_present("操作描述"):
                return "NG", "工序缺少操作描述"
        return "UNABLE", "未读到工序"


def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""


def test_a_read_cell_points_at_this_file_without_trace_verification(tmp_path: Path) -> None:
    import json

    from agent.mapping import checker_response
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序名称", "操作描述", "产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别", "制造", "运输", "存储", "检验", "返工", "报废"])
    sheet.append(["焊接", None, None, None, None, None, "制造", None, None, None, None, None])
    workbook.save(path)
    workbook.close()
    raw = path.read_bytes()
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": CELL_PROGRAM}}])
    host = Checker(developer, tmp_path)
    artifact = host.adapt_checker(audit_file(path), rules(), raw)
    results = host.execute_checker(artifact, audit_file(path), rules(), raw)

    assert results[0].status == "NG"
    assert results[0].evidence is not None
    assert results[0].evidence.used == "structure"
    cells = []
    for source in results[0].evidence.sources:
        locator = json.loads(source.locator)
        assert locator["sheet"] == "过程流程图"
        cells.append(locator["cell"])
    assert {"A2", "B2"} <= set(cells)
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256=hashlib.sha256(raw).hexdigest(),
        rule_version_id="same-rule",
        rule_body=body,
    )
    response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, results)])

    assert response.note == "deep_checker_audit"
    assert response.coverage.verification is None
    assert response.coverage.locator_gap is False
    source = response.findings[0].locator["semantic"]["sources"][0]
    assert source["file_version_id"] == "version-1"
    assert source["locator"]["kind"] == "xlsx_cell"
    assert source["locator"]["sheet"] == "过程流程图"
    assert source["locator"]["cell"] in cells


def test_a_related_cell_keeps_its_own_file_version(tmp_path: Path) -> None:
    import json

    from agent.mapping import checker_response
    from agent.protocol import FrozenFile
    from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R9", "category": "跨文件", "rule_to_check": "特性在两份文件中一致"}],
    }
    target = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256=hashlib.sha256(raw).hexdigest(),
        rule_version_id="same-rule",
        rule_body=body,
    )
    reference = FrozenFile(
        file_version_id="related-version",
        file_id="file-related",
        role="reference",
        filename="characteristics.xlsx",
        sha256="b" * 64,
        rule_version_id="related-rule",
        rule_body={"type": PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name()},
    )
    result = AuditResultItem(
        classification="过程流程图",
        item="跨文件",
        priority="P1",
        status="NG",
        suggestions="特性未覆盖",
        clause_id="R9",
        checks_performed=1,
        evidence=AuditEvidence(
            required="structure",
            used="structure",
            source_format="xlsx",
            prepared_format="xlsx",
            sources=(
                EvidenceSource(kind="structure", locator=json.dumps({"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A2", "file_version_id": "version-1"}, ensure_ascii=False)),
                EvidenceSource(kind="structure", locator=json.dumps({"kind": "xlsx_cell", "sheet": "特殊特性清单", "cell": "B2", "file_version_id": "related-version"}, ensure_ascii=False)),
            ),
        ),
    )
    response = checker_response(request().model_copy(update={"files": [target, reference]}), [(target, [result])])
    sources = response.findings[0].locator["semantic"]["sources"]

    assert response.coverage.verification is None
    assert response.coverage.locator_gap is False
    assert sources[0]["file_version_id"] == "version-1"
    assert sources[0]["locator"] == {"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A2"}
    assert sources[1]["file_version_id"] == "related-version"
    assert sources[1]["locator"] == {"kind": "xlsx_cell", "sheet": "特殊特性清单", "cell": "B2"}


DUPLICATE_PROGRAM = """
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.kinds import PpapDocumentType


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True

    @rule("工序名称", clause_id="R4")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        return "UNABLE", "未确定工序范围"

    @rule("重复入口", clause_id="R4")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        return "UNABLE", "另一个入口"


def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""


def test_duplicate_clause_binding_is_revised_before_the_program_is_fixed(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper(
        [
            {"entrypoint": "checker:create_audit", "files": {"checker.py": DUPLICATE_PROGRAM}},
            valid_draft(),
        ]
    )
    host = Checker(developer, tmp_path)

    artifact = host.adapt_checker(audit_file(path), rules(), raw)

    assert developer.calls == 3
    assert "条款 R4 绑定错误" in developer.prompts[1]
    assert "绑定了多个检查入口" in developer.prompts[1]
    assert artifact.files["checker.py"] == VALID_PROGRAM.encode()


def test_a_fixed_program_reports_a_missing_clause_binding_without_dropping_the_other(tmp_path: Path) -> None:
    from agent.execution.checker_guest import execute_fixed_checker

    path = tmp_path / "target.xlsx"
    workbook_bytes(path)
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"},
            {"id": "R5", "category": "操作描述", "rule_to_check": "操作描述需要单独检查"},
        ],
    )
    artifact = CheckerArtifact(
        input_digest="c" * 64,
        executor_revision="coverage",
        entrypoint="checker:create_audit",
        files={"checker.py": VALID_PROGRAM.encode()},
    )

    results = execute_fixed_checker(
        artifact,
        audit_file=audit_file(path),
        rule=rule,
        group_context=None,
        work=tmp_path / "work",
        digest=artifact.input_digest,
        executor_revision="coverage",
    )

    assert [(item.clause_id, item.status) for item in results] == [("R4", "UNABLE"), ("R5", "ERR")]
    assert "没有对应的检查入口" in results[1].suggestions
    assert "未确定工序范围" in results[0].suggestions


EXTRA_PROGRAM = """
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.kinds import PpapDocumentType


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True

    @rule("工序名称", clause_id="R4")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        return "UNABLE", "未确定工序范围"

    @rule("多余入口", clause_id="EXTRA")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        return "UNABLE", "不在本次规则中的入口"


def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""


def test_an_extra_clause_binding_is_reported_without_dropping_the_rule_clause(tmp_path: Path) -> None:
    from agent.execution.checker_guest import execute_fixed_checker
    from agent.mapping import checker_response
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper(
        [
            {"entrypoint": "checker:create_audit", "files": {"checker.py": EXTRA_PROGRAM}},
            valid_draft(),
        ]
    )
    host = Checker(developer, tmp_path)
    artifact = host.adapt_checker(audit_file(path), rules(), raw)

    assert developer.calls == 3
    assert "条款 EXTRA 绑定错误" in developer.prompts[1]
    assert "不在本次生效规则中" in developer.prompts[1]
    assert artifact.files["checker.py"] == VALID_PROGRAM.encode()

    fixed = CheckerArtifact(
        input_digest="d" * 64,
        executor_revision="coverage",
        entrypoint="checker:create_audit",
        files={"checker.py": EXTRA_PROGRAM.encode()},
    )
    results = execute_fixed_checker(
        fixed,
        audit_file=audit_file(path),
        rule=rules(),
        group_context=None,
        work=tmp_path / "work",
        digest=fixed.input_digest,
        executor_revision="coverage",
    )
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256=hashlib.sha256(raw).hexdigest(),
        rule_version_id="same-rule",
        rule_body=body,
    )
    response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, results)])
    by_clause = {item.locator["semantic"]["clause_id"]: item for item in response.findings}

    assert [(item.clause_id, item.status) for item in results] == [("R4", "UNABLE"), ("EXTRA", "ERR")]
    assert response.coverage.verification is None
    assert [item["id"] for item in response.coverage.rule_items] == ["R4"]
    assert by_clause["R4"].status == "UNABLE"
    assert by_clause["EXTRA"].status == "ERR"
    assert "不在本次生效规则中" in by_clause["EXTRA"].suggestions
    assert by_clause["EXTRA"].locator["semantic"]["binding"] == "extra"


RELATED_CELL_PROGRAM = """
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.kinds import PpapDocumentType
from documents.excel import load_excel_workbook


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("关联清单",)

    @rule("关联清单", clause_id="R4")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        related = self.get_related_files(PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST)
        if len(related) != 1:
            return "UNABLE", "关联文件没有进入本次检查"
        version = str((related[0].file_metadata or {}).get("file_version_id") or "")
        workbook = load_excel_workbook(related[0].file_url)
        try:
            sheet = workbook.active
            ctx.bind_workbook(workbook, version)
            ctx.note_cell(str(sheet.title), 2, 2, file_version_id=version, excerpt="伪造摘录")
        finally:
            workbook.close()
        ctx.record_execution()
        return "NG", "已读取关联清单"


def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""


def test_a_related_workbook_cell_is_attributed_to_that_file_version(tmp_path: Path) -> None:
    from agent.execution.checker_guest import execute_fixed_checker
    from agent.mapping import checker_response
    from agent.protocol import FrozenFile
    from audit.contracts import AuditInput, GroupContextInput

    path = tmp_path / "target.xlsx"
    workbook_bytes(path)
    related_path = tmp_path / "list.xlsx"
    related_book = Workbook()
    related_sheet = related_book.active
    related_sheet.title = "特殊特性清单"
    related_sheet.append(["特性描述", "特性值"])
    related_sheet.append(["长度", "10mm"])
    related_book.save(related_path)
    related_book.close()
    related_type = PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name()
    related = AuditInput(
        audit_id="related-version",
        file_url=str(related_path),
        file_metadata={"file_version_id": "related-version", "role": "reference"},
    )
    group = GroupContextInput(by_doc_type={related_type: [related]}, by_audit_id={related.audit_id: related})
    clauses = [{"id": "R4", "category": "关联清单", "rule_to_check": "特殊特性清单要参与核对"}]
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=clauses,
    )
    artifact = CheckerArtifact(
        input_digest="e" * 64,
        executor_revision="coverage",
        entrypoint="checker:create_audit",
        files={"checker.py": RELATED_CELL_PROGRAM.encode()},
    )
    results = execute_fixed_checker(
        artifact,
        audit_file=audit_file(path),
        rule=rule,
        group_context=group,
        work=tmp_path / "work",
        digest=artifact.input_digest,
        executor_revision="coverage",
    )
    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        rule_version_id="same-rule",
        rule_body={"rule_profile": "v3", "type": rule.type, "rules": clauses},
    )
    reference = FrozenFile(
        file_version_id="related-version",
        file_id="file-related",
        role="reference",
        filename="list.xlsx",
        sha256=hashlib.sha256(related_path.read_bytes()).hexdigest(),
        rule_version_id="related-rule",
        rule_body={"type": related_type},
    )
    response = checker_response(request().model_copy(update={"files": [frozen, reference]}), [(frozen, results)])
    source = response.findings[0].locator["semantic"]["sources"][0]

    assert results[0].status == "NG"
    assert response.coverage.verification is None
    assert source["file_version_id"] == "related-version"
    assert source["locator"] == {"kind": "xlsx_cell", "sheet": "特殊特性清单", "cell": "B2"}


UNNUMBERED_PROGRAM = """
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.kinds import PpapDocumentType


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True

    @rule("工序名称", clause_id="R4")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        return "UNABLE", "未确定工序范围"

    @rule("未编号")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        return "PASS", "不应按序号执行"


def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""


def test_a_method_without_a_clause_id_is_reported_without_dropping_the_rule_clause(tmp_path: Path) -> None:
    from agent.execution.checker_guest import execute_fixed_checker
    from agent.mapping import checker_response
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    artifact = CheckerArtifact(
        input_digest="f" * 64,
        executor_revision="coverage",
        entrypoint="checker:create_audit",
        files={"checker.py": UNNUMBERED_PROGRAM.encode()},
    )
    results = execute_fixed_checker(
        artifact,
        audit_file=audit_file(path),
        rule=rules(),
        group_context=None,
        work=tmp_path / "work",
        digest=artifact.input_digest,
        executor_revision="coverage",
    )
    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256=hashlib.sha256(raw).hexdigest(),
        rule_version_id="same-rule",
        rule_body={
            "rule_profile": "v3",
            "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
            "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
        },
    )
    response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, results)])
    by_key = {item.item_key: item for item in response.findings}

    assert [(item.clause_id, item.status) for item in results] == [("R4", "UNABLE"), (None, "ERR")]
    assert "检查入口缺少条款 ID" in results[1].suggestions
    assert [item["id"] for item in response.coverage.rule_items] == ["R4"]
    assert by_key["version-1:rule:R4"].status == "UNABLE"
    missing = by_key["version-1:binding:missing-clause-id-1"]
    assert missing.status == "ERR"
    assert "检查入口缺少条款 ID" in missing.suggestions
    assert missing.locator["semantic"]["binding"] == "missing_clause_id"
    assert response.coverage.verification is None


def _gap_program(gap: str) -> str:
    duplicated = 'Check("B", "PASS", "第一次"), Check("B", "UNABLE", "第二次"),' if gap == "duplicate" else ""
    return f"""
from audit.contracts import AuditResultItem

class Item:
    def __init__(self, clause_id):
        self.id = clause_id or ""
        self.category = clause_id or "未命名"
        self.rule_to_check = clause_id or "未命名"
        self.priority = "P1"

class Check:
    def __init__(self, clause_id, status, suggestions):
        self.rule_item = Item(clause_id)
        self._clause_id = clause_id
        self._status = status
        self._suggestions = suggestions

    def check(self):
        return AuditResultItem(
            classification="规则审核",
            item=self._clause_id or "未命名",
            priority="P1",
            status=self._status,
            suggestions=self._suggestions,
            clause_id=self._clause_id,
        )

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Check("A", "NG", "已比较"), {duplicated} Check("Z", "UNABLE", "多余"), Check(None, "ERR", "未命名"))
        def close(self):
            return None
    return Audit()
"""


def _two_clause_rule() -> RuleInput:
    return RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=[
            {"id": "A", "category": "A", "rule_to_check": "第一项", "priority": "P1"},
            {"id": "B", "category": "B", "rule_to_check": "第二项", "priority": "P1"},
        ],
    )


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


@pytest.mark.parametrize("gap", ["missing", "duplicate"])
def test_fixed_execution_keeps_a_completed_clause_when_another_is_missing_or_duplicated(tmp_path: Path, gap: str) -> None:
    from agent.execution.checker_guest import execute_fixed_checker
    from agent.mapping import checker_response, product_result
    from audit.audits import RoundInput, RoundWork

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    rule = _two_clause_rule()
    program = _gap_program(gap)
    draft = {"entrypoint": "checker:create_audit", "files": {"checker.py": program}}
    developer = ScriptedDeveloper([draft for _ in range(6)])
    host = Checker(developer, tmp_path)
    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rule, raw)

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 6
    assert "条款结果与生效规则不一致" in developer.prompts[1]
    assert "A|NG" in developer.prompts[1]

    artifact = CheckerArtifact(
        input_digest="a" * 64,
        executor_revision="coverage",
        entrypoint="checker:create_audit",
        files={"checker.py": program.encode()},
    )
    results = execute_fixed_checker(
        artifact,
        audit_file=audit_file(path),
        rule=rule,
        group_context=None,
        work=tmp_path / "fixed",
        digest=artifact.input_digest,
        executor_revision="coverage",
    )
    frozen = _frozen_target(raw, list(rule.rules))
    response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, results)])
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
                    sha256=hashlib.sha256(raw).hexdigest(),
                    rule_version_id="same-rule",
                    rule_body=frozen.rule_body,
                )
            ],
        ),
    )
    by_key = {item.item_key: item for item in response.findings}
    gap_text = "条款 B 绑定错误: 绑定了多个检查入口" if gap == "duplicate" else "条款 B 绑定错误: 没有对应的检查入口"

    assert [(item.clause_id, item.status) for item in results] == [("A", "NG"), ("B", "ERR"), ("Z", "ERR"), (None, "ERR")]
    assert results[0].suggestions == "已比较"
    assert results[1].suggestions == gap_text
    assert "第一次" not in " ".join(item.suggestions for item in results)
    assert by_key["version-1:rule:A"].status == "NG"
    assert by_key["version-1:rule:A"].suggestions == "已比较"
    assert by_key["version-1:rule:B"].status == "ERR"
    assert gap_text in by_key["version-1:rule:B"].suggestions
    assert by_key["version-1:rule:Z"].locator["semantic"]["binding"] == "extra"
    assert by_key["version-1:binding:missing-clause-id-1"].locator["semantic"]["binding"] == "missing_clause_id"
    assert [item["id"] for item in response.coverage.rule_items] == ["A", "B"]
    assert [item["status"] for item in stored["findings"] if item["item_key"].endswith(":rule:A") or item["item_key"].endswith(":rule:B")] == ["NG", "ERR"]
    assert response.coverage.verification is None


def test_duplicate_rule_ids_fail_before_authored_code_or_model_runs(tmp_path: Path) -> None:
    from agent.execution.checker_guest import CheckerTrialError, run_program

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    rule = rules().model_copy(update={"rules": [rules().rules[0], rules().rules[0]]})
    developer = ScriptedDeveloper([valid_draft()])
    host = Checker(developer, tmp_path)
    with pytest.raises(DeepError) as caught:
        host.adapt_checker(audit_file(path), rule, raw)
    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 0
    with pytest.raises(CheckerTrialError):
        run_program({"checker.py": b"raise AssertionError('must not execute')"}, tmp_path / "fixed", audit_file(path), rule, None)


def test_an_open_question_reaches_product_clarification_without_trace_verification(tmp_path: Path) -> None:
    from agent.execution.checker_guest import execute_fixed_checker
    from agent.mapping import checker_response

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    program = """
from audit.checker_lib.nio.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.kinds import PpapDocumentType


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True

    @rule("表头", clause_id="A")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.note_cell("Sheet", 1, 1)
        ctx.ask("header-meaning", "表头按什么理解？", (("left", "左列", "按左列表头理解"), ("right", "右列", "按右列表头理解")))
        return "PASS", "未确认"

    @rule("比较", clause_id="B")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.record_ng("已比较")
        return "NG", "已比较"


def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""
    clauses = [
        {"id": "A", "category": "表头", "rule_to_check": "表头含义需要确认"},
        {"id": "B", "category": "比较", "rule_to_check": "比较已有反例"},
    ]
    rule = RuleInput(rule_id="same-rule", rule_profile="v3", type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(), rules=clauses)
    artifact = CheckerArtifact(input_digest="c" * 64, executor_revision="coverage", entrypoint="checker:create_audit", files={"checker.py": program.encode()})
    results = execute_fixed_checker(artifact, audit_file=audit_file(path), rule=rule, group_context=None, work=tmp_path / "fixed", digest=artifact.input_digest, executor_revision="coverage")
    frozen = _frozen_target(raw, clauses)
    response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, results)])
    by_clause = {item.locator["semantic"]["clause_id"]: item for item in response.findings}
    question = response.coverage.clarifications[0]

    assert [(item.clause_id, item.status) for item in results] == [("A", "UNABLE"), ("B", "NG")]
    assert by_clause["A"].status == "UNABLE"
    assert by_clause["B"].status == "NG"
    assert "已比较" in by_clause["B"].suggestions
    assert question["id"] == "header-meaning"
    assert question["kind"] == "interpretation"
    assert question["input_id"] == "version-1"
    assert question["rule_ids"] == ["A"]
    assert question["scope"]["rule_targets"] == [{"document_id": "version-1", "rule_id": "A"}]
    assert question["candidates"][0]["explanation"] == "按左列表头理解"
    assert response.coverage.verification is None


def test_two_results_for_one_clause_do_not_reject_the_other_clause() -> None:
    from agent.mapping import checker_response, product_result
    from audit.audits import RoundInput, RoundWork
    from audit.contracts import AuditResultItem

    def item(clause_id: str, status: str, suggestions: str) -> AuditResultItem:
        return AuditResultItem(
            classification="规则审核",
            item=clause_id,
            priority="P1",
            status=status,
            suggestions=suggestions,
            clause_id=clause_id,
        )

    clauses = [
        {"id": "A", "category": "A", "rule_to_check": "第一项"},
        {"id": "B", "category": "B", "rule_to_check": "第二项"},
    ]
    frozen = _frozen_target(b"target-bytes", clauses)
    response = checker_response(
        request().model_copy(update={"files": [frozen]}),
        [(frozen, [item("A", "UNABLE", "第一次"), item("A", "NG", "第二次"), item("B", "NG", "保留")])],
    )
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
    by_key = {item.item_key: item for item in response.findings}

    assert by_key["version-1:rule:A"].status == "UNABLE"
    assert by_key["version-1:rule:A:2"].status == "NG"
    assert by_key["version-1:rule:A:2"].locator["semantic"]["binding"] == "duplicate"
    assert by_key["version-1:rule:B"].status == "NG"
    assert by_key["version-1:rule:B"].suggestions == "保留"
    assert [row["item_key"] for row in stored["findings"]] == ["version-1:rule:A", "version-1:rule:B", "version-1:rule:A:2"]
    assert response.coverage.verification is None


def test_frozen_suite_formalization_is_the_development_what(tmp_path: Path) -> None:
    """The runner is scripted. This does not accept model semantic quality."""
    from audit.formal_digest import display_digest, formal_digest

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)

    def seal(item: dict) -> dict:
        payload = {
            "format_version": "1",
            "formal_profile": item["formal_profile"],
            "owl_content": item["owl_content"],
            "clif_content": item["clif_content"],
            "vocabulary": item["vocabulary"],
            "source_rule_version_id": "rule-9",
            "clause_id": "c1",
            "obligations": item["obligations"],
            "unresolved": [],
            "assumptions": item["assumptions"],
        }
        sealed = {**item, **payload, "rule_version_id": "rule-9", "formal_digest": formal_digest(payload)}
        sealed["display_digest"] = display_digest(sealed["cnl_text"], sealed["formal_digest"], [], sealed["completeness"])
        return sealed

    confirmed = seal(
        {
            "completeness": "complete",
            "formal_profile": "owl2-plus-clif-v1",
            "owl_content": "Ontology(Class(Invoice))",
            "clif_content": "(cardinality-exists)",
            "cnl_text": "每一条发票至少关联一条订单",
            "display_digest": "b" * 64,
            "source_rule_digest": "c" * 64,
            "confirmed_by": "user-1",
            "confirmed_at": "2026-11-10T00:00:00Z",
            "confirmed_candidate_id": "cand-9",
            "source_suite_revision_id": "suite-9",
            "obligations": [],
            "assumptions": [],
            "vocabulary": {},
        }
    )
    body = {
        "rule_profile": "v3",
        "type": "自定义",
        "rules": [{"id": "c1", "rule_to_check": "每一条发票至少关联一条订单"}],
        "appendix": [{"note": "附录甲"}],
        "formalizations": {"c1": confirmed},
    }
    encoded = json.dumps(body).encode()

    def frozen(payload: dict) -> RuleInput:
        return RuleInput.model_validate({**payload, "rule_id": "rule-9"})

    class Stop:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.input_tokens = 0
            self.output_tokens = 0

        def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
            del work, schema, deadline, reasoning_effort
            self.prompts.append(prompt)
            raise ModelProtocolError("stop")

    def capture(payload: dict) -> str:
        developer = Stop()
        host = Checker(developer, tmp_path)
        with pytest.raises(DeepError) as caught:
            host.adapt_checker(audit_file(path), frozen(payload), raw)
        assert caught.value.code == "deep_model_protocol"
        assert developer.prompts
        return developer.prompts[0]

    prompt = capture(json.loads(encoded))
    guidance = prompt.split("已确认形式要求只约束本次列出的条款", 1)[1]
    assert "条款 c1 的已确认形式要求" in guidance
    assert "cand-9" in guidance and "suite-9" in guidance
    assert "(cardinality-exists)" in guidance and "(cardinality-unique)" not in guidance
    assert "每一条发票至少关联一条订单" in prompt and "附录甲" in prompt
    assert "不要把唯一匹配" in prompt
    other = json.loads(encoded)
    other["formalizations"]["c1"] = seal({**confirmed, "clif_content": "(cardinality-unique)", "cnl_text": "每一条发票恰好关联一条订单", "source_suite_revision_id": "suite-10"})
    changed = capture(other)
    assert "(cardinality-unique)" in changed and "(cardinality-exists)" not in changed
    assert changed != prompt
    assert input_digest(audit_file(path), frozen(json.loads(encoded)), file_sha256="a" * 64, role="target") != input_digest(audit_file(path), frozen(other), file_sha256="a" * 64, role="target")
    assert json.loads(encoded)["formalizations"]["c1"]["clif_content"] == "(cardinality-exists)"


def test_a_supply_need_reaches_the_finding_only_for_an_unfinished_clause() -> None:
    from agent.mapping import checker_response
    from agent.protocol import FrozenFile
    from audit.contracts import AuditResultItem, SupplyNeed

    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256="a" * 64,
        rule_version_id="same-rule",
        rule_body={"rules": [{"id": "R1", "category": "对端", "rule_to_check": "与图纸一致"}, {"id": "R2", "category": "对端", "rule_to_check": "与图纸一致"}]},
    )
    need = SupplyNeed(kind="source_mismatch", document="图纸", part_number="P1", found_part_number="P2")
    unfinished = AuditResultItem(classification="对端", item="R1", priority="P0", status="UNABLE", suggestions="比对源《图纸》零件号不一致", clause_id="R1", supply=need)
    passed = AuditResultItem(classification="对端", item="R2", priority="P0", status="PASS", suggestions="一致", clause_id="R2", supply=need)
    response = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [unfinished, passed])])

    semantic = {finding.locator["rule_item_id"]: finding.locator["semantic"] for finding in response.findings}
    assert semantic["R1"]["supply"] == {"kind": "source_mismatch", "document": "图纸", "part_number": "P1", "found_part_number": "P2"}
    assert "supply" not in semantic["R2"]


def test_a_graded_ng_carries_its_level_and_override_into_the_finding() -> None:
    from agent.mapping import checker_response
    from agent.protocol import FrozenFile
    from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

    frozen = FrozenFile(
        file_version_id="version-1",
        file_id="file-1",
        role="target",
        filename="target.xlsx",
        sha256="a" * 64,
        rule_version_id="same-rule",
        rule_body={"rules": [{"id": "R1", "category": "孔径", "rule_to_check": "孔径在公差内", "severity": "B"}]},
    )
    evidence = AuditEvidence(
        required="structure",
        used="structure",
        source_format="xlsx",
        prepared_format="xlsx",
        sources=(EvidenceSource(kind="structure", locator='{"kind":"xlsx_cell","sheet":"检验","cell":"A2","file_version_id":"version-1"}'),),
    )
    result = AuditResultItem.model_validate(
        {
            "classification": "孔径",
            "item": "R1",
            "priority": "P0",
            "status": "NG",
            "suggestions": "孔径超差",
            "clause_id": "R1",
            "checks_performed": 1,
            "evidence": evidence.model_dump(),
            "severity": "A",
            "severity_override": {"default": "B", "level": "A", "reason": "超差导致总装干涉"},
        }
    )
    semantic = checker_response(request().model_copy(update={"files": [frozen]}), [(frozen, [result])]).findings[0].locator["semantic"]
    assert semantic["severity"] == "A"
    assert semantic["severity_override"] == {"default": "B", "level": "A", "reason": "超差导致总装干涉"}
