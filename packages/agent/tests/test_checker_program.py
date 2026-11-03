import hashlib
from pathlib import Path

import pytest
from agent.auditor import SemanticRuntime
from agent.checker_program import input_digest
from agent.checker_spec import CheckerArtifact
from agent.client import DeepError
from agent.execution.guest_model import ModelProtocolError
from agent.protocol import ExecuteRequest, FrozenFile
from agent.run import ExecutionContext
from audit.contracts import AuditInput, RuleInput
from audit.kinds import PpapDocumentType
from openpyxl import Workbook

REVISION = "checker-test-revision"
VALID_PROGRAM = """
from audit.checkers.v3.excel_agent import ExcelAgent, rule
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
from audit.checkers.v3.excel_agent import ExcelAgent, rule
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


class MemoryObjects:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    def get(self, key: str, max_bytes: int | None = None) -> bytes:
        from audit.storage_errors import ObjectMissing

        if key not in self.items:
            raise ObjectMissing(key)
        return self.items[key]

    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None:
        from audit.storage_errors import ObjectExists

        if key in self.items:
            raise ObjectExists(key)
        self.items[key] = body


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


class IsolatedRuntime:
    adapts_checkers = True

    def __init__(self, developer: ScriptedDeveloper) -> None:
        self.developer = developer
        self.operations: list[str] = []

    def engine_revision(self) -> str:
        return REVISION

    def ready(self) -> bool:
        return True

    def run(self, spec, inputs: Path, work: Path, deadline: float, cancelled, on_progress=None, prior_usage=None) -> None:
        from agent.checker_program import run_adapt_manifest, run_execute_manifest

        self.operations.append(spec.operation)
        if spec.operation == "adapt-checker":
            run_adapt_manifest(inputs, work, self.developer, deadline)
            return
        if spec.operation == "execute-checker":
            run_execute_manifest(inputs, work)
            return
        raise AssertionError(spec.operation)


def request() -> ExecuteRequest:
    return ExecuteRequest(protocol_revision="test", round_id="round-1", files=[], executor="ppap-deep", executor_revision=REVISION, job_id="job-1")


def broken_draft() -> dict:
    return {"entrypoint": "checker:create_audit", "files": {"checker.py": "def unused():\n    return None\n"}}


def valid_draft() -> dict:
    return {"entrypoint": "checker:create_audit", "files": {"checker.py": VALID_PROGRAM}}


@pytest.mark.parametrize(
    "code,expected",
    [
        ("INPUT_INTEGRITY_ERROR", "deep_integrity_mismatch"),
        ("MODEL_TIMEOUT", "deep_timeout"),
        ("MODEL_PROTOCOL_ERROR", "deep_model_protocol"),
        ("RESOURCE_LIMIT", "deep_budget_exhausted"),
        ("ISOLATION_UNAVAILABLE", "deep_runtime_unavailable"),
        ("CANCELLED", "deep_cancelled"),
    ],
)
def test_checker_runtime_fault_keeps_its_product_failure_code(tmp_path: Path, code: str, expected: str) -> None:
    from audit.protocol import ControllerFault

    class FailingRuntime(IsolatedRuntime):
        def run(self, *args, **kwargs):
            raise ControllerFault(code)

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    host = SemanticRuntime(FailingRuntime(ScriptedDeveloper([])), MemoryObjects(), timeout=30)
    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())
    assert caught.value.code == expected


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
    runtime = IsolatedRuntime(developer)
    host = SemanticRuntime(runtime, MemoryObjects(), timeout=30)
    prior = CheckerArtifact(input_digest="a" * 64, executor_revision="old", entrypoint="checker:create_audit", files={"checker.py": b"# PRIOR-START\n"})
    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext(), prior=prior)

    assert developer.calls == 3
    assert "缺少 create_audit" in developer.prompts[1]
    assert "这些结果只说明程序跑完，不说明业务判断正确。" in developer.prompts[2]
    assert "R4|UNABLE" in developer.prompts[2]
    assert "PRIOR-START" in developer.prompts[0]
    assert artifact.entrypoint == "checker:create_audit"
    stored = [key for key in host.objects.items if key.endswith(f"/checkers/{artifact.input_digest}.json")]
    assert stored
    assert rules().rule_id not in Path(stored[0]).name
    trial_key = stored[0].replace(".json", ".trials.json")
    assert "这些结果只说明程序跑完，不说明业务判断正确。" in host.objects.items[trial_key].decode()
    assert "R4|UNABLE" in host.objects.items[trial_key].decode()

    calls_after_adapt = developer.calls
    results = host.execute_checker(request(), artifact, audit_file(path), rules(), raw, role="target", context=ExecutionContext())
    assert developer.calls == calls_after_adapt
    assert runtime.operations == ["adapt-checker", "execute-checker"]
    assert [(item.clause_id, item.status) for item in results] == [("R4", "UNABLE")]
    assert "未确定工序范围" in results[0].suggestions


def test_a_fixed_checker_records_the_question_source_and_unanswered_state(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "过程流程图"
    sheet["A1"] = "工序名称"
    sheet["A2"] = "焊接"
    book.save(path)
    book.close()
    raw = path.read_bytes()
    program = """
from audit.checkers.v3.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.kinds import PpapDocumentType


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True
    EXPECTED_RULE_CATEGORIES = ("工序名称",)

    @rule("工序名称", clause_id="R4")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        ctx.note_cell("过程流程图", 2, 1)
        ctx.ask("header-meaning", "表头按什么理解？", (("left", "左列", "按左列表头理解"), ("right", "右列", "按右列表头理解")))
        return "PASS", "未确认"


def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": program}}, {"entrypoint": "checker:create_audit", "files": {"checker.py": program}}])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())
    stored = [key for key in host.objects.items if key.endswith(f"/checkers/{artifact.input_digest}.json")]
    text = host.objects.items[stored[0].replace(".json", ".trials.json")].decode()

    import json

    rows = json.loads(text)
    question = json.loads(rows[1])
    assert "这些结果只说明程序跑完，不说明业务判断正确。" in rows[0]
    assert question["id"] == "header-meaning"
    assert question["question"] == "表头按什么理解？"
    assert question["answer"] is None
    assert question["candidates"] == [
        {"id": "left", "label": "左列", "explanation": "按左列表头理解"},
        {"id": "right", "label": "右列", "explanation": "按右列表头理解"},
    ]
    assert question["clause_id"] == "R4"
    assert question["sources"][0]["locator"] == {"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A2"}


def test_recomputing_an_observation_is_a_new_calculation() -> None:
    import json

    from agent.checker_program import store_observation
    from audit.storage_errors import ObjectExists

    class Once:
        def __init__(self) -> None:
            self.items: dict[str, bytes] = {}

        def put_exclusive(self, key: str, body: bytes, content_type: str) -> None:
            if key in self.items:
                raise ObjectExists(key)
            self.items[key] = body

    store = Once()
    observation = ["上次试跑已覆盖全部生效条款"]
    first = store_observation(store, "checkers/abc.trials.json", observation)
    second = store_observation(store, "checkers/abc.trials.json", observation)

    assert first == "checkers/abc.trials.json"
    assert second != first
    assert ".observations/" in second
    assert json.loads(store.items[first]) == observation
    recomputed = json.loads(store.items[second])
    assert recomputed["recomputed"] is True
    assert recomputed["observations"] == observation
    assert recomputed["computation_id"]
    assert first not in store.items or store.items[first] == store.items[first]


