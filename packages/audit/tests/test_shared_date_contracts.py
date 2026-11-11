"""Shared date regressions through real workbooks and public V2/V3 checks."""

from datetime import date, datetime, timedelta
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from audit.checker_lib.li import psw_excel_agent as psw_v2
from audit.checker_lib.li.dvp_r_excel_agent import DvpRExcelAgent as V2DvpRAgent
from audit.checker_lib.li.pfmea_agent import PfmeaAgent as V2PfmeaAgent
from audit.checker_lib.li.xir_assembly_issue_agent import XirAssemblyIssueAgent as V2XirAgent
from audit.checker_lib.nio import psw_excel_agent as psw_v3
from audit.checker_lib.nio.dvp_r_excel_agent import DvpRExcelAgent as V3DvpRAgent
from audit.checker_lib.nio.pfmea_agent import PfmeaAgent as V3PfmeaAgent
from audit.checker_lib.nio.xir_assembly_issue_agent import XirAssemblyIssueAgent as V3XirAgent
from audit.contracts import AuditInput, RuleInput
from openpyxl import Workbook


def check_workbook(tmp_path, workbook, agent_type, profile, rule_number):
    path = tmp_path / "date-contract.xlsx"
    workbook.save(path)
    workbook.close()
    return check_file(path, agent_type, profile, rule_number)


def check_file(path, agent_type, profile, rule_number):
    agent = agent_type(
        AuditInput(audit_id="date-contract", file_url=str(path)),
        RuleInput(
            rule_id="confirmed-date-rules",
            rule_profile=profile,
            type=agent_type.DOC_TYPE.get_type_name(),
            rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, rule_number + 1)],
        ),
    )
    try:
        result = agent.check(rule_number - 1)
        assert result is not None
        return result
    finally:
        agent.close()


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2PfmeaAgent), ("v3", V3PfmeaAgent)], ids=["v2", "v3"])
@pytest.mark.parametrize(("last_revision", "expected"), [(46011, "PASS"), ("46011.0", "PASS"), (46012, "NG")], ids=["same-date", "serial-text", "different-date"])
def test_pfmea_dates_match_history_across_chinese_native_and_serial_values(tmp_path, profile, agent_type, last_revision, expected):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PFMEA"
    sheet.append(["PFMEA开始日期：", "2025年3月16日"])
    sheet.append(["PFMEA修订日期：", datetime(2025, 12, 20)])
    history = workbook.create_sheet("变更履历")
    history.append(["修订日期"])
    history.append([datetime(2025, 3, 16)])
    history.append([last_revision])

    result = check_workbook(tmp_path, workbook, agent_type, profile, 2)

    assert result.status == expected
    if expected == "NG":
        assert "修订日期" in result.suggestions


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2DvpRAgent), ("v3", V3DvpRAgent)], ids=["v2", "v3"])
@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2025年3月16日", "2025年3月17日", "PASS"),
        (46011, 46012, "PASS"),
        ("46011.0", "46012.0", "PASS"),
        (datetime(2025, 12, 20), datetime(2025, 12, 21), "PASS"),
        ("2025年3月16日", "2025年3月15日", "NG"),
    ],
    ids=["chinese-date", "excel-serial", "serial-text", "native-date", "end-before-start"],
)
def test_dvpr_plan_dates_accept_supported_formats_and_keep_chronology(tmp_path, profile, agent_type, start, end, expected):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "DVP&R"
    sheet.append(["序号", "试验项目", "试验要求", "试验类型", "计划开始", "计划结束"])
    sheet.append([1, "试验A", "要求A", "PV", start, end])

    result = check_workbook(tmp_path, workbook, agent_type, profile, 4)

    assert result.status == expected
    if expected == "NG":
        assert "计划结束日期" in result.suggestions


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2XirAgent), ("v3", V3XirAgent)], ids=["v2", "v3"])
@pytest.mark.parametrize(
    ("deadline", "expected"),
    [
        (date.today() + timedelta(days=30), "PASS"),
        (datetime.now() + timedelta(days=30), "PASS"),
        ((date.today() + timedelta(days=30)).strftime("%Y/%m/%d"), "PASS"),
        (date.today() - timedelta(days=1), "NG"),
        (datetime.now() - timedelta(days=1), "NG"),
        ("not-a-date", "NG"),
        (None, "NG"),
    ],
    ids=["future-date", "future-datetime", "future-slash-text", "past-date", "past-datetime", "invalid", "missing"],
)
def test_issue_deadlines_accept_supported_future_dates_and_reject_invalid_or_past_values(tmp_path, profile, agent_type, deadline, expected):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "项目问题清单汇总"
    sheet.append(["问题描述", "对应措施", "时间节点", "负责人"])
    sheet.append(["问题A", "调整工装", deadline, "负责人A"])

    result = check_workbook(tmp_path, workbook, agent_type, profile, 3)

    assert result.status == expected
    if expected == "NG":
        assert result.suggestions


