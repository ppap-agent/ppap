"""The canonical control-plan rule DOCX parses verbatim into its eleven rules."""

from __future__ import annotations

from pathlib import Path

from audit.kinds import PpapDocumentType

_CONTROL_PLAN = Path(__file__).resolve().parents[3] / "assets" / "nio" / "history" / "poc规则" / "10规则-控制计划.docx"


class NoModelRewrite:
    def __call__(self, *args, **kwargs):
        raise AssertionError("Explicit DOCX rule tables do not require model rewriting")


def test_control_plan_rule_doc_parses_to_eleven_rules() -> None:
    from worker.intake import parse_rule_file

    parsed = parse_rule_file(_CONTROL_PLAN.name, _CONTROL_PLAN.read_bytes(), document_type="控制计划", ai=NoModelRewrite())
    assert parsed["type"] == PpapDocumentType.CONTROL_PLAN.get_type_name()
    assert len(parsed["rules"]) == 11
    assert parsed["rule_profile"] == "v3"
