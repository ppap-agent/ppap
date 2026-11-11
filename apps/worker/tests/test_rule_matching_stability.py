"""Current file preparation preserves deterministic suggestions and version scope."""

from io import BytesIO

import pytest
from documents.ai import AIResult
from openpyxl import Workbook
from product.rule_matching import RuleMatchingAIService
from product.rules import latest_rule_versions
from worker.intake import analyze_file


class MatchingAI:
    """Only the external model boundary is controlled; extraction and matching are real."""

    def __init__(self, *selections: str) -> None:
        self.selections = iter(selections)
        self.requests: list[dict] = []

    def __call__(self, operation: str, **request) -> AIResult:
        self.requests.append({"operation": operation, **request})
        if operation == "document_metadata_extraction":
            return AIResult({})
        if operation == "rule_matching":
            return AIResult({"candidateId": next(self.selections)})
        raise AssertionError("unexpected_external_request")


def workbook_bytes(text: str) -> bytes:
    workbook = Workbook()
    workbook.active.append([text])
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    return content.getvalue()


def rule(rule_id: str, title: str, document_type: str, **fields) -> dict:
    return {"id": rule_id, "title": title, "type": document_type, "kind": "engine", **fields}


@pytest.fixture(autouse=True)
def empty_matching_cache():
    RuleMatchingAIService.clear_cache()
    yield
    RuleMatchingAIService.clear_cache()


def test_no_available_rules_returns_an_explicit_miss_not_a_model_guess() -> None:
    ai = MatchingAI()
    result = analyze_file("控制计划.xlsx", workbook_bytes("控制计划"), [], ai=ai)

    assert (result["matched_rule_id"], result["matching_status"], result["notices"]) == (None, "unmatched", ["no_available_rules"])
    assert [request["operation"] for request in ai.requests] == ["document_metadata_extraction"]


@pytest.mark.parametrize("reverse", [False, True])
def test_current_rule_catalog_has_stable_order_independent_of_provider_order(reverse: bool) -> None:
    rules = [
        rule("flow-old", "Flow", "过程流程图", created_at="2026-01-01"),
        rule("cp-b", "CP", "控制计划"),
        rule("flow-current", "Flow", "过程流程图", created_at="2026-02-01", predecessor_id="flow-old"),
        rule("cp-a", "CP", "控制计划"),
    ]
    if reverse:
        rules.reverse()

    assert [item["id"] for item in latest_rule_versions(rules)] == ["cp-a", "cp-b", "flow-current"]


@pytest.mark.parametrize(
    "filename,text",
    [
        ("PFMEA-NIO-Sirius-Spoiler-ASM_02_v2.xlsx", "PFMEA revision history severity occurrence detection RPN"),
        ("BLANC扰流板总成PFMEA_V3.xlsx", "工序名称 操作描述 输入 输出 制造 运输 存储 检验 返工 报废"),
    ],
    ids=["strong-filename", "filename-alias-over-generic-content"],
)
def test_filename_identifies_pfmea_without_an_external_rule_selection(filename: str, text: str) -> None:
    ai = MatchingAI()
    result = analyze_file(
        filename,
        workbook_bytes(text),
        [rule("cp", "10 控制计划.docx", "控制计划"), rule("flow", "8 过程流程图.docx", "过程流程图"), rule("pfmea", "9 PFMEA.docx", "潜在过程失效模式及后果分析")],
        ai=ai,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["document_type"]) == ("pfmea", "matched", "潜在过程失效模式及后果分析")
    assert [request["operation"] for request in ai.requests] == ["document_metadata_extraction"]


def test_content_keywords_identify_the_reference_type_on_the_existing_unscoped_entry() -> None:
    ai = MatchingAI()
    result = analyze_file(
        "P0363244_扰流板总成.xlsx",
        workbook_bytes("特殊特性清单 特性分类 特性描述 控制方法 防错方案 样本数"),
        [rule("cp", "10 控制计划.docx", "控制计划"), rule("scl", "11 特殊特性清单.docx", "过程特殊特性清单")],
        ai=ai,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["document_type"]) == ("scl", "matched", "过程特殊特性清单")
    assert [request["operation"] for request in ai.requests] == ["document_metadata_extraction"]


def test_unscoped_opaque_file_does_not_take_the_only_rule_after_model_rejection() -> None:
    ai = MatchingAI("unknown")
    result = analyze_file("review_document.xlsx", workbook_bytes("generic worksheet header"), [rule("cp", "10 控制计划.docx", "控制计划")], ai=ai)

    assert (result["matched_rule_id"], result["matching_status"]) == (None, "unmatched")
    assert [request["operation"] for request in ai.requests] == ["document_metadata_extraction", "rule_matching"]


def test_late_content_does_not_drive_rule_selection_outside_the_matching_excerpt() -> None:
    ai = MatchingAI("unknown")
    result = analyze_file(
        "P0363244_扰流板总成.xlsx",
        workbook_bytes("无关内容" * 700 + " 特殊特性清单 特性分类 特性描述 控制方法 防错方案 样本数 FILE-LATE-SIGNAL"),
        [rule("cp", "10 控制计划.docx", "控制计划"), rule("scl", "11 特殊特性清单.docx", "过程特殊特性清单")],
        ai=ai,
    )

    assert (result["matched_rule_id"], result["matching_status"]) == (None, "unmatched")
    requests = [request for request in ai.requests if request["operation"] == "rule_matching"]
    assert len(requests) == 1
    assert "FILE-LATE-SIGNAL" not in requests[0]["prompt"]


def test_cached_selection_is_reused_only_for_the_same_current_rule_versions() -> None:
    ai = MatchingAI("R02", "R01")
    content = workbook_bytes("generic worksheet header")
    original = [rule("flow-v1", "08 过程流程图.docx", "过程流程图"), rule("scl-v1", "11 特殊特性清单.docx", "过程特殊特性清单")]
    replacement = [rule("flow-v2", "08 过程流程图.docx", "过程流程图"), rule("scl-v2", "11 特殊特性清单.docx", "过程特殊特性清单")]

    first = analyze_file("未命名审核文件.xlsx", content, original, ai=ai)
    repeated = analyze_file("未命名审核文件.xlsx", content, original, ai=ai)
    changed = analyze_file("未命名审核文件.xlsx", content, replacement, ai=ai)

    assert (first["matched_rule_id"], repeated["matched_rule_id"], changed["matched_rule_id"]) == ("scl-v1", "scl-v1", "flow-v2")
    assert [request["operation"] for request in ai.requests] == [
        "document_metadata_extraction",
        "rule_matching",
        "document_metadata_extraction",
        "document_metadata_extraction",
        "rule_matching",
    ]
