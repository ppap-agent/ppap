"""Customer-specific T12 facts are evaluated through the actual Round boundary."""

import io
import json

import pytest
from audit.audits import RoundInput, RoundWork
from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
from audit.presets import preset_rule_body
from openpyxl import Workbook
from product import run_round_from_bytes


@pytest.mark.parametrize(("missing", "expected"), [(None, ["PASS", "PASS"]), ("identity", ["NG", "PASS"]), ("value", ["PASS", "NG"])])
def test_uaes_material_identity_and_characteristic_values(missing, expected):
    book = Workbook()
    sheet = book.active
    sheet.title = "特殊特性清单"
    sheet.append(["物料名称", None if missing == "identity" else "支架"])
    sheet.append(["物料编号", "P12345"])
    sheet.append(["编号", "产品特性", "特性及公差", "特性类型"])
    sheet.append([1, "孔径", None if missing == "value" else "10±0.1", "CC"])
    content = io.BytesIO()
    book.save(content)
    book.close()
    body = preset_rule_body("UAES", "U12")
    execution = freeze_target_rule_execution(body, "特性.xlsx")
    target = RoundInput("target", "file", "version", "特性.xlsx", "ready", rule_version_id="uaes-u12", rule_body=body, rule_execution=execution)
    result = run_round_from_bytes(RoundWork("round", "tenant", [target]), {"version": content.getvalue()})
    assert [finding["status"] for finding in result["findings"]] == expected, [(f["status"], f["suggestions"]) for f in result["findings"]]
    assert result["coverage"]["unchecked"] == []
    for finding in result["findings"]:
        sources = finding["locator"]["evidence"]["sources"]
        assert sources
        assert all(json.loads(source["locator"])["file_version_id"] == "version" for source in sources)


@pytest.mark.parametrize(
    ("missing", "expected"), [(None, ["PASS", "PASS", "PASS"]), ("supplier", ["NG", "PASS", "PASS"]), ("risk", ["PASS", "PASS", "NG"]), ("risk_without_step", ["PASS", "PASS", "NG"])]
)
def test_li_auto_ccsc_uses_its_own_fields_and_high_risk_rule(missing, expected):
    book = Workbook()
    sheet = book.active
    sheet.title = "特殊特性清单"
    sheet.append(["零件名称", "支架", "零件号", "P12345"])
    sheet.append(["供应商名称", None if missing == "supplier" else "供应商", "供应商编码", "S01"])
    columns = ["过程/流程步骤", "部件(来料检时涉及)", "客户要求/功能要求", "特性名称", "检验岗位", "产品/过程特性分类", "名义值/规格中心值", "规格下限", "规格上限"]
    controls = ["测量设备", "测量方式", "防错控制", "检查控制", "抽样频率", "SPC监控", "SPC线上管理计划时间", "说明（关键要求/操作参数等）"]
    sheet.append(columns + ["T1-特性监控方法"] + [None] * 7 + ["现有管理方式风险评估", "高风险管理项预防措施"])
    sheet.append([None] * 9 + controls)
    sheet.merge_cells("J3:Q3")
    for index in (*range(1, 10), 18, 19):
        sheet.merge_cells(start_row=3, end_row=4, start_column=index, end_column=index)
    sheet.append(["10 压装", "衬套", "定位", "孔径", "检验", "产品", 10, 9.9, 10.1] + ["适用"] * 8 + ["高风险", None if missing == "risk" else "定位防错"])
    if missing == "risk_without_step":
        sheet.append([None] * 17 + ["高风险", None])
    content = io.BytesIO()
    book.save(content)
    book.close()
    body = preset_rule_body("理想", "L12")
    execution = freeze_target_rule_execution(body, "特性.xlsx")
    assert execution["checker"]["profile"] == "v4"
    target = RoundInput("target", "file", "version", "特性.xlsx", "ready", rule_version_id="li-l12", rule_body=body, rule_execution=execution)
    result = run_round_from_bytes(RoundWork("round", "tenant", [target]), {"version": content.getvalue()})
    assert [finding["status"] for finding in result["findings"]] == expected, [(f["status"], f["suggestions"]) for f in result["findings"]]
    assert result["coverage"]["unchecked"] == []