def conditional_psw(tmp_path, approval_date):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    for cell, value in {"A10": "认可情况", "G10": "批准", "K10": "条件批准", "P10": "拒绝", "T15": "日期：", "U15": approval_date}.items():
        sheet[cell] = value
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    with ZipFile(buffer) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    sheet_xml = ElementTree.fromstring(parts["xl/worksheets/sheet1.xml"])
    # Office 2010 controlPr/anchor + formControlPr describe a real checked control.
    # https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.office2010.excel.formcontrolproperties
    sheet_xml.append(
        ElementTree.fromstring("""
        <controls xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
          xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing">
          <control name="ConditionalApproval" shapeId="1025" r:id="rIdCtrl1">
            <controlPr><anchor moveWithCells="1" sizeWithCells="1">
              <from><xdr:col>10</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>9</xdr:row><xdr:rowOff>0</xdr:rowOff></from>
              <to><xdr:col>11</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>10</xdr:row><xdr:rowOff>0</xdr:rowOff></to>
            </anchor></controlPr>
          </control>
        </controls>""")
    )
    parts["xl/worksheets/sheet1.xml"] = ElementTree.tostring(sheet_xml)
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rIdCtrl1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/ctrlProp" Target="../ctrlProps/ctrlProp1.xml"/>
        </Relationships>"""
    parts["xl/ctrlProps/ctrlProp1.xml"] = b'<formControlPr xmlns="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main" objectType="CheckBox" checked="Checked"/>'
    content_types = ElementTree.fromstring(parts["[Content_Types].xml"])
    ElementTree.SubElement(
        content_types,
        "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
        PartName="/xl/ctrlProps/ctrlProp1.xml",
        ContentType="application/vnd.ms-excel.controlproperties+xml",
    )
    parts["[Content_Types].xml"] = ElementTree.tostring(content_types)
    path = tmp_path / "conditional.xlsx"
    with ZipFile(path, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return path


class FixedPswClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 1, tzinfo=tz)


@pytest.mark.parametrize(("profile", "module"), [("v2", psw_v2), ("v3", psw_v3)], ids=["v2", "v3"])
@pytest.mark.parametrize(
    ("approval_date", "expected", "reason"),
    [
        (46011, "PASS", ""),
        ("46011", "PASS", ""),
        ("46011.0", "PASS", ""),
        (datetime(2025, 12, 20), "PASS", ""),
        ("日期：2025年3月16日", "NG", "超出3个月"),
        ("日期：2026年2月30日", "NG", "无法识别"),
    ],
    ids=["excel-serial", "serial-text", "decimal-serial-text", "native-date", "old-chinese-date", "invalid-calendar-date"],
)
def test_psw_conditional_approval_dates_keep_recency_after_normalization(tmp_path, monkeypatch, profile, module, approval_date, expected, reason):
    monkeypatch.setattr(module, "datetime", FixedPswClock)
    path = conditional_psw(tmp_path, approval_date)

    result = check_file(path, module.PswExcelAgent, profile, 9)

    assert result.status == expected
    if reason:
        assert reason in result.suggestions
