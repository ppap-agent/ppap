"""V4 uses the existing customer checkers against frozen files and effective clauses."""

import hashlib
import io
import json

import fitz
import pytest
from agent.checker_program import adapt, execute, input_digest
from agent.execution.checker_context import write_starting_point
from agent.execution.checker_guest import CheckerTrialError, run_program
from agent.execution.checker_native import bind_native_checks
from audit.checker_lib.nio.excel_agent import rule as check_rule
from audit.checker_lib.uaes.excel import unable
from audit.checker_lib.uaes.native import UnknownFact
from audit.checker_lib.uaes.records import UaesPhotoAgent
from audit.checker_lib.uaes.special_characteristics import UaesSpecialCharacteristicsAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule, preset_rule_body, validate_preset_body
from openpyxl import Workbook


def preset(entry="U12", *, customer="UAES") -> RuleInput:
    return RuleInput.model_validate({**preset_rule_body(customer, entry), "rule_id": "rule-target"})


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


def locator_version(locator):
    """The file version of a cell locator (JSON) or of a converted PDF page (file:{version};page:N)."""
    if locator.startswith("file:"):
        return locator[len("file:") :].split(";", 1)[0]
    return json.loads(locator)["file_version_id"]


def program(work, target, rule, actions):
    work.mkdir(parents=True, exist_ok=True)
    write_starting_point(work, target, rule)
    source = (work / "template-checker.py").read_text()
    for action in actions:
        source = source.replace("raise NotImplementedError('核对本次条款后实现')", action, 1)
    return source.encode()


@pytest.mark.parametrize(
    ("customer", "entry", "module", "filename"),
    [
        ("蔚来", "N03", "nio.control_plan_agent", "target.xlsx"),
        ("理想", "L11", "li.process_flow_chart_excel_agent", "target.xlsx"),
        ("UAES", "U12", "uaes.special_characteristics", "target.xlsx"),
        ("UAES", "U12", "uaes.special_characteristics", "target.pdf"),
        # Photo carriers still read through the UAES snapshot reader.
        ("UAES", "U13", "uaes.records", "target.png"),
    ],
)
def test_starting_point_uses_customer_resolver_and_source_bindings(tmp_path, customer, entry, module, filename):
    rule = preset(entry, customer=customer)
    target = AuditInput(audit_id="target", file_url=filename)
    guide = write_starting_point(tmp_path, target, rule)
    assert module in guide
    bindings = json.loads((tmp_path / "reference-bindings.json").read_text())
    original = preset_rule(customer, entry)
    assert bindings[original.clauses[0].id].startswith("rule_")
    template = (tmp_path / "template-checker.py").read_text()
    assert template.count("clause_id=") == len(rule.rules)
    native = entry == "U13"
    assert ("bind_native_checks" in template) is native
    assert ("resource=RESOURCE" in template) is not native
    assert (template.count("ctx: RuleContext") == len(rule.rules)) is not native
    assert ("无 ctx 参数" in guide) is native


@pytest.mark.parametrize("change", [{"customers": ["other"]}, {"models": ["M2"]}, {"parts": ["P2"]}, {"preset_key": "U07"}])
def test_complete_rule_identity_invalidates_a_fixed_program(change):
    target = AuditInput(audit_id="target", file_url="target.xlsx")
    rule = preset()
    args = dict(file_sha256="a" * 64, role="target")
    assert input_digest(target, rule, **args) != input_digest(target, rule.model_copy(update=change), **args)


def test_preset_clauses_are_identified_by_their_ids():
    body = preset_rule_body("UAES", "U12")
    body["rules"][0]["rule_to_check"] = "只检查物料编号"
    validate_preset_body(body)
    body["rules"][0]["id"] = "other"
    with pytest.raises(ValueError, match="invalid_preset_clauses"):
        validate_preset_body(body)


@pytest.mark.parametrize("count", [1, 2, 3])
def test_uaes_checks_follow_effective_clause_count_order_and_native_evidence(tmp_path, count):
    path = tmp_path / "special.xlsx"
    special_file(path, value="")
    rule = preset()
    clauses = [*rule.rules, {**rule.rules[0], "id": "extra", "category": "重复身份核对"}][:count]
    rule = rule.model_copy(update={"rules": clauses})
    target = AuditInput(audit_id="target", file_url=str(path))
    source = program(tmp_path / "author", target, rule, ["return super().rule_1(ctx)", "return super().rule_2(ctx)", "return super().rule_1(ctx)"][:count])
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


def photo_file(path):
    path.write_bytes(fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), False).tobytes("png"))


class TaskSpecial(UaesSpecialCharacteristicsAgent):
    """The authored ExcelAgent template shape: only this task's clause-bound methods run."""

    CLAUSE_BOUND_CHECKS = True
    POSITIONAL_CLAUSE_CHECKS = False
    EXPECTED_RULE_CATEGORIES = ()

    @classmethod
    def declared_rules(cls):
        return tuple(item for item in super().declared_rules() if item[0] in cls.__dict__)


