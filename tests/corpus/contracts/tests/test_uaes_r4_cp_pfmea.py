"""R4 rules through the candidate loader; independently written fixture cells, no model verdicts."""

import json
from pathlib import Path

import pytest
from agent.execution.audit_runtime import _load_program, _restore_modules
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.standard_suites import _load_leaf
from openpyxl import Workbook


def workbook(path, title, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def result(tmp_path, kind, clause, path, peers=None):
    root = Path(__file__).resolve().parents[4] / "assets/uaes/rulebook/v1.0/candidates/m4"
    leaf = _load_leaf(root, "uaes", root / "uaes" / kind)
    location = tmp_path / f"program-{len(list(tmp_path.glob('program-*')))}"
    modules, saved = _load_program(leaf.program_files, location)

    def source(p):
        return AuditInput(audit_id=p.name, file_url=str(p), file_metadata={"file_version_id": p.name})

    files = {k: [source(p)] for k, p in (peers or {}).items()}
    group = GroupContextInput(by_doc_type=files, by_audit_id={f.audit_id: f for v in files.values() for f in v})
    audit = modules["checker"].create_audit(source(path), RuleInput.of(json.loads(leaf.files["rules.json"]), rule_id=kind), group)
    try:
        return audit.evaluate(clause)
    finally:
        audit.close()
        _restore_modules(saved)


def test_cp_resource_alternatives_are_complete_columns_not_a_union(tmp_path):
    table = [["工序号", "过程名称/操作描述", "机器/装置/夹具/工装", "工装设备", "评价/测量技术"], [10, "成形", "M1", None, "目视"], [20, "切断", None, "M2", "目视"]]
    f = workbook(tmp_path / "sheet.xlsx", "控制计划", table)
    r = result(tmp_path, "U08", "CP:1", f)
    assert r.status == "NG" and "C3" in r.suggestions and "D2" in r.suggestions, r.suggestions
    table[1][3] = "工装T1"
    workbook(f, "控制计划", table)
    assert result(tmp_path, "U08", "CP:1", f).status == "PASS"


def test_resource_cannot_skip_second_numbered_operation_with_same_name(tmp_path):
    f = workbook(tmp_path / "plan.xlsx", "控制计划", [["工序号", "工序名称/操作描述", "生产资源", "评价/测量技术"], [1, "清洗", "清洗线", "目视"], [2, "清洗", None, None]])
    r = result(tmp_path, "U08", "CP:1", f)
    assert r.status == "NG" and "C3" in r.suggestions, r.suggestions


def test_no_change_history_not_confused_with_header_date(tmp_path):
    f = workbook(tmp_path / "control.xlsx", "控制计划", [["零件号", "F010000001"], ["修订日期", "2024-01-01"], ["工序号", "过程名称操作描述", "评价/测量技术"], [1, "清洗", "目视"]])
    r = result(tmp_path, "U08", "CP:6", f)
    assert r.status == "NG" and "第4项不满足" in r.suggestions, r.suggestions


@pytest.mark.parametrize("other", ["F010000002", "F010000001X", "F010000001-02"])
def test_mixed_material_list_only_compares_exact_target(tmp_path, other):
    target = workbook(
        tmp_path / "target.xlsx",
        "PFMEA",
        [["零件号", "F010000001"], ["项目", "X2"], ["项目/功能", "潜在失效模式", "潜在失效原因", "严重度", "频度", "探测度", "CLASS类别"], ["4#轴径", "Φ9±0.2", "夹偏", 5, 3, 3, "SC/1"]],
    )
    rows = [["项目", "X2"], ["序号", "产品名称/零件号", "特殊特性名称", "特性值", "特性类别"], [1, "F010000001组件", "7#轴径", "Φ9±0.2", "1"], [2, other + "组件", "外径", "Φ16±0.1", "SC/2"]]
    source = workbook(tmp_path / "features.xlsx", "特殊特性清单", rows)
    r = result(tmp_path, "U07", "PFMEA:3", target, {"过程特殊特性清单": source})
    assert r.status == "PASS", r.suggestions
    rows[-1][1] = "F010000001组件"
    workbook(source, "特殊特性清单", rows)
    r = result(tmp_path, "U07", "PFMEA:3", target, {"过程特殊特性清单": source})
    assert r.status == "NG" and "外径" in r.suggestions, r.suggestions


def test_locally_tagged_feature_requires_own_class_cell(tmp_path):
    path = workbook(
        tmp_path / "f.xlsx",
        "PFMEA",
        [["项目/功能", "潜在失效模式", "潜在失效原因", "严重度", "频度", "探测度", "CLASS类别"], ["2#深度5", "6±0.1", "磨损", 5, 3, 3, 5], ["8#平面度5", "0.2", "偏置", 5, 3, 3, None]],
    )
    r = result(tmp_path, "U07", "PFMEA:3", path)
    assert r.status == "NG" and "第3行" in r.suggestions and "独立类别栏为空" in r.suggestions, r.suggestions


def test_merged_resource_and_manual_exception(tmp_path):
    from openpyxl import load_workbook

    path = workbook(
        tmp_path / "merged.xlsx",
        "控制计划",
        [["工序号", "工序名称/操作描述", "生产设备", "评价/测量技术"], [10, "清洗", "清洗机", "温控表"], [None, None, None, "记录表"], [20, "手工包装", "/", "目视"]],
    )
    book = load_workbook(path)
    for col in "ABC":
        book.active.merge_cells(f"{col}2:{col}3")
    book.save(path)
    finding = result(tmp_path, "U08", "CP:1", path)
    assert finding.status == "PASS", finding.suggestions


def test_change_history_on_another_sheet_is_not_absent(tmp_path):
    from openpyxl import load_workbook

    path = workbook(tmp_path / "change.xlsx", "控制计划", [["零件号", "F010000001"], ["修订日期", "2025-02-02"], ["工序号", "过程名称操作描述", "评价/测量技术"], [1, "清洗", "目视"]])
    book = load_workbook(path)
    log = book.create_sheet("历史")
    log.append(["版本", "日期", "变更内容"])
    log.append(["B", "2025-02-01", "更换设备"])
    book.save(path)
    finding = result(tmp_path, "U08", "CP:6", path)
    assert finding.status == "NG" and "第4项不满足" not in finding.suggestions, finding.suggestions


def test_same_unknown_mark_does_not_establish_meaning(tmp_path):
    target = workbook(
        tmp_path / "marked.xlsx",
        "PFMEA",
        [["零件号", "F010000001"], ["项目", "X2"], ["项目/功能", "潜在失效模式", "潜在失效原因", "严重度", "频度", "探测度", "CLASS类别"], ["轴径", "Φ9±0.2", "夹偏", 5, 3, 3, "ZQ"]],
    )
    source = workbook(tmp_path / "list.xlsx", "特殊特性清单", [["项目", "X2"], ["序号", "产品名称/零件号", "特殊特性名称", "特性值", "特性类别"], [1, "F010000001", "轴径", "Φ9±0.2", "ZQ"]])
    finding = result(tmp_path, "U07", "PFMEA:3", target, {"过程特殊特性清单": source})
    assert finding.status == "UNABLE" and "图例" in finding.suggestions, finding.suggestions


def cp6_target(tmp_path, change, *, empty_plan_number=False):
    from openpyxl import load_workbook

    target = workbook(
        tmp_path / "cp.xlsx",
        "控制计划",
        [
            ["控制计划编号", None if empty_plan_number else "CP-X"],
            ["供应商名称", "部件厂"],
            ["供应商代码", "SP-29"],
            ["编制人", "作者"],
            ["版本号", "B"],
            ["创建日期", "2025-01-08"],
            ["零件名称", "车轴"],
            ["零件号", "F010000001"],
            ["项目编号", "项目甲"],
            ["工序号", "工序名称/操作描述", "评价/测量技术"],
            [1, "清洗", "目视"],
        ],
    )
    book = load_workbook(target)
    history = book.create_sheet("修订履历")
    history.append(["版本", "日期", "变更内容"])
    history.append(["A", "2025-01-02", "初始发行"])
    history.append(["B", change, "新版本"])
    book.save(target)
    return target


@pytest.mark.parametrize(("change", "expected"), [("2025-01-03", "NG"), ("2025-01-04", "PASS"), ("2025-01-05", "PASS")])
def test_cp6_uses_current_drawings_review_date_and_real_change_history(tmp_path, change, expected):
    target = cp6_target(tmp_path, change)
    drawing = workbook(
        tmp_path / "drawing.xlsx", "图纸", [["项目编号", "项目甲"], ["零件号", "F010000001"], ["零件名称", "车轴"], ["版本号", "R1"], ["制图日期", "2025-01-02"], ["审核日期", "2025-01-04"]]
    )
    finding = result(tmp_path, "U08", "CP:6", target, {"图纸": drawing})
    assert finding.status == expected and "2025-01-04" in finding.suggestions and "B3" in finding.suggestions, finding.suggestions


def test_cp6_rejects_an_incomplete_revision_row_and_ignores_other_project(tmp_path):
    target = cp6_target(tmp_path, None)
    drawing = workbook(tmp_path / "drawing.xlsx", "图纸", [["项目编号", "项目乙"], ["零件号", "F010000001"], ["零件名称", "车轴"], ["审核日期", "2025-01-02"]])
    finding = result(tmp_path, "U08", "CP:6", target, {"图纸": drawing})
    assert finding.status == "NG" and "第3项不满足" in finding.suggestions and "未能完整读取" in finding.suggestions, finding.suggestions


def test_cp6_does_not_take_value_from_unrelated_field(tmp_path):
    target = cp6_target(tmp_path, "2025-01-05", empty_plan_number=True)
    drawing = workbook(tmp_path / "draw.xlsx", "图纸", [["项目编号", "项目甲"], ["零件号", "F010000001"], ["零件名称", "车轴"], ["审核日期", "2025-01-02"]])
    finding = result(tmp_path, "U08", "CP:6", target, {"图纸": drawing})
    assert finding.status == "NG" and "控制计划编号" in finding.suggestions and "（1项，重要）" in finding.suggestions, finding.suggestions


@pytest.mark.parametrize(
    ("operation", "spec", "code", "expected"),
    [
        ("装配", "不得反装", "1", "PASS"),
        ("冲压", "不得反装", "1", "NG"),
        ("装配", "防错装", "1", "NG"),
        ("装配", "不得反装", "SC/2", "NG"),
    ],
)
def test_qualitative_process_characteristic_requires_all_four_keys(tmp_path, operation, spec, code, expected):
    target = workbook(
        tmp_path / "target.xlsx",
        "PFMEA",
        [
            ["零件号", "F010000001"],
            ["项目", "X2"],
            ["工序名称", "项目/功能", "要求", "潜在失效模式", "潜在失效原因", "严重度", "频度", "探测度", "CLASS类别"],
            ["装配", "装配方向", "不得反装", "反装", "夹偏", 5, 3, 3, "SC/1"],
        ],
    )
    source = workbook(
        tmp_path / "features.xlsx",
        "特殊特性清单",
        [
            ["项目", "X2"],
            ["序号", "产品名称/零件号", "工序名称", "过程特殊特性", "过程特性值", "过程特性符号"],
            [1, "F010000001", operation, "装配方向", spec, code],
        ],
    )
    finding = result(tmp_path, "U07", "PFMEA:3", target, {"过程特殊特性清单": source})
    assert finding.status == expected, finding.suggestions


def test_original_pfmea_omission_is_located_without_image_model(tmp_path):
    original = next((Path(__file__).resolve().parents[4] / "assets/uaes/samples").rglob("专用PFMEA-A40.xlsx"))
    finding = result(tmp_path, "U07", "PFMEA:3", original)
    assert finding.status == "NG" and "第355行" in finding.suggestions and "已知特殊特性漏标" in finding.suggestions, finding.suggestions
    assert any(json.loads(source.locator).get("cell") == "F355" for source in finding.evidence.sources)


def test_original_cp_resource_check_names_actual_operation(tmp_path):
    original = next((Path(__file__).resolve().parents[4] / "assets/uaes/samples").rglob("控制计划-776.xlsx"))
    finding = result(tmp_path, "U08", "CP:1", original)
    assert finding.status == "NG" and "D71" in finding.suggestions and "激光打标" in finding.suggestions, finding.suggestions
    assert any(json.loads(source.locator).get("cell") == "D71" for source in finding.evidence.sources)


def cp_drawing_fixture(tmp_path, *, freq, rows, project="项目甲", complete=True):
    cp = workbook(
        tmp_path / "cp-drawing.xlsx",
        "控制计划",
        [
            ["零件号", "F010000001"],
            ["项目号", "项目甲"],
            ["工序号", "工序名称/操作描述", "评价/测量技术", "产品特性", "过程特性", "产品/过程规格/公差", "频次", "控制方法"],
            [1, "车削", "卡尺", "外径", None, "Φ10±0.1", freq, "SPC"],
            *rows,
        ],
    )
    drawing = workbook(
        tmp_path / "drawing-features.xlsx",
        "图纸全部特殊特性" if complete else "图纸特性摘录",
        [
            ["零件号", "F010000001"],
            ["项目号", project],
            ["特性名称", "图纸规格", "CPK检验要求"],
            ["外径", "Φ10±0.10", "Cpk≥1.33"],
            ["安装方向", "箭头朝上", "否"],
        ],
    )
    return cp, {"图纸": drawing}


@pytest.mark.parametrize(("freq", "expected"), [("每两小时绘制 X-R 图", "PASS"), ("每两小时", "NG"), ("无需SPC", "NG")])
def test_cp11_checks_only_spc_in_the_corresponding_frequency_column(tmp_path, freq, expected):
    path, peers = cp_drawing_fixture(tmp_path, freq=freq, rows=[])
    r = result(tmp_path, "U08", "CP:11", path, peers)
    assert r.status == expected and "频率/频次" in r.suggestions, r.suggestions
    assert r.checks_performed is not None and r.checks_performed > 0
    assert "安装方向" not in r.suggestions
    assert any(json.loads(source.locator).get("cell") == "C4" for source in r.evidence.sources)


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([[2, "装配", "目视", None, "安装方向", "箭头朝上", "每次"]], "PASS"),
        ([[2, "装配", "目视", "安装方向", None, "无倒角"], [3, "终检", "目视", "外观", None, "箭头朝上"]], "NG"),
    ],
)
def test_cp12_checks_qualitative_drawing_feature_in_one_row(tmp_path, rows, expected):
    path, peers = cp_drawing_fixture(tmp_path, freq="每次", rows=rows)
    r = result(tmp_path, "U08", "CP:12", path, peers)
    assert r.status == expected, r.suggestions
    assert any(json.loads(source.locator).get("cell") == "B5" for source in r.evidence.sources)


