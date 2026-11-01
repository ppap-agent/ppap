"""Checking-fixture field contracts through real files and public checks."""

import pytest
from audit.checkers.v2.checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent as V2Agent
from audit.checkers.v3.checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent as V3Agent
from audit.contracts import AuditInput, RuleInput
from openpyxl import Workbook


@pytest.fixture(params=[("v2", V2Agent), ("v3", V3Agent)], ids=["v2", "v3"])
def check(request, tmp_path):
    profile, agent_type = request.param

    def check_workbook(workbook, rule_index=0):
        path = tmp_path / "checking-fixture.xlsx"
        workbook.save(path)
        workbook.close()
        agent = agent_type(
            AuditInput(audit_id="fixture-report", file_url=str(path)),
            RuleInput(
                rule_id="confirmed-fixture-rules",
                rule_profile=profile,
                type=agent_type.DOC_TYPE.get_type_name(),
                rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 4)],
            ),
        )
        try:
            result = agent.check(rule_index)
            assert result is not None
            return result
        finally:
            agent.close()

    return check_workbook


def complete_report():
    workbook = Workbook()
    report = workbook.active
    report.title = "检具认可报告"
    labels = ("零件号", "零件名", "G.D&T图号", "产品图纸更改级别", "总成号", "年型", "设计方", "制造方", "检具号", "三坐标检测机构", "日期 Date:")
    for row, label in enumerate(labels, 1):
        report.cell(row, 1, label)
        report.cell(row, 2, f"值{row}")
    report["A12"], report["B12"] = "检具认可负责人", "张三"
    report["A20"], report["B20"] = "1. 所附文件 Attachment", "状态"
    report["A21"], report["B21"] = "三坐标检测报告", "合格"
    report["A30"] = "2. 结论 Conclusion"
    report["A31"] = "（认可状态及后续是否有行动措施）OK"
    fixture_list = workbook.create_sheet("检具清单")
    fixture_list.append(["序号", "零件号", "零件名称", "检具号", "检具名称", "检具数量", "备注"])
    fixture_list.append([1, "P-1", "零件", "G-1", "检具", 1])
    return workbook


def test_conclusion_below_the_label_is_a_business_value(check):
    result = check(complete_report())

    assert result.status == "PASS"
    assert result.suggestions == ""


@pytest.mark.parametrize(
    ("responsible", "expected", "reason"),
    [
        ("named-narrative", "PASS", ""),
        ("label-only", "NG", "检具认可负责人字段或核查叙述"),
        ("empty-dedicated-field", "NG", "检具认可负责人字段"),
    ],
)
def test_responsible_person_narrative_cannot_replace_an_explicit_empty_field(check, responsible, expected, reason):
    workbook = complete_report()
    report = workbook.active
    report["B12"] = None
    report["A31"], report["B30"] = None, "OK"
    narrative = "采购技术中心检具负责人 张三 于 2026年3月17日完成核查"
    if responsible == "empty-dedicated-field":
        report["A13"] = narrative
    else:
        report["A12"] = narrative if responsible == "named-narrative" else "采购技术中心检具负责人"

    result = check(workbook)

    assert result.status == expected
    if reason:
        assert reason in result.suggestions
    else:
        assert result.suggestions == ""


def test_conclusion_instructions_are_not_a_completed_conclusion(check):
    workbook = complete_report()
    workbook.active["A31"] = "（认可状态及后续是否有行动措施）"

    result = check(workbook)

    assert result.status == "NG"
    assert "检具认可结论为空" in result.suggestions


def test_partial_header_overlap_does_not_turn_the_report_into_a_fixture_list(check):
    workbook = complete_report()
    workbook.remove(workbook["检具清单"])
    report = workbook.active
    report.title = "Sheet2"
    report["A14"], report["B14"] = "零件号", "零件名称"
    report["A31"], report["B30"] = None, "OK"

    result = check(workbook)

    assert result.status == "NG"
    assert "未找到工作表[检具清单]" in result.suggestions


def test_attachment_failure_requires_the_formal_conclusion_not_an_earlier_mention(check):
    workbook = Workbook()
    report = workbook.active
    report.title = "检具认可报告"
    report["B16"] = "核查情况及认可结论见下表："
    report["B17"] = "1. 所附文件 Attachment"
    report["B18"], report["C18"] = "(1).三坐标检测报告", "无（ ）/合格（ ）/不合格（√）"
    report["B27"] = "备注Remark"
    report["B34"], report["B35"] = "2. 结 论 Conclusion", "（认可状态及后续是否有行动措施）"

    result = check(workbook, 2)

    assert result.status == "NG"
    assert "结论" in result.suggestions
