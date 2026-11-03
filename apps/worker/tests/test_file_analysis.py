"""File recognition keeps metadata and rule suggestions tied to the input file."""

import json
from io import BytesIO

import pytest
from openpyxl import Workbook
from worker.intake import analyze_file


class MetadataAI:
    def __init__(self, metadata: dict, classification: str = "未知") -> None:
        self.metadata = metadata
        self.classification = classification

    def structured_completion(self, *, operation: str, **kwargs) -> str:
        if operation == "document_metadata_extraction":
            return json.dumps(self.metadata)
        if operation == "document_classification":
            return self.classification
        raise AssertionError("unexpected_external_request")


class UnavailableAI(MetadataAI):
    def __init__(self, operation: str) -> None:
        super().__init__({"customer": "甲客户"})
        self.operation = operation

    def structured_completion(self, *, operation: str, **kwargs) -> str:
        if operation == self.operation:
            raise TimeoutError("private_external_error")
        return super().structured_completion(operation=operation, **kwargs)


def workbook_bytes(*rows: tuple[str, str]) -> bytes:
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    return content.getvalue()


def test_recognized_part_number_is_separate_from_part_name() -> None:
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("零件名称", "外壳"), ("零件号", "P-204")),
        [],
        ai=MetadataAI({"customer": "甲客户", "model": "M1", "part": "外壳", "part_number": "P-204"}),
    )

    assert result["metadata"] == {"customer": "甲客户", "model": "M1", "part": "外壳", "part_number": "P-204"}


def rule(rule_id: str, customers: list[str], document_type: str = "控制计划", **fields) -> dict:
    return {"id": rule_id, "title": f"规则-{rule_id}", "kind": "engine", "type": document_type, "customers": customers, **fields}


def test_unknown_customer_explains_how_to_recover_without_taking_an_unscoped_rule() -> None:
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("文件类型", "控制计划")),
        [rule("customer-rule", ["甲客户"]), rule("generic", [])],
        ai=MetadataAI({}),
        scoped=True,
    )

    assert (result["matching_status"], result["matched_rule_id"], result["notices"]) == ("unmatched", None, ["matching_customer_required"])


@pytest.mark.parametrize("customer", [None, "NIO"])
def test_explicitly_unrestricted_rule_matches_without_inventing_a_customer(customer) -> None:
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("文件类型", "控制计划")),
        [rule("unconfirmed", []), rule("confirmed", [], customer_scope="all"), rule("other-customer", ["甲客户"])],
        ai=MetadataAI({"customer": customer}),
        scoped=True,
    )

    assert (result["matching_status"], result["matched_rule_id"], result["metadata"]["customer"], result["notices"]) == ("matched", "confirmed", customer, [])


def test_customer_preference_selects_an_actual_rule_without_replacing_recognized_customer() -> None:
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("客户", "乙客户"), ("文件类型", "控制计划")),
        [rule("generic", []), rule("identified", ["乙客户"]), rule("preferred", ["甲客户"]), rule("other-type", ["甲客户"], "过程流程图")],
        ai=MetadataAI({"customer": "乙客户"}),
        customer_preference="甲客户",
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["document_type"], result["metadata"]["customer"]) == (
        "preferred",
        "matched",
        "控制计划",
        "乙客户",
    )


def test_missing_preferred_scope_falls_back_to_the_customer_identified_in_the_file() -> None:
    result = analyze_file(
        "供应商资料.xlsx",
        workbook_bytes(("客户", "乙客户"), ("文件类型", "过程流程图")),
        [rule("other-type", ["甲客户"]), rule("identified", ["乙客户"], "过程流程图")],
        ai=MetadataAI({"customer": "乙客户"}),
        customer_preference="甲客户",
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["document_type"]) == ("identified", "matched", "过程流程图")


