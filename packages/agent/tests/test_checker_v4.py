"""V4 uses the existing customer checkers against frozen files and effective clauses."""

import hashlib
import io
import json
from pathlib import Path

import fitz
import pytest
from agent.checker_program import adapt, checker_start_prefix, checker_type_start_prefix, execute, group_context_for_spec, input_digest, rule_for_file
from agent.client import DeepError
from agent.execution.checker_context import write_starting_point
from agent.execution.checker_guest import CheckerTrialError, run_program
from agent.execution.checker_native import bind_native_checks
from agent.protocol import ExecuteRequest, FrozenFile
from audit.checker_lib.nio.excel_agent import rule as check_rule
from audit.checker_lib.uaes.native import UnknownFact
from audit.checker_lib.uaes.special_characteristics import UaesSpecialCharacteristicsAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule, preset_rule_body, validate_preset_body
from openpyxl import Workbook


def frozen(version, entry="U12", *, customer="UAES", role="target", raw=b"file", **body):
    return FrozenFile(
        file_version_id=version,
        file_id="file-" + version,
        filename=version + ".xlsx",
        role=role,
        sha256=hashlib.sha256(raw).hexdigest(),
        rule_version_id="rule-" + version,
        rule_body={**preset_rule_body(customer, entry), **body},
    )


def request(*files):
    return ExecuteRequest(protocol_revision="test", round_id="round", tenant_id="tenant", job_id="job", executor="ppap-deep", executor_revision="v4-test", files=list(files))


def workbook(rows, sheet="特殊特性清单"):
    book = Workbook()
    book.active.title = sheet
    for row in rows:
        book.active.append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def special_file(path, value="10±0.1"):
    path.write_bytes(workbook([["物料名称", "支架"], ["物料编号", "P12345"], ["编号", "产品特性", "特性及公差", "特性类型"], [1, "孔径", value, "CC"]]))


def program(work, target, rule, actions):
    work.mkdir(parents=True, exist_ok=True)
    write_starting_point(work, target, rule)
    source = (work / "template-checker.py").read_text()
    for action in actions:
        source = source.replace("raise NotImplementedError('核对本次条款后实现')", action, 1)
    return source.encode()


@pytest.mark.parametrize(
    ("customer", "entry", "module"), [("蔚来", "N03", "nio.control_plan_agent"), ("理想", "L11", "li.process_flow_chart_excel_agent"), ("UAES", "U12", "uaes.special_characteristics")]
)
def test_starting_point_uses_customer_resolver_and_source_bindings(tmp_path, customer, entry, module):
    rule = rule_for_file(frozen("target", entry, customer=customer))
    target = AuditInput(audit_id="target", file_url="target.xlsx")
    guide = write_starting_point(tmp_path, target, rule)
    assert module in guide
    bindings = json.loads((tmp_path / "reference-bindings.json").read_text())
    original = preset_rule(customer, entry)
    assert bindings[original.clauses[0].id].startswith("rule_")
    template = (tmp_path / "template-checker.py").read_text()
    assert template.count("clause_id=") == len(rule.rules)
    assert "bind_native_checks" in template if customer == "UAES" else "resource=RESOURCE" in template


def test_complete_rules_and_all_roles_enter_the_group_without_losing_source_identity():
    first = frozen("first", models=["M1"], parts=["P1"], appendix=[{"text": "当前约定", "scope": {"document_ids": ["first"]}}, {"text": "别的文件", "scope": {"document_ids": ["other"]}}])
    peer = frozen("peer", "U07")
    photo = frozen("photo", "U13", role="reference")
    group = group_context_for_spec(request(first, peer, photo))
    assert set(group.by_audit_id) == {"first", "peer", "photo"}
    assert group.by_doc_type["E07"][0].audit_id == "photo"
    assert group.by_audit_id["first"].file_metadata["v4_source_type"] == "T12"
    rule = group.rules_by_id["first"]
    assert (rule.customers, rule.models, rule.parts, rule.preset_key) == (["UAES"], ["M1"], ["P1"], "U12")
    assert [item["text"] for item in rule.appendix] == ["当前约定"]
    # Applicability is not an observation of the uploaded document's customer.
    assert group.by_audit_id["first"].customer is None
    assert group_context_for_spec(request(first)).by_audit_id.keys() == {"first"}
    with pytest.raises(DeepError, match="deep_input_invalid"):
        group_context_for_spec(request(first, first))


