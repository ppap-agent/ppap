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


def test_decodable_positioned_images_cannot_certify_trial_photo_content(tmp_path):
    from io import BytesIO

    from openpyxl.drawing.image import Image
    from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
    from PIL import Image as PillowImage

    book = Workbook()
    sheet = book.active
    sheet.title = "试验报告"
    for row, label in [(1, "试验前"), (10, "试验中"), (20, "试验后")]:
        sheet.cell(row, 1, label)
        pixels = BytesIO()
        PillowImage.new("RGB", (10, 10), "white").save(pixels, format="PNG")
        pixels.seek(0)
        image = Image(pixels)
        image.anchor = TwoCellAnchor(_from=AnchorMarker(col=0, row=row), to=AnchorMarker(col=2, row=row + 3))
        sheet.add_image(image)
    path = tmp_path / "试验图片.xlsx"
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS["L03"](AuditInput(audit_id="image-version", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L03")))
    try:
        assert agent._execute_rule_check(5).status == "UNABLE"
    finally:
        agent.close()


def test_chart_object_cannot_replace_test_phase_photos(tmp_path):
    from openpyxl.chart import BarChart, Reference

    book = Workbook()
    sheet = book.active
    sheet.append(["试验前", "试验中", "试验后"])
    sheet.append([1, 2, 3])
    chart = BarChart()
    chart.add_data(Reference(sheet, min_row=2, max_row=2, min_col=1, max_col=3))
    sheet.add_chart(chart, "A2")
    path = tmp_path / "图表.xlsx"
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS["L03"](AuditInput(audit_id="chart-version", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L03")))
    try:
        assert agent._execute_rule_check(5).status == "UNABLE"
    finally:
        agent.close()


@pytest.mark.parametrize(
    ("rule_number", "standard", "values", "method", "expected"),
    [
        (3, "10±0.5", (9.5, 10.5), "卡尺", "PASS"),
        (3, "10±0.5", (9.49, 10), "卡尺", "NG"),
        (3, "未给定", (10, 10.1), "卡尺", "UNABLE"),
        (5, "10±0.5", (10, 10), "卡尺", "NG"),
        (5, "10±0.5", (10, 10.1), "卡尺", "PASS"),
        (7, "未给定", ("OK", "OK"), "通止规", "PASS"),
        (7, "未给定", ("OK", "OK"), "卡尺", "NG"),
    ],
)
def test_inspection_source_uses_dimension_regions_not_nio_coordinates(tmp_path, rule_number, standard, values, method, expected):
    rows = [["序号", "检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", None, "判定结果"], ["尺寸"], [1, "直径", standard, method, *values, "OK"]]
    assert execute(tmp_path, "L01", rule_number, rows, "尺寸检验报告").status == expected


def test_equipment_required_formula_without_cache_is_unknown_not_defect(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "设备开发验收清单"
    sheet.append(["产线/工序", "设备开发", "设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间", "设备验收", "验收结果", "整改措施", "整改计划"])
    sheet.append(["10 压装", "开发", "压装机", '= "供应商"', "M1", 0, 1, "专用", "2026-01-01", "验收", "通过", None, None])
    path = tmp_path / "公式设备.xlsx"
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS["L13"](AuditInput(audit_id="formula-version", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L13")))
    try:
        facts = {row["id"]: row for row in agent.precheck()}
        assert facts["L13:S3"]["status"] == "satisfied"
        assert facts["L13:S4"]["status"] == "unknown"
        assert facts["L13:S4"]["code"] is None
    finally:
        agent.close()


@pytest.mark.parametrize(
    ("state", "expected"), [("无（ ）/合格（√）/不合格（ ）", "PASS"), ("无（ ）/合格（ ）/不合格（√）", "NG"), ("无（ ）/完成（√）/未完成（ ）", "PASS"), ("无（ ）/完成（ ）/未完成（√）", "NG")]
)
def test_fixture_negative_condition_follows_the_selected_option(tmp_path, state, expected):
    rows = [["所附文件", "状态"], ["三坐标报告", state], ["结论", None]]
    assert execute(tmp_path, "L09", 3, rows, "检具认可报告").status == expected


@pytest.mark.parametrize(
    ("number", "second", "group"),
    [
        (3, [None, "孔径", "10±0.5", "卡尺", 50, 60, "OK"], "尺寸"),
        (3, [2, None, "10±0.5", "卡尺", 50, 60, "OK"], "尺寸"),
        (4, [None, "孔径", "10±0.5", "卡尺/千分尺", 10, 10.1, "OK"], "尺寸"),
        (5, [None, "孔径", "10±0.5", "卡尺", 10, 10, "OK"], "尺寸"),
        (6, [None, "外观", "无划伤", "目视", "PASS", "PASS", "OK"], "非尺寸"),
        (7, [None, "孔径", "未给定", "卡尺", "OK", "OK", "OK"], "尺寸"),
    ],
)
def test_inspection_populated_row_cannot_disappear_with_missing_serial_or_project(tmp_path, number, second, group):
    first = [1, "直径", "10±0.5", "卡尺", 10, 10.1, "OK"] if group == "尺寸" else [1, "外观", "无划伤", "目视", "OK", "OK", "OK"]
    rows = [["序号", "检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", None, "判定结果"], [group], first, second]
    assert execute(tmp_path, "L01", number, rows, "尺寸检验报告").status == "NG"


@pytest.mark.parametrize("number", [3, 5])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "未识别数值"])
def test_unknown_measurements_cannot_pass_tolerance_or_duplicates(tmp_path, number, value):
    rows = [["序号", "检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", None, "判定结果"], ["尺寸"], [1, "直径", "10±0.5", "卡尺", 10, value, "OK"]]
    assert execute(tmp_path, "L01", number, rows, "尺寸检验报告").status == "UNABLE"


def test_unlabelled_later_measurement_cell_is_not_an_empty_row(tmp_path):
    rows = [["序号", "检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", None, "判定结果"], ["尺寸"], [1, "直径", "10±0.5", "卡尺", 10, 10.1, "OK"], [None, None, None, None, None, 50, None]]
    assert execute(tmp_path, "L01", 3, rows, "尺寸检验报告").status == "UNABLE"


def test_merged_dimension_group_label_is_not_a_measurement_row(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "尺寸检验报告"
    sheet.append(["序号", "检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", None, "判定结果"])
    sheet.append(["尺寸"])
    sheet.merge_cells("A2:G2")
    sheet.append([1, "直径", "10±0.5", "卡尺", 10, 10.1, "OK"])
    path = tmp_path / "合并分区.xlsx"
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS["L01"](AuditInput(audit_id="v", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L01")))
    try:
        assert agent._execute_rule_check(3).status == "PASS"
    finally:
        agent.close()


def test_material_status_row_does_not_disappear_when_part_number_is_blank(tmp_path):
    rows = [["零件号", "自制", "外协-国产", "外协-进口", "领用件"], ["P123", "X", None, None, None], [None, "X", "X", None, None]]
    assert execute(tmp_path, "L04", 3, rows, "清单").status == "NG"


@pytest.mark.parametrize(
    ("number", "second"),
    [(2, [None, "[X]", "孔径", None, "卡尺", "每批"]), (3, [None, "[G]", None, None, "卡尺", "每批"]), (4, [None, "[G]", "孔径", None, None, "每批"]), (5, [None, "NA", "孔径", None, "卡尺", None])],
)
def test_control_plan_classification_rows_do_not_require_a_process_label(tmp_path, number, second):
    book = Workbook()
    sheet = book.active
    sheet.title = "控制计划"
    sheet.cell(1, 3, "特性")
    sheet.merge_cells("C1:D1")
    sheet.append(["工序名称", "特殊特性分类", "产品", "过程", "控制方法", "频率"])
    sheet.append(["压装", "[G]", "孔径", None, "卡尺", "每批"])
    sheet.append(second)
    path = tmp_path / "无工序标签.xlsx"
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS["L08"](AuditInput(audit_id="v", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L08")))
    try:
        assert agent._execute_rule_check(number).status == "NG"
    finally:
        agent.close()


@pytest.mark.parametrize("number", [2, 3])
def test_flow_category_trigger_does_not_require_a_process_name(tmp_path, number):
    rows = [["工序名称", "操作描述", "产品特殊特性", "特性类别", "过程特殊特性", "特性类别"], ["压装", "压入", "孔径", "【G】", "温度", "【Z】"], [None, "压入", None, "【G】", None, "【Z】"]]
    assert execute(tmp_path, "L11", number, rows, "过程流程图").status == "NG"


@pytest.mark.parametrize(
    ("number", "headers", "first", "second", "expected"),
    [
        (4, ["线体/工序类别", "良率", "合格品数", "产出数量"], ["线体1", 0.9, 9, 10], ["线体2", None, 9, 10], "UNABLE"),
        (5, ["线体/工序类别", "单线体/工序总产出JPH", "产出数量", "净利用时间（h）", "参与测试线体数量"], ["线体1", 5, 10, 2, 1], ["线体2", 5, 10, 2, None], "UNABLE"),
        (
            6,
            ["线体/工序类别", "线体/工序预估月度产能", "单线体/工序总产出JPH", "现有线体数", "班次/天", "每班净利用时间（h）", "每月标准工作天数", "累积良率"],
            ["线体1", 100, 1, 1, 1, 1, 100, 1],
            ["线体2", None, 1, 1, 1, 1, 100, 1],
            "UNABLE",
        ),
    ],
)
def test_capacity_calculation_does_not_drop_a_row_with_one_missing_operand(tmp_path, number, headers, first, second, expected):
    assert execute(tmp_path, "L14", number, [headers, first, second], "产能评估表").status == expected


def test_capacity_text_percentages_are_ratios_not_unknown_strings(tmp_path):
    rows = [["线体/工序类别", "良率", "合格品数", "产出数量"], ["线体1", "90%", 9, 10]]
    assert execute(tmp_path, "L14", 4, rows, "产能评估表").status == "PASS"


def test_control_plan_unique_flat_characteristic_fields_are_semantically_sufficient(tmp_path):
    rows = [["工序名称", "特殊特性分类", "产品", "过程"], ["压装", "[G]", "孔径", None]]
    assert execute(tmp_path, "L08", 3, rows, "控制计划").status == "PASS"


def test_flow_data_text_is_not_an_extra_category_header(tmp_path):
    rows = [["工序名称", "操作描述", "产品特殊特性", "特性类别", "过程特殊特性", "特性类别"], ["压装", "压入", "特性类别", "【G】", "温度", "【Z】"]]
    assert execute(tmp_path, "L11", 2, rows, "过程流程图").status == "PASS"


def test_control_plan_competing_same_named_characteristic_fields_are_unknown(tmp_path):
    rows = [["工序名称", "特殊特性分类", "产品", "产品", "过程"], ["压装", "[G]", "孔径", "另一个特性", None]]
    assert execute(tmp_path, "L08", 3, rows, "控制计划").status == "UNABLE"


def test_control_plan_complete_semantic_characteristic_paths_are_supported(tmp_path):
    rows = [["工序名称", "特殊特性分类", "产品特性", "过程特性"], ["压装", "[G]", "孔径", None]]
    assert execute(tmp_path, "L08", 3, rows, "控制计划").status == "PASS"


@pytest.mark.parametrize(("number", "changed_column", "value"), [(2, 0, None), (3, 8, "软模件"), (4, 11, "2025-01-01"), (5, 13, "2025-01-01"), (6, 14, "PASS"), (7, 15, None)])
def test_validation_business_rows_do_not_require_a_serial_number(tmp_path, number, changed_column, value):
    headers = [
        "序号",
        "试验项目",
        "试验要求",
        "试验标准/方法",
        "试验类型",
        "完成节点",
        "试验地点",
        "数量",
        "状态",
        "外观属性",
        "计划开始",
        "计划结束",
        "试验实际开始日期",
        "试验实际结束日期",
        "实际试验结果",
        "试验报告编号",
    ]
    first = [1, "耐久", "无开裂", "GB/T 1234", "PV", "PPAP", "实验室", 3, "硬模件", "内饰", "2026-01-01", "2026-02-01", "2026-01-01", "2026-02-01", "合格", "R001"]
    second = list(first)
    second[0] = None
    second[1] = "高低温"
    second[changed_column] = value
    assert execute(tmp_path, "L02", number, [headers, first, second], "DVP&R").status == "NG"


@pytest.mark.parametrize("number", [2, 3, 4, 5, 6, 7])
def test_validation_whole_row_merged_section_is_the_explicit_source_exception(tmp_path, number):
    book = Workbook()
    sheet = book.active
    sheet.title = "DVP&R"
    sheet.append(
        [
            "序号",
            "试验项目",
            "试验要求",
            "试验标准/方法",
            "试验类型",
            "完成节点",
            "试验地点",
            "数量",
            "状态",
            "外观属性",
            "计划开始",
            "计划结束",
            "试验实际开始日期",
            "试验实际结束日期",
            "实际试验结果",
            "试验报告编号",
        ]
    )
    sheet.append([1, "耐久", "无开裂", "GB/T 1234", "PV", "PPAP", "实验室", 3, "硬模件", "内饰", "2026-01-01", "2026-02-01", "2026-01-01", "2026-02-01", "合格", "R001"])
    sheet.cell(3, 1, "环境试验组")
    sheet.merge_cells("A3:P3")
    path = tmp_path / "分组试验.xlsx"
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS["L02"](AuditInput(audit_id="v", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L02")))
    try:
        assert agent._execute_rule_check(number).status == "PASS"
    finally:
        agent.close()


@pytest.mark.parametrize(
    ("number", "header", "values", "expected"),
    [
        (3, "模具设计尺寸", ["1×2×3", "4×5×6"], "PASS"),
        (3, "模具设计尺寸", ["1×2×3", "4x5x6"], "NG"),
        (3, "模具设计尺寸", ["1×2×3", "=1+1"], "UNABLE"),
        (6, "实物确认", ["有", "无"], "PASS"),
        (6, "实物确认", ["有", "未知"], "NG"),
    ],
)
def test_tooling_single_source_column_does_not_require_a_process_identifier(tmp_path, number, header, values, expected):
    rows = [[header], *[[value] for value in values]]
    assert execute(tmp_path, "L10", number, rows, "Sheet1").status == expected


@pytest.mark.parametrize(("number", "column", "value"), [(3, 4, "4x5x6"), (6, 5, "未知")])
def test_tooling_missing_process_does_not_hide_populated_source_columns(tmp_path, number, column, value):
    headers = ["工艺类型", "一级分总成零件名称", "模具制造供应商", "模具存放地点", "模具设计尺寸", "实物确认"]
    first = ["注塑", "罩壳", "供应商", "上海", "1×2×3", "有"]
    second = [None, "罩壳", "供应商", "上海", "1×2×3", "有"]
    second[column] = value
    assert execute(tmp_path, "L10", number, [headers, first, second], "模具验收报告").status == "NG"


@pytest.mark.parametrize("number", [3, 5])
def test_psw_uninterpreted_stamp_signal_cannot_certify_company_text_or_signature(tmp_path, number):
    book = Workbook()
    sheet = book.active
    sheet.title = "零件提交保证书"
    sheet.append(["零件名称", "支架"])
    path = tmp_path / "印章信号.xlsx"
    book.save(path)
    book.close()
    agent = EXCEL_AGENTS["L17"](AuditInput(audit_id="v", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L17")))
    agent._collect_stamp_signals = lambda *args: {"strong_seal": True, "strong_signature": True}
    try:
        assert agent._execute_rule_check(number).status == "UNABLE"
    finally:
        agent.close()
