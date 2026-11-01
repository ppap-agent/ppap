"""Real Product/PG/Worker/guest feedback loop; only model and S3 are controlled."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from worker import process_claimed

from .conftest import EXPLANATION, change_payload


def test_original_documents_confirmed_appendix_recompute_without_hiding_conflict(deep_feedback_env):
    env = deep_feedback_env
    client, round_id = env["client"], env["round_id"]
    path = f"/audit-rounds/{round_id}"
    initial = client.get("/rounds/" + round_id).json()
    assert [r["status"] for r in initial["findings"]] == ["UNABLE", "UNABLE", "PASS"]
    view = client.get(path + "/deep-feedback").json()
    assert len(view["clarifications"]) == 1
    payload = change_payload(view, env["uploaded"])
    applied = client.post(path + "/rule-changes", json=payload)
    assert applied.status_code == 200, applied.text
    receipt = applied.json()
    assert receipt["analysis_round_id"] != round_id
    assert client.post(path + "/rule-changes", json=payload).json() == receipt
    env["run_jobs"]()
    current = client.get("/rounds/" + receipt["analysis_round_id"]).json()
    assert [r["status"] for r in current["findings"]] == ["PASS", "NG", "PASS"]
    assert client.get("/rounds/" + round_id).json()["findings"] == initial["findings"]
    reopened = TestClient(create_app(env["settings"], auth_store=env["store"]))
    reopened.cookies.set(SESSION_COOKIE, env["session_cookie"])
    loaded = reopened.get(path + "/deep-feedback").json()
    assert loaded["current_round_id"] == receipt["analysis_round_id"]
    assert loaded["clarifications"] == []
    assert loaded["appendix"][0]["content"] == EXPLANATION
    exported = reopened.get(path + "/deep-feedback/export").json()
    assert exported["training_eligible"] is False and exported["human_gold"] is False
    assert exported["attempts"] and all(attempt["usage_source"] == "controlled" for attempt in exported["attempts"])
    assert "synthetic" in json.dumps(exported)
    assert client.get("/rules/" + env["rule"]["id"]).json()["body"] == env["rule"]["body"]


def test_manual_review_remains_separate_and_removing_appendix_restores_unknown(deep_feedback_env):
    env = deep_feedback_env
    client, source_id = env["client"], env["round_id"]
    path = f"/audit-rounds/{source_id}"
    original = client.get(f"/rounds/{source_id}").json()
    item_key = original["findings"][0]["item_key"]
    reviewed = client.put(
        f"/rounds/{source_id}/reviews",
        json={
            "item_key": item_key,
            "judgment": "manual-unsatisfied",
            "rationale": "构造测试中的人工记录",
            "next_step": "核对解释",
            "version": 0,
            "citations": [{"file_version_id": env["uploaded"]["PFD"]["id"], "location": "PFD!A2", "excerpt": "010"}],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["findings"][0]["status"] == "UNABLE"
    assert reviewed.json()["findings"][0]["review"]["judgment"] == "manual-unsatisfied"

    view = client.get(path + "/deep-feedback").json()
    applied = client.post(path + "/rule-changes", json=change_payload(view, env["uploaded"]))
    assert applied.status_code == 200, applied.text
    receipt = applied.json()
    env["run_jobs"]()
    clarified = client.get("/rounds/" + receipt["analysis_round_id"]).json()
    assert [finding["status"] for finding in clarified["findings"]] == ["PASS", "NG", "PASS"]
    assert all(finding["review"] is None for finding in clarified["findings"])
    assert client.get(f"/rounds/{source_id}").json()["findings"] == reviewed.json()["findings"]

    current_view = client.get(path + "/deep-feedback").json()
    removed = client.post(
        path + "/rule-changes",
        json={
            "operation_id": str(uuid4()),
            "expected_revision": current_view["revision"],
            "intent": "save_and_analyze",
            "remove_appendix_ids": [receipt["appendix"][0]["id"]],
            "confirmation_source": "synthetic",
        },
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["appendix"] == []
    env["run_jobs"]()
    restored = client.get("/rounds/" + removed.json()["analysis_round_id"]).json()
    assert [finding["status"] for finding in restored["findings"]] == ["UNABLE", "UNABLE", "PASS"]
    assert next(item for item in restored["inputs"] if item["role"] == "target")["rule"]["body"]["appendix"] == []
    assert all(finding["review"] is None for finding in restored["findings"])
    assert client.get("/rounds/" + receipt["analysis_round_id"]).json()["findings"] == clarified["findings"]
    loaded = client.get(path + "/deep-feedback").json()
    assert loaded["current_round_id"] == restored["id"]
    assert len(loaded["clarifications"]) == 1


def test_reconstructed_api_and_worker_finish_the_same_saved_operation(deep_feedback_env):
    env = deep_feedback_env
    original, source_id = env["client"], env["round_id"]
    path = f"/audit-rounds/{source_id}"
    view = original.get(path + "/deep-feedback").json()
    payload = change_payload(view, env["uploaded"])
    saved = original.post(path + "/rule-changes", json=payload)
    assert saved.status_code == 200, saved.text
    receipt = saved.json()
    store = build_product_store(env["settings"], objects=env["store"].objects, role="api")
    client = TestClient(create_app(env["settings"], auth_store=store))
    client.cookies.set(SESSION_COOKIE, env["session_cookie"])
    assert client.get(path + "/rule-changes/" + payload["operation_id"]).json() == receipt
    worker = build_product_store(env["settings"], objects=env["store"].objects, role="worker")
    while job := worker.claim_job("reconstructed-feedback-worker"):
        process_claimed(worker, job, "reconstructed-feedback-worker")
    current = client.get("/rounds/" + receipt["analysis_round_id"]).json()
    assert current["execution"] == "completed"
    assert [finding["status"] for finding in current["findings"]] == ["PASS", "NG", "PASS"]
    loaded = client.get(path + "/deep-feedback").json()
    assert len(loaded["operations"]) == 1 and len(loaded["results"]) == 2
    assert client.post(path + "/rule-changes", json=payload).json() == receipt


def test_shared_published_rule_keeps_target_specific_appendices_through_worker(deep_feedback_env):
    """Two CP files share one version; confirming CP2 leaves CP1 unresolved."""
    env = deep_feedback_env
    client = env["client"]
    raw = env["documents"]["CP"]["bytes"]
    created = client.post("/files", json={"original_filename": "CP2.xlsx", "content_type": "application/octet-stream", "byte_size": len(raw)})
    assert created.status_code == 201, created.text
    cp2 = created.json()
    path = f"/files/{cp2['file_id']}/versions/{cp2['id']}"
    assert client.put(path + "/blob", content=raw).status_code == 200
    assert client.post(path + "/finalize").status_code == 200
    draft = client.post("/drafts").json()["id"]
    target_ids = [env["uploaded"]["CP"]["id"], cp2["id"]]
    items = [{"file_version_id": identity, "role": "target", "rule_version_id": env["rule"]["id"]} for identity in target_ids]
    items.append({"file_version_id": env["uploaded"]["PFD"]["id"], "role": "reference"})
    assert client.put("/drafts/" + draft, json={"items": items, "analysis_mode": "deep"}).status_code == 200
    accepted = client.post("/drafts/" + draft + "/accept", json={"idempotency_key": str(uuid4())})
    assert accepted.status_code == 201, accepted.text
    root = accepted.json()["id"]
    env["run_jobs"]()

    def statuses(round_value):
        return {identity: {row["locator"]["rule_item_id"]: row["status"] for row in round_value["findings"] if row["locator"]["file_version_id"] == identity} for identity in target_ids}

    initial = client.get("/rounds/" + root).json()
    unresolved = {"R1": "UNABLE", "R2": "UNABLE", "R3": "PASS"}
    assert statuses(initial) == dict.fromkeys(target_ids, unresolved)
    view = client.get(f"/audit-rounds/{root}/deep-feedback").json()
    question = next(item for item in view["clarifications"] if item["document_ids"] == [cp2["id"]])
    payload = {
        "operation_id": str(uuid4()),
        "expected_revision": view["revision"],
        "question_id": question["id"],
        "expected_question_fingerprint": question["fingerprint"],
        "candidate_id": question["candidates"][0]["id"],
        "kind": "interpretation",
        "content": question["candidates"][0]["explanation"],
        "scope": question["scope"],
        "intent": "save_and_analyze",
        "confirmation_source": "synthetic",
    }
    saved = client.post(f"/audit-rounds/{root}/rule-changes", json=payload)
    assert saved.status_code == 200, saved.text
    env["run_jobs"]()
    current = client.get("/rounds/" + saved.json()["analysis_round_id"]).json()
    assert current["execution"] == "completed"
    assert statuses(current) == {target_ids[0]: unresolved, cp2["id"]: {"R1": "PASS", "R2": "NG", "R3": "PASS"}}
    targets = [item for item in current["inputs"] if item["role"] == "target"]
    assert {item["rule"]["id"] for item in targets} == {env["rule"]["id"]}
    bodies = {item["file_version_id"]: item["rule"]["body"] for item in targets}
    assert bodies[target_ids[0]]["appendix"] == []
    assert bodies[cp2["id"]]["appendix"][0]["scope"]["document_ids"] == [cp2["id"]]
    assert statuses(client.get("/rounds/" + root).json()) == statuses(initial)


def compiled_change(question, revision, *, intent="save_and_analyze", kind="interpretation"):
    return {
        "operation_id": str(uuid4()),
        "expected_revision": revision,
        "question_id": question["id"],
        "expected_question_fingerprint": question["fingerprint"],
        "candidate_id": "literal",
        "kind": kind,
        "content": EXPLANATION,
        "scope": question["scope"],
        "intent": intent,
        "confirmation_source": "synthetic",
    }


@pytest.mark.parametrize("deep_feedback_env", ["compiled_candidate_model"], indirect=True)
def test_compiled_candidate_literal_save_then_analyze_keeps_other_target_and_history(deep_feedback_env):
    env = deep_feedback_env
    client = env["client"]
    raw = env["documents"]["CP"]["bytes"]
    created = client.post("/files", json={"original_filename": "CP2.xlsx", "content_type": "application/octet-stream", "byte_size": len(raw)})
    assert created.status_code == 201, created.text
    cp2 = created.json()
    upload_path = f"/files/{cp2['file_id']}/versions/{cp2['id']}"
    assert client.put(upload_path + "/blob", content=raw).status_code == 200
    assert client.post(upload_path + "/finalize").status_code == 200
    target_ids = [env["uploaded"]["CP"]["id"], cp2["id"]]
    draft = client.post("/drafts").json()["id"]
    items = [{"file_version_id": target, "role": "target", "rule_version_id": env["rule"]["id"]} for target in target_ids]
    items.append({"file_version_id": env["uploaded"]["PFD"]["id"], "role": "reference"})
    assert client.put("/drafts/" + draft, json={"items": items, "analysis_mode": "deep"}).status_code == 200
    accepted = client.post("/drafts/" + draft + "/accept", json={"idempotency_key": str(uuid4())})
    assert accepted.status_code == 201, accepted.text
    root = accepted.json()["id"]
    path = f"/audit-rounds/{root}"
    env["run_jobs"]()

    def statuses(value):
        return {target: {row["locator"]["rule_item_id"]: row["status"] for row in value["findings"] if row["locator"]["file_version_id"] == target} for target in target_ids}

    initial = client.get("/rounds/" + root).json()
    unresolved = {"R1": "UNABLE", "R2": "NG", "R3": "PASS"}
    assert statuses(initial) == dict.fromkeys(target_ids, unresolved)
    view = client.get(path + "/deep-feedback").json()
    assert len(view["clarifications"]) == 2
    questions = {question["scope"]["rule_targets"][0]["document_id"]: question for question in view["clarifications"]}
    question = questions[cp2["id"]]
    assert question["scope"] == {
        "document_ids": sorted([env["uploaded"]["PFD"]["id"], cp2["id"]]),
        "rule_ids": ["R1"],
        "rule_targets": [{"document_id": cp2["id"], "rule_id": "R1"}],
    }
    assert {(source["document_id"], source["locator"]["sheet"], source["locator"]["cell"], source["excerpt"]) for source in question["sources"]} == {
        (env["uploaded"]["PFD"]["id"], "PFD", "A1", "工序编号"),
        (cp2["id"], "CP", "A1", "作业单元编号"),
    }
    for finding in initial["findings"]:
        semantic = finding["locator"]["semantic"]
        clause = next(rule for rule in env["rule"]["body"]["rules"] if rule["id"] == finding["locator"]["rule_item_id"])
        assert semantic["rule_text"] == clause["rule_to_check"]
        assert semantic["rule_source_file_version_id"] == env["rule"]["source_file_version_id"]
    assert env["model"].semantic_answers == []
    payload = compiled_change(question, view["revision"], intent="save_only")
    invalid = client.post(path + "/rule-changes", json={**payload, "content": EXPLANATION + " 不同的含义。"})
    assert invalid.status_code == 409 and invalid.json()["error"] == "feedback_candidate_changed"
    saved = client.post(path + "/rule-changes", json=payload)
    assert saved.status_code == 200, saved.text
    receipt = saved.json()
    assert receipt["analysis_round_id"] is None
    assert receipt["appendix"][0]["content"] == EXPLANATION
    assert receipt["appendix"][0]["confirmation_source"] == "synthetic"
    assert client.get("/rounds/" + root).json()["findings"] == initial["findings"]
    loaded = client.get(path + "/deep-feedback").json()
    assert loaded["input_changed"] is True and loaded["current_round_id"] == root
    analyzed = client.post(path + "/rule-changes", json={"operation_id": str(uuid4()), "expected_revision": loaded["revision"], "intent": "save_and_analyze", "confirmation_source": "synthetic"})
    assert analyzed.status_code == 200, analyzed.text
    result_id = analyzed.json()["analysis_round_id"]
    assert result_id != root
    env["run_jobs"]()
    current = client.get("/rounds/" + result_id).json()
    assert statuses(current) == {target_ids[0]: unresolved, cp2["id"]: {"R1": "PASS", "R2": "NG", "R3": "PASS"}}
    assert {(answer["audit_id"], answer["rule_id"], answer["value"]) for answer in env["model"].semantic_answers} == {(cp2["id"], "R1", "true")}
    assert len(env["model"].semantic_answers) == 2
    bodies = {item["file_version_id"]: item["rule"]["body"] for item in current["inputs"] if item["role"] == "target"}
    assert bodies[target_ids[0]]["appendix"] == []
    assert bodies[cp2["id"]]["appendix"][0]["content"] == EXPLANATION
    assert bodies[cp2["id"]]["appendix"][0]["scope"] == question["scope"]
    historical = client.get("/rounds/" + root).json()
    assert historical["findings"] == initial["findings"] and historical["inputs"] == initial["inputs"]
    reopened = TestClient(create_app(env["settings"], auth_store=build_product_store(env["settings"], objects=env["store"].objects, role="api")))
    reopened.cookies.set(SESSION_COOKIE, env["session_cookie"])
    remaining = reopened.get(path + "/deep-feedback").json()["clarifications"]
    assert [item["id"] for item in remaining] == [questions[target_ids[0]]["id"]]
    assert client.get("/rules/" + env["rule"]["id"]).json()["body"] == env["rule"]["body"]
    exported = reopened.get(path + "/deep-feedback/export").json()
    assert exported["training_eligible"] is False and exported["human_gold"] is False


@pytest.mark.parametrize("deep_feedback_env", ["compiled_candidate_model"], indirect=True)
def test_compiled_candidate_standard_edit_recompiles_frozen_appendix_through_mcp(deep_feedback_env):
    env = deep_feedback_env
    standard_change = env["model"].standard_change
    client, root = env["client"], env["round_id"]
    path = f"/audit-rounds/{root}"
    initial = client.get("/rounds/" + root).json()
    assert [row["status"] for row in initial["findings"]] == ["UNABLE", "NG", "PASS"]
    view = client.get(path + "/deep-feedback").json()
    question = view["clarifications"][0]
    change = compiled_change(question, view["revision"], kind="standard_change")
    invalid = client.post(path + "/rule-changes", json={**change, "content": standard_change})
    assert invalid.status_code == 409 and invalid.json()["error"] == "feedback_candidate_changed"
    token = env["mcp_access"]()
    args = {"name": "apply_rule_change", "arguments": {"round_id": root, "change": change}}
    pending = env["mcp_rpc"](token, "tools/call", args)["result"]
    assert pending["resultType"] == "input_required"
    key = next(iter(pending["inputRequests"]))
    applied = env["mcp_rpc"](
        token,
        "tools/call",
        {**args, "requestState": pending["requestState"], "inputResponses": {key: {"action": "accept", "content": {"content": standard_change}}}},
    )
    assert "error" not in applied, applied
    receipt = applied["result"]["structuredContent"]
    entry = receipt["appendix"][0]
    assert entry["kind"] == "standard_change" and entry["content"] == standard_change
    assert "candidate_id" not in entry and entry["confirmation_source"] == "synthetic"
    historical = client.get("/rounds/" + root).json()
    assert historical["findings"] == initial["findings"] and historical["inputs"] == initial["inputs"]
    env["run_jobs"]()
    current = client.get("/rounds/" + receipt["analysis_round_id"]).json()
    assert [row["status"] for row in current["findings"]] == ["NG", "NG", "PASS"]
    assert env["model"].semantic_answers == []
    compiled = next(item for item in env["model"].compilations if item["appendix"])
    assert compiled["appendix"][0]["content"] == standard_change
    assert "candidate_id" not in compiled["appendix"][0]
    assert compiled["require"] == {"kind": "value_test", "field": "classification", "op": "eq", "value": "SC"}
    assert client.get(path + "/deep-feedback").json()["clarifications"] == []
    frozen = next(item for item in current["inputs"] if item["role"] == "target")["rule"]["body"]
    assert frozen["rules"] == env["rule"]["body"]["rules"]
    assert frozen["appendix"][0]["content"] == standard_change
    assert client.get("/rounds/" + root).json()["findings"] == initial["findings"]
