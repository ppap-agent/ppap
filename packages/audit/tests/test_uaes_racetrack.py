"""U08:6: race-track dimensions are the rows marked with a symbol the plan's legend frames as a race track."""

import io

import pytest
from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook
from openpyxl.drawing.image import Image as Picture
from PIL import Image


def _plan(tmp_path, process_method):
    book = Workbook()
    sheet = book.active
    sheet.title = "CP"
    sheet.append(["工序名称/操作描述", "设备/工装", "产品特性", "特殊特性", "评价/测量技术", "频率", "控制方法"])
    sheet.append(["机加工", "加工中心", "29#位置度", "3", "CMM", "1次/4H", process_method])
    sheet.append([None, None, "71#平行度5", None, "CMM", "1次/4H", "SPC控制"])
    sheet.append(["出货检验", "检验台", "29#位置度", "3", "CMM", "每批", "出货检验记录"])
    sheet.append(["备注：", "内部过程特性符号"])
    stream = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(stream, format="PNG")
    sheet.add_image(Picture(stream), "C5")
    path = tmp_path / "cp.xlsx"
    book.save(path)
    return path


@pytest.mark.parametrize(("method", "expected"), [("首件/巡检", "NG"), ("SPC控制", "PASS")])
def test_process_rows_with_a_legend_race_track_symbol_need_spc(tmp_path, monkeypatch, method, expected):
    monkeypatch.setattr(UaesControlPlanAgent, "transcribe", lambda self, image, prompt, schema, subject: {"symbol": "3", "racetrack_frame": True})
    body = preset_rule_body("UAES", "U08")
    agent = UaesControlPlanAgent(AuditInput(audit_id="cp", file_url=str(_plan(tmp_path, method))), RuleInput(rule_id="u08", **{key: value for key, value in body.items() if key != "kind"}))
    names = [name for name, _ in agent.declared_rules()]
    result = agent.check(names.index("rule_5"))
    # The outgoing inspection re-checks the dimension and needs no SPC; "71#平行度5" names class 5, not a legend symbol.
    assert result.status == expected
    assert expected == "PASS" or ("第2行" in result.suggestions and "第4行" not in result.suggestions)
