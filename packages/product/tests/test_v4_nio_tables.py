"""Original NIO source decisions execute through frozen Round bindings."""

from io import BytesIO

import pytest
from audit.audits import RoundInput, RoundWork
from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
from audit.presets import preset_rule_body
from openpyxl import Workbook
from product import run_round_from_bytes


def workbook(rows, title):
    book = Workbook()
    book.active.title = title
    for row in rows:
        book.active.append(row)
    content = BytesIO()
    book.save(content)
    book.close()
    return content.getvalue()


def run(entry, rows, title, reference=None):
    body = preset_rule_body("蔚来", entry)
    target = RoundInput("target", "file", "version", "原件.xlsx", "ready", rule_version_id="rule", rule_body=body, rule_execution=freeze_target_rule_execution(body, "原件.xlsx"))
    files = [target]
    contents = {"version": workbook(rows, title)}
    if reference:
        references = reference if isinstance(reference, list) else [reference]
        for index, (_other_entry, other_rows, other_title) in enumerate(references):
            version = "other" if index == 0 else f"other-{index}"
            files.append(RoundInput("reference", version, version, other_title + ".xlsx", "ready"))
            contents[version] = workbook(other_rows, other_title)
    result = run_round_from_bytes(RoundWork("round", "tenant", files), contents)
    assert all(item["coverage"] == "mapped" for item in result["coverage"]["rule_items"]), result
    return result["findings"]


@pytest.mark.parametrize(("name", "status"), [("IQC", "PASS"), ("来料检查", "NG"), ("=A1", "UNABLE")])
def test_pfd_start_uses_the_exact_source_and_preserves_formula_unknown(name, status):
    findings = run("N05", [["工序名称"], [name]], "过程流程图")
    assert findings[5]["status"] == status, findings[5]


def test_pfd_rework_itself_saying_inspection_does_not_replace_the_next_row():
    findings = run("N05", [["工序名称"], ["IQC"], ["返工检验"], ["包装"], ["OQC"]], "过程流程图")
    assert findings[7]["status"] == "NG", findings[7]


def test_pfd_rework_followed_by_inspection():
    findings = run("N05", [["工序名称"], ["IQC"], ["返工"], ["重检"], ["OQC"]], "过程流程图")
    assert findings[7]["status"] == "PASS", findings[7]


@pytest.mark.parametrize(("title", "status"), [("P1 特殊特性清单", "PASS"), ("特殊特性清单", "NG")])
def test_scl_identifier_has_no_invented_minimum_number_length(title, status):
    findings = run("N04", [[title]], "特殊特性清单")
    assert findings[0]["status"] == status, findings[0]


@pytest.mark.parametrize(("product", "status"), [("孔径", "PASS"), (None, "NG"), ("=A1", "UNABLE")])
def test_cp_special_characteristic_needs_a_product_or_process(product, status):
    findings = run("N03", [["特殊特性分类", "产品", "过程"], ["[CC]", product, None]], "控制计划")
    assert findings[1]["status"] == status, findings[1]


@pytest.mark.parametrize(("stage", "status"), [("☑ 生产 □ 样件 □ 试生产", "PASS"), ("□ 生产 ☑ 样件 □ 试生产", "NG")])
def test_cp_production_stage(stage, status):
    findings = run("N03", [[stage]], "控制计划")
    assert findings[0]["status"] == status, findings[0]


@pytest.mark.parametrize(("part", "name", "status"), [("P-1", "成型", "PASS"), ("P-1", "包装", "NG"), ("P-2", "成型", "UNABLE")])
def test_cp_pfd_pair_checks_identity_and_has_both_originals(part, name, status):
    own = [["零件号", "P-1"], ["版本", "A"], ["零件/工序号", "工序名称/操作描述"], [10, "成型"]]
    other = [["零件号", part], ["版本", "A"], ["工序号", "工序名称"], [10, name]]
    findings = run("N03", own, "控制计划", ("N05", other, "过程流程图"))
    finding = findings[5]
    assert finding["status"] == status, finding
    if status in {"PASS", "NG"}:
        evidence = finding["locator"]["evidence"]["sources"]
        import json

        assert {json.loads(item["locator"])["file_version_id"] for item in evidence} == {"version", "other"}


