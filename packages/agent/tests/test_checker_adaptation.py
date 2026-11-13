"""The development loop reuses human methods and repairs their task resources."""

import hashlib
from pathlib import Path

import pytest
from agent.checker_program import adapt, execute
from agent.execution.checker_context import write_starting_point
from agent.execution.checker_guest import run_program
from audit.contracts import AuditInput, RuleInput
from audit.kinds import PpapDocumentType
from openpyxl import Workbook


def inputs(path: Path, count: int = 1):
    target = AuditInput(audit_id="target", file_url=str(path), file_metadata={"file_version_id": "current-version"})
    rules = RuleInput(
        rule_id="current-rule",
        rule_profile="v3",
        type=PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name(),
        rules=[{"id": f"title-{index}", "category": "产品编号", "rule_to_check": "标题含产品编号"} for index in range(count)],
    )
    return target, rules


def human_program(work: Path, target: AuditInput, rules: RuleInput) -> str:
    work.mkdir(parents=True)
    write_starting_point(work, target, rules)
    assert "class ProcessSpecialCharacteristicAgent" in (work / "reference-checker.py").read_text()
    assert "特殊特性清单" in (work / "reference-resources.json").read_text()
    return (work / "template-checker.py").read_text().replace("raise NotImplementedError('核对本次条款后实现')", "return super().rule_1(ctx)")


def workbook(path: Path, sheet: str) -> None:
    book = Workbook()
    book.active.title = sheet
    book.active["A1"] = "P12345特殊特性清单"
    book.save(path)
    book.close()


def resource(sheet: str) -> str:
    return f"""from dataclasses import replace
from types import MappingProxyType
from audit.engine.rule_engine import get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType

base = get_doc_resource_by_doc_type(PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST)
sheet = base.sheets["主表"].model_copy(update={{"include_keywords": ({sheet!r},)}})
RESOURCE = replace(base, sheets=MappingProxyType({{"主表": sheet}}))
"""


@pytest.mark.parametrize("count", [1, 3])
def test_task_rule_count_does_not_inherit_the_original_fixed_method_count(tmp_path: Path, count: int):
    path = tmp_path / "source.xlsx"
    workbook(path, "特殊特性清单")
    target, rules = inputs(path, count)
    source = human_program(tmp_path / "starting", target, rules)
    results = run_program({"checker.py": source.encode()}, tmp_path / "run", target, rules, None)
    assert [(item.clause_id, item.status) for item in results] == [(f"title-{index}", "PASS") for index in range(count)]
    assert all(item.checks_performed and item.evidence.sources for item in results)


def test_prior_diagnosis_failed_candidate_repair_and_fixed_execution(tmp_path: Path):
    path = tmp_path / "new.xlsx"
    workbook(path, "供应商资料")
    target, rules = inputs(path)
    source = human_program(tmp_path / "starting", target, rules)
    from agent.checker_spec import CheckerArtifact

    prior = CheckerArtifact(input_digest="a" * 64, executor_revision="previous", entrypoint="checker:create_audit", files={"checker.py": source.encode()})

    class Developer:
        input_tokens = output_tokens = 0
        calls = 0

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            self.calls += 1
            assert (work / "current-checker.py").read_text() == source
            if self.calls == 1:
                assert "旧程序在本次输入上的诊断" in prompt
                assert "title-0|NG" in prompt
                assert "未找到工作表" in prompt
                draft_resource = "raise ValueError('resource is incomplete')"
            else:
                if self.calls == 2:
                    assert "试跑合同或输入失败: ValueError" in prompt
                    assert "resource is incomplete" not in prompt
                    assert (work / "current-resources.py").read_text() == "raise ValueError('resource is incomplete')"
                draft_resource = resource("供应商资料")
            return schema.model_validate({"entrypoint": "checker:create_audit", "files": {"checker.py": source, "resources.py": draft_resource}})

    developer = Developer()
    raw = path.read_bytes()
    args = dict(file_bytes=raw, file_sha256=hashlib.sha256(raw).hexdigest(), role="target", executor_revision="current")
    fixed = adapt(target, rules, runner=developer, work=tmp_path / "adapt", deadline=10**12, prior=prior, **args)
    result = execute(fixed, target, rules, work=tmp_path / "execute", **args)
    assert developer.calls == 3
    assert result[0].status == "PASS"
    assert fixed.files["checker.py"] == prior.files["checker.py"]
    assert fixed.input_digest != prior.input_digest
    assert fixed.executor_revision == "current"
    assert "供应商资料" in str(result[0].evidence.sources)
    assert "current-version" in str(result[0].evidence.sources)