@pytest.mark.parametrize("change", [{"customers": ["other"]}, {"models": ["M2"]}, {"parts": ["P2"]}, {"preset_key": "U07"}])
def test_complete_rule_identity_invalidates_fixed_and_same_rule_starts(change):
    target = AuditInput(audit_id="target", file_url="target.xlsx")
    rule = rule_for_file(frozen("target"))
    revised = rule.model_copy(update=change)
    args = dict(file_sha256="a" * 64, role="target")
    assert input_digest(target, rule, **args) != input_digest(target, revised, **args)
    assert checker_start_prefix("tenant", "rev", rule) != checker_start_prefix("tenant", "rev", revised)
    if "models" not in change and "parts" not in change:
        assert checker_type_start_prefix("tenant", "rev", rule) != checker_type_start_prefix("tenant", "rev", revised)


@pytest.mark.parametrize("change", [{"customers": []}, {"customers": ["UAES", "蔚来"]}, {"preset_key": "unknown"}])
def test_untrusted_v4_source_is_rejected_before_development(change):
    with pytest.raises(DeepError, match="deep_input_invalid"):
        rule_for_file(frozen("target", **change))


def test_preset_clauses_are_identified_by_their_ids():
    item = frozen("target")
    item.rule_body["rules"][0]["rule_to_check"] = "只检查物料编号"
    assert rule_for_file(item).rules[0]["rule_to_check"] == "只检查物料编号"
    validate_preset_body(item.rule_body)
    item.rule_body["rules"][0]["id"] = "other"
    with pytest.raises(ValueError, match="invalid_preset_clauses"):
        validate_preset_body(item.rule_body)


@pytest.mark.parametrize("count", [1, 2, 3])
def test_uaes_checks_follow_effective_clause_count_order_and_native_evidence(tmp_path, count):
    path = tmp_path / "special.xlsx"
    special_file(path, value="")
    rule = rule_for_file(frozen("target"))
    clauses = [*rule.rules, {**rule.rules[0], "id": "extra", "category": "重复身份核对"}][:count]
    rule = rule.model_copy(update={"rules": clauses})
    target = AuditInput(audit_id="target", file_url=str(path))
    source = program(tmp_path / "author", target, rule, ["return super().rule_1()", "return super().rule_2()", "return super().rule_1()"][:count])
    # Run the same source after reordering the effective rule list: binding is by ID.
    rule = rule.model_copy(update={"rules": list(reversed(clauses))})
    results = run_program({"checker.py": source}, tmp_path / "run", target, rule, None)
    assert [(item.clause_id, item.status) for item in results] == list(reversed([(clause["id"], "NG" if index == 1 else "PASS") for index, clause in enumerate(clauses)]))
    assert all(result.evidence.sources for result in results)
    assert all(json.loads(source.locator)["file_version_id"] == "target" for result in results for source in result.evidence.sources)
    one = run_program({"checker.py": source}, tmp_path / "partial", target, rule, None, only_clauses=[clauses[0]["id"]])
    assert [(result.clause_id, result.status) for result in one] == [(clauses[0]["id"], "PASS")]
    missing = rule.model_copy(update={"rules": [*rule.rules, {**clauses[0], "id": "missing"}]})
    with pytest.raises(CheckerTrialError, match="条款结果与生效规则不一致"):
        run_program({"checker.py": source}, tmp_path / "invalid", target, missing, None)


def test_native_checks_reset_evidence_and_keep_unknown_and_errors_separate(tmp_path):
    path = tmp_path / "special.xlsx"
    special_file(path)

    class TaskAudit(UaesSpecialCharacteristicsAgent):
        @check_rule("身份", clause_id="identity")
        def identity_check(self):
            return super().rule_1()

        @check_rule("未知", clause_id="unknown")
        def unknown_check(self):
            raise UnknownFact("缺少参考资料")

        @check_rule("故障", clause_id="broken")
        def broken_check(self):
            raise RuntimeError("source read failed")

        @check_rule("无观察", clause_id="empty")
        def empty_check(self):
            return "PASS", "没有执行任何读取"

    rule = RuleInput(rule_id="r", rules=[{"id": name} for name in ["identity", "unknown", "broken", "empty"]])
    audit = TaskAudit(AuditInput(audit_id="target", file_url=str(path)), rule)
    checks = bind_native_checks(audit)
    first = checks[0].check()
    results = [check.check() for check in checks[1:]]
    assert [result.status for result in results] == ["UNABLE", "ERR", "UNABLE"]
    assert first.checks_performed == 1
    assert [result.checks_performed for result in results] == [0, 0, 0]
    assert all(not result.evidence.sources for result in results)
    assert checks[0].check().model_dump() == first.model_dump()


