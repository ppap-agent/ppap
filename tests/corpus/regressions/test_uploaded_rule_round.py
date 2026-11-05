"""Original customer DOCX requirements, not checker-generated rule bodies."""

from copy import deepcopy
from pathlib import Path

import pytest
from audit.audits import RoundInput, RoundWork
from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
from corpus_contracts.poc_oracle import POC_SEQ_TO_DOC_TYPE, load_poc_cases
from corpus_contracts.product_round import compare_file, engine_rule_body, failing_verdicts, format_verdicts
from product import run_round_from_bytes
from worker.intake import parse_rule_file
from worker.intake.rule_sources import rule_sources

RULE_ROOT = Path(__file__).resolve().parents[3] / "assets" / "poc测试" / "poc规则"
SOURCES = {
    "1": "1-2规则-尺寸检验报告.docx",
    "8": "8规则-过程流程图.docx",
    "9": "9规则-潜在过程失效模式及后果分析（PFMEA）.docx",
    "10": "10规则-控制计划.docx",
    "11": "11规则-特殊特性清单.docx",
}


class NoRuleRewrite:
    def structured_completion(self, **kwargs):
        raise AssertionError("A structured source must not be paraphrased for checker matching")


def uploaded_rules(seq):
    content = (RULE_ROOT / SOURCES[seq]).read_bytes()
    body = parse_rule_file("renamed.docx", content, document_type=POC_SEQ_TO_DOC_TYPE[seq].get_type_name(), ai=NoRuleRewrite())
    excerpts = {unit["excerpt"] for unit in rule_sources("renamed.docx", content)}
    assert all(item["rule_to_check"] in excerpts for item in body["rules"])
    return body


@pytest.mark.parametrize("seq, count", [("1", 11), ("8", 9), ("9", 7), ("10", 11), ("11", 2)])
def test_original_docx_keeps_confirmed_order_without_interpreting_text_or_filename(seq, count):
    body = uploaded_rules(seq)
    assert body["rule_profile"] == "v3"
    assert len(body["rules"]) == count
    body["rules"].reverse()
    for item in body["rules"]:
        item["id"] = "customer-" + item["id"]
        item["category"] = "客户自定标题"
    execution = freeze_target_rule_execution(body, "unrelated-name.xlsx")
    for position, item in enumerate(execution["items"]):
        assert item["coverage"] == "mapped", item
        assert item["checker_index"] == position
        assert item["id"] == body["rules"][position]["id"]
    changed = deepcopy(body)
    for item in changed["rules"]:
        item["rule_to_check"] += "；另需满足客户新增条件，否则不通过"
    assert all(item["coverage"] == "mapped" for item in freeze_target_rule_execution(changed, "unrelated-name.xlsx")["items"])


def test_original_docx_bindings_match_the_existing_worker_oracle_for_all_cases():
    bodies = {seq: uploaded_rules(seq) for seq in SOURCES}
    cases = load_poc_cases()
    assert len(cases) == 26
    assert sum(len(case.expected) for case in cases) == 209
    for case in cases:
        actual = freeze_target_rule_execution(bodies[case.file_seq], case.file_name)
        _body, existing = engine_rule_body(POC_SEQ_TO_DOC_TYPE[case.file_seq].get_type_name(), case.file_name, "v3")
        identity = ("id", "coverage", "checker_method", "checker_index")
        assert [tuple(item.get(key) for key in identity) for item in actual["items"]] == [tuple(item.get(key) for key in identity) for item in existing["items"]], case.file_name
        assert [item["id"] for item in actual["items"]] == [str(index) for index in sorted(case.expected)], case.file_name


@pytest.mark.usefixtures("ocr_transport_diagnostics")
def test_original_docx_three_file_round_matches_all_27_oracle_results():
    cases = [case for case in load_poc_cases(bundle="Cetus Spoiler-特殊特性清单见原文件夹") if case.file_seq in SOURCES]
    assert len(cases) == 3
    inputs, blobs = [], {}
    for case in cases:
        body = uploaded_rules(case.file_seq)
        version = "file-" + case.file_seq
        inputs.append(
            RoundInput(
                "target", case.file_seq, version, case.file_name, "ready", rule_version_id="rules-" + case.file_seq, rule_body=body, rule_execution=freeze_target_rule_execution(body, case.file_name)
            )
        )
        blobs[version] = case.file_path.read_bytes()
    result = run_round_from_bytes(RoundWork("uploaded-customer-rules", "corpus", inputs), blobs)
    items = result["coverage"]["rule_items"]
    assert len(items) == 27
    pending = [item for item in items if item["coverage"] != "mapped"]
    assert pending == []
    failures = []
    for case in cases:
        expected = {index: item.status for index, item in case.expected.items()}
        failures.extend(failing_verdicts(compare_file(file_name=case.file_name, version_id="file-" + case.file_seq, expected=expected, result=result, setup_error=None)))
    if failures:
        pytest.fail(format_verdicts(failures))
    assert len(result["findings"]) == 27