@pytest.mark.parametrize(
    "metadata,available_rules,reason",
    [
        ({}, [rule("known-customer", ["甲客户"])], "matching_customer_required"),
        ({}, [rule("generic", [])], "matching_rules_customer_unassigned"),
        ({"customer": "甲客户"}, [rule("generic", [])], "matching_rules_customer_unassigned"),
        ({"customer": "甲客户"}, [rule("wrong-customer", ["乙客户"])], "matching_customer_rules_missing"),
        ({"customer": "甲客户"}, [rule("wrong-type", ["甲客户"], "过程流程图")], "matching_document_type_rules_missing"),
        ({"customer": "甲客户"}, [rule("unknown-type", ["甲客户"], "未知")], "matching_document_type_rules_missing"),
        ({"customer": "甲客户"}, [{"id": "no-customers", "title": "客户控制计划", "kind": "engine", "type": "控制计划"}], "matching_rules_customer_unassigned"),
    ],
)
def test_scoped_recognition_does_not_invent_a_customer_or_generic_rule(metadata, available_rules, reason) -> None:
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("文件类型", "控制计划")),
        available_rules,
        ai=MetadataAI(metadata),
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["document_type"], result["notices"]) == (None, "unmatched", "控制计划", [reason])


def test_unknown_document_type_does_not_take_the_only_available_customer_rule() -> None:
    result = analyze_file(
        "供应商资料.xlsx",
        workbook_bytes(("客户", "甲客户")),
        [rule("only-rule", ["甲客户"])],
        ai=MetadataAI({"customer": "甲客户"}),
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["document_type"]) == (None, "unmatched", None)
    assert result["notices"] == ["matching_document_type_required"]


@pytest.mark.parametrize("reverse", [False, True])
def test_ambiguous_scoped_rules_do_not_use_order_or_invent_model_priority(reverse) -> None:
    rules = [rule("first", ["甲客户"], models=["M1"]), rule("second", ["甲客户"], models=[])]
    if reverse:
        rules.reverse()
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("客户", "甲客户"), ("车型", "M1")),
        rules,
        ai=MetadataAI({"customer": "甲客户", "model": "M1"}),
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["notices"]) == (None, "failed", ["ambiguous_available_rules"])


def test_ambiguous_preferred_scope_does_not_silently_choose_another_customer() -> None:
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("客户", "乙客户")),
        [rule("preferred-first", ["甲客户"]), rule("preferred-second", ["甲客户"]), rule("identified", ["乙客户"])],
        ai=MetadataAI({"customer": "乙客户"}),
        customer_preference="甲客户",
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["notices"]) == (None, "failed", ["ambiguous_available_rules"])
    assert result["candidate_rule_ids"] == ["preferred-first", "preferred-second"]


@pytest.mark.parametrize("customers", [[], ["甲客户"]])
def test_multiple_applicable_rules_include_explicitly_unrestricted_candidates(customers) -> None:
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("文件类型", "控制计划")),
        [rule("all", [], customer_scope="all"), rule("other", customers, customer_scope="all" if not customers else "restricted")],
        ai=MetadataAI({"customer": "甲客户"}),
        scoped=True,
    )
    assert (result["matching_status"], result["matched_rule_id"], result["candidate_rule_ids"]) == ("failed", None, ["all", "other"])


def test_special_characteristic_list_is_recognized_as_reference_without_a_fake_rule() -> None:
    result = analyze_file(
        "11 特殊特性清单.xlsx",
        workbook_bytes(("特殊特性清单", "产品及过程特殊特性")),
        [rule("control-plan", [], customer_scope="all")],
        ai=MetadataAI({"part_number": "P0377535"}),
        scoped=True,
    )
    assert (result["matching_status"], result["matched_rule_id"], result["document_type"], result["notices"]) == ("reference", None, "过程特殊特性清单", [])


def test_legacy_default_still_selects_the_latest_engine_rule_without_customer_scope() -> None:
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("文件类型", "控制计划")),
        [rule("old", [], created_at="2026-01-01"), rule("new", [], created_at="2026-02-01", predecessor_id="old")],
        ai=MetadataAI({}),
    )

    assert (result["matched_rule_id"], result["matching_status"], result["document_type"]) == ("new", "matched", "控制计划")