def test_native_prior_diagnosis_and_resource_adaptation_reuse_the_original_algorithm(tmp_path):
    from agent.checker_spec import CheckerArtifact

    path = tmp_path / "new-layout.xlsx"
    path.write_bytes(workbook([["品名", "支架"], ["料号", "P12345"]]))
    target = AuditInput(audit_id="target", file_url=str(path))
    rule = rule_for_file(frozen("target"))
    rule = rule.model_copy(update={"rules": rule.rules[:1]})
    old = program(tmp_path / "old", target, rule, ["return super().rule_1()"])
    prior = CheckerArtifact(input_digest="a" * 64, executor_revision="previous", entrypoint="checker:create_audit", files={"checker.py": old})
    source = old.decode().replace(
        "    def __init__(",
        "    def identity(self, name):\n        from resources import ALIASES\n        return self.native().field(ALIASES[name])\n\n    def __init__(",
    )

    class Developer:
        input_tokens = output_tokens = 0
        calls = 0

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            self.calls += 1
            if self.calls == 1:
                assert "旧程序在本次输入上的诊断" in prompt
                assert "|UNABLE|" in prompt
            else:
                assert "|PASS|" in prompt
            return schema.model_validate(
                {
                    "entrypoint": "checker:create_audit",
                    "files": {"checker.py": source, "resources.py": "ALIASES = {'物料名称': ('品名',), '物料编号': ('料号',)}\n"},
                }
            )

    args = dict(file_bytes=path.read_bytes(), file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), role="target", executor_revision="v4")
    developer = Developer()
    artifact = adapt(target, rule, runner=developer, prior=prior, work=tmp_path / "adapt", deadline=10**12, **args)
    results = execute(artifact, target, rule, work=tmp_path / "execute", **args)
    assert developer.calls == 2
    assert results[0].status == "PASS" and results[0].checks_performed == 1
    assert {json.loads(item.locator)["cell"] for item in results[0].evidence.sources} == {"A1", "A2", "B1", "B2"}


def test_v4_round_develops_replays_and_uses_other_targets_as_related_sources(tmp_path):
    from agent.auditor import SemanticRuntime
    from agent.checker_program import run_adapt_manifest, run_execute_manifest
    from agent.run import ExecutionContext
    from audit.storage_errors import ObjectExists, ObjectMissing

    class MemoryObjects:
        def __init__(self):
            self.items = {}

        def get(self, key, *, max_bytes):
            if key not in self.items:
                raise ObjectMissing(key)
            return self.items[key]

        def put_exclusive(self, key, body, content_type):
            if key in self.items:
                raise ObjectExists(key)
            self.items[key] = body

    class Runtime:
        adapts_checkers = True

        def engine_revision(self):
            return spec.executor_revision

        def run(self, job, inputs, work, deadline, cancelled, on_progress=None):
            if job.operation == "adapt-checker":
                run_adapt_manifest(inputs, work, developer, deadline)
            else:
                assert job.operation == "execute-checker"
                run_execute_manifest(inputs, work)

    # U07:3 pairs the list by 同项目、同物料编号.
    identity = [["项目", "M1"], ["零件号", "P1"], ["零件名称", "支架"]]
    pfmea_raw = workbook([*identity, ["功能/要求", "特殊特性"], ["孔径", "CC"]], "PFMEA")
    special_raw = workbook([*identity, ["物料名称", "支架"], ["物料编号", "P1"], ["特性名称", "特性类别"], ["孔径", "CC"]])
    pfmea_rule = preset_rule_body("UAES", "U07")["rules"][2]
    special_rule = preset_rule_body("UAES", "U12")["rules"][0]
    pfmea = frozen("pfmea", "U07", raw=pfmea_raw, rules=[pfmea_rule], models=["M1"], parts=["P1"])
    special = frozen("special", raw=special_raw, rules=[special_rule])
    spec = request(pfmea, special)
    blobs = {"pfmea": pfmea_raw, "special": special_raw}

    class Developer:
        input_tokens = output_tokens = 0
        calls = 0

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            self.calls += 1
            view = json.loads(prompt.split("本次文件：\n", 1)[1].split("\n", 1)[0])
            version = view["audit_id"]
            assert {item["audit_id"] for item in view["related"]} == set(blobs)
            assert {item["role"] for item in view["related"]} == {"target"}
            for item in view["related"]:
                assert Path(item["file_url"]).read_bytes() == blobs[item["audit_id"]]
                assert item["rule"]["customers"] == ["UAES"]
            assert '"models":["M1"]' in prompt and '"parts":["P1"]' in prompt
            source = (work / "template-checker.py").read_text()
            action = "return super().rule_3()" if version == "pfmea" else "return super().rule_1()"
            return schema.model_validate({"entrypoint": "checker:create_audit", "files": {"checker.py": source.replace("raise NotImplementedError('核对本次条款后实现')", action)}})

    developer = Developer()
    host = SemanticRuntime(Runtime(), MemoryObjects(), timeout=30)
    first = host.execute(spec, blobs, context=ExecutionContext())
    assert [item.status for item in first.findings] == ["PASS", "PASS"], [(item.status, item.suggestions) for item in first.findings]
    assert developer.calls == 4
    second = host.execute(spec, blobs, context=ExecutionContext())
    assert second.findings == first.findings
    assert developer.calls == 4
    locators = first.findings[0].locator["semantic"]["sources"]
    assert {item["file_version_id"] for item in locators} == {"pfmea", "special"}
    group = group_context_for_spec(spec)
    target, rule = group.by_audit_id["pfmea"], group.rules_by_id["pfmea"]
    original = input_digest(target, rule, file_sha256=pfmea.sha256, role="target", group_context=group)
    changed = special.model_copy(update={"sha256": "f" * 64})
    assert original != input_digest(target, rule, file_sha256=pfmea.sha256, role="target", group_context=group_context_for_spec(request(pfmea, changed)))


