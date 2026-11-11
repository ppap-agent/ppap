"""File recognition keeps metadata and rule suggestions tied to the input file."""

import json
from io import BytesIO

import pytest
from documents.ai import AIResult
from documents.model_access import ModelUnavailable
from openpyxl import Workbook
from worker.intake import analyze_file


class MetadataAI:
    def __init__(self, metadata: dict, classification: str = "未知") -> None:
        self.metadata = metadata
        self.classification = classification

    def __call__(self, operation: str, **kwargs) -> AIResult:
        if operation == "document_metadata_extraction":
            return AIResult(json.loads(json.dumps(self.metadata)))
        if operation == "document_classification":
            return AIResult({"document_type": self.classification})
        raise AssertionError("unexpected_external_request")


class UnavailableAI(MetadataAI):
    def __init__(self, operation: str) -> None:
        super().__init__({"customer": "甲客户"})
        self.operation = operation

    def __call__(self, operation: str, **kwargs) -> AIResult:
        if operation == self.operation:
            raise TimeoutError("private_external_error")
        return super().__call__(operation, **kwargs)


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


@pytest.mark.parametrize("label", ["客户", "客户名称", "审核客户", "客户名", "Customer", "Customer Name"])
def test_v4_customer_conflict_uses_explicit_file_facts_not_a_models_guess(label):
    presets = [rule("nio-cp", ["蔚来"], rule_profile="v4"), rule("li-cp", ["理想"], rule_profile="v4")]
    conflict = analyze_file("控制计划.xlsx", workbook_bytes((label, "理想"), ("文件类型", "控制计划")), presets, ai=MetadataAI({"customer": "蔚来"}), customer_preference="蔚来", scoped=True)
    assert conflict["matching_status"] == "failed"
    assert conflict["matched_rule_id"] is None
    assert "matching_customer_conflict" in conflict["notices"]
    guess = analyze_file("控制计划.xlsx", workbook_bytes(("文件类型", "控制计划")), presets, ai=MetadataAI({"customer": "理想"}), customer_preference="蔚来", scoped=True)
    assert guess["matched_rule_id"] == "nio-cp"


@pytest.mark.parametrize("reference_only", [False, True])
def test_explicit_validated_uaes_photo_scope_routes_without_inventing_text(reference_only):
    from PIL import Image

    content = BytesIO()
    Image.new("RGB", (2, 2), "white").save(content, format="PNG")

    class NoTextModel:
        def __call__(self, *args, **kwargs):
            raise AssertionError("A validated photo must not require a text model")

    result = analyze_file(
        "样品.png",
        content.getvalue(),
        [rule("photo-preset", ["UAES"], "E07", rule_profile="v4")],
        ai=NoTextModel(),
        customer_preference="UAES",
        scoped=True,
        reference_only=reference_only,
        validation_scope={"customer_preference": "UAES", "preset_key": "U13"},
    )
    assert result["document_type"] == "E07"
    assert result["matching_status"] == ("reference" if reference_only else "matched")
    assert result["matched_rule_id"] == (None if reference_only else "photo-preset")
    assert result["metadata"]["customer"] is None
    assert result["notices"] == ["photo_content_requires_manual_confirmation"]


def test_a_different_customer_cannot_reuse_a_photo_scope_to_bypass_recognition():
    result = analyze_file("样品.png", b"unused", [rule("nio", ["蔚来"])], customer_preference="蔚来", scoped=True, validation_scope={"customer_preference": "UAES", "preset_key": "U13"})
    assert result["matching_status"] == "failed" and result["matched_rule_id"] is None


def test_v4_auto_customer_uses_original_facts_while_retaining_model_metadata():
    result = analyze_file(
        "控制计划.xlsx",
        workbook_bytes(("客户", "蔚来"), ("文件类型", "控制计划")),
        [rule("nio", ["蔚来"], rule_profile="v4"), rule("li", ["理想"], rule_profile="v4")],
        ai=MetadataAI({"customer": "理想"}),
        scoped=True,
    )
    assert result["matched_rule_id"] == "nio"
    assert result["metadata"]["customer"] == "理想"


def test_v4_auto_without_a_customer_fact_requires_confirmation_even_with_a_model_guess():
    result = analyze_file("控制计划.xlsx", workbook_bytes(("文件类型", "控制计划")), [rule("nio", ["蔚来"], rule_profile="v4")], ai=MetadataAI({"customer": "蔚来"}), scoped=True)
    assert result["matched_rule_id"] is None
    assert result["notices"] == ["matching_customer_required"]


