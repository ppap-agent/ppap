"""Semantic fallback consumes bounded, located observations, never raw guesses."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from audit.checkers.v2.base_agent import BaseAgent
from audit.contracts import AuditInput, RuleInput, RuleItem
from audit.engine.checkers.text_evidence import evaluate_located_text, evaluate_text_rule
from docx import Document
from PIL import Image


def rule(*, clause="说明检验职责", kind="text"):
    return RuleItem(id="clause-1", category="检验要求", priority="P1", rule_to_check=clause, type_of_rule=kind)


def original(tmp_path, text="检验职责由质量工程师承担。", *, suffix=".md"):
    path = tmp_path / ("original" + suffix)
    if suffix == ".docx":
        document = Document()
        document.add_paragraph(text)
        document.save(path)
    else:
        path.write_text(text, encoding="utf-8")
    return AuditInput(audit_id="frozen-version", file_url=str(path), content="Unlocated content must not be sent", file_metadata={"private_metadata": "DO NOT SEND"})


@pytest.fixture
def model(monkeypatch):
    calls = []
    behavior = {"relation": "supports", "scope": "local"}

    def request(**kwargs):
        calls.append(kwargs)
        if "error" in behavior:
            raise behavior["error"]
        payload = json.loads(kwargs["user_prompt"])
        source = next((item for item in payload["sources"] if "检验职责" in item["text"]), payload["sources"][0])
        response = {
            "relation": behavior["relation"],
            "scope": behavior["scope"],
            "citations": [{"id": source["id"], "quote": source["text"]}],
            "explanation": "所引原文明确说明检验职责。",
        }
        if "replace" in behavior:
            response.update(behavior["replace"])
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr("audit.engine.checkers.client.AIClient", lambda: SimpleNamespace(structured_completion=request))
    return calls, behavior


@pytest.mark.parametrize("suffix", [".md", ".txt", ".docx"])
def test_public_fallback_uses_original_text_locations_and_one_clause(tmp_path, model, suffix):
    audit = original(tmp_path, suffix=suffix)
    agent = BaseAgent(audit, RuleInput(rule_id="frozen-rules", rules=[rule().model_dump()]))
    checked = agent.check(0)
    assert checked.status == "PASS"
    assert checked.evidence.sources[0].kind == "structure"
    source = json.loads(checked.evidence.sources[0].locator)
    assert source["file_version_id"] == audit.audit_id
    assert source["source_sha256"] == hashlib.sha256(open(audit.file_url, "rb").read()).hexdigest()
    assert source["kind"] == ("docx_paragraph" if suffix == ".docx" else "text_line")
    calls, _ = model
    assert len(calls) == 1
    request = calls[0]
    payload = json.loads(request["user_prompt"])
    assert payload["clause"] == "说明检验职责"
    assert payload["document_count"] == 1
    assert request["request_timeout"] == 20 and request["retry_limit"] == 0
    assert request["operation"] == "rule_text_evidence"
    assert "DO NOT SEND" not in request["user_prompt"]
    assert "Unlocated content" not in request["user_prompt"]
    assert "image_base64_list" not in request


def test_same_frozen_fact_and_clause_are_not_requested_twice(tmp_path, model):
    audit = original(tmp_path)
    items = [rule().model_dump(), {**rule().model_dump(), "id": "clause-2"}]
    agent = BaseAgent(audit, RuleInput(rule_id="frozen-rules", rules=items))
    assert agent.check(0).status == agent.check(1).status == "PASS"
    assert len(model[0]) == 1


def test_missing_source_never_sends_unlocated_content_to_a_model(tmp_path, model):
    audit = AuditInput(audit_id="v", file_url=str(tmp_path / "missing.pdf"), content="apparently complete")
    checked = evaluate_text_rule(audit, rule(), {})
    assert checked.status == "UNABLE"
    assert checked.checks_performed == 0
    assert model[0] == []


@pytest.mark.parametrize("kind, clause", [("image", "代表实际签字且盖章"), ("calculation", "计算结果"), ("text", "核对 S/O/D 的 AP 优先级")])
def test_graphic_and_standard_dependent_facts_have_no_native_text_fallback(tmp_path, model, kind, clause):
    checked = evaluate_text_rule(original(tmp_path, "已签字 已盖章 计算正确 AP通过"), rule(kind=kind, clause=clause), {})
    assert checked.status == "UNABLE"
    assert model[0] == []


@pytest.mark.parametrize("change", ["id", "quote", "extra_verdict", "missing_quote"])
def test_forged_or_missing_citations_cannot_produce_a_verdict(tmp_path, model, change):
    calls, behavior = model
    if change == "extra_verdict":
        behavior["replace"] = {"status": "PASS"}
    else:
        behavior["replace"] = {"citations": [] if change == "missing_quote" else [{"id": "missing" if change == "id" else "0", "quote": "invented quote"}]}
    checked = evaluate_text_rule(original(tmp_path), rule(), {})
    assert checked.status == ("UNABLE" if change == "missing_quote" else "ERR")
    assert checked.checks_performed == 0
    assert len(calls) == 1


@pytest.mark.parametrize("relation, scope", [("supports", "local"), ("supports", "all"), ("contradicts", "all"), ("contradicts", "absence"), ("contradicts", "cross_file")])
def test_partial_or_cross_file_scope_cannot_prove_complete_compliance_or_absence(tmp_path, model, relation, scope):
    calls, behavior = model
    behavior.update(relation=relation, scope=scope)
    audit = original(tmp_path, "无关内容。\n" * 1000 + "检验职责由质量工程师承担。\n" + "其他内容。\n" * 1000)
    checked = evaluate_text_rule(audit, rule(), {})
    assert checked.status == "UNABLE"
    assert len(calls) == 1
    payload = json.loads(calls[0]["user_prompt"])
    assert payload["coverage"] == "partial_observed_content"
    assert len(calls[0]["user_prompt"]) < 12000


def test_partial_scope_can_record_an_explicit_local_contradiction(tmp_path, model):
    model[1].update(relation="contradicts", scope="local")
    audit = original(tmp_path, "无关内容。\n" * 1000 + "检验职责明确不由质量工程师承担。\n" + "其他内容。\n" * 1000)
    checked = evaluate_text_rule(audit, rule(), {})
    assert checked.status == "NG"
    assert checked.evidence.sources
    assert json.loads(model[0][0]["user_prompt"])["coverage"] == "partial_observed_content"


def test_failed_text_request_is_not_retried_or_resent_by_another_clause(tmp_path, model):
    model[1]["error"] = TimeoutError("private endpoint must not escape")
    audit = original(tmp_path)
    cache = {}
    first = evaluate_text_rule(audit, rule(), cache)
    second = evaluate_text_rule(audit, rule(), cache)
    assert first.status == second.status == "ERR"
    assert "private endpoint" not in first.suggestions
    assert len(model[0]) == 1


def test_changed_frozen_source_cannot_reuse_a_text_result(tmp_path, model):
    from pathlib import Path

    audit = original(tmp_path)
    cache = {}
    assert evaluate_text_rule(audit, rule(), cache).status == "PASS"
    Path(audit.file_url).write_text("Changed original", encoding="utf-8")
    assert evaluate_text_rule(audit, rule(), cache).status == "ERR"
    assert len(model[0]) == 1


def test_image_semantics_accept_only_located_visual_observations(tmp_path, model):
    path = tmp_path / "original.png"
    Image.new("RGB", (20, 20), "white").save(path)
    audit = AuditInput(audit_id="frozen-image", file_url=str(path))
    fragment = {
        "kind": "visual",
        "text": "已确认该区域有签字内容。",
        "locator": {"kind": "image_region", "bbox": [1, 1, 10, 10], "file_version_id": audit.audit_id, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
    }
    checked = evaluate_located_text(audit, rule(kind="image", clause="签字区域有内容"), [fragment], complete=True, cache={})
    assert checked.status == "PASS"
    assert checked.evidence.used == "visual"
    assert model[0][0]["operation"] == "rule_text_evidence"


@pytest.mark.parametrize("change", ["version", "hash", "bounds", "kind", "native_text"])
def test_invalid_location_is_rejected_before_any_text_request(tmp_path, model, change):
    audit = original(tmp_path)
    text = "检验职责由质量工程师承担。"
    locator = {"kind": "text_line", "line": 1, "file_version_id": audit.audit_id, "source_sha256": hashlib.sha256(open(audit.file_url, "rb").read()).hexdigest()}
    fragment = {"text": text, "locator": locator}
    if change == "version":
        locator["file_version_id"] = "another-version"
    elif change == "hash":
        locator["source_sha256"] = "0" * 64
    elif change == "bounds":
        locator["line"] = 999
    elif change == "kind":
        locator["kind"] = "invented"
    else:
        fragment["text"] = "invented original content"
    assert evaluate_located_text(audit, rule(), [fragment], complete=True, cache={}).status == "ERR"
    assert model[0] == []
