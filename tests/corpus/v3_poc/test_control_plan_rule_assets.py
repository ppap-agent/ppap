"""Canonical control-plan sources and verbatim structured DOCX parsing."""

from __future__ import annotations

from pathlib import Path

from audit.kinds import PpapDocumentType

_CONTROL_PLAN = Path(__file__).resolve().parents[3] / "assets" / "poc测试" / "poc规则" / "10规则-控制计划.docx"
_MARKDOWN = _CONTROL_PLAN.parent / "markdown" / (_CONTROL_PLAN.name + ".md")


def test_control_plan_markdown_keeps_the_frozen_rule_and_media_contract() -> None:
    markdown = _MARKDOWN.read_text(encoding="utf-8")
    for index in range(1, 12):
        assert f"<td>{index}</td>" in markdown
    assert "2a" not in markdown
    assert "2b" not in markdown
    for term in ("流程一致性审核", "特性一致性审核", "措施一致性审核", "控制方法（制造过程）"):
        assert term in markdown
    assert "media/10规则-控制计划/media/image13.png" in markdown
    assert "image14.png" not in markdown


class NoModelRewrite:
    def structured_completion(self, **kwargs):
        raise AssertionError("Explicit DOCX rule tables do not require model rewriting")


def test_control_plan_rule_doc_parses_to_eleven_rules() -> None:
    from worker.intake import parse_rule_file

    parsed = parse_rule_file(_CONTROL_PLAN.name, _CONTROL_PLAN.read_bytes(), document_type="控制计划", ai=NoModelRewrite())
    assert parsed["type"] == PpapDocumentType.CONTROL_PLAN.get_type_name()
    assert len(parsed["rules"]) == 11
    assert parsed["rule_profile"] == "v3"