def test_two_failed_trials_can_still_be_confirmed(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper([broken_draft(), broken_draft(), valid_draft(), valid_draft()])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    assert developer.calls == 4
    assert artifact.files["checker.py"] == VALID_PROGRAM.encode()


def test_failed_checker_development_does_not_publish_an_empty_success(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper([broken_draft() for _ in range(6)])
    runtime = IsolatedRuntime(developer)
    host = SemanticRuntime(runtime, MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    assert developer.calls == 6
    assert caught.value.code == "deep_output_invalid"
    assert host.objects.items == {}


def test_stored_checker_result_enters_the_product_record_without_trace_verification(tmp_path: Path) -> None:
    from agent.mapping import product_result
    from agent.protocol import DeepVerification, FrozenFile
    from audit.audits import RoundInput, RoundWork

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    sha = hashlib.sha256(raw).hexdigest()
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
        sha256=sha,
        rule_version_id="same-rule",
        rule_body=body,
    )
    spec = request().model_copy(update={"files": [frozen]})
    developer = ScriptedDeveloper([valid_draft()])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    host.adapt_checker(spec, audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    response = host.execute(spec, {"version-1": raw}, context=ExecutionContext())
    work = RoundWork(
        round_id=spec.round_id,
        tenant_id=spec.tenant_id,
        inputs=[
            RoundInput(
                role="target",
                file_id="file-1",
                file_version_id="version-1",
                filename="target.xlsx",
                status="ready",
                sha256=sha,
                rule_version_id="same-rule",
                rule_body=body,
            )
        ],
    )
    stored = product_result(response, work)

    assert stored["note"] == "deep_checker_audit"
    assert stored["coverage"]["verification"] is None
    assert stored["coverage"]["rule_items"][0]["verification_scope"] == "checker_execution"
    assert stored["coverage"]["rule_items"][0]["status"] == "UNABLE"
    assert stored["findings"][0]["locator"]["semantic"]["clause_id"] == "R4"
    assert "host_source_trace_v1" not in str(stored["coverage"])

    historical = response.model_copy(
        update={
            "note": "deep_semantic_audit",
            "coverage": response.coverage.model_copy(
                update={
                    "verification": DeepVerification(
                        method="host_source_trace_v1",
                        round_id=spec.round_id,
                        job_id=spec.job_id,
                        attempt=spec.attempt,
                        executor_revision=REVISION,
                        semantic_engine_revision="a" * 64,
                    )
                }
            ),
        }
    )
    preserved = product_result(historical, work)
    assert preserved["coverage"]["verification"]["method"] == "host_source_trace_v1"
    assert host._stored_checker_response(spec, {"version-1": b"different-bytes"}, ExecutionContext()) is None
    missed = SemanticRuntime(IsolatedRuntime(ScriptedDeveloper([])), MemoryObjects(), timeout=30)
    assert missed._stored_checker_response(spec, {"version-1": raw}, ExecutionContext()) is None


def test_execute_rejects_the_same_rule_on_another_file_before_running_it(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper([valid_draft()])
    runtime = IsolatedRuntime(developer)
    host = SemanticRuntime(runtime, MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    def reject_run(*_args, **_kwargs):
        raise AssertionError("fixed program ran for a different file")

    runtime.run = reject_run
    with pytest.raises(DeepError) as caught:
        host.execute_checker(request(), artifact, audit_file(path), rules(), raw, role="reference", context=ExecutionContext())

    assert caught.value.code == "deep_output_identity_mismatch"


def _reject_run(*_args, **_kwargs):
    raise AssertionError("fixed program ran")


def test_execute_checker_rejects_identity_changes_before_running(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    runtime = IsolatedRuntime(ScriptedDeveloper([valid_draft()]))
    host = SemanticRuntime(runtime, MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())
    runtime.run = _reject_run

    changed = rules()
    changed.rules[0]["rule_to_check"] = "另一项要求"
    with pytest.raises(DeepError) as caught:
        host.execute_checker(request(), artifact, audit_file(path), changed, raw, role="target", context=ExecutionContext())
    assert caught.value.code == "deep_output_identity_mismatch"

    with pytest.raises(DeepError) as caught:
        host.execute_checker(request(), artifact, audit_file(path), rules(), b"different-bytes", role="target", context=ExecutionContext())
    assert caught.value.code == "deep_output_identity_mismatch"

    moved = artifact.model_copy(update={"executor_revision": "other-revision"})
    with pytest.raises(DeepError) as caught:
        host.execute_checker(request(), moved, audit_file(path), rules(), raw, role="target", context=ExecutionContext())
    assert caught.value.code == "deep_output_identity_mismatch"

    other_spec = request().model_copy(update={"executor_revision": "other-revision"})
    with pytest.raises(DeepError) as caught:
        host.execute_checker(other_spec, artifact, audit_file(path), rules(), raw, role="target", context=ExecutionContext())
    assert caught.value.code == "executor_revision_mismatch"


def test_a_swapped_program_does_not_execute_under_the_same_input_digest(tmp_path: Path) -> None:
    from agent.checker_spec import program_digest
    from agent.execution.checker_guest import CheckerProgramError, execute_fixed_checker
    from pydantic import ValidationError

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    runtime = IsolatedRuntime(ScriptedDeveloper([valid_draft(), valid_draft()]))
    host = SemanticRuntime(runtime, MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())
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


def test_product_execute_rejects_a_stored_checker_with_a_different_digest(tmp_path: Path) -> None:
    from agent.checker_program import encoded
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    sha = hashlib.sha256(raw).hexdigest()
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
        sha256=sha,
        rule_version_id="same-rule",
        rule_body=body,
    )
    spec = request().model_copy(update={"files": [frozen]})
    runtime = IsolatedRuntime(ScriptedDeveloper([valid_draft()]))
    host = SemanticRuntime(runtime, MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(spec, audit_file(path), rules(), raw, role="target", context=ExecutionContext())
    key = next(item for item in host.objects.items if item.endswith(f"/checkers/{artifact.input_digest}.json"))
    host.objects.items[key] = encoded(artifact.model_copy(update={"input_digest": "b" * 64}).model_dump(mode="json"))
    runtime.run = _reject_run

    with pytest.raises(DeepError) as caught:
        host.execute(spec, {"version-1": raw}, context=ExecutionContext())

    assert caught.value.code == "deep_output_identity_mismatch"


def test_a_pass_with_text_but_no_check_does_not_become_pass(tmp_path: Path) -> None:
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    sha = hashlib.sha256(raw).hexdigest()
    stated = valid_draft()
    stated["files"]["checker.py"] = VALID_PROGRAM.replace('return "UNABLE", "未确定工序范围"', 'return "PASS", "只写了建议"')
    checked = valid_draft()
    checked["files"]["checker.py"] = VALID_PROGRAM.replace(
        'return "UNABLE", "未确定工序范围"',
        'ctx.record_execution()\n        return "PASS", ""',
    )
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    frozen = FrozenFile(file_version_id="version-1", file_id="file-1", role="target", filename="target.xlsx", sha256=sha, rule_version_id="same-rule", rule_body=body)
    spec = request().model_copy(update={"files": [frozen]})

    stated_host = SemanticRuntime(IsolatedRuntime(ScriptedDeveloper([stated])), MemoryObjects(), timeout=30)
    stated_host.adapt_checker(spec, audit_file(path), rules(), raw, role="target", context=ExecutionContext())
    stated_response = stated_host.execute(spec, {"version-1": raw}, context=ExecutionContext())
    assert [item.status for item in stated_response.findings] == ["ERR"]
    assert "零断言不能判为通过" in stated_response.findings[0].suggestions

    checked_host = SemanticRuntime(IsolatedRuntime(ScriptedDeveloper([checked])), MemoryObjects(), timeout=30)
    checked_host.adapt_checker(spec, audit_file(path), rules(), raw, role="target", context=ExecutionContext())
    checked_response = checked_host.execute(spec, {"version-1": raw}, context=ExecutionContext())
    assert [item.status for item in checked_response.findings] == ["PASS"]


def test_empty_pass_from_a_fixed_checker_does_not_become_pass(tmp_path: Path) -> None:
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    sha = hashlib.sha256(raw).hexdigest()
    empty_pass = valid_draft()
    empty_pass["files"]["checker.py"] = VALID_PROGRAM.replace('return "UNABLE", "未确定工序范围"', 'return "PASS", ""')
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    frozen = FrozenFile(file_version_id="version-1", file_id="file-1", role="target", filename="target.xlsx", sha256=sha, rule_version_id="same-rule", rule_body=body)
    spec = request().model_copy(update={"files": [frozen]})
    host = SemanticRuntime(IsolatedRuntime(ScriptedDeveloper([empty_pass])), MemoryObjects(), timeout=30)
    host.adapt_checker(spec, audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    response = host.execute(spec, {"version-1": raw}, context=ExecutionContext())

    assert [item.status for item in response.findings] == ["ERR"]
    assert "空结果不能判为通过" in response.findings[0].suggestions
    assert "PASS" not in {item.status for item in response.findings}


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


def test_explicit_empty_scope_pass_reaches_the_product_without_becoming_zero_assertion_err(tmp_path: Path) -> None:
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    sha = hashlib.sha256(raw).hexdigest()
    allowed = valid_draft()
    allowed["files"]["checker.py"] = VALID_PROGRAM.replace(
        'return "UNABLE", "未确定工序范围"',
        'ctx.record_empty_scope("PASS", "规则写明没有工序即通过")\n        return "PASS", "方法末尾通过"',
    )
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    frozen = FrozenFile(file_version_id="version-1", file_id="file-1", role="target", filename="target.xlsx", sha256=sha, rule_version_id="same-rule", rule_body=body)
    spec = request().model_copy(update={"files": [frozen]})
    host = SemanticRuntime(IsolatedRuntime(ScriptedDeveloper([allowed])), MemoryObjects(), timeout=30)
    host.adapt_checker(spec, audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    response = host.execute(spec, {"version-1": raw}, context=ExecutionContext())

    assert [item.status for item in response.findings] == ["PASS"]
    assert response.findings[0].suggestions == "规则写明没有工序即通过"
    assert response.findings[0].locator["semantic"]["empty_scope_basis"] == "规则写明没有工序即通过"
    assert "零断言不能判为通过" not in response.findings[0].suggestions
    assert response.coverage.verification is None


def test_one_stored_checker_failure_does_not_drop_the_other_target(tmp_path: Path) -> None:
    from agent.checker_program import encoded, input_digest
    from agent.checker_spec import CheckerArtifact
    from agent.protocol import FrozenFile

    good_path = tmp_path / "good.xlsx"
    bad_path = tmp_path / "bad.xlsx"
    good_raw = workbook_bytes(good_path)
    bad_raw = b"not-a-workbook-but-hashed"
    bad_path.write_bytes(bad_raw)
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    good = FrozenFile(
        file_version_id="version-good",
        file_id="file-good",
        role="target",
        filename="good.xlsx",
        sha256=hashlib.sha256(good_raw).hexdigest(),
        rule_version_id="same-rule",
        rule_body=body,
    )
    bad = FrozenFile(
        file_version_id="version-bad",
        file_id="file-bad",
        role="target",
        filename="bad.xlsx",
        sha256=hashlib.sha256(bad_raw).hexdigest(),
        rule_version_id="same-rule",
        rule_body=body,
    )
    spec = request().model_copy(update={"files": [bad, good]})
    host = SemanticRuntime(IsolatedRuntime(ScriptedDeveloper([valid_draft()])), MemoryObjects(), timeout=30)
    host.adapt_checker(
        spec,
        AuditInput(audit_id=good.file_version_id, file_url=good.filename, file_metadata={"file_version_id": good.file_version_id, "sha256": good.sha256, "role": good.role}),
        rules(),
        good_raw,
        role="target",
        context=ExecutionContext(),
    )
    boom = """
class Boom:
    def check(self):
        raise RuntimeError("target failed")

def create_audit(audit_file, rule, group_context=None):
    class Audit:
        checks = (Boom(),)
        def close(self):
            return None
    return Audit()
"""
    bad_audit = AuditInput(audit_id=bad.file_version_id, file_url=bad.filename, file_metadata={"file_version_id": bad.file_version_id, "sha256": bad.sha256, "role": bad.role})
    digest = input_digest(bad_audit, rules(), file_sha256=bad.sha256, role=bad.role)
    artifact = CheckerArtifact(input_digest=digest, executor_revision=REVISION, entrypoint="checker:create_audit", files={"checker.py": boom.encode()})
    host.objects.put_exclusive(f"{host._attempt_prefix(spec)}/checkers/{digest}.json", encoded(artifact.model_dump(mode="json")), "application/json")

    response = host.execute(spec, {bad.file_version_id: bad_raw, good.file_version_id: good_raw}, context=ExecutionContext())

    by_file = {item.locator["file_version_id"]: item for item in response.findings}
    assert by_file[bad.file_version_id].status == "ERR"
    assert by_file[good.file_version_id].status == "UNABLE"
    assert response.coverage.rule_items[0]["coverage"] == "execution_error"
    assert "PASS" not in {item.status for item in response.findings if item.locator["file_version_id"] == bad.file_version_id}
    assert bad.filename not in response.coverage.checked
    assert good.filename in response.coverage.checked


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
    from agent.checker_program import group_context_for_spec
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    related_raw = b"related-rules"
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    reference_body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name(),
        "rules": [{"id": "S1", "category": "特性", "rule_to_check": "长度必须标注单位"}],
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
        file_version_id="related-1",
        file_id="file-related",
        role="reference",
        filename="special.xlsx",
        sha256=hashlib.sha256(related_raw).hexdigest(),
        rule_version_id="related-rule",
        rule_body=reference_body,
    )
    group = group_context_for_spec(request().model_copy(update={"files": [target, reference]}))
    program = VALID_PROGRAM.replace(
        'return "UNABLE", "未确定工序范围"',
        'return "UNABLE", self.group_context.rules_by_id["related-1"].rules[0]["rule_to_check"]',
    )
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": program}}])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(
        request(),
        audit_file(path),
        rules(),
        raw,
        role="target",
        context=ExecutionContext(),
        group_context=group,
        related_bytes={"related-1": related_raw},
    )
    assert "长度必须标注单位" in developer.prompts[0]
    assert "工序名称不为空时要有操作描述" in developer.prompts[0]
    results = host.execute_checker(
        request(),
        artifact,
        audit_file(path),
        rules(),
        raw,
        role="target",
        context=ExecutionContext(),
        group_context=group,
        related_bytes={"related-1": related_raw},
    )
    assert results[0].status == "UNABLE"
    assert results[0].suggestions == "长度必须标注单位"


def test_product_execute_stages_reference_bytes_inside_the_guest(tmp_path: Path) -> None:
    from agent.checker_program import group_context_for_spec, verified_related_bytes
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    related_raw = b"RELATED-OK"
    host_secret = tmp_path / "HOST-ONLY-PATH.xlsx"
    host_secret.write_bytes(related_raw)
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    related_type = PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name()
    target = FrozenFile(file_version_id="version-1", file_id="file-1", role="target", filename="target.xlsx", sha256=hashlib.sha256(raw).hexdigest(), rule_version_id="same-rule", rule_body=body)
    reference = FrozenFile(
        file_version_id="related-1",
        file_id="file-related",
        role="reference",
        filename="special.xlsx",
        sha256=hashlib.sha256(related_raw).hexdigest(),
        rule_body={"type": related_type},
    )
    spec = request().model_copy(update={"files": [target, reference]})
    group = group_context_for_spec(spec)
    assert group is not None and group.by_doc_type
    poisoned = group.model_copy(deep=True)
    for item in poisoned.by_audit_id.values():
        item.file_url = str(host_secret)
    for rows in poisoned.by_doc_type.values():
        for item in rows:
            item.file_url = str(host_secret)
    program = VALID_PROGRAM.replace(
        'return "UNABLE", "未确定工序范围"',
        """
        from audit.kinds import PpapDocumentType
        files = self.group_context.get_files_by_type(PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST)
        text = open(files[0].file_url, "rb").read().decode()
        return "UNABLE", files[0].file_url + "|" + text
        """,
    )
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": program}}])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    host.adapt_checker(spec, audit_file(path), rules(), raw, role="target", context=ExecutionContext(), group_context=poisoned, related_bytes=verified_related_bytes(spec, {"related-1": related_raw}))
    assert "related-" in developer.prompts[0]
    assert "target.xlsx" in developer.prompts[0]
    assert str(host_secret) not in developer.prompts[0]

    response = host.execute(spec, {"version-1": raw, "related-1": related_raw}, context=ExecutionContext())

    suggestion = response.findings[0].suggestions
    assert "RELATED-OK" in suggestion
    assert "related-" in suggestion
    assert str(host_secret) not in suggestion
    assert reference.filename in "".join(response.coverage.unchecked)

    with pytest.raises(DeepError) as caught:
        host.execute(spec, {"version-1": raw, "related-1": b"WRONG"}, context=ExecutionContext())
    assert caught.value.code == "deep_integrity_mismatch"


def test_fresh_execute_adapts_once_and_replays_the_fixed_checker(tmp_path: Path) -> None:
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    sha = hashlib.sha256(raw).hexdigest()
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    frozen = FrozenFile(file_version_id="version-1", file_id="file-1", role="target", filename="target.xlsx", sha256=sha, rule_version_id="same-rule", rule_body=body)
    spec = request().model_copy(update={"files": [frozen]})
    developer = ScriptedDeveloper([valid_draft()])
    developer.input_tokens = 11
    developer.output_tokens = 4
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    host.runtime.usage_source = "provider"

    first = host.execute(spec, {"version-1": raw}, context=ExecutionContext())
    calls = developer.calls
    second = host.execute(spec, {"version-1": raw}, context=ExecutionContext())

    assert first.note == "deep_checker_audit"
    assert first.findings[0].status == "UNABLE"
    assert first.usage["model_requests"] == 2
    assert first.usage["input_tokens"] == 11
    assert first.usage["output_tokens"] == 4
    assert first.usage_source == "provider"
    assert second.usage == {}
    assert developer.calls == calls
    assert second.findings[0].status == "UNABLE"
    assert "host_source_trace_v1" not in str(second.coverage)

    resume = spec.model_copy(update={"attempt": 2, "resume_attempt": 1})
    resumed = host.execute(resume, {"version-1": raw}, context=ExecutionContext())
    assert developer.calls == calls
    assert resumed.note == "deep_checker_audit"
    assert resumed.findings[0].status == "UNABLE"
    assert resumed.usage == first.usage
    assert resumed.usage_source == first.usage_source
    assert host.runtime.operations.count("adapt-checker") == 1


def test_uncertain_checker_adapt_is_not_retried_on_the_same_attempt(tmp_path: Path) -> None:
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    sha = hashlib.sha256(raw).hexdigest()
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    frozen = FrozenFile(file_version_id="version-1", file_id="file-1", role="target", filename="target.xlsx", sha256=sha, rule_version_id="same-rule", rule_body=body)
    spec = request().model_copy(update={"files": [frozen]})

    class UnknownDeveloper:
        def __init__(self) -> None:
            self.calls = 0
            self.input_tokens = 0
            self.output_tokens = 0

        def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
            self.calls += 1
            raise ModelProtocolError("usage unknown")

    developer = UnknownDeveloper()
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    with pytest.raises(DeepError) as caught:
        host.execute(spec, {"version-1": raw}, context=ExecutionContext())
    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 1

    with pytest.raises(DeepError) as caught:
        host.execute(spec, {"version-1": raw}, context=ExecutionContext())
    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 1
    assert not any(key.endswith("/bundle.json") for key in host.objects.items)

    resume = spec.model_copy(update={"attempt": 2, "resume_attempt": 1})
    with pytest.raises(DeepError) as caught:
        host.execute(resume, {"version-1": raw}, context=ExecutionContext())
    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 1
    assert host.runtime.operations == ["adapt-checker"]

    later = spec.model_copy(update={"attempt": 2})
    with pytest.raises(DeepError) as caught:
        host.execute(later, {"version-1": raw}, context=ExecutionContext())
    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 1

    following = spec.model_copy(update={"attempt": 3})
    with pytest.raises(DeepError) as caught:
        host.execute(following, {"version-1": raw}, context=ExecutionContext())
    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 1
    assert host.runtime.operations == ["adapt-checker"]


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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    rule = rules().model_copy(update={"appendix": appendix})
    artifact = host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())

    assert "本文件口径" in developer.prompts[0]
    assert "别的文件口径" not in developer.prompts[0]
    assert "其他条款口径" not in developer.prompts[0]
    assert "只限制了别的文件" not in developer.prompts[0]
    assert "只限制了别的条款" not in developer.prompts[0]
    assert "空目标列表" not in developer.prompts[0]
    results = host.execute_checker(request(), artifact, audit_file(path), rule, raw, role="target", context=ExecutionContext())
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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    target = audit_file(path)
    artifact = host.adapt_checker(request(), target, rules(), raw, role="target", context=ExecutionContext())

    assert developer.calls == 0
    results = host.execute_checker(request(), artifact, target, rules(), raw, role="target", context=ExecutionContext())
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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_model_protocol"
    prompt = developer.prompts[0]
    assert "不要按文档类型或文件名选择预设" in prompt
    assert "PPAP_" not in prompt
    assert "Control plan layout is not a preset" in prompt
    assert str(path) not in prompt


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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(
        request(),
        audit_file(path),
        rule,
        raw,
        role="target",
        context=ExecutionContext(),
    )

    assert "本次已确认附录" in developer.prompts[0]
    assert "空值不是通过" in developer.prompts[0]
    assert "不是上次结论" in developer.prompts[0]
    results = host.execute_checker(
        request(),
        artifact,
        audit_file(path),
        rule,
        raw,
        role="target",
        context=ExecutionContext(),
    )
    assert results[0].status == "UNABLE"
    assert results[0].suggestions == "空值不是通过"
    for changed_appendix in ([{"id": "note-1", "text": "只检查指定工序"}], []):
        changed = rule.model_copy(update={"appendix": changed_appendix})
        with pytest.raises(DeepError) as caught:
            host.execute_checker(request(), artifact, audit_file(path), changed, raw, role="target", context=ExecutionContext())
        assert caught.value.code == "deep_output_identity_mismatch"
    assert host.runtime.operations == ["adapt-checker", "execute-checker"]


def test_direct_checker_api_scopes_the_same_appendix_for_prompt_identity_and_execution(tmp_path: Path) -> None:
    from agent.checker_program import adapt, execute

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    target = audit_file(path)
    confirmed = {"id": "mine", "content": "本文件口径", "scope": {"rule_targets": [{"document_id": "version-1", "rule_id": "R4"}]}}
    unrelated = {"id": "other", "content": "别的文件口径", "scope": {"document_ids": ["other-version"]}}
    rule = rules().model_copy(update={"appendix": [confirmed, unrelated]})
    program = VALID_PROGRAM.replace(
        'return "UNABLE", "未确定工序范围"',
        'return "UNABLE", "|".join(item["content"] for item in self.rule.appendix) or "无确认附录"',
    )
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": program}}])
    args = dict(file_bytes=raw, file_sha256=hashlib.sha256(raw).hexdigest(), role="target", executor_revision=REVISION)
    artifact = adapt(target, rule, runner=developer, work=tmp_path / "adapt", deadline=10**12, **args)
    assert "本文件口径" in developer.prompts[0]
    assert "别的文件口径" not in developer.prompts[0]
    scoped = rule.model_copy(update={"appendix": [confirmed]})
    assert artifact.input_digest == input_digest(target, scoped, file_sha256=args["file_sha256"], role="target")
    result = execute(artifact, target, scoped, work=tmp_path / "execute", **args)[0]
    assert result.status == "UNABLE"
    assert result.suggestions == "本文件口径"

    for changed_appendix in ([{**confirmed, "content": "修改本文件口径"}], []):
        changed = rule.model_copy(update={"appendix": changed_appendix})
        with pytest.raises(DeepError) as caught:
            execute(artifact, target, changed, work=tmp_path / "changed", **args)
        assert caught.value.code == "deep_output_identity_mismatch"

    removed = rule.model_copy(update={"appendix": []})
    fresh = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": program}}])
    replacement = adapt(target, removed, runner=fresh, work=tmp_path / "removed", deadline=10**12, **args)
    assert "本次已确认附录" not in fresh.prompts[0]
    result = execute(replacement, target, removed, work=tmp_path / "removed-execute", **args)[0]
    assert result.status == "UNABLE"
    assert result.suggestions == "无确认附录"
    assert rule.appendix == [confirmed, unrelated]


def test_development_prompt_names_framework_modules_and_hides_acceptance_samples(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper([valid_draft()])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    prompt = developer.prompts[0]
    assert "from audit.checkers.v3.excel_agent import ExcelAgent, rule" in prompt
    assert "from audit.engine.rule_engine.fluent import Table" in prompt
    assert "from documents.pdf import PdfSpec" in prompt
    assert "不要按文档类型或文件名选择预设" in prompt
    assert "/opt/agent/" not in prompt
    assert "tests/corpus" not in prompt
    assert str(path) not in prompt
    assert "用文件工具" not in prompt
    assert "resource=RESOURCE" in prompt
    assert "进入同一个 `ctx.resource`" in prompt
    assert "CLAUSE_BOUND_CHECKS = True" in prompt
    assert "ctx.bind_workbook(workbook, 关联文件版本ID)" in prompt
    assert "ctx.note_cell(表名, 行号, 列号, file_version_id=关联文件版本ID)" in prompt
    assert "ctx.ask(" in prompt
    assert "未定义数据区规范" in prompt
    assert "不要读取 `unreadable`" in prompt
    assert "`state` 只能是 `known`、`blank`、`missing` 或 `unresolved`" in prompt
    assert "ColumnModel" in prompt


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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rules(), path.read_bytes(), role="target", context=ExecutionContext())

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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    with pytest.raises(DeepError) as caught:
        host.adapt_checker(
            request(),
            audit_file(target),
            rules(),
            target.read_bytes(),
            role="target",
            context=ExecutionContext(),
            group_context=group,
            related_bytes={"related-xlsx": related_raw, "related-pdf": pdf_raw},
        )

    assert caught.value.code == "deep_model_protocol"
    prompt = developer.prompts[0]
    assert "目标文件 version-1" in prompt
    assert "关联文件 related-xlsx，角色 reference" in prompt
    assert "特性名称 | 规格" in prompt
    assert "不要把这里的单元格记到目标文件" in prompt
    assert "关联文件 related-pdf，角色 reference" in prompt
    assert "这是 PDF。这里没有单元格预览。" in prompt
    assert "# PDF 资源适配" in prompt
    assert "template-pdf-spec.py" in prompt


def test_related_pdf_requires_the_spec_for_its_file_version(tmp_path: Path) -> None:
    from audit.checkers.v3.excel_agent import ExcelAgent
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
            "from audit.checkers.v3.excel_agent import ExcelAgent, rule",
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
    from audit.checkers.v3.excel_agent import ExcelAgent
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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 2
    assert "PDF 的观察与表格建模" not in developer.prompts[0]
    assert "模板尚未实现本次条款" in developer.prompts[1]
    assert host.objects.items == {}


def test_resume_replays_a_saved_checker_and_marks_a_missing_target_unexecuted(tmp_path: Path) -> None:
    from agent.checker_program import encoded, input_digest
    from agent.checker_spec import CheckerArtifact
    from agent.protocol import FrozenFile

    path = tmp_path / "good.xlsx"
    raw = workbook_bytes(path)
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    good = FrozenFile(
        file_version_id="version-good",
        file_id="file-good",
        role="target",
        filename="good.xlsx",
        sha256=hashlib.sha256(raw).hexdigest(),
        rule_version_id="same-rule",
        rule_body=body,
    )
    missing = FrozenFile(
        file_version_id="version-missing",
        file_id="file-missing",
        role="target",
        filename="missing.xlsx",
        sha256=hashlib.sha256(b"missing-bytes").hexdigest(),
        rule_version_id="same-rule",
        rule_body=body,
    )
    spec = request().model_copy(update={"files": [good, missing]})
    developer = ScriptedDeveloper([valid_draft()])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    audit = AuditInput(
        audit_id=good.file_version_id,
        file_url=good.filename,
        file_metadata={"file_version_id": good.file_version_id, "sha256": good.sha256, "role": good.role},
    )
    digest = input_digest(audit, rules(), file_sha256=good.sha256, role=good.role)
    artifact = CheckerArtifact(input_digest=digest, executor_revision=REVISION, entrypoint="checker:create_audit", files={"checker.py": VALID_PROGRAM.encode()})
    host.objects.put_exclusive(f"{host._attempt_prefix(spec)}/checkers/{digest}.json", encoded(artifact.model_dump(mode="json")), "application/json")

    resume = spec.model_copy(update={"attempt": 2, "resume_attempt": 1})
    response = host.execute(resume, {good.file_version_id: raw, missing.file_version_id: b"missing-bytes"}, context=ExecutionContext())

    by_file = {item.locator["file_version_id"]: item for item in response.findings}
    assert by_file[good.file_version_id].status == "UNABLE"
    assert by_file[missing.file_version_id].status == "ERR"
    assert developer.calls == 0
    assert host.runtime.operations == ["execute-checker"]


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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())
    results = host.execute_checker(request(), artifact, audit_file(path), rule, raw, role="target", context=ExecutionContext())

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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())

    assert developer.calls == 3
    assert "条款 B1 试跑失败" in developer.prompts[1]
    assert "缺原件位置 page:4" in developer.prompts[1]
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


def test_develop_checker_forwards_reasoning_effort(tmp_path: Path) -> None:
    from agent.execution.checker_guest import develop_checker

    path = tmp_path / "target.xlsx"
    workbook_bytes(path)
    seen: list[str] = []

    class EffortDeveloper:
        input_tokens = 0
        output_tokens = 0

        def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
            del work, prompt, deadline
            seen.append(reasoning_effort)
            return schema.model_validate(valid_draft())

    develop_checker(
        audit_file=audit_file(path),
        rule=rules(),
        group_context=None,
        digest="c" * 64,
        executor_revision=REVISION,
        runner=EffortDeveloper(),
        work=tmp_path / "adapt",
        deadline=10**12,
        reasoning_effort="high",
    )

    assert seen == ["high", "high"]


def test_a_failed_revision_does_not_fix_the_unconfirmed_program(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    revised = VALID_PROGRAM.replace("def create_audit", "def missing_entry", 1)
    developer = ScriptedDeveloper([valid_draft(), {"entrypoint": "checker:create_audit", "files": {"checker.py": revised}}, *[broken_draft() for _ in range(4)]])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 6
    assert host.objects.items == {}
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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 2
    assert host.objects.items == {}


def test_the_sixth_trial_cannot_fix_the_first_full_success(tmp_path: Path) -> None:
    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper([*[broken_draft() for _ in range(5)], valid_draft()])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 6
    assert host.objects.items == {}


def test_the_last_successful_trial_is_not_fixed_without_confirmation(tmp_path: Path) -> None:
    from agent.protocol import FrozenFile

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    sha = hashlib.sha256(raw).hexdigest()
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
        sha256=sha,
        rule_version_id="same-rule",
        rule_body=body,
    )
    spec = request().model_copy(update={"files": [frozen]})

    class StopBeforeConfirmation:
        def __init__(self) -> None:
            self.calls = 0
            self.input_tokens = 0
            self.output_tokens = 0
            self._drafts = [broken_draft(), broken_draft(), valid_draft()]

        def run(self, work: Path, prompt: str, schema, deadline: float, *, reasoning_effort: str):
            del work, prompt, deadline, reasoning_effort
            self.calls += 1
            if self._drafts:
                return schema.model_validate(self._drafts.pop(0))
            raise ModelProtocolError("confirmation dropped")

    developer = StopBeforeConfirmation()
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.execute(spec, {"version-1": raw}, context=ExecutionContext())

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 4
    assert not any(key.endswith(".json") and "/checkers/" in key for key in host.objects.items)
    assert host.runtime.operations == ["adapt-checker"]


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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    artifact = host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())

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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_output_invalid"
    assert "这是局部试跑" not in developer.prompts[1]
    assert "B should wait" in developer.prompts[1]
    assert host.objects.items == {}


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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 3
    assert "这是局部试跑" not in developer.prompts[2]
    assert "B should wait" in developer.prompts[2]
    assert host.objects.items == {}


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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 3
    assert "这是局部试跑" not in developer.prompts[2]
    assert "B should wait" in developer.prompts[2]
    assert host.objects.items == {}


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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 3
    assert "本次已有结果的条款" in developer.prompts[2]
    assert "本次已有结果的条款：A" in developer.prompts[2]
    assert "B should wait" not in developer.prompts[2]
    assert host.objects.items == {}


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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 3
    assert host.objects.items == {}
    assert "这些结果只说明程序跑完，不说明业务判断正确。" in developer.prompts[1]
    assert "这是局部试跑" not in developer.prompts[2]
    assert "B should wait" in developer.prompts[2]
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
    host = SemanticRuntime(IsolatedRuntime(ScriptedDeveloper([])), MemoryObjects(), timeout=30)
    results = host.execute_checker(request(), artifact, audit, rule, raw, role="target", context=ExecutionContext())

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
    host = SemanticRuntime(IsolatedRuntime(ScriptedDeveloper([])), MemoryObjects(), timeout=30)
    results = host.execute_checker(request(), artifact, audit, rule, raw, role="target", context=ExecutionContext())

    assert [(item.clause_id, item.status) for item in results] == [("C1", "ERR"), ("C2", "UNABLE"), ("C3", "ERR")]
    assert "AttributeError" in results[0].suggestions
    assert "ValidationError" in results[2].suggestions
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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 6
    assert host.objects.items == {}
    assert "条款 C2 检查结果无效" in developer.prompts[1]
    assert "AttributeError" in developer.prompts[1]
    assert "C1|UNABLE" in developer.prompts[1]


def test_a_fixed_target_is_not_adapted_again_when_another_target_is_still_open(tmp_path: Path) -> None:
    from agent.protocol import FrozenFile

    def raw_for(path: Path, marker: str) -> bytes:
        workbook = Workbook()
        workbook.active["A1"] = marker
        workbook.save(path)
        workbook.close()
        return path.read_bytes()

    good_raw = raw_for(tmp_path / "good.xlsx", "good")
    open_raw = raw_for(tmp_path / "open.xlsx", "open")
    body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": "工序名称不为空时要有操作描述"}],
    }
    good = FrozenFile(
        file_version_id="version-good",
        file_id="file-good",
        role="target",
        filename="good.xlsx",
        sha256=hashlib.sha256(good_raw).hexdigest(),
        rule_version_id="same-rule",
        rule_body=body,
    )
    pending = FrozenFile(
        file_version_id="version-open",
        file_id="file-open",
        role="target",
        filename="open.xlsx",
        sha256=hashlib.sha256(open_raw).hexdigest(),
        rule_version_id="same-rule",
        rule_body=body,
    )
    spec = request().model_copy(update={"files": [good, pending]})
    developer = ScriptedDeveloper([valid_draft(), valid_draft(), *[broken_draft() for _ in range(6)]])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    blobs = {good.file_version_id: good_raw, pending.file_version_id: open_raw}

    first = host.execute(spec, blobs, context=ExecutionContext())
    by_file = {item.locator["file_version_id"]: item for item in first.findings}
    assert by_file[good.file_version_id].status == "UNABLE"
    assert by_file[pending.file_version_id].status == "ERR"
    assert developer.calls == 8

    with pytest.raises(DeepError) as caught:
        host.execute(spec, blobs, context=ExecutionContext())

    assert caught.value.code == "deep_model_protocol"
    assert developer.calls == 9
    assert "version-good" not in developer.prompts[-1]
    assert "version-open" in developer.prompts[-1]


def test_task_resource_file_joins_the_registry_resource_in_one_context(tmp_path: Path) -> None:
    import sys

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": RESOURCE_CHECKER, "resources.py": RESOURCE_FILE}}])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    before = list(sys.path)
    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    assert set(artifact.files) == {"checker.py", "resources.py"}
    assert list(sys.path) == before
    assert "resources" not in sys.modules
    results = host.execute_checker(request(), artifact, audit_file(path), rules(), raw, role="target", context=ExecutionContext())

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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

    assert developer.calls == 3
    assert "CLAUSE_BOUND_CHECKS = True" in developer.prompts[1]
    assert artifact.files["checker.py"] == VALID_PROGRAM.encode()


CELL_PROGRAM = """
from audit.checkers.v3.excel_agent import ExcelAgent, rule
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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())
    results = host.execute_checker(request(), artifact, audit_file(path), rules(), raw, role="target", context=ExecutionContext())

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


DUPLICATE_PROGRAM = """
from audit.checkers.v3.excel_agent import ExcelAgent, rule
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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)

    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

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
from audit.checkers.v3.excel_agent import ExcelAgent, rule
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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(request(), audit_file(path), rules(), raw, role="target", context=ExecutionContext())

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
from audit.checkers.v3.excel_agent import ExcelAgent, rule
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
from audit.checkers.v3.excel_agent import ExcelAgent, rule
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
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    with pytest.raises(DeepError) as caught:
        host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())

    assert caught.value.code == "deep_output_invalid"
    assert developer.calls == 6
    assert host.objects.items == {}
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
    gap_text = "条款 B 有多个结果" if gap == "duplicate" else "条款 B 没有结果"

    assert [(item.clause_id, item.status) for item in results] == [("A", "NG"), ("B", "ERR"), (None, "ERR"), ("Z", "UNABLE")]
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


