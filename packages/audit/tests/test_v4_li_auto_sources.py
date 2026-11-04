"""Independent native-workbook cases for Li Auto's literal source decisions."""

import pytest
from audit.checkers.v4.li_auto import EXCEL_AGENTS
from audit.checkers.v4.li_auto.bindings import BINDINGS
from audit.contracts import AuditInput, RuleInput
from audit.engine.checkers.rule_declarations import discover_declared_rules
from audit.presets import customer_presets, preset_rule_body
from openpyxl import Workbook


def execute(tmp_path, entry, number, rows, title=None, *, sheets=None):
    book = Workbook()
    book.active.title = title or "报告"
    for row in rows:
        book.active.append(row)
    for name, contents in (sheets or {}).items():
        ws = book.create_sheet(name)
        for row in contents:
            ws.append(row)
    path = tmp_path / "原件.xlsx"
    book.save(path)
    book.close()
    cls = EXCEL_AGENTS[entry]
    agent = cls(AuditInput(audit_id="version-1", file_url=str(path)), RuleInput(rule_id="rule", **(preset_rule_body("理想", entry) | {"rules": []})))
    try:
        return agent._execute_rule_check(number)
    finally:
        agent.close()


def test_each_original_clause_has_an_explicit_existing_method():
    for preset in customer_presets():
        if preset.customer != "理想":
            continue
        cls = EXCEL_AGENTS[preset.entry_id]
        declarations = dict(discover_declared_rules(cls))
        assert len(declarations) == len(preset.clauses)
        for clause in preset.clauses:
            assert BINDINGS["v4"][clause.id] in declarations
        for source_id, method in getattr(cls, "PRECHECK_METHODS", {}).items():
            assert source_id in {clause.id for clause in preset.clauses}
            assert BINDINGS["v4"][source_id] == method


@pytest.mark.parametrize(
    ("value", "expected"), [("符合", "PASS"), ("通过", "PASS"), ("合格", "PASS"), ("P", "PASS"), ("pass", "PASS"), ("PASS", "NG"), ("测试通过", "NG"), ("Pass", "NG"), ("基本合格", "NG")]
)
def test_report_verdict_uses_exact_source_words(tmp_path, value, expected):
    result = execute(tmp_path, "L03", 2, [["试验结论", value]])
    assert result.status == expected
    assert result.evidence and result.evidence.sources


@pytest.mark.parametrize(("value", "expected"), [("DV", "PASS"), ("pv", "PASS"), ("PV试验", "NG"), ("量产", "NG"), (None, "NG")])
def test_report_type_is_dv_or_pv_only(tmp_path, value, expected):
    assert execute(tmp_path, "L03", 3, [["试验类型", value]]).status == expected


@pytest.mark.parametrize(("field", "number"), [("文件编号", 1), ("样品数量", 6)])
def test_untamplated_report_required_field(tmp_path, field, number):
    assert execute(tmp_path, "L03", number, [[field, "X-1"]]).status == "PASS"
    assert execute(tmp_path, "L03", number, [[field, None]]).status == "NG"


def test_report_conflicting_values_and_visual_placeholders_are_unknown(tmp_path):
    assert execute(tmp_path, "L03", 2, [["结论", "通过"], ["结论", "不通过"]]).status == "UNABLE"
    assert execute(tmp_path, "L03", 4, [["印章", "供应商公司"]]).status == "UNABLE"
    assert execute(tmp_path, "L03", 5, [["试验前", "图片"], ["试验中", "图片"], ["试验后", "图片"]]).status == "UNABLE"
    assert execute(tmp_path, "L03", 2, [["结论", "=A2"]]).status == "UNABLE"


@pytest.mark.parametrize(("value", "expected"), [("1.34", "PASS"), ("1.33", "NG"), ("NA", "PASS"), ("NaN", "UNABLE"), ("Infinity", "UNABLE"), ("=1+1", "UNABLE")])
def test_cpk_must_be_finite_and_strictly_above_threshold(tmp_path, value, expected):
    assert execute(tmp_path, "L06", 1, [["工序流程编号", "P阶段Cpk结果"], ["10", value]], "CCSC").status == expected


@pytest.mark.parametrize(("mark", "expected"), [("关键特殊特性", "PASS"), ("重要特殊特性", "PASS"), ("NA", "PASS"), ("[G]", "PASS"), ("[Z]", "PASS"), ("G", "NG"), ("[X]", "NG")])
def test_cp_literal_special_categories(tmp_path, mark, expected):
    rows = [["工序名称", "特殊特性分类"], ["压装", mark]]
    assert execute(tmp_path, "L08", 2, rows, "控制计划").status == expected


@pytest.mark.parametrize(("status", "expected"), [("Y", "PASS"), ("y", "NG"), (None, "NG")])
def test_outsourced_ppap_is_literal_uppercase_y(tmp_path, status, expected):
    rows = [["零件号", "自制", "外协-国产", "外协-进口", "领用件", "完成PPAP"], ["P123", None, "X", None, None, status]]
    assert execute(tmp_path, "L04", 4, rows, "清单").status == expected


@pytest.mark.parametrize(("statuses", "expected"), [(["X", None, None, None], "PASS"), (["X", "X", None, None], "NG"), ([None, None, None, None], "NG")])
def test_material_business_row_has_exactly_one_status(tmp_path, statuses, expected):
    rows = [["零件号", "自制", "外协-国产", "外协-进口", "领用件"], ["P123", *statuses]]
    assert execute(tmp_path, "L04", 3, rows, "清单").status == expected


