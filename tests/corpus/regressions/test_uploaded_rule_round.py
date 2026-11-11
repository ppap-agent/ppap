"""Original customer DOCX requirements, not checker-generated rule bodies."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from audit.audits import RoundInput, RoundWork
from audit.suite_migration import migrated_builtin_suite, user_rule_files
from corpus_contracts.core_round import bind_suite, run_suite_round
from corpus_contracts.poc_oracle import POC_SEQ_TO_DOC_TYPE, load_poc_cases
from corpus_contracts.product_round import compare_file, failing_verdicts, format_verdicts
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
    def __call__(self, *args, **kwargs):
        raise AssertionError("A structured source must not be paraphrased for checker matching")


def uploaded_rules(seq):
    content = (RULE_ROOT / SOURCES[seq]).read_bytes()
    body = parse_rule_file("renamed.docx", content, document_type=POC_SEQ_TO_DOC_TYPE[seq].get_type_name(), ai=NoRuleRewrite())
    excerpts = {unit["excerpt"] for unit in rule_sources("renamed.docx", content)}
    assert all(item["rule_to_check"] in excerpts for item in body["rules"])
    return body


SOURCE_TYPES = {"1": "全功能检验报告", "8": "过程流程图", "9": "潜在过程失效模式及后果分析", "10": "控制计划", "11": "过程特殊特性清单"}


@pytest.mark.parametrize("seq, count", [("1", 11), ("8", 9), ("9", 7), ("10", 11), ("11", 2)])
def test_original_docx_keeps_confirmed_source_bindings_without_interpreting_text_or_filename(seq, count):
    body = uploaded_rules(seq)
    assert body["rule_profile"] == "v3"
    assert len(body["rules"]) == count
    body["rules"].reverse()
    aliases = {}
    for item in body["rules"]:
        source_id = item["id"]
        item["id"] = "customer-" + source_id
        item["category"] = "客户自定标题"
        aliases[item["id"]] = source_id
    source = migrated_builtin_suite("v3", SOURCE_TYPES[seq])
    suite = bind_suite(source, body, aliases)
    assert list(suite.entrypoints) == [item["id"] for item in body["rules"]]
    assert set(aliases.values()) == set(source.entrypoints)
    assert all(aliases[item["id"]] == item["id"].removeprefix("customer-") for item in body["rules"])
    changed = deepcopy(body)
    for item in changed["rules"]:
        item["rule_to_check"] += "；另需满足客户新增条件，否则不通过"
    # New uploaded requirements do not acquire business coverage by order/text.
    imported = json.loads(user_rule_files(changed)["rules.json"])
    assert imported["checker_binding"] == {}
    assert imported["rules"] == changed["rules"]


def test_original_docx_bindings_match_the_existing_worker_oracle_for_all_cases():
    bodies = {seq: uploaded_rules(seq) for seq in SOURCES}
    cases = load_poc_cases()
    assert len(cases) == 26
    assert sum(len(case.expected) for case in cases) == 209
    for case in cases:
        body = bodies[case.file_seq]
        source = migrated_builtin_suite("v3", SOURCE_TYPES[case.file_seq])
        aliases = {item["id"]: item["id"] for item in body["rules"]}
        suite = bind_suite(source, body, aliases)
        assert set(suite.entrypoints) == set(source.entrypoints), case.file_name
        assert list(suite.entrypoints) == [str(index) for index in sorted(case.expected)], case.file_name
        assert json.loads(suite.files["rules.json"])["rules"] == body["rules"]


@pytest.mark.usefixtures("ocr_transport_diagnostics")
def test_original_docx_three_file_round_matches_all_27_oracle_results():
    cases = [case for case in load_poc_cases(bundle="Cetus Spoiler-特殊特性清单见原文件夹") if case.file_seq in SOURCES]
    assert len(cases) == 3
    inputs, blobs, suites = [], {}, {}
    for case in cases:
        body = uploaded_rules(case.file_seq)
        version = "file-" + case.file_seq
        inputs.append(RoundInput("target", case.file_seq, version, case.file_name, "ready", rule_version_id="rules-" + case.file_seq, rule_body=body))
        blobs[version] = case.file_path.read_bytes()
        suites[version] = bind_suite(migrated_builtin_suite("v3", SOURCE_TYPES[case.file_seq]), body, {item["id"]: item["id"] for item in body["rules"]})
    result = run_suite_round(RoundWork("uploaded-customer-rules", "corpus", inputs), blobs, suites=suites)
    items = result["coverage"]["rule_items"]
    assert len(items) == 27
    pending = [item for item in items if item["coverage"] != "executed"]
    assert pending == []
    failures = []
    for case in cases:
        expected = {index: item.status for index, item in case.expected.items()}
        failures.extend(failing_verdicts(compare_file(file_name=case.file_name, version_id="file-" + case.file_seq, expected=expected, result=result, setup_error=None, expected_coverage="executed")))
    if failures:
        pytest.fail(format_verdicts(failures))
    assert len(result["findings"]) == 27