def test_uaes_excel_checks_reset_evidence_and_keep_unknown_and_errors_separate(tmp_path):
    path = tmp_path / "special.xlsx"
    special_file(path)

    class TaskAudit(TaskSpecial):
        @check_rule("身份", clause_id="identity")
        def identity_check(self, ctx):
            return super().rule_1(ctx)

        @check_rule("未知", clause_id="unknown")
        def unknown_check(self, ctx):
            raise unable("缺少参考资料")

        @check_rule("故障", clause_id="broken")
        def broken_check(self, ctx):
            raise RuntimeError("source read failed")

        @check_rule("无观察", clause_id="empty")
        def empty_check(self, ctx):
            return "PASS", "没有执行任何读取"

    rule = RuleInput(rule_id="r", rules=[{"id": name} for name in ["identity", "unknown", "broken", "empty"]])
    audit = TaskAudit(AuditInput(audit_id="target", file_url=str(path)), rule)
    checks = audit.checks
    first = checks[0].check()
    results = [check.check() for check in checks[1:]]
    # Unknown stays UNABLE; a fault is ERR; a PASS with no executed assertion never passes.
    assert [result.status for result in results] == ["UNABLE", "ERR", "ERR"]
    assert "零断言不能判为通过" in results[2].suggestions
    assert first.status == "PASS" and first.checks_performed > 0 and first.evidence.sources
    assert [result.checks_performed for result in results] == [0, 0, 0]
    assert all(result.evidence is None or not result.evidence.sources for result in results)
    assert checks[0].check().model_dump() == first.model_dump()


def test_native_checks_reset_evidence_and_keep_unknown_and_errors_separate(tmp_path):
    # Photo carriers still run the snapshot reader through bind_native_checks.
    path = tmp_path / "photo.png"
    photo_file(path)

    class TaskAudit(UaesPhotoAgent):
        @check_rule("照片", clause_id="identity")
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
    assert first.status == "PASS"
    assert [result.status for result in results] == ["UNABLE", "ERR", "UNABLE"]
    assert first.checks_performed == 1
    assert [result.checks_performed for result in results] == [0, 0, 0]
    assert all(not result.evidence.sources for result in results)
    assert checks[0].check().model_dump() == first.model_dump()


def test_prior_diagnosis_and_resource_adaptation_reuse_the_original_algorithm(tmp_path):
    from agent.checker_spec import CheckerArtifact

    path = tmp_path / "new-layout.xlsx"
    # A layout whose headers the UAES resource does not know: the original algorithm cannot locate the table.
    path.write_bytes(workbook([["No.", "Characteristic", "Spec", "Class", "Part"], [1, "孔径", "10±0.1", "CC", "支架 P12345"]]))
    target = AuditInput(audit_id="target", file_url=str(path))
    rule = preset()
    rule = rule.model_copy(update={"rules": rule.rules[:1]})
    old = program(tmp_path / "old", target, rule, ["return super().rule_1(ctx)"])
    prior = CheckerArtifact(input_digest="a" * 64, executor_revision="previous", entrypoint="checker:create_audit", files={"checker.py": old})
    # Only the resource changes; the template loads resources.RESOURCE and the inherited rule_1 runs unchanged.
    resources = (
        "from audit.checker_lib.uaes.resources import combine, special_list\n"
        "ALIASES = {'特性编号': ('No.',), '特性名称': ('Characteristic',), '特性值': ('Spec',), '特性类别': ('Class',), '零件': ('Part',)}\n"
        "COLUMNS = tuple(column.model_copy(update={'aliases': (*column.aliases, *ALIASES.get(column.canonical_name, ()))}) for column in special_list.DOCUMENT[2])\n"
        "RESOURCE = combine((special_list.DOCUMENT[0], special_list.DOCUMENT[1], COLUMNS))\n"
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
            return schema.model_validate({"entrypoint": "checker:create_audit", "files": {"checker.py": old.decode(), "resources.py": resources}})

    args = dict(file_bytes=path.read_bytes(), file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), role="target", executor_revision="v4")
    developer = Developer()
    artifact = adapt(target, rule, runner=developer, prior=prior, work=tmp_path / "adapt", deadline=10**12, **args)
    results = execute(artifact, target, rule, work=tmp_path / "execute", **args)
    assert developer.calls == 2
    assert results[0].status == "PASS" and results[0].checks_performed > 0, results[0].suggestions
    # The evidence is the characteristic row of the new layout, read through the adapted resource.
    assert {json.loads(item.locator)["cell"] for item in results[0].evidence.sources} == {"A2", "E2"}
    assert {json.loads(item.locator)["file_version_id"] for item in results[0].evidence.sources} == {"target"}