def test_native_electronic_pdf_development_and_fixed_visual_guard(tmp_path, monkeypatch):
    path = tmp_path / "source.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), "物料名称: 支架\n物料编号: P12345", fontname="china-s")
    doc.save(path)
    doc.close()
    monkeypatch.setattr("documents.ai.run_json", lambda *args, **kwargs: pytest.fail("fixed checker must not call a model"))
    rule = rule_for_file(frozen("target"))
    rule = rule.model_copy(update={"rules": [rule.rules[0], {**rule.rules[0], "id": "stamp", "category": "受控章", "rule_to_check": "须有实际受控章"}]})
    target = AuditInput(audit_id="target", file_url=str(path))

    class Developer:
        input_tokens = output_tokens = 0

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            assert "P12345" in prompt
            source = program(work, target, rule, ["return super().rule_1()", "return self.controlled_stamp()"])
            return schema.model_validate({"entrypoint": "checker:create_audit", "files": {"checker.py": source.decode()}})

    args = dict(file_bytes=path.read_bytes(), file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), role="target", executor_revision="v4")
    artifact = adapt(target, rule, runner=Developer(), work=tmp_path / "adapt", deadline=10**12, **args)
    results = execute(artifact, target, rule, work=tmp_path / "execute", **args)
    assert [result.status for result in results] == ["PASS", "UNABLE"]
    assert "尚未核实" in results[1].suggestions
    assert all(json.loads(source.locator)["file_version_id"] == "target" for result in results for source in result.evidence.sources)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [("personal_model_unavailable", "UNABLE"), ("personal_model_request_failed", "ERR"), ("personal_model_request_uncertain", "ERR"), ("other", "ERR"), ("io", "ERR"), ("service", "ERR")],
)
def test_native_authored_checks_distinguish_missing_model_grant_from_request_faults(tmp_path, failure, expected):
    from documents.model_access import ModelUnavailable

    path = tmp_path / "special.xlsx"
    special_file(path)

    class TaskAudit(UaesSpecialCharacteristicsAgent):
        @check_rule("视觉事实", clause_id="visual")
        def visual_check(self):
            if failure == "io":
                raise OSError("private provider details")
            if failure == "service":
                raise RuntimeError("private provider details")
            raise ModelUnavailable(failure)

    rules = RuleInput(rule_id="r", rules=[{"id": "visual"}])
    audit = TaskAudit(AuditInput(audit_id="target", file_url=str(path)), rules)
    result = bind_native_checks(audit)[0].check()
    assert result.status == expected
    assert result.checks_performed == 0
    assert result.evidence.sources == ()
    assert "private provider details" not in result.suggestions
    if expected == "UNABLE":
        assert "尚未核实" in result.suggestions
