"""Control-plan rule parsing through the real model.

Corpus / V3 already provides the model credentials. A missing credential is a
setup failure, not an accuracy mismatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ppap_core.settings import get_settings
from ppap_core.types import PpapDocumentType

_CONTROL_PLAN = Path(__file__).resolve().parents[3] / "assets" / "poc测试" / "poc规则" / "10规则-控制计划.docx"


def test_control_plan_rule_doc_parses_to_eleven_rules() -> None:
    settings = get_settings()
    if not (settings.OPENAI_API_KEY.strip() and settings.MODEL_NAME.strip()):
        pytest.fail("setup_failure: model credential is missing; rule parsing did not start")
    from ppap_application.rule_processing import parse_rule_file

    parsed = parse_rule_file(_CONTROL_PLAN.name, _CONTROL_PLAN.read_bytes(), document_type="控制计划")
    assert parsed["type"] == PpapDocumentType.CONTROL_PLAN.get_type_name()
    assert len(parsed["rules"]) == 11