@pytest.mark.parametrize("part,revision", [("无", "A"), ("TBD", "A"), ("零件名称", "A"), ("P-1", "无"), ("P-1", "版本号")])
def test_cp_pair_unknown_keys_are_not_established_by_equal_placeholder_text(part, revision):
    own = [["零件号", part], ["版本", revision], ["零件/工序号", "工序名称/操作描述"], [10, "成型"]]
    other = [["零件号", part], ["版本", revision], ["工序号", "工序名称"], [10, "成型"]]
    findings = run("N03", own, "控制计划", ("N05", other, "过程流程图"))
    assert findings[5]["status"] == "UNABLE"


def test_pfd_blank_physical_row_does_not_count_as_the_next_inspection():
    findings = run("N05", [["工序名称"], ["返工"], [None], ["重检"]], "过程流程图")
    assert findings[7]["status"] == "NG"


def test_cp_product_tolerance_column_is_not_the_product_characteristic_column():
    findings = run("N03", [["特殊特性分类", "产品/工序规范/公差", "产品", "过程"], ["[CC]", "10±1", None, None]], "控制计划")
    assert findings[1]["status"] == "NG", findings[1]


def test_pfd_reads_late_process_after_more_than_forty_blank_rows():
    findings = run("N05", [["工序名称"], ["IQC"], *[[None] for _ in range(45)], ["返工"], ["包装"]], "过程流程图")
    assert findings[7]["status"] == "NG", findings[7]


def test_cp_special_characteristic_requires_a_control_method():
    findings = run("N03", [["特殊特性分类", "控制方法"], ["[CC]", None]], "控制计划")
    assert findings[2]["status"] == "NG", findings[2]


def test_cp_frequency_checks_every_process_row():
    findings = run("N03", [["零件/工序号", "工序名称/操作描述", "频率"], [10, "成型", "每班"], [20, "检验", None]], "控制计划")
    assert findings[3]["status"] == "NG", findings[3]


def test_nio_scl_precheck_separates_missing_columns_and_missing_identity(tmp_path):
    from audit.checkers.v4.nio.special_characteristics import NioSpecialCharacteristicsAgent
    from audit.contracts import AuditInput, RuleInput

    path = tmp_path / "清单.xlsx"
    path.write_bytes(
        workbook(
            [
                ["P1特殊特性清单"],
                ["零件编号", "零件名称", "特性分类", "特性描述", "特性值", "特性类别", "特殊特性种类", "控制方法（制造过程）", "防错方案描述", "频次", "样本数"],
                [None, "支架", "产品", "孔径", "10±1", "SC", "CC", "量规", "定位", "每班", 1],
            ],
            "特殊特性清单",
        )
    )
    agent = NioSpecialCharacteristicsAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="rule", **preset_rule_body("蔚来", "N04")))
    facts = {item["source_id"]: item for item in agent.precheck()}
    assert facts["N04:S1"]["status"] == "satisfied"
    assert facts["N04:S2"]["status"] == "satisfied"
    assert facts["N04:S3"]["status"] == "defect"
    assert facts["N04:S3"]["code"] == "W02"
    assert all(item["certified"] is False for item in facts.values())
    agent.close()


@pytest.mark.parametrize(("part", "kind", "expected"), [("P-1", "CC", "PASS"), ("P-1", "SC", "NG"), ("P-2", "CC", "UNABLE")])
def test_cp_pfmea_special_characteristic_uses_the_complete_step(part, kind, expected):
    own = [["零件号", "P-1"], ["版本", "A"], ["零件/工序号", "工序名称/操作描述", "特殊特性分类"], [10, "成型", "CC"]]
    other = [["零件号", part], ["版本", "A"], ["过程步骤", "特殊特性"], ["10 成型", kind]]
    findings = run("N03", own, "控制计划", ("N02", other, "PFMEA"))
    assert findings[6]["status"] == expected, findings[6]