@pytest.mark.parametrize(("value", "expected"), [(0.9, "PASS"), (0.8, "NG"), ("NaN", "UNABLE"), ("=9/10", "UNABLE")])
def test_plant_yield_uses_finite_arithmetic(tmp_path, value, expected):
    rows = [["线体/工序类别", "良率", "合格品数", "产出数量"], ["线体1", value, 9, 10]]
    assert execute(tmp_path, "L14", 4, rows, "产能评估表").status == expected


def test_empty_source_scope_does_not_pass(tmp_path):
    for entry, index, title, headers in [
        ("L06", 1, "CCSC", ["工序流程编号", "P阶段Cpk结果"]),
        ("L08", 2, "控制计划", ["工序名称", "特殊特性分类"]),
        ("L14", 4, "产能评估表", ["线体/工序类别", "良率", "合格品数", "产出数量"]),
    ]:
        assert execute(tmp_path, entry, index, [headers], title).status == "UNABLE"


@pytest.mark.parametrize(("kind", "state", "expected"), [("PV", "硬模件", "PASS"), ("pv", "硬模件", "PASS"), ("DV", "硬模件", "NG"), ("PV", "软模件", "NG")])
def test_validation_samples_must_be_pv_and_hard_tool(tmp_path, kind, state, expected):
    rows = [["序号", "试验项目", "试验类型", "状态"], [1, "耐久", kind, state]]
    assert execute(tmp_path, "L02", 3, rows, "DVP&R").status == expected


@pytest.mark.parametrize(
    ("first", "last", "expected"),
    [("2026-01-01", "2026-01-02", "PASS"), ("2026-01-02", "2026-01-02", "NG"), ("2026-01-03", "2026-01-02", "NG"), ("未定", "2026-01-02", "UNABLE"), (None, "2026-01-02", "NG")],
)
def test_validation_plan_dates_are_ordered_or_unknown(tmp_path, first, last, expected):
    rows = [["序号", "试验项目", "计划开始", "计划结束"], [1, "耐久", first, last]]
    assert execute(tmp_path, "L02", 4, rows, "DVP&R").status == expected


@pytest.mark.parametrize(("quantity", "expected"), [("3件", "PASS"), ("3", "PASS"), ("三件", "NG"), (None, "NG")])
def test_annual_sample_quantity_is_checked_for_each_test(tmp_path, quantity, expected):
    rows = [["试验项目", "试验要求", "试验标准/方法", "样本数量"], ["耐久", "满足规范", "GB1", quantity]]
    assert execute(tmp_path, "L16", 4, rows, "表格").status == expected


@pytest.mark.parametrize(("filled", "expected"), [(True, "PASS"), (False, "NG")])
def test_msa_all_declared_nonremark_business_columns_are_required(tmp_path, filled, expected):
    rows = [["序号", "新增业务列", "备注"], [1, "已填" if filled else None, None]]
    assert execute(tmp_path, "L05", 2, rows, "测量系统分析计划表").status == expected


@pytest.mark.parametrize(("description", "expected"), [("压装至定位", "PASS"), (None, "NG")])
def test_flow_named_operation_requires_description(tmp_path, description, expected):
    rows = [["工序名称", "操作描述"], ["压装", description]]
    assert execute(tmp_path, "L11", 4, rows, "过程流程图").status == expected


@pytest.mark.parametrize(("mark", "description", "expected"), [("【G】", "孔径", "PASS"), ("【Z】", None, "NG")])
def test_flow_first_category_triggers_product_characteristic(tmp_path, mark, description, expected):
    rows = [["工序名称", "操作描述", "产品特殊特性", "特性类别", "过程特殊特性", "特性类别"], ["压装", "压入", description, mark, "温度", None]]
    assert execute(tmp_path, "L11", 2, rows, "过程流程图").status == expected


@pytest.mark.parametrize(("attachment", "expected"), [("报告.pdf", "PASS"), (None, "NG")])
def test_psw_delivery_list_excludes_only_certification_row(tmp_path, attachment, expected):
    rows = [["项目", "附件"], ["尺寸报告", attachment], ["零件认证相关", None]]
    assert execute(tmp_path, "L17", 1, rows, "生产件批准交付物文件清单").status == expected


def test_equipment_precheck_reads_identity_before_device_supplier(tmp_path):
    rows = [
        ["车型", "X1", "零件名称", "支架", "零件号", "P123", "供应商", "零件供应商"],
        ["产线/工序", "设备开发", "设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间", "设备验收", "验收结果", "整改措施", "整改计划"],
        ["10 压装", "开发", "压装机", "设备供应商", "M1", 0, 1, "专用", "2026-01-01", "验收", "通过", None, None],
    ]
    book = Workbook()
    ws = book.active
    ws.title = "设备开发验收清单"
    for row in rows:
        ws.append(row)
    path = tmp_path / "设备清单.xlsx"
    book.save(path)
    book.close()
    cls = EXCEL_AGENTS["L13"]
    agent = cls(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="r", **(preset_rule_body("理想", "L13") | {"rules": []})))
    try:
        facts = {fact["id"]: fact for fact in agent.precheck()}
        assert len(facts) == 10
        assert {key: row["message"] for key, row in facts.items() if row["status"] != "satisfied"} == {}
        assert all(not row["certified"] for row in facts.values())
    finally:
        agent.close()
    # The equipment supplier cannot satisfy the missing part supplier.
    rows[0][-1] = None
    book = Workbook()
    ws = book.active
    ws.title = "设备开发验收清单"
    for row in rows:
        ws.append(row)
    book.save(path)
    book.close()
    agent = cls(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="r", **(preset_rule_body("理想", "L13") | {"rules": []})))
    try:
        facts = {fact["id"]: fact for fact in agent.precheck()}
        assert facts["L13:S10"]["status"] == "defect"
        assert facts["L13:S3"]["status"] == "satisfied"
    finally:
        agent.close()