def test_native_electronic_pdf_development_and_fixed_visual_guard(tmp_path, monkeypatch):
    path = tmp_path / "source.pdf"
    doc = fitz.open()
    page = doc.new_page()
    # An electronic PDF: a ruled table of text the grid converter reads as cells.
    for index, (label, value) in enumerate([("物料名称", "支架"), ("物料编号", "P12345")]):
        page.insert_text((44, 76 + index * 24), label, fontname="china-s", fontsize=11)
        page.insert_text((164, 76 + index * 24), value, fontname="china-s", fontsize=11)
    for y in (60, 84, 108):
        page.draw_line((40, y), (300, y))
    for x in (40, 160, 300):
        page.draw_line((x, 60), (x, 108))
    doc.save(path)
    doc.close()
    monkeypatch.setattr("documents.ai.run_json", lambda *args, **kwargs: pytest.fail("fixed checker must not call a model"))
    rule = preset()
    rule = rule.model_copy(update={"rules": [rule.rules[0], {**rule.rules[0], "id": "stamp", "category": "受控章", "rule_to_check": "须有实际受控章"}]})
    target = AuditInput(audit_id="target", file_url=str(path))

    class Developer:
        input_tokens = output_tokens = 0

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            assert "P12345" in prompt
            source = program(work, target, rule, ["return super().rule_1(ctx)", "return self.stamp()"]).decode()
            # A PDF target names its grid layout; this reuses the carrier's own first layout.
            source = source.replace("    EXPECTED_RULE_CATEGORIES = ()", "    EXPECTED_RULE_CATEGORIES = ()\n    PDF_SPEC = UaesSpecialCharacteristicsAgent.DOCUMENT_MODULE.PDF_SPECS[0]", 1)
            return schema.model_validate({"entrypoint": "checker:create_audit", "files": {"checker.py": source}})

    args = dict(file_bytes=path.read_bytes(), file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), role="target", executor_revision="v4")
    artifact = adapt(target, rule, runner=Developer(), work=tmp_path / "adapt", deadline=10**12, **args)
    results = execute(artifact, target, rule, work=tmp_path / "execute", **args)
    assert [result.status for result in results] == ["PASS", "UNABLE"]
    assert "尚未核实" in results[1].suggestions
    assert all(locator_version(source.locator) == "target" for result in results for source in result.evidence.sources)
    assert {"A1", "B1", "A2", "B2"} <= {json.loads(source.locator).get("cell") for source in results[0].evidence.sources if source.locator.startswith("{")}


# No grant at all (the default code) is as open as a missing personal key; only request faults are ERR.
FAILURES = [
    ("personal_model_unavailable", "UNABLE"),
    ("model_unavailable", "UNABLE"),
    ("platform_model_unconfigured", "UNABLE"),
    ("personal_model_request_failed", "ERR"),
    ("personal_model_request_uncertain", "ERR"),
    ("other", "ERR"),
    ("io", "ERR"),
    ("service", "ERR"),
]


def raise_failure(failure):
    from documents.model_access import ModelUnavailable

    if failure == "io":
        raise OSError("private provider details")
    if failure == "service":
        raise RuntimeError("private provider details")
    raise ModelUnavailable(failure)


@pytest.mark.parametrize(("failure", "expected"), FAILURES)
def test_native_authored_checks_distinguish_missing_model_grant_from_request_faults(tmp_path, failure, expected):
    path = tmp_path / "photo.png"
    photo_file(path)

    class TaskAudit(UaesPhotoAgent):
        @check_rule("视觉事实", clause_id="visual")
        def visual_check(self):
            raise_failure(failure)

    rules = RuleInput(rule_id="r", rules=[{"id": "visual"}])
    audit = TaskAudit(AuditInput(audit_id="target", file_url=str(path)), rules)
    result = bind_native_checks(audit)[0].check()
    assert result.status == expected
    assert result.checks_performed == 0
    assert result.evidence.sources == ()
    assert "private provider details" not in result.suggestions
    if expected == "UNABLE":
        assert "尚未核实" in result.suggestions


@pytest.mark.parametrize(
    ("failure", "expected"),
    FAILURES,
)
def test_uaes_excel_visual_facts_distinguish_missing_model_grant_from_request_faults(tmp_path, monkeypatch, failure, expected):
    path = tmp_path / "special.xlsx"
    special_file(path)
    monkeypatch.setattr("audit.checker_lib.uaes.native.UaesSourceAgent.visual_pdf", lambda self, fields, **kwargs: raise_failure(failure))

    class TaskAudit(TaskSpecial):
        @check_rule("视觉事实", clause_id="visual")
        def visual_check(self, ctx):
            return self.visual({"signature": "原件有实际签字"}, "原件签字")

    rules = RuleInput(rule_id="r", rules=[{"id": "visual"}])
    audit = TaskAudit(AuditInput(audit_id="target", file_url=str(path)), rules)
    result = audit.checks[0].check()
    assert result.status == expected
    assert result.checks_performed == 0
    assert result.evidence is None or result.evidence.sources == ()
    assert "private provider details" not in result.suggestions
    if expected == "UNABLE":
        assert "尚未核实" in result.suggestions