@pytest.mark.parametrize(("description", "expected"), [("孔径", "PASS"), ("位置", "NG")])
def test_cp_scl_characteristic_includes_category_value_and_kind(description, expected):
    own = [["零件号", "P-1"], ["版本", "A"], ["产品", "过程", "特殊特性分类", "产品/工序规范/公差"], ["孔径", None, "CC", "10±1"]]
    other = [["零件号", "P-1"], ["版本", "A"], ["特性分类", "特性描述", "特性值", "特殊特性种类"], ["产品", description, "10±1", "CC"]]
    findings = run("N03", own, "控制计划", ("N04", other, "特殊特性清单"))
    assert findings[7]["status"] == expected, findings[7]


@pytest.mark.parametrize(("method", "expected"), [("整定和检具并按防错卡、测量卡执行", "PASS"), ("整定和检具", "NG")])
def test_cp_pfmea_all_four_measures_must_be_present(method, expected):
    own = [["零件号", "P-1"], ["版本", "A"], ["工序名称/操作描述", "评价测量技术"], ["成型", method]]
    other = [["零件号", "P-1"], ["版本", "A"], ["过程步骤", "现行预防控制", "现行探测控制", "预防控制", "探测措施"], ["10 成型", "整定", "检具", "防错卡", "测量卡"]]
    findings = run("N03", own, "控制计划", ("N02", other, "PFMEA"))
    assert findings[8]["status"] == expected, findings[8]


@pytest.mark.parametrize(("quantity", "expected"), [(1, "PASS"), (2, "NG")])
def test_cp_scl_method_quantity_frequency_must_all_match(quantity, expected):
    own = [["零件号", "P-1"], ["版本", "A"], ["产品", "过程", "特殊特性分类", "评价测量技术", "数量", "频率"], ["孔径", None, "CC", "量规", quantity, "每班"]]
    other = [["零件号", "P-1"], ["版本", "A"], ["特性分类", "特性描述", "控制方法（制造过程）", "样本数", "频次"], ["产品", "孔径", "量规", 1, "每班"]]
    findings = run("N03", own, "控制计划", ("N04", other, "特殊特性清单"))
    assert findings[9]["status"] == expected, findings[9]


def test_scl_literal_title_can_identify_an_unrenamed_worksheet():
    findings = run("N04", [["P1 特殊特性清单"]], "Sheet1")
    assert findings[0]["status"] == "PASS", findings[0]


def test_pfd_structure_warning_code_distinguishes_a_missing_column_from_an_empty_value(tmp_path):
    from audit.checkers.v4.nio.process_flow import NioProcessFlowAgent
    from audit.contracts import AuditInput, RuleInput

    def facts(rows):
        path = tmp_path / "流程.xlsx"
        path.write_bytes(workbook(rows, "过程流程图"))
        agent = NioProcessFlowAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="rule", **preset_rule_body("蔚来", "N05")))
        try:
            return {item["id"]: item for item in agent.precheck()}
        finally:
            agent.close()

    missing = facts([["产品特性类别"], ["CC"]])["N05:S3"]
    empty = facts([["产品特性类别", "产品特殊特性"], ["CC", None]])["N05:S3"]
    assert (missing["status"], missing["code"]) == ("defect", "B05")
    assert (empty["status"], empty["code"]) == ("defect", "W02")


def test_cp_duplicate_product_columns_are_ambiguous_instead_of_missing():
    finding = run("N03", [["特殊特性分类", "产品", "产品", "过程"], ["[CC]", "孔径", "孔径", None]], "控制计划")[1]
    assert finding["status"] == "UNABLE", finding


def test_cp_uncached_stage_checkbox_is_unknown_instead_of_unselected():
    finding = run("N03", [['="☑ 生产 □ 样件 □ 试生产"']], "控制计划")[0]
    assert finding["status"] == "UNABLE", finding


def test_a_checked_production_word_inside_process_data_is_not_the_plan_stage():
    finding = run("N03", [["零件/工序号", "工序名称/操作描述"], [10, "☑ 生产"]], "控制计划")[0]
    assert finding["status"] == "NG", finding


@pytest.mark.parametrize("prefix", ["New", "Version2", "第2版"])
def test_title_preamble_is_not_automatically_a_product_identifier(prefix):
    finding = run("N04", [[prefix + " 特殊特性清单"]], "特殊特性清单")[0]
    assert finding["status"] == "UNABLE", finding
