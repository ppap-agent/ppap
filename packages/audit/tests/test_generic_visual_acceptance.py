"""Controlled original-page traces for generic OCR to text interpretation."""

import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import fitz
import pytest
from audit.checkers.v2.base_agent import BaseAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.runtime import bind_runtime
from PIL import Image


def original(tmp_path, *, pages=1, rotated=False):
    path = tmp_path / "source.pdf"
    with fitz.open() as document:
        for _ in range(pages):
            page = document.new_page(width=200, height=100)
            page.draw_rect(fitz.Rect(20, 20, 160, 90), color=(1, 0, 0), fill=(1, 0, 0))
            if rotated:
                page.set_rotation(90)
        document.save(path)
    return path


def agent(path):
    rule = {"id": "one", "category": "图形条款", "priority": "P0", "rule_to_check": "核对原件红色区域", "type_of_rule": "image"}
    return BaseAgent(AuditInput(audit_id="frozen-version", file_url=str(path)), RuleInput(rule_id="frozen-rule", rules=[rule]))


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled-ocr-key")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://ocr.invalid/v1")
    monkeypatch.setenv("OCR_MODEL_NAME", "existing-ocr-model")
    text_calls = []

    def compare(**kwargs):
        text_calls.append(kwargs)
        source = json.loads(kwargs["user_prompt"])["sources"][0]
        return json.dumps({"relation": "supports", "scope": "all", "citations": [{"id": source["id"], "quote": source["text"]}], "explanation": "已提供的区域观察符合条款。"}, ensure_ascii=False)

    monkeypatch.setattr("audit.engine.checkers.client.AIClient", lambda: SimpleNamespace(structured_completion=compare))
    return text_calls


def response(*, empty=False, unreadable=False):
    return {
        "observations": [] if empty else [{"bbox": [0.1, 0.2, 0.8, 0.9], "text": "该原件区域为红色。"}],
        "coverage": [{"need": 0, "status": "unreadable" if unreadable else "partial" if empty else "complete", "observations": [] if empty else [0]}],
    }


def test_one_empty_required_page_cannot_make_another_pages_observation_complete(tmp_path, configured):
    path = original(tmp_path, pages=2)
    calls = []

    def read(**request):
        calls.append(request)
        return response(empty=len(calls) == 2)

    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        checked = agent(path).check(0)
    assert len(calls) == 2
    assert checked.status == "UNABLE"
    assert len(configured) == 1
    assert json.loads(configured[0]["user_prompt"])["coverage"] == "partial_observed_content"


def test_original_unrotated_pixels_and_coordinates_reach_the_text_step(tmp_path, configured):
    path = original(tmp_path, rotated=True)
    before = path.read_bytes()
    calls = []

    def read(**request):
        calls.append(request)
        assert len(request["images"]) == 1
        with Image.open(BytesIO(request["images"][0])) as pixels:
            assert pixels.size == (400, 200)
            assert pixels.convert("RGB").getpixel((160, 100)) == (255, 0, 0)
        return response()

    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        checker = agent(path)
        first, reused = checker.check(0), checker.check(0)
    assert first.status == reused.status == "PASS"
    assert len(calls) == len(configured) == 1
    assert path.read_bytes() == before
    source = json.loads(first.evidence.sources[0].locator)
    assert source["page"] == 1
    assert source["bbox"] == [20, 20, 160, 90]
    assert source["coordinate_system"] == "pdf_points_unrotated"
    assert source["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert source["file_version_id"] == "frozen-version"


def test_failed_second_page_is_not_resent_and_no_partial_verdict_is_published(tmp_path, configured):
    path = original(tmp_path, pages=2)
    calls = []

    def read(**request):
        calls.append(request)
        if len(calls) == 2:
            raise TimeoutError("private endpoint")
        return response()

    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        checker = agent(path)
        results = [checker.check(0), checker.check(0)]
    assert [item.status for item in results] == ["ERR", "ERR"]
    assert len(calls) == 2
    assert configured == []
    assert all("private endpoint" not in item.suggestions for item in results)


def test_source_replaced_after_ocr_cannot_authorize_a_text_verdict(tmp_path, configured):
    path = original(tmp_path)
    calls = []

    def read(**request):
        calls.append(request)
        path.write_bytes(path.read_bytes() + b"\n")
        return response()

    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        checked = agent(path).check(0)
    assert checked.status == "ERR"
    assert len(calls) == 1
    assert configured == []


@pytest.mark.parametrize("bad", ["empty", "unreadable", "out_of_bounds", "invented_verdict"])
def test_missing_or_invalid_graphic_evidence_cannot_become_pass_or_ng(tmp_path, configured, bad):
    path = original(tmp_path)
    raw = response(empty=bad == "empty", unreadable=bad == "unreadable")
    if bad == "out_of_bounds":
        raw["observations"][0]["bbox"] = [0, 0, 1.1, 1]
    elif bad == "invented_verdict":
        raw["status"] = "PASS"
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=lambda **request: raw)):
        checked = agent(path).check(0)
    assert checked.status == ("ERR" if bad in {"out_of_bounds", "invented_verdict"} else "UNABLE")
    if bad != "unreadable":
        assert configured == []


