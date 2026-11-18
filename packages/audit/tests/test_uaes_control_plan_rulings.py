"""U08:2 and U08:6 follow the customer's 2026-11-13 rulings on the control plan."""

import pytest
from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook

HEADER = ["工序号", "工序名称/操作描述", "设备/工装", "产品特性", "过程特性", "特殊特性", "产品/过程规格/公差", "评价/测量技术", "频率", "控制方法"]


def _check(tmp_path, rows, rule):
    book = Workbook()
    sheet = book.active
    sheet.title = "CP"
    sheet.append(HEADER)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "cp.xlsx"
    book.save(path)
    body = preset_rule_body("UAES", "U08")
    agent = UaesControlPlanAgent(AuditInput(audit_id="cp", file_url=str(path)), RuleInput(rule_id="u08", **{key: value for key, value in body.items() if key != "kind"}))
    names = [name for name, _ in agent.declared_rules()]
    return agent.check(names.index(rule))


def _traced(incoming="ALSI12FE，炉号+批次号", storage="按批号存放，先进先出", shipping="按批次号核对出货"):
    return [
        ["10", "进料检验", "光谱仪", "材料成分", None, None, incoming, "光谱仪", "每批", "来料检验记录"],
        ["90", "机加工", "加工中心", None, "2#孔径", "3", "Φ8.12±0.0125", "气动量仪", "1次/4H", "SPC控制"],
        ["140", "入库", "货架", "存放", None, None, storage, "目视", "每批", "点检表"],
        ["160", "发货", "叉车", "运输", None, None, shipping, "目视", "每批", "出货记录"],
    ]


def test_traceability_and_fifo_written_in_the_specification_column_pass(tmp_path):
    result = _check(tmp_path, _traced(), "rule_2")
    assert result.status == "PASS"


def test_traceability_written_only_in_another_column_does_not_count(tmp_path):
    rows = _traced(incoming="ALSI12FE")
    rows[0][9] = "按批次号追溯"
    result = _check(tmp_path, rows, "rule_2")
    assert result.status == "NG"
    assert "进料环节" in result.suggestions and "出货环节" not in result.suggestions


def test_a_storage_age_limit_is_not_fifo(tmp_path):
    result = _check(tmp_path, _traced(storage="按批号存放，库龄不超过30天"), "rule_2")
    assert result.status == "NG"
    assert "存放时限" in result.suggestions


def test_every_stage_must_exist(tmp_path):
    result = _check(tmp_path, _traced()[:3], "rule_2")
    assert result.status == "NG"
    assert "没有识别出出货环节" in result.suggestions


@pytest.mark.parametrize(("method", "expected"), [("首件/巡检", "NG"), ("SPC控制", "PASS"), ("X-R控制图", "PASS")])
def test_race_track_dimensions_are_registered_dimensions_of_manufacturing(tmp_path, method, expected):
    rows = [
        ["90", "机加工", "加工中心", None, "29#位置度", "5", "位置度Φ0.2 A-B H", "CMM", "1次/4H", method],
        # No special mark: not a race-track dimension, whatever its control.
        [None, None, None, None, "4#电机舱尺寸", None, "Φ33.88±0.05", "CMM", "1次/4H", "过程检查记录"],
        # The outgoing inspection re-checks the dimension and needs no capability control.
        ["150", "出货检验", "检验台", None, "29#位置度", "5", "位置度Φ0.2 A-B H", "CMM", "每批", "出货检验记录"],
    ]
    result = _check(tmp_path, rows, "rule_5")
    assert result.status == expected
    assert expected == "PASS" or ("第2行" in result.suggestions and "第3行" not in result.suggestions and "第4行" not in result.suggestions)


def test_capability_written_in_the_evaluation_column_counts(tmp_path):
    rows = [["90", "机加工", "加工中心", None, "2#孔径", "3", "Φ8.12±0.0125", "气动量仪/Cpk≥1.33", "1次/4H", "过程检查记录"]]
    assert _check(tmp_path, rows, "rule_5").status == "PASS"


def test_no_race_track_dimension_is_left_to_a_person(tmp_path):
    rows = [
        ["90", "机加工", "加工中心", "外观", None, "3", "无毛刺", "目视", "1次/4H", "过程检查记录"],
        # A special dimension written only as its specification leaves the characteristic columns blank.
        ["90", "机加工", "加工中心", None, None, "SC", "46.4 +0.2/-0.1", "CMM", "1次/3H", "检查作业指导书"],
    ]
    result = _check(tmp_path, rows, "rule_5")
    assert result.status == "UNABLE"
    assert "未发现跑道尺寸，请核实" in result.suggestions and "特性列为空" in result.suggestions
