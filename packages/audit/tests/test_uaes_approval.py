"""U19: the clause's own wording for conflicting remarks and for what the deviation list must contain."""

import json
from types import SimpleNamespace

from audit.checker_lib.uaes.approval import UaesApprovalAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.rule_engine import Sheet
from audit.presets import preset_rule_body
from openpyxl import Workbook


def _check(tmp_path, monkeypatch, book: Workbook, remarks: str, remark_at: tuple[int, int] | None = None):
    path = tmp_path / "approval.xlsx"
    book.save(path)
    cell = SimpleNamespace(row_index=remark_at[0], col_index=remark_at[1]) if remark_at else None
    monkeypatch.setattr(UaesApprovalAgent, "cover", lambda self, ctx: (Sheet(ctx, ctx.workbook.active), None))
    monkeypatch.setattr(UaesApprovalAgent, "rejected", lambda self, ctx, sheet, section: "")
    monkeypatch.setattr(UaesApprovalAgent, "remark", lambda self, sheet: (remarks, cell))
    monkeypatch.setattr(UaesApprovalAgent, "identity", lambda self, ctx, sheet: ("F01RD0YC50", "机加工壳体"))
    monkeypatch.setattr(UaesApprovalAgent, "deviation_lists", lambda self, ctx, part, name: [None])
    body = preset_rule_body("UAES", "U19")
    agent = UaesApprovalAgent(AuditInput(audit_id="u19", file_url=str(path)), RuleInput.of(body, rule_id="u19"))
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


def _cited(result):
    return [json.loads(source.locator)["cell"] for source in (result.evidence.sources if result.evidence else ())]


def test_a_conflicting_remark_cites_the_cover_remark(tmp_path, monkeypatch):
    book = Workbook()
    book.active["B5"] = "UAES 批准的前提/备注 Remark: 尺寸偏差认可，其余完全认可"
    result = _check(tmp_path, monkeypatch, book, "尺寸偏差认可，其余完全认可", remark_at=(5, 2))
    assert result.status == "NG"
    assert result.suggestions == "封面备注“尺寸偏差认可，其余完全认可”同时含“完全认可”与“偏差认可”，两类表述冲突，不通过，请人工复核。"
    assert _cited(result) == ["B5"]


def test_an_expired_deviation_cites_its_remark_cell(tmp_path, monkeypatch):
    book = Workbook()
    book.active.title = "偏差认可汇总"
    book.active.append(["偏差项", "UAES评估确认", "是否同意偏差", "备注"])
    book.active.append(["平面度超差", "", "Yes", "临时偏差至2099-01-01"])
    book.active.append(["孔径超差", "", "Yes", "临时偏差至2020-01-01"])
    result = _check(tmp_path, monkeypatch, book, "条件认可，供应商2099-01-01前完成整改")
    assert result.status == "NG"
    assert result.suggestions == "孔径超差偏差项有效期(2020-01-01)已过期，请确认已更新批准或整改闭环。"
    assert _cited(result) == ["D3"]


def test_citing_leaves_a_passing_deviation_list_without_cells(tmp_path, monkeypatch):
    book = Workbook()
    book.active.title = "偏差认可汇总"
    book.active.append(["偏差项", "UAES评估确认", "是否同意偏差", "备注"])
    book.active.append(["平面度超差", "", "Yes", "临时偏差至2099-01-01"])
    result = _check(tmp_path, monkeypatch, book, "条件认可，供应商2099-01-01前完成整改")
    assert result.status == "PASS"
    assert result.suggestions == "条件认可封面承诺期限(2099-01-01)与全部偏差行有效期一致且未过期。"
    assert _cited(result) == []