def test_scoped_candidates_are_not_silently_replaced_by_a_successor() -> None:
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("客户", "甲客户")),
        [rule("frozen", ["甲客户"], created_at="2026-01-01"), rule("successor", ["乙客户"], created_at="2026-02-01", predecessor_id="frozen")],
        ai=MetadataAI({"customer": "甲客户"}),
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"]) == ("frozen", "matched")


def test_opaque_filename_can_match_a_rule_after_classifying_the_real_document_text() -> None:
    result = analyze_file(
        "附件001.xlsx",
        workbook_bytes(("客户", "甲客户"), ("工序说明", "接收材料 → 加工 → 检验 → 入库")),
        [rule("flow", ["甲客户"], "过程流程图")],
        ai=MetadataAI({"customer": "甲客户"}, classification="过程流程图"),
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["document_type"]) == ("flow", "matched", "过程流程图")


@pytest.mark.parametrize(
    "operation,filename,notice",
    [
        ("document_metadata_extraction", "控制计划.xlsx", "metadata_extraction_failed"),
        ("document_classification", "附件001.xlsx", "document_classification_failed"),
    ],
)
def test_external_recognition_failure_is_distinct_from_a_definitive_rule_miss(operation, filename, notice) -> None:
    result = analyze_file(
        filename,
        workbook_bytes(("客户", "甲客户")),
        [rule("customer-rule", ["甲客户"])],
        ai=UnavailableAI(operation),
        customer_preference="甲客户",
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["notices"]) == (None, "failed", [notice])


def test_a_real_preferred_customer_rule_can_match_when_the_document_customer_is_unknown() -> None:
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("文件类型", "控制计划")),
        [rule("preferred", ["甲客户"]), rule("generic", [])],
        ai=MetadataAI({}),
        customer_preference="甲客户",
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["metadata"]["customer"]) == ("preferred", "matched", None)


@pytest.mark.parametrize("content,notice", [(b"damaged workbook", "document_text_unavailable"), (workbook_bytes(), "empty_document_text")], ids=["damaged", "empty"])
def test_unreadable_or_empty_files_fail_recognition_even_with_a_customer_preference(content, notice) -> None:
    result = analyze_file("控制计划.xlsx", content, [rule("preferred", ["甲客户"])], ai=MetadataAI({}), customer_preference="甲客户", scoped=True)

    assert (result["matched_rule_id"], result["matching_status"], result["notices"]) == (None, "failed", [notice])


@pytest.mark.parametrize("classification", ["", "private unrecognized provider response"])
def test_malformed_classification_is_a_safe_failure(classification) -> None:
    result = analyze_file(
        "附件001.xlsx",
        workbook_bytes(("客户", "甲客户")),
        [rule("customer-rule", ["甲客户"])],
        ai=MetadataAI({"customer": "甲客户"}, classification=classification),
        scoped=True,
    )

    assert (result["matched_rule_id"], result["matching_status"], result["notices"]) == (None, "failed", ["document_classification_failed"])


def test_v4_customer_conflict_uses_explicit_file_facts_not_a_models_guess():
    presets = [rule("nio-cp", ["蔚来"], rule_profile="v4"), rule("li-cp", ["理想"], rule_profile="v4")]
    conflict = analyze_file("控制计划.xlsx", workbook_bytes(("客户", "理想"), ("文件类型", "控制计划")), presets, ai=MetadataAI({"customer": "蔚来"}), customer_preference="蔚来", scoped=True)
    assert conflict["matching_status"] == "failed"
    assert conflict["matched_rule_id"] is None
    assert "matching_customer_conflict" in conflict["notices"]
    guess = analyze_file("控制计划.xlsx", workbook_bytes(("文件类型", "控制计划")), presets, ai=MetadataAI({"customer": "理想"}), customer_preference="蔚来", scoped=True)
    assert guess["matched_rule_id"] == "nio-cp"