def test_duplicate_rule_ids_run_the_unique_clause_and_keep_distinct_keys(tmp_path: Path) -> None:
    from agent.mapping import checker_response, product_result
    from audit.audits import RoundInput, RoundWork

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    clauses = [
        {"id": "R4", "category": "工序名称", "rule_to_check": "第一次工序名称"},
        {"id": "R4", "category": "工序名称", "rule_to_check": "第二次工序名称"},
        {"id": "R5", "category": "操作描述", "rule_to_check": "操作描述需要单独检查"},
    ]
    rule = RuleInput(
        rule_id="same-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
        rules=clauses,
    )
    program = """
from audit.checkers.v3.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.kinds import PpapDocumentType


class TaskAudit(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    CLAUSE_BOUND_CHECKS = True

    @rule("工序名称", clause_id="R4")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        return "NG", "工序名称不一致"

    @rule("操作描述", clause_id="R5")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        return "UNABLE", "操作描述未核对"


def create_audit(audit_file, rule, group_context=None):
    return TaskAudit(audit_file, rule, group_context)
"""
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": program}}])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    artifact = host.adapt_checker(request(), audit_file(path), rule, raw, role="target", context=ExecutionContext())
    assert developer.calls == 2
    results = host.execute_checker(request(), artifact, audit_file(path), rule, raw, role="target", context=ExecutionContext())
    frozen = _frozen_target(raw, clauses)
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

    assert [(item.clause_id, item.status) for item in results] == [("R4", "ERR"), ("R4", "ERR"), ("R5", "UNABLE")]
    assert results[0].suggestions == "条款 R4 在生效规则中重复"
    assert results[1].suggestions == "条款 R4 在生效规则中重复"
    assert results[2].suggestions == "操作描述未核对"
    assert [item.item_key for item in response.findings] == ["version-1:rule:R4", "version-1:rule:R4:2", "version-1:rule:R5"]
    assert by_key["version-1:rule:R4"].item == "第一次工序名称"
    assert by_key["version-1:rule:R4:2"].item == "第二次工序名称"
    assert by_key["version-1:rule:R5"].status == "UNABLE"
    assert [item["item_key"] for item in stored["findings"]] == ["version-1:rule:R4", "version-1:rule:R4:2", "version-1:rule:R5"]
    assert response.coverage.verification is None


