"""U19: the clause's own wording for conflicting remarks and for what the deviation list must contain."""

from audit.checker_lib.uaes.approval import UaesApprovalAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def _check(tmp_path, monkeypatch, book: Workbook, remarks: str):
    path = tmp_path / "approval.xlsx"
    book.save(path)
    monkeypatch.setattr(UaesApprovalAgent, "cover", lambda self, ctx: (None, None))
    monkeypatch.setattr(UaesApprovalAgent, "rejected", lambda self, ctx, sheet, section: "")
    monkeypatch.setattr(UaesApprovalAgent, "remark", lambda self, sheet: (remarks, None))
    monkeypatch.setattr(UaesApprovalAgent, "identity", lambda self, ctx, sheet: ("F01RD0YC50", "机加工壳体"))
    monkeypatch.setattr(UaesApprovalAgent, "deviation_lists", lambda self, ctx, part, name: [None])
    body = preset_rule_body("UAES", "U19")
    agent = UaesApprovalAgent(AuditInput(audit_id="u19", file_url=str(path)), RuleInput(rule_id="u19", **{key: value for key, value in body.items() if key != "kind"}))
    return agent.check([name for name, _ in agent.declared_rules()].index("rule_1"))


def test_a_remark_with_both_kinds_of_wording_fails_and_names_both(tmp_path, monkeypatch):
    result = _check(tmp_path, monkeypatch, Workbook(), "尺寸偏差认可，其余完全认可")
    assert result.status == "NG"
    assert "完全认可" in result.suggestions and "偏差认可" in result.suggestions and "请人工复核" in result.suggestions


def test_a_deviation_list_needs_its_fields_not_every_row_filled(tmp_path, monkeypatch):
    book = Workbook()
    book.active.title = "偏差认可汇总"
    book.active.append(["偏差项", "UAES评估确认", "是否同意偏差", "备注"])
    book.active.append(["平面度超差", "", "Yes", "临时偏差至2099-01-01"])
    result = _check(tmp_path, monkeypatch, book, "条件认可，供应商2099-01-01前完成整改")
    assert result.status == "PASS", result.suggestions
