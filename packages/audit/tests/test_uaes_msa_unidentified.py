"""U05:2/U05:3: a gauge number or name the original confirms is not written fails ("无法辨识则不通过")."""

from contextlib import contextmanager

from audit.checker_lib.uaes.msa import Report, UaesMsaAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def _agent(tmp_path, monkeypatch, related, kappas):
    target = tmp_path / "msa.xlsx"
    Workbook().save(target)
    path = tmp_path / "related.xlsx"
    related.save(path)

    @contextmanager
    def reading(self, ctx, item, document):
        from audit.checker_lib.nio.cross_file_extractors import _load_workbook_auto

        with ctx.reading(_load_workbook_auto(str(path)), "related"):
            yield ctx

    monkeypatch.setattr(UaesMsaAgent, "only", lambda self, kind, label: "related")
    monkeypatch.setattr(UaesMsaAgent, "reading", reading)
    monkeypatch.setattr(UaesMsaAgent, "kappas", lambda self, ctx: kappas)
    body = preset_rule_body("UAES", "U05")
    agent = UaesMsaAgent(AuditInput(audit_id="msa", file_url=str(target)), RuleInput(rule_id="u05", **{key: value for key, value in body.items() if key != "kind"}))
    names = [name for name, _ in agent.declared_rules()]
    return lambda method: agent.check(names.index(method))


def test_a_go_no_go_gauge_without_its_number_fails(tmp_path, monkeypatch):
    plan = Workbook()
    plan.active.title = "PGQP1"
    plan.active.append(["Items/项目", "SC/特殊特性", "Measuring Equipment\n测量设备"])
    plan.active.append(["2#螺栓孔", "5", "塞规"])
    check = _agent(tmp_path, monkeypatch, plan, [Report("Kappa", "“Kappa”", names={"目视"})])
    result = check("rule_3")
    assert result.status == "NG" and "没有写量具编号" in result.suggestions


def test_a_human_check_against_a_kappa_without_any_gauge_name_fails(tmp_path, monkeypatch):
    plan = Workbook()
    plan.active.title = "CP"
    plan.active.append(["工序名称/操作描述", "设备/工装", "评价/测量技术"])
    plan.active.append(["出货检验", "检验台", "目视"])
    check = _agent(tmp_path, monkeypatch, plan, [Report("Kappa", "“Kappa”", conclusions=["Acceptable"])])
    result = check("rule_4")
    assert result.status == "NG" and "未写量具名称" in result.suggestions
