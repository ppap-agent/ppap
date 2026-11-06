from io import BytesIO

import pytest
from audit.audits import RoundInput, RoundWork
from audit.suite_migration import migrated_builtin_suite
from corpus_contracts.core_round import bind_suite, run_suite_round
from openpyxl import Workbook

START_REQUIREMENT = "识别“工序名称”列是否存在，如果存在则继续识别；当“工序名称”列下包含“进料检验”或“IQC”或相近词义的内容则通过，否则不通过"
FINAL_REQUIREMENT = "识别“工序名称”列是否存在，如果存在则继续识别；当“工序名称”列下包含“出货”或“FQC”或“OQC”或相近词义的内容则通过，否则不通过"


def process_round(rows, *, text="起始工序拆解完整性识别", bindings=None, rules=None, extra_sheets=(), merges=()):
    """Exercise incoming/final checks through explicit source-clause bindings."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    for row in rows:
        sheet.append(row)
    for span in merges:
        sheet.merge_cells(span)
    for title, extra_rows in extra_sheets:
        extra = workbook.create_sheet(title)
        for row in extra_rows:
            extra.append(row)
    blob = BytesIO()
    workbook.save(blob)
    workbook.close()
    body = {
        "kind": "engine",
        "type": "过程流程图",
        "rule_profile": "v3",
        "rules": rules if rules is not None else [{"id": "customer-start", "category": "入料检查", "rule_to_check": text}],
    }
    if bindings is None:
        bindings = {"customer-start": "6"}
    suite = bind_suite(migrated_builtin_suite("v3", "过程流程图"), body, bindings)
    target = RoundInput("target", "process", "process-v1", "process.xlsx", "ready", rule_version_id="rules-v1", rule_body=body)
    return run_suite_round(RoundWork("round", "tenant", [target]), {"process-v1": blob.getvalue()}, suites={"process-v1": suite})


def test_start_inspection_needs_only_the_required_process_name_column():
    result = process_round([["工序名称"], ["IQC"]])

    assert [(item["item_key"], item["status"]) for item in result["findings"]] == [("process-v1:rule:customer-start", "PASS")]


def test_full_final_inspection_requirement_executes_without_unrelated_headers():
    result = process_round([["工序名称"], ["FQC"]], text=FINAL_REQUIREMENT, bindings={"customer-start": "7"})

    assert [(item["item"], item["status"]) for item in result["findings"]] == [(FINAL_REQUIREMENT, "PASS")]


def test_unrelated_material_and_inspection_words_do_not_prove_incoming_inspection():
    result = process_round([["工序名称"], ["原材料包装、成品检验"]])

    assert [item["status"] for item in result["findings"]] == ["NG"]


@pytest.mark.parametrize(
    "rows, expected",
    [
        ([["工序名称"], ["包装"]], "NG"),
        ([["工序"], ["IQC"]], "NG"),
        ([["工序名称"]], "NG"),
        ([["提示：工序名称需填写", None], ["IQC", None], [None, "工序名称"], [None, "包装"]], "NG"),
        ([["工序名称"], ["工序名称：进料检验"]], "PASS"),
        ([["工序名称"], ["说明"]] + [[]] * 120 + [["IQC"]], "PASS"),
        ([[]] * 85 + [["工序名称"], ["原材料检验"]], "PASS"),
        ([["工序名称"], ["工序名称"], ["来料检验"]], "PASS"),
    ],
)
def test_full_start_requirement_handles_missing_columns_and_process_layouts(rows, expected):
    result = process_round(rows, text=START_REQUIREMENT)

    assert [item["status"] for item in result["findings"]] == [expected]


def test_process_name_column_can_be_on_a_later_matching_sheet():
    result = process_round([["封面"]], text=START_REQUIREMENT, extra_sheets=[("过程流程图2", [["工序名称"], ["IQC"]])])

    assert [item["status"] for item in result["findings"]] == ["PASS"]


def test_merged_process_names_and_repeated_headers_do_not_hide_inspection():
    result = process_round([["工序名称"], ["IQC"], [], ["工序名称"]], text=START_REQUIREMENT, merges=["A2:A3"])

    assert [item["status"] for item in result["findings"]] == ["PASS"]


@pytest.mark.parametrize("text, value, clause", [(START_REQUIREMENT, "IQC", "6"), (FINAL_REQUIREMENT, "FQC", "7")])
def test_process_value_merged_across_the_name_column_keeps_its_left_hand_anchor(text, value, clause):
    result = process_round([[None, "工序名称"], [value]], text=text, merges=["A2:B2"], bindings={"customer-start": clause})

    assert [item["status"] for item in result["findings"]] == ["PASS"]


def test_explicit_source_clause_bindings_are_preserved_instead_of_remapped_by_new_order():
    rules = [
        {"id": "final", "category": "出货要求", "priority": "P1", "rule_to_check": FINAL_REQUIREMENT},
        {"id": "start", "category": "来料要求", "priority": "P0", "rule_to_check": START_REQUIREMENT},
    ]
    result = process_round([["工序名称"], ["IQC"]], rules=rules, bindings={"final": "7", "start": "6"})

    assert [(item["item_key"], item["classification"], item["priority"], item["status"]) for item in result["findings"]] == [
        ("process-v1:rule:final", "出货要求", "P1", "NG"),
        ("process-v1:rule:start", "来料要求", "P0", "PASS"),
    ]


def test_existing_frozen_unsupported_rule_is_not_remapped_by_new_checker_support():
    result = process_round([["工序名称"], ["IQC"]], text=START_REQUIREMENT, bindings={})

    assert result["findings"] == []
    assert result["coverage"]["rule_items"][0]["coverage"] == "unsupported"


def test_same_named_files_use_their_own_frozen_bytes_and_keep_distinct_result_sources():
    inputs, blobs = [], {}
    for version_id, process in [("version-first", "IQC"), ("version-second", "包装")]:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "过程流程图"
        sheet.append(["工序名称"])
        sheet.append([process])
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        blobs[version_id] = buffer.getvalue()
        inputs.append(
            RoundInput(
                "target",
                "file-" + version_id,
                version_id,
                "process.xlsx",
                "ready",
                rule_version_id="rules-v1",
                rule_body={"kind": "engine", "type": "过程流程图", "rule_profile": "v3", "rules": [{"id": "start", "rule_to_check": START_REQUIREMENT}]},
            )
        )

    suites = {item.file_version_id: bind_suite(migrated_builtin_suite("v3", "过程流程图"), item.rule_body, {"start": "6"}) for item in inputs}
    result = run_suite_round(RoundWork("frozen-round", "tenant", inputs), blobs, suites=suites)

    assert [(row["item_key"], row["status"], row["locator"]["file_id"], row["locator"]["file_version_id"], row["locator"]["filename"]) for row in result["findings"]] == [
        ("version-first:rule:start", "PASS", "file-version-first", "version-first", "process.xlsx"),
        ("version-second:rule:start", "NG", "file-version-second", "version-second", "process.xlsx"),
    ]
