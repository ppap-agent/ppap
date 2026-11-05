"""Excel stage facts use the text-located XLSX region, with source and attempt reuse."""

import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from audit.checkers.v3.control_plan_agent import ControlPlanAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.runtime import bind_runtime
from openpyxl import Workbook
from PIL import Image


def stage_agent(tmp_path, suffix="xls"):
    from io import BytesIO

    path = tmp_path / ("original." + suffix)
    workbook = Workbook()
    workbook.active.title = "控制计划"
    workbook.active.merge_cells("A1:F1")
    workbook.active["A1"] = "样件 试生产 批量生产"
    agent = ControlPlanAgent(AuditInput(audit_id="frozen", file_url=str(path)), RuleInput(rule_id="confirmed", rule_profile="v3", type="控制计划", rules=[]))
    serialized = BytesIO()
    workbook.save(serialized)
    path.write_bytes(serialized.getvalue())
    agent._workbook_cache = workbook
    return agent, path


@pytest.mark.parametrize("suffix", ["xls", "xlsx"])
@pytest.mark.parametrize("value,expected", [(True, "PASS"), (False, "NG"), (None, "UNABLE"), ("failure", "ERR")])
def test_excel_region_preserves_identity_and_reuses_success_unknown_and_errors(tmp_path, monkeypatch, suffix, value, expected):
    from documents.excel import visual

    for key, item in {"OCR_OPENAI_API_KEY": "controlled", "OCR_OPENAI_BASE_URL": "https://example.invalid/v1", "OCR_MODEL_NAME": "controlled-ocr"}.items():
        monkeypatch.setenv(key, item)
    image = BytesIO()
    Image.new("RGB", (300, 100), "white").save(image, format="PNG")
    renders = []

    def render(content, *, sheet_name, cell_range, keywords):
        renders.append((content, sheet_name, cell_range))
        return image.getvalue()

    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        if value == "failure":
            raise ValueError("private provider body")
        return {"production_stage_selected": value}

    monkeypatch.setattr(visual, "render_xlsx_field", render)
    monkeypatch.setattr(visual, "has_region_graphics", lambda *a, **kw: True)
    agent, path = stage_agent(tmp_path, suffix)
    original = path.read_bytes()
    try:
        with bind_runtime(SimpleNamespace(read_ocr_visual_facts=request)):
            results = [agent.check(0), agent.check(0)]
        assert [result.status for result in results] == [expected, expected]
        assert len(calls) == len(renders) == 1
        assert renders[0][:2] == (path.read_bytes(), "控制计划")
        assert renders[0][2] == "A1:F1"
        assert all("private provider body" not in result.suggestions for result in results)
        if type(value) is bool:
            evidence = results[0].evidence
            assert evidence.source_format == suffix and evidence.prepared_format == "png"
            locator = json.loads(evidence.sources[0].locator)
            assert locator["source_sha256"] == hashlib.sha256(original).hexdigest()
            assert locator["sheet"] == "控制计划" and locator["cell_range"] == "A1:F1"
            assert locator["observed_value"] is value
        path.write_bytes(b"PK-different-source")
        with bind_runtime(SimpleNamespace(read_ocr_visual_facts=request)):
            assert agent.check(0).status == "ERR"
        assert len(calls) == 1
    finally:
        agent.close()


def test_legacy_stage_without_ocr_configuration_never_renders_or_passes(tmp_path, monkeypatch):
    from documents.excel import visual

    monkeypatch.setenv("OCR_OPENAI_API_KEY", "")
    monkeypatch.setattr(visual, "render_xlsx_field", lambda *args, **kwargs: pytest.fail("unconfigured request must not render"))
    monkeypatch.setattr(visual, "has_region_graphics", lambda *args, **kwargs: True)
    agent, _ = stage_agent(tmp_path)
    try:
        assert agent.check(0).status == "UNABLE"
    finally:
        agent.close()
