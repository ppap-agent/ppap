"""U05: same part number and gauge name match exactly; what cannot be identified fails ("无法辨识则不通过")."""

from contextlib import contextmanager

from audit.checker_lib.uaes.msa import Report, UaesMsaAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def _agent(tmp_path, monkeypatch, related, kappas, target_book=None):
    target = tmp_path / "msa.xlsx"
    (target_book or Workbook()).save(target)
    path = tmp_path / "related.xlsx"
    related.save(path)

    @contextmanager
    def reading(self, ctx, item, document):
        from audit.checker_lib.excel import load_workbook_auto

        with ctx.reading(load_workbook_auto(str(path)), "related"):
            yield ctx

    monkeypatch.setattr(UaesMsaAgent, "only", lambda self, kind, label: "related")
    monkeypatch.setattr(UaesMsaAgent, "batch_files", lambda self, kind: ["related"])
    monkeypatch.setattr(UaesMsaAgent, "reading", reading)
    monkeypatch.setattr(UaesMsaAgent, "kappas", lambda self, ctx: kappas)
    body = preset_rule_body("UAES", "U05")
    agent = UaesMsaAgent(AuditInput(audit_id="msa", file_url=str(target)), RuleInput.of(body, rule_id="u05"))
    names = [name for name, _ in agent.declared_rules()]
    return lambda method: agent.check(names.index(method))


def _pgqp1(part="F01R10Y776"):
    plan = Workbook()
    plan.active.title = "PGQP1"
    plan.active.append(["Part No/零件号:", part])
    plan.active.append(["Items/项目", "SC/特殊特性", "Measuring Equipment\n测量设备"])
    plan.active.append(["2#螺栓孔", "5", "塞规"])
    return plan


def _control_plan(method="目视", part="F01R20V040"):
    plan = Workbook()
    plan.active.title = "CP"
    plan.active.append(["零件号", part])
    plan.active.append(["工序名称/操作描述", "设备/工装", "评价/测量技术"])
    plan.active.append(["出货检验", "检验台", method])
    return plan


def test_a_go_no_go_gauge_without_its_number_fails(tmp_path, monkeypatch):
    check = _agent(tmp_path, monkeypatch, _pgqp1(), [Report("Kappa", "“Kappa”", names={"目视"}, parts={"F01R10Y776"})])
    result = check("rule_3")
    assert result.status == "NG" and "没有写量具编号" in result.suggestions


def test_a_go_no_go_gauge_without_any_kappa_asks_for_one(tmp_path, monkeypatch):
    result = _agent(tmp_path, monkeypatch, _pgqp1(), [])("rule_3")
    assert result.status == "NG" and "请复审或提交kappa文件一起审核" in result.suggestions


def test_a_go_no_go_gauge_pairs_only_a_kappa_of_the_same_part(tmp_path, monkeypatch):
    other = _agent(tmp_path, monkeypatch, _pgqp1(), [Report("Kappa", "“Kappa”", parts={"F01RD0Y776"})])("rule_3")
    assert other.status == "NG" and "没有零件号为F01R10Y776的Kappa报告" in other.suggestions
    unknown = _agent(tmp_path, monkeypatch, _pgqp1(), [Report("Kappa", "“Kappa”")])("rule_3")
    assert unknown.status == "NG" and "未写零件号" in unknown.suggestions and "人工复核" in unknown.suggestions


def test_a_human_check_against_a_kappa_without_any_gauge_name_fails(tmp_path, monkeypatch):
    check = _agent(tmp_path, monkeypatch, _control_plan(), [Report("Kappa", "“Kappa”", parts={"F01R20V040"}, conclusions=["Acceptable"])])
    result = check("rule_4")
    assert result.status == "NG" and "没有量具名称" in result.suggestions


def test_a_human_check_needs_a_kappa_of_the_same_part_and_whole_name(tmp_path, monkeypatch):
    same = [Report("Kappa", "“Kappa”", names={"目视"}, parts={"F01R20V040"})]
    assert _agent(tmp_path, monkeypatch, _control_plan(), same)("rule_4").status == "PASS"
    # "量具名称相同": a Kappa named 目视 is not the one for 目视/卡尺.
    partial = _agent(tmp_path, monkeypatch, _control_plan("目视/卡尺"), same)("rule_4")
    assert partial.status == "NG" and "量具名称相同或为综合检具" in partial.suggestions
    other = _agent(tmp_path, monkeypatch, _control_plan(), [Report("Kappa", "“Kappa”", names={"综合检具"}, parts={"F01R20V04O"})])("rule_4")
    assert other.status == "NG" and "没有零件号为F01R20V040" in other.suggestions
    unknown = _agent(tmp_path, monkeypatch, _control_plan(), [Report("Kappa", "“Kappa”", names={"综合检具"})])("rule_4")
    assert unknown.status == "NG" and "未写零件号" in unknown.suggestions and "人工复核" in unknown.suggestions


def test_an_english_grr_judgement_is_its_conclusion(tmp_path, monkeypatch):
    book = Workbook()
    plan = book.active
    plan.title = "测量系统分析计划"
    plan.append(["序号", "量具名称", "量具编号", "计划日期"])
    plan.append([1, "CMM", "271806", "2025-01-01"])
    report = book.create_sheet("双性分析")
    report.append(["量具编号", "271806"])
    report.append(["Judgement：", "%R&R<10%，Measurement system can accept it！"])
    result = _agent(tmp_path, monkeypatch, Workbook(), [], target_book=book)("rule_2")
    assert result.status == "NG" and "Measurement system can accept it" in result.suggestions
