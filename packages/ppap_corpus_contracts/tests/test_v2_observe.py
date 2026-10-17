from pathlib import Path

from ppap_corpus_contracts.v2_assets_shared import FileCase, RuleExpectation
from ppap_corpus_contracts.v2_observe import collect_v2_rule_outcomes
from ppap_excel_audit_engine.checkers.agents.psw_pdf_agent import PswPDFAgent


def test_unreadable_psw_retains_visual_failure_diagnostic(tmp_path: Path) -> None:
    case = FileCase(
        file_name="unreadable.pdf",
        file_path=tmp_path / "unreadable.pdf",
        expected={3: RuleExpectation("NG", "印章检查", "")},
        agent_cls=PswPDFAgent,
        file_type="pdf",
    )

    outcome = collect_v2_rule_outcomes(case)[3].actual

    assert outcome.status == "ERR"
    assert outcome.diagnostic_code == "psw.visual.runtime_error"
