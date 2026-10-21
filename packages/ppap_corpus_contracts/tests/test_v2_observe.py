from pathlib import Path

from openpyxl import Workbook
from ppap_core.contracts import AuditInput
from ppap_corpus_contracts.baseline_exceptions import ObservedRuleOutcome
from ppap_corpus_contracts.execution_outcome import O
from ppap_corpus_contracts.v2_assets_shared import FileCase, RuleExpectation, make_dummy_rule
from ppap_corpus_contracts.v2_observe import collect_v2_rule_outcomes, format_v2_rule_outcome
from ppap_excel_audit_engine.checkers.agents.psw_excel_agent import PswExcelAgent
from ppap_excel_audit_engine.checkers.agents.psw_pdf_agent import PswPDFAgent


def test_unreadable_psw_retains_visual_failure_diagnostic(tmp_path: Path) -> None:
    case = FileCase(
        file_name="unreadable.pdf",
        file_path=tmp_path / "unreadable.pdf",
        expected={3: RuleExpectation("NG", "印章检查", "")},
        agent_cls=PswPDFAgent,
        file_type="pdf",
    )

    observed = collect_v2_rule_outcomes(case)[3]
    outcome = observed.actual

    assert outcome.status == "ERR"
    assert outcome.diagnostic_code == "psw.visual.runtime_error"
    assert observed.detail == ""


def test_status_mismatch_keeps_the_actual_checker_explanation(tmp_path: Path, capsys) -> None:
    source = tmp_path / "psw.xlsx"
    workbook = Workbook()
    workbook.active.append(["零件提交保证书"])
    workbook.active.append(["重量(kg)", "1.2"])
    workbook.save(source)
    workbook.close()
    original = source.read_bytes()
    case = FileCase(
        file_name=source.name,
        file_path=source,
        expected={2: RuleExpectation("PASS", "重量保留四位小数", "")},
        agent_cls=PswExcelAgent,
        file_type="excel",
    )
    agent = PswExcelAgent(AuditInput(file_url=str(source), audit_id="diagnostic-source"), make_dummy_rule(2))
    try:
        checked = agent.check(1)
    finally:
        agent.close()
    assert checked is not None
    assert checked.suggestions == "重量小数位数应为4位，识别值=1.2，小数位=1"

    outcome = collect_v2_rule_outcomes(case, verbose_check=True)[2]

    assert outcome.actual.status == "NG"
    assert outcome.is_mismatch
    assert checked.suggestions in capsys.readouterr().out
    assert checked.suggestions in format_v2_rule_outcome(outcome)
    assert source.read_bytes() == original


def test_explanation_is_bounded_and_cannot_inject_a_report_line() -> None:
    outcome = ObservedRuleOutcome("NG", O("PASS"), detail="first\n\x1b[31msecond " + "x" * 2000 + "NOT-IN-REPORT")

    text = format_v2_rule_outcome(outcome)

    assert text.startswith('expected NG, got PASS/result; reason="first ')
    assert "\\u001b[31msecond" in text
    assert "\n" not in text
    assert "\x1b" not in text
    assert "NOT-IN-REPORT" not in text
    assert len(text) < 1100


def test_explanation_does_not_change_result_equality_or_mismatch() -> None:
    first = ObservedRuleOutcome("NG", O("PASS"), detail="first explanation")
    second = ObservedRuleOutcome("NG", O("PASS"), detail="another explanation")

    assert first == second
    assert first.is_mismatch and second.is_mismatch
    assert format_v2_rule_outcome(ObservedRuleOutcome("PASS", O("PASS"))) == "expected PASS, got PASS/result"
