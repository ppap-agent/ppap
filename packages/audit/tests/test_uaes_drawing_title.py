"""A PDF drawing whose title block is lettered with strokes is identified from its transcribed drawing number."""

import fitz
import pytest
from audit.checker_lib.uaes.materials import UaesMaterialsAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body


@pytest.mark.parametrize(("number", "part", "expected"), [("F 01R 10Y 776", "F01R10Y776", True), ("F 01R 10Y 776", "F01RD 0Y77601", False), (None, "F01R10Y776", None)])
def test_the_drawing_number_comes_from_the_title_block_image(tmp_path, monkeypatch, number, part, expected):
    path = tmp_path / "drawing.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), "GENERAL TOLERANCES")
        document.save(path)
    asked = []

    def transcribe(self, image, prompt, schema, subject):
        asked.append(subject)
        return {"drawing_number": number, "designation": "机加工壳体", "version": "01"}

    monkeypatch.setattr(UaesMaterialsAgent, "transcribe", transcribe)
    body = preset_rule_body("UAES", "U04")
    agent = UaesMaterialsAgent(AuditInput(audit_id="list", file_url=str(path)), RuleInput(rule_id="u04", **{key: value for key, value in body.items() if key != "kind"}))
    drawing = AuditInput(audit_id="drawing", file_url=str(path))
    assert agent.drawing_shows(None, drawing, part) is expected
    assert agent.drawing_shows(None, drawing, part) is expected
    assert len(asked) == 1