def test_electronic_pdf_spec_can_be_repaired_in_the_checker_loop(tmp_path: Path, monkeypatch):
    import fitz

    def no_ocr(*args, **kwargs):
        pytest.fail("fixed checker must not start OCR")

    monkeypatch.setattr("documents.ai.run_json_async", no_ocr)
    path = tmp_path / "source.pdf"
    document = fitz.open()
    page = document.new_page()
    for x in (40, 300, 500):
        page.draw_line((x, 40), (x, 160))
    for y in (40, 80, 120, 160):
        page.draw_line((40, y), (500, y))
    page.insert_text((50, 65), "P12345特殊特性清单", fontname="china-s", fontsize=12)
    page.insert_text((50, 105), "part", fontsize=12)
    page.insert_text((310, 105), "value", fontsize=12)
    page.insert_text((50, 145), "12345", fontsize=12)
    page.insert_text((310, 145), "complete", fontsize=12)
    document.save(path)
    document.close()
    target, rules = inputs(path)
    source = human_program(tmp_path / "starting", target, rules).replace(
        "    CLAUSE_BOUND_CHECKS = True",
        "    from resources import SPEC as PDF_SPEC\n    CLAUSE_BOUND_CHECKS = True",
    )
    good = resource("Table") + "\nfrom documents.pdf import PdfSpec\nSPEC = PdfSpec()\n"
    bad = resource("Table") + ("\nfrom documents.pdf import PdfSpec, RegionSpec, RegionMode\nSPEC = PdfSpec(region=RegionSpec(mode=RegionMode.FIXED_BOX, fixed_box=(0, 500, 500, 700)))\n")

    class Developer:
        input_tokens = output_tokens = 0
        calls = 0

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            self.calls += 1
            if self.calls == 2:
                assert "title-0|ERR" in prompt
                assert (work / "current-resources.py").read_text() == bad
            return schema.model_validate(
                {
                    "entrypoint": "checker:create_audit",
                    "files": {
                        "checker.py": source,
                        "resources.py": bad if self.calls == 1 else good,
                    },
                }
            )

    developer = Developer()
    raw = path.read_bytes()
    args = dict(file_bytes=raw, file_sha256=hashlib.sha256(raw).hexdigest(), role="target", executor_revision="current")
    fixed = adapt(target, rules, runner=developer, work=tmp_path / "adapt", deadline=10**12, **args)
    result = execute(fixed, target, rules, work=tmp_path / "execute", **args)[0]
    assert developer.calls == 3
    assert result.status == "PASS"
    assert fixed.files["resources.py"] == good.encode()
    assert any("file:current-version;page:1" in item.locator for item in result.evidence.sources)
    assert result.evidence.conversion_strategies == ("manual_linegrid",)


def test_interrupted_identical_candidate_is_not_a_confirmation(tmp_path):
    from agent.execution.checker_guest import CheckerProgramError, develop_checker
    from agent.execution.guest_model import read_checker_draft

    path = tmp_path / "source.xlsx"
    workbook(path, "特殊特性清单")
    target, rules = inputs(path)
    source = human_program(tmp_path / "starting", target, rules)

    class Interrupted:
        input_tokens = output_tokens = 0
        calls = 0

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            self.calls += 1
            (work / "checker.py").write_text(source)
            return read_checker_draft(work, schema, {"subtype": "error_max_turns"})

    developer = Interrupted()
    with pytest.raises(CheckerProgramError, match="确认轮没有完成"):
        develop_checker(
            audit_file=target,
            rule=rules,
            group_context=None,
            digest="a" * 64,
            executor_revision="current",
            runner=developer,
            work=tmp_path / "adapt",
            deadline=10**12,
        )
    assert developer.calls == 6
    assert not (tmp_path / "adapt/result/bundle.json").exists()


def test_visual_capability_gap_is_unable_in_the_original_single_check_finalizer(tmp_path):
    path = tmp_path / "source.xlsx"
    workbook(path, "特殊特性清单")
    target, rules = inputs(path)
    source = human_program(tmp_path / "starting", target, rules).replace(
        "return super().rule_1(ctx)",
        "from audit.engine.runtime import get_runtime\n        return get_runtime().run_image_prompt()",
    )
    result = run_program({"checker.py": source.encode()}, tmp_path / "run", target, rules, None)[0]
    assert result.status == "UNABLE"
    assert "视觉观察" in result.suggestions


def test_older_checker_source_remains_available_with_current_execution_contract(tmp_path):
    target, rules = inputs(tmp_path / "source.xlsx")
    rules.rule_profile = "v2"
    prompt = write_starting_point(tmp_path, target, rules)
    assert "audit.checker_lib.li.process_special_characteristic_agent" in prompt
    assert "class ProcessSpecialCharacteristicAgent" in (tmp_path / "reference-checker.py").read_text()
    assert "class TaskAudit(ExcelAgent)" in (tmp_path / "template-checker.py").read_text()


@pytest.mark.parametrize("subtype,submitted", [("success", True), ("error_max_turns", False)])
def test_cli_completion_can_use_natural_language_but_budget_stop_cannot_confirm(tmp_path, subtype, submitted):
    from agent.execution.checker_guest import CheckerDraft
    from agent.execution.guest_model import read_checker_draft

    (tmp_path / "checker.py").write_text("# candidate")
    draft = read_checker_draft(tmp_path, CheckerDraft, {"subtype": subtype, "result": "已核对当前程序，提交。"})
    assert draft.submitted is submitted


def test_invalid_json_receipt_does_not_silently_request_a_full_trial(tmp_path):
    from agent.execution.checker_guest import CheckerDraft
    from agent.execution.guest_model import ModelProtocolError, read_checker_draft

    (tmp_path / "checker.py").write_text("# candidate")
    with pytest.raises(ModelProtocolError, match="receipt"):
        read_checker_draft(tmp_path, CheckerDraft, {"subtype": "success", "result": '{"clauses": ["A"]'})


def test_task_imports_execute_current_resource_bytes_and_restore_host_modules(tmp_path, monkeypatch):
    import sys
    from types import ModuleType

    path = tmp_path / "source.xlsx"
    workbook(path, "特殊特性清单")
    target, rules = inputs(path)
    source = human_program(tmp_path / "starting", target, rules).replace(
        "return super().rule_1(ctx)",
        "from resources import VALUE\n        return 'UNABLE', VALUE",
    )
    original_module = ModuleType("resources")
    monkeypatch.setitem(sys.modules, "resources", original_module)
    original_path = list(sys.path)
    for value in ("first", "later"):
        files = {"checker.py": source.encode(), "resources.py": f"RESOURCE = None\nVALUE = {value!r}".encode()}
        result = run_program(files, tmp_path / "reused-directory", target, rules, None)[0]
        assert result.suggestions == value
        assert sys.modules["resources"] is original_module
        assert sys.path == original_path
