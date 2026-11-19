"""UAES clauses decided on what the originals actually show (U01:3, U05, U06, U07:2)."""

from pathlib import Path

from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook

SAMPLES = Path(__file__).resolve().parents[3] / "assets" / "uaes" / "samples"


def _check(agent_class, preset: str, path: Path, method: str):
    body = preset_rule_body("UAES", preset)
    agent = agent_class(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id=preset.lower(), **{key: value for key, value in body.items() if key != "kind"}))
    names = [name for name, _ in agent.declared_rules()]
    return agent.check(names.index(method))


def test_a_formula_error_does_not_hide_a_clear_capability_failure():
    from audit.checker_lib.uaes.engineering import UaesCapabilityAgent

    path = SAMPLES / "F01RD0YA40_机加工壳体" / "缺陷样本(-test-)" / "07 过程能力研究" / "A40 CPK-机加工 - test-缺32号关键尺寸、2号尺寸出错、16号尺寸NG.xlsx"
    # Every report is read: 16#位置度 Ppk 1.155962 fails though 2#电机舱直径 shows #DIV/0!.
    ppk = _check(UaesCapabilityAgent, "U06", path, "rule_2")
    assert ppk.status == "NG" and "1.155962" in ppk.suggestions
    summary = _check(UaesCapabilityAgent, "U06", path, "rule_3")
    assert summary.status == "NG" and "没有反应计划或备注" in summary.suggestions
    # Only the error is left for U06:1, whose clause says "如果无法辨识则不通过" and asks for a person.
    cpk = _check(UaesCapabilityAgent, "U06", path, "rule_1")
    assert cpk.status == "NG" and "#DIV/0!" in cpk.suggestions and "请人工复核" in cpk.suggestions


def test_a_signature_picture_in_the_supplier_area_signs_each_report_with_requirements():
    from audit.checker_lib.uaes.inspection import UaesInspectionAgent

    path = SAMPLES / "F01RD0YA40_机加工壳体" / "测试文件" / "01-1 检验报告" / "F-PU-207_样品检验报告_Sample Inspection Report_V01.xls (1).xls"
    # The blank reliability report has no requirement and needs no signature.
    result = _check(UaesInspectionAgent, "U01", path, "rule_3")
    assert result.status == "PASS", result.suggestions
    assert "可靠性" not in result.suggestions


def test_a_kappa_part_number_written_under_its_label_is_read():
    from audit.checker_lib.nio.cross_file_extractors import _load_workbook_auto
    from audit.checker_lib.uaes.msa import UaesMsaAgent
    from audit.engine.rule_engine import RuleContext

    path = SAMPLES / "F01R10Y776_机加壳体" / "测试文件" / "06 测量系统分析评价" / "776-Kappa.xls"
    body = preset_rule_body("UAES", "U05")
    agent = UaesMsaAgent(AuditInput(audit_id="version", file_url=str(path)), RuleInput(rule_id="u05", **{key: value for key, value in body.items() if key != "kind"}))
    assert agent._rule_resource is not None
    ctx = RuleContext(None, _load_workbook_auto(str(path)), agent._rule_resource)
    (kappa,) = agent.reports_in(ctx)
    assert kappa.parts == {"F01RD0Y776"} and not kappa.names


def test_pfmea_scores_pass_on_the_clause_s_checkable_items(tmp_path):
    from audit.checker_lib.uaes.fmea import UaesPfmeaAgent

    book = Workbook()
    sheet = book.active
    sheet.title = "PFMEA"
    sheet.append(["PFMEA"])
    sheet.append(["潜在失效模式", "潜在失效后果", "严重度", "潜在失效原因", "频度", "预防\nPrevent", "探测\nDetect", "探测度"])
    sheet.append(["裂纹", "外观不良", 5, "参数异常", 3, "参数监控", "目视", 6])
    sheet.append(["气孔", "泄漏", 7, "熔液含气", 3, "除气", "气密", 3])
    path = tmp_path / "pfmea.xlsx"
    book.save(path)
    passed = _check(UaesPfmeaAgent, "U07", path, "rule_2")
    assert passed.status == "PASS", passed.suggestions
    # Each scored item is a check made: a PASS with zero checks is turned into a technical error downstream.
    assert passed.checks_performed and passed.checks_performed >= 2
    # The same mode and effect scored with another severity fails.
    sheet.append([None, "泄漏", 5, "排气不良", 3, "模具评审", "气密", 3])
    book.save(path)
    failed = _check(UaesPfmeaAgent, "U07", path, "rule_2")
    assert failed.status == "NG" and "严重度S却分别为7与5" in failed.suggestions