@pytest.mark.parametrize("rule", ["CP:11", "CP:12"])
def test_cp_drawing_requires_same_project_and_complete_baseline(tmp_path, rule):
    path, peers = cp_drawing_fixture(tmp_path, freq="X-R图", rows=[], project="项目乙")
    r = result(tmp_path, "U08", rule, path, peers)
    assert r.status == "NG" and "未提交项目" in r.suggestions, r.suggestions
    path, peers = cp_drawing_fixture(tmp_path, freq="X-R图", rows=[], complete=False)
    r = result(tmp_path, "U08", rule, path, peers)
    assert r.status == "UNABLE" and "完整" in r.suggestions, r.suggestions


def test_cp12_uses_printed_inventory_title_and_literal_part_identity(tmp_path):
    from openpyxl import load_workbook

    target, peers = cp_drawing_fixture(tmp_path, freq="每次", rows=[[2, "装配", "目视", "安装方向", None, "箭头朝上", "每次"]])
    plan = load_workbook(target)
    plan.active["B1"] = "Ax/１７-b"
    plan.save(target)
    source = peers["图纸"]
    doc = load_workbook(source)
    doc.active.title = "清单页"
    doc.active.insert_rows(1)
    doc.active["A1"] = "图纸全部特殊特性"
    doc.active["A2"] = "物料编号：Ax/１７-b"
    doc.active["B2"] = None
    doc.save(source)
    doc.active["A2"] = "物料编号：AX/17-b"
    doc.save(tmp_path / "another.xlsx")

    finding = result(tmp_path, "U08", "CP:12", target, {**peers, "工程图纸": tmp_path / "another.xlsx"})

    assert finding.status == "PASS" and "2项特殊特性" in finding.suggestions, finding.suggestions