def test_per_need_coverage_and_observations_are_isolated_and_reused(tmp_path, configured):
    path = original(tmp_path)
    checker = agent(path)
    checker.rule.rules.append({"id": "two", "category": "图形条款二", "priority": "P0", "rule_to_check": "核对蓝色签字区域", "type_of_rule": "image"})
    raw = response()
    raw["observations"].append({"bbox": [0.5, 0.2, 0.8, 0.9], "text": "签字区域为蓝色。"})
    raw["coverage"] = [
        {"need": 0, "status": "partial", "observations": [0]},
        {"need": 1, "status": "complete", "observations": [1]},
    ]
    calls = []

    def read(**request):
        calls.append(request)
        assert '"0": "核对原件红色区域"' in request["prompt"]
        assert '"1": "核对蓝色签字区域"' in request["prompt"]
        return raw

    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        first, second = checker.check(0), checker.check(1)
    assert first.status == "UNABLE"
    assert second.status == "PASS"
    assert len(calls) == 1
    payloads = [json.loads(call["user_prompt"]) for call in configured]
    assert payloads[0]["coverage"] == "partial_observed_content"
    assert [source["text"] for source in payloads[0]["sources"]] == ["该原件区域为红色。"]
    assert payloads[1]["coverage"] == "complete_observed_content"
    assert [source["text"] for source in payloads[1]["sources"]] == ["签字区域为蓝色。"]


@pytest.mark.parametrize("bad", ["missing_need", "duplicate_need", "missing_observation", "boolean_reference", "duplicate_reference", "empty_complete"])
def test_invalid_need_coverage_cannot_authorize_full_scope(tmp_path, configured, bad):
    raw = response()
    if bad == "missing_need":
        raw["coverage"] = []
    elif bad == "duplicate_need":
        raw["coverage"] *= 2
    elif bad == "missing_observation":
        raw["coverage"][0]["observations"] = [1]
    elif bad == "boolean_reference":
        raw["coverage"][0]["observations"] = [False]
    elif bad == "duplicate_reference":
        raw["coverage"][0]["observations"] = [0, 0]
    elif bad == "empty_complete":
        raw["coverage"][0]["observations"] = []
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=lambda **request: raw)):
        checked = agent(original(tmp_path)).check(0)
    assert checked.status == "ERR"
    assert configured == []


def test_unreferenced_observation_cannot_fill_missing_need_evidence(tmp_path, configured):
    raw = response()
    raw["coverage"][0] = {"need": 0, "status": "partial", "observations": []}
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=lambda **request: raw)):
        checked = agent(original(tmp_path)).check(0)
    assert checked.status == "UNABLE"
    assert configured == []