def test_an_open_question_reaches_product_clarification_without_trace_verification(tmp_path: Path) -> None:
    from agent.execution.checker_guest import execute_fixed_checker
    from agent.mapping import checker_response

    path = tmp_path / "target.xlsx"
    raw = workbook_bytes(path)
    program = """
from audit.checkers.v3.excel_agent import ExcelAgent, rule
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


def test_a_new_file_rechecks_the_old_program_and_the_same_input_does_not(tmp_path: Path) -> None:
    from agent.checker_program import checker_start_prefix, start_generation_key
    from agent.protocol import FrozenFile

    def frozen(path: Path, raw: bytes, version: str, rule_version: str, text: str) -> tuple[FrozenFile, dict]:
        body = {
            "rule_profile": "v3",
            "type": PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(),
            "rules": [{"id": "R4", "category": "工序名称", "rule_to_check": text}],
        }
        item = FrozenFile(
            file_version_id=version,
            file_id=version,
            role="target",
            filename=path.name,
            sha256=hashlib.sha256(raw).hexdigest(),
            rule_version_id=rule_version,
            rule_body=body,
        )
        return item, body

    first_path = tmp_path / "first.xlsx"
    first_raw = workbook_bytes(first_path)
    first_file, _ = frozen(first_path, first_raw, "version-1", "same-rule", "工序名称不为空时要有操作描述")
    marked = "# FROM-FILE-A\n" + VALID_PROGRAM
    developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": marked}}])
    host = SemanticRuntime(IsolatedRuntime(developer), MemoryObjects(), timeout=30)
    spec = request().model_copy(update={"files": [first_file], "tenant_id": "tenant-a"})

    first = host.execute(spec, {"version-1": first_raw}, context=ExecutionContext())

    assert first.findings[0].suggestions == "未确定工序范围"
    rule = rules()
    start_prefix = checker_start_prefix("tenant-a", REVISION, rule)
    start_keys = [key for key in host.objects.items if key.startswith(start_prefix + "/")]
    assert start_keys == [start_generation_key(start_prefix, 1)]
    assert "same-rule" not in start_keys[0]
    assert first_file.sha256 not in start_keys[0]

    same = spec.model_copy(update={"round_id": "round-2", "job_id": "job-2"})

    class RefuseDevelopment:
        calls = 0

        def run(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("the same input must not be developed again")

    same_runtime = IsolatedRuntime(RefuseDevelopment())
    same_host = SemanticRuntime(same_runtime, host.objects, timeout=30)
    repeated = same_host.execute(same, {"version-1": first_raw}, context=ExecutionContext())

    assert same_runtime.developer.calls == 0
    assert same_runtime.operations == ["execute-checker"]
    assert repeated.findings[0].suggestions == "未确定工序范围"

    second_path = tmp_path / "second.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "另一份文件"
    workbook.save(second_path)
    workbook.close()
    second_raw = second_path.read_bytes()
    second_file, _ = frozen(second_path, second_raw, "version-2", "same-rule", "工序名称不为空时要有操作描述")
    revised = VALID_PROGRAM.replace("未确定工序范围", "来自新文件")
    next_developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": revised}}])
    next_runtime = IsolatedRuntime(next_developer)
    next_host = SemanticRuntime(next_runtime, host.objects, timeout=30)
    next_spec = request().model_copy(update={"files": [second_file], "tenant_id": "tenant-a", "round_id": "round-3", "job_id": "job-3"})

    opened = next_host.execute(next_spec, {"version-2": second_raw}, context=ExecutionContext())

    assert "FROM-FILE-A" in next_developer.prompts[0]
    assert "不能直接沿用到其他文件" in next_developer.prompts[0]
    assert opened.findings[0].suggestions == "来自新文件"
    assert "adapt-checker" in next_runtime.operations

    replay_runtime = IsolatedRuntime(RefuseDevelopment())
    replay_host = SemanticRuntime(replay_runtime, host.objects, timeout=30)
    replay_first = request().model_copy(update={"files": [first_file], "tenant_id": "tenant-a", "round_id": "round-6", "job_id": "job-6"})
    replay_second = request().model_copy(update={"files": [second_file], "tenant_id": "tenant-a", "round_id": "round-7", "job_id": "job-7"})

    again_first = replay_host.execute(replay_first, {"version-1": first_raw}, context=ExecutionContext())
    again_second = replay_host.execute(replay_second, {"version-2": second_raw}, context=ExecutionContext())

    assert replay_runtime.developer.calls == 0
    assert replay_runtime.operations == ["execute-checker", "execute-checker"]
    assert again_first.findings[0].suggestions == "未确定工序范围"
    assert again_second.findings[0].suggestions == "来自新文件"
    assert start_generation_key(start_prefix, 2) in host.objects.items

    other_file, _ = frozen(second_path, second_raw, "version-2", "other-rule", "另一条规则")
    other_developer = ScriptedDeveloper([valid_draft()])
    other_runtime = IsolatedRuntime(other_developer)
    other_host = SemanticRuntime(other_runtime, host.objects, timeout=30)
    other_spec = request().model_copy(update={"files": [other_file], "tenant_id": "tenant-a", "round_id": "round-4", "job_id": "job-4"})

    other = other_host.execute(other_spec, {"version-2": second_raw}, context=ExecutionContext())

    assert "来自新文件" in other_developer.prompts[0]
    assert "不能直接沿用到其他文件" in other_developer.prompts[0]
    assert "adapt-checker" in other_runtime.operations
    assert other.findings[0].suggestions == "未确定工序范围"
    control_body = {
        "rule_profile": "v3",
        "type": PpapDocumentType.CONTROL_PLAN.get_type_name(),
        "rules": [{"id": "C1", "category": "控制", "rule_to_check": "控制计划条款"}],
    }
    control_file = FrozenFile(
        file_version_id="version-control",
        file_id="version-control",
        role="target",
        filename="control.xlsx",
        sha256=hashlib.sha256(second_raw).hexdigest(),
        rule_version_id="control-rule",
        rule_body=control_body,
    )
    control_program = (
        VALID_PROGRAM.replace('clause_id="R4"', 'clause_id="C1"')
        .replace('EXPECTED_RULE_CATEGORIES = ("工序名称",)', 'EXPECTED_RULE_CATEGORIES = ("控制",)')
        .replace('@rule("工序名称", clause_id="C1")', '@rule("控制", clause_id="C1")')
    )
    control_developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": control_program}}])
    control_runtime = IsolatedRuntime(control_developer)
    control_host = SemanticRuntime(control_runtime, host.objects, timeout=30)
    control_spec = request().model_copy(update={"files": [control_file], "tenant_id": "tenant-a", "round_id": "round-5", "job_id": "job-5"})

    control_host.execute(control_spec, {"version-control": second_raw}, context=ExecutionContext())

    assert "来自新文件" not in control_developer.prompts[0]
    assert "FROM-FILE-A" not in control_developer.prompts[0]

    third_path = tmp_path / "third.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "第三份文件"
    workbook.save(third_path)
    workbook.close()
    third_raw = third_path.read_bytes()
    third_file, _ = frozen(third_path, third_raw, "version-3", "same-rule", "工序名称不为空时要有操作描述")
    third_program = VALID_PROGRAM.replace("未确定工序范围", "来自第三份")
    third_developer = ScriptedDeveloper([{"entrypoint": "checker:create_audit", "files": {"checker.py": third_program}}])
    third_runtime = IsolatedRuntime(third_developer)
    third_host = SemanticRuntime(third_runtime, host.objects, timeout=30)
    third_spec = request().model_copy(update={"files": [third_file], "tenant_id": "tenant-a", "round_id": "round-8", "job_id": "job-8"})

    third = third_host.execute(third_spec, {"version-3": third_raw}, context=ExecutionContext())

    assert "来自新文件" in third_developer.prompts[0]
    assert "FROM-FILE-A" not in third_developer.prompts[0]
    assert "不能直接沿用到其他文件" in third_developer.prompts[0]
    assert "adapt-checker" in third_runtime.operations
    assert third.findings[0].suggestions == "来自第三份"


def test_a_legacy_start_object_stays_and_the_newer_generation_is_used() -> None:
    from agent.checker_program import checker_start_key, checker_start_prefix, encoded, start_generation_key

    host = SemanticRuntime(IsolatedRuntime(ScriptedDeveloper([])), MemoryObjects(), timeout=30)
    rule = rules()
    prefix = checker_start_prefix("tenant-a", REVISION, rule)
    legacy_key = checker_start_key("tenant-a", REVISION, rule)
    legacy = CheckerArtifact(
        input_digest="a" * 64,
        executor_revision=REVISION,
        entrypoint="checker:create_audit",
        files={"checker.py": b"# LEGACY\n"},
    )
    newer = CheckerArtifact(
        input_digest="b" * 64,
        executor_revision=REVISION,
        entrypoint="checker:create_audit",
        files={"checker.py": b"# NEWER\n"},
    )
    legacy_body = encoded(legacy.model_dump(mode="json"))
    host.objects.put_exclusive(legacy_key, legacy_body, "application/json")
    host._append_start(prefix, newer.model_dump(mode="json"))

    found = host._load_checker_start(request().model_copy(update={"tenant_id": "tenant-a"}), rule)

    assert found is not None
    assert found.files["checker.py"] == b"# NEWER\n"
    assert found.input_digest == "b" * 64
    assert host.objects.get(legacy_key) == legacy_body
    assert start_generation_key(prefix, 1) in host.objects.items
    assert start_generation_key(prefix, 2) not in host.objects.items