@pytest.mark.parametrize("filename,classification", [("包装方案.xlsx", "未知"), ("附件001.xlsx", "E03")])
def test_uaes_source_e_types_use_their_own_registered_identity(filename, classification):
    result = analyze_file(
        filename,
        workbook_bytes(("客户", "UAES"), ("包装形式", "周转箱")),
        [rule("pack", ["UAES"], "E03", rule_profile="v4")],
        ai=MetadataAI({}, classification),
        customer_preference="UAES",
        scoped=True,
    )
    assert result["matching_status"] == "matched", result
    assert result["matched_rule_id"] == "pack"
    assert result["document_type"] == "E03"


def test_a_different_customer_cannot_classify_a_uaes_only_e_type():
    result = analyze_file(
        "附件001.xlsx",
        workbook_bytes(("客户", "理想")),
        [rule("pack", ["UAES"], "E03", rule_profile="v4"), rule("li", ["理想"], rule_profile="v4")],
        ai=MetadataAI({}, "E03"),
        customer_preference="理想",
        scoped=True,
    )
    assert result["matching_status"] == "failed"
    assert result["matched_rule_id"] is None


def test_builtin_v4_catalog_does_not_disable_an_existing_unrestricted_v2_auto_rule():
    result = analyze_file(
        "控制计划.xlsx", workbook_bytes(("文件类型", "控制计划")), [rule("legacy", [], customer_scope="all"), rule("nio", ["蔚来"], rule_profile="v4")], ai=MetadataAI({}), scoped=True
    )
    assert result["matched_rule_id"] == "legacy", result
    assert result["matching_status"] == "matched"


@pytest.mark.parametrize("filename", ["PGQP1.xlsx", "F-PU-127 采购件质量计划.xlsx"])
def test_uaes_pgqp1_is_an_automatic_reference_never_a_formal_target(filename):
    result = analyze_file(filename, workbook_bytes(("客户", "UAES"), ("文件类型", "PGQP1")), [rule("uaes-cp", ["UAES"], rule_profile="v4")], ai=MetadataAI({}), customer_preference="UAES", scoped=True)
    assert (result["document_type"], result["matching_status"], result["matched_rule_id"]) == ("E12", "reference", None)


def test_pgqp1_reference_routing_does_not_escape_the_uaes_customer_scope():
    result = analyze_file("PGQP1.xlsx", workbook_bytes(("客户", "理想")), [rule("li-cp", ["理想"], rule_profile="v4")], ai=MetadataAI({}), customer_preference="理想", scoped=True)
    assert result["matching_status"] != "reference"
    assert result["matched_rule_id"] is None


def test_pgqp1_mentioned_in_a_pfmea_filename_does_not_turn_the_pfmea_into_a_reference():
    result = analyze_file(
        "PFMEA-PGQP1比对.xlsx",
        workbook_bytes(("客户", "UAES"), ("文件类型", "PFMEA")),
        [rule("uaes-pfmea", ["UAES"], "PFMEA", rule_profile="v4")],
        ai=MetadataAI({}),
        customer_preference="UAES",
        scoped=True,
    )
    assert result["matching_status"] == "matched"
    assert result["matched_rule_id"] == "uaes-pfmea"


@pytest.mark.parametrize("filename", ["供应商自定义追溯记录.xlsx", "renamed.xlsx"])
def test_published_custom_document_type_can_match_without_a_platform_preset(filename):
    kind = "供应商自定义追溯记录"
    result = analyze_file(
        filename,
        workbook_bytes(("批号", "B1")),
        [rule("custom", ["甲客户"], kind), rule("official", ["UAES"], "E01", rule_profile="v4")],
        ai=MetadataAI({"customer": "甲客户"}, classification=kind),
        scoped=True,
    )
    assert (result["matching_status"], result["matched_rule_id"], result["document_type"]) == ("matched", "custom", kind)
    assert result["notices"] == []


def test_custom_document_type_does_not_bypass_customer_scope():
    result = analyze_file(
        "供应商自定义追溯记录.xlsx",
        workbook_bytes(("批号", "B1")),
        [rule("custom", ["甲客户"], "供应商自定义追溯记录")],
        ai=MetadataAI({"customer": "乙客户"}),
        scoped=True,
    )
    assert result["matched_rule_id"] is None
    assert result["matching_status"] == "unmatched"


@pytest.mark.parametrize("code,notice", [("platform_model_unconfigured", "metadata_extraction_failed"), ("tenant_model_unavailable", "metadata_extraction_failed")])
def test_an_unavailable_model_is_a_safe_recognition_failure(code, notice) -> None:
    class NoModel(MetadataAI):
        def __call__(self, operation: str, **kwargs) -> AIResult:
            raise ModelUnavailable(code)

    result = analyze_file("控制计划.xlsx", workbook_bytes(("客户", "甲客户")), [rule("customer-rule", ["甲客户"])], ai=NoModel({}), customer_preference="甲客户", scoped=True)
    assert (result["matched_rule_id"], result["matching_status"], result["notices"]) == (None, "failed", [notice])
