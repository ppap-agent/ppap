"""Real PG application contract; controlled coverage isolates user operations."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import uuid4

import pytest
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb


def _adopt_confirmed_appendix(store, round_id, appendix):
    """Explicit tenant adoption of exact confirmed bytes, never a freeze bypass."""
    principal = store.principal_for_fixture("alice")
    releases = SuiteReleaseStore(store.settings, store.objects, role="api")
    current = store.read_deep_feedback(principal, round_id)["current_round_id"]
    seen = {}
    for item in store.get_round(current)["inputs"]:
        if item["role"] != "target":
            continue
        body = item["rule"]["body"]
        clauses = {rule["id"] for rule in body["rules"]}
        selected = []
        for part in appendix:
            targets = [target for target in part["scope"]["rule_targets"] if target["document_id"] == item["file_version_id"] and target["rule_id"] in clauses]
            if targets:
                selected.append({**part, "scope": {**part["scope"], "rule_ids": list(dict.fromkeys(target["rule_id"] for target in targets)), "rule_targets": targets}})
        key = body["suite_key"]
        if key in seen:
            assert seen[key] == selected, "Different per-target appendices require separate Suites"
            continue
        seen[key] = selected
        active = releases.active(principal, key)
        candidate = releases.save_candidate(principal, active["current_revision_id"], {"appendix.json": json.dumps(selected, ensure_ascii=False)})
        releases.publish(principal, key, candidate["id"], active["revision"], str(uuid4()), force_unverified=True)


def _prepare_analysis(store, client, round_id, payload):
    # Confirmation first fixes timestamps/identity. Starting analysis is a fresh
    # explicit operation after adopting the matching immutable Suite revision.
    response = client.post(f"/audit-rounds/{round_id}/rule-changes", json={**payload, "intent": "save_only"})
    assert response.status_code == 200, response.text
    saved = response.json()
    _adopt_confirmed_appendix(store, round_id, saved["appendix"])
    payload.clear()
    payload.update(operation_id=str(uuid4()), expected_revision=saved["revision"], intent="save_and_analyze")
    return saved


@pytest.fixture
def feedback_app(auth_store_factory, monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test-only")
    monkeypatch.setenv("PPAP_BUILD_SHA", "a" * 40)
    settings = Settings(usage_charge_enabled=False)
    store = auth_store_factory(settings)
    store.seed_isolated_dev()
    client = TestClient(create_app(settings, lambda _: {"state": "ok"}, lambda _: {"state": "ok"}, store))
    assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
    version = client.post("/files", json={"original_filename": "cp.xlsx", "content_type": "application/octet-stream", "byte_size": 4}).json()
    base = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.put(base + "/blob", content=b"test").status_code == 200
    assert client.post(base + "/finalize").status_code == 200
    published = store.publish_rule(
        store.principal_for_fixture("alice"),
        "Feedback rule",
        {"kind": "engine", "type": "控制计划", "rules": [{"id": "R1", "category": "工序", "rule_to_check": "工序编号一致", "type_of_rule": "table"}]},
        version["id"],
    )
    draft = client.post("/drafts").json()["id"]
    response = client.put(f"/drafts/{draft}", json={"analysis_mode": "deep", "items": [{"file_version_id": version["id"], "role": "target", "rule_version_id": published["id"]}]})
    assert response.status_code == 200, response.text
    response = client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "feedback-source"})
    assert response.status_code == 201, response.text
    round_id = response.json()["id"]
    rule_id = response.json()["inputs"][0]["rule"]["body"]["rules"][0]["id"]
    question = {
        "id": "question-1",
        "input_id": "input-1",
        "kind": "interpretation",
        "question": "编号列含义？",
        "document_ids": [version["id"]],
        "rule_ids": [rule_id],
        "candidates": [{"id": "candidate-1", "label": "工序编号", "explanation": "作业单元编号表示工序编号"}],
        "sources": [],
        "scope": {"document_ids": [version["id"]], "rule_ids": [rule_id]},
    }
    with store._conn() as conn:
        conn.execute("UPDATE product.audit_rounds SET coverage=%s WHERE id=%s", (Jsonb({"clarifications": [question]}), round_id))
    payload = {
        "operation_id": str(uuid4()),
        "expected_revision": 0,
        "kind": "interpretation",
        "question_id": question["id"],
        "candidate_id": "candidate-1",
        "content": question["candidates"][0]["explanation"],
        "scope": question["scope"],
        "intent": "save_only",
        "confirmation_source": "synthetic",
    }
    payload["expected_question_fingerprint"] = client.get(f"/audit-rounds/{round_id}/deep-feedback").json()["clarifications"][0]["fingerprint"]
    return store, client, round_id, payload


def test_save_only_survives_new_app_and_replay_without_starting_analysis(feedback_app):
    store, client, rid, payload = feedback_app
    before = client.get(f"/audit-rounds/{rid}/deep-feedback").json()
    response = client.post(f"/audit-rounds/{rid}/rule-changes", json=payload)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["analysis_round_id"] is None
    assert receipt["revision"] == 1
    assert client.post(f"/audit-rounds/{rid}/rule-changes", json=payload).json() == receipt
    reopened = TestClient(create_app(store.settings, lambda _: {"state": "ok"}, lambda _: {"state": "ok"}, store))
    reopened.cookies.update(client.cookies)
    view = reopened.get(f"/audit-rounds/{rid}/deep-feedback").json()
    assert view["input_changed"] is True
    assert view["clarifications"] == []
    assert len(view["results"]) == len(before["results"]) == 1
    assert view["appendix"][0]["confirmation_source"] == "synthetic"
    assert reopened.get(f"/audit-rounds/{rid}/rule-changes/{payload['operation_id']}").json() == receipt
    exported = reopened.get(f"/audit-rounds/{rid}/deep-feedback/export").json()
    assert exported["human_gold"] is exported["training_eligible"] is False
    assert "test-only" not in str(exported)


def test_adopted_feedback_fixes_new_input_and_reuses_original_receipt(feedback_app):
    store, client, rid, payload = feedback_app
    original = store.get_round(rid)
    _prepare_analysis(store, client, rid, payload)
    response = client.post(f"/audit-rounds/{rid}/rule-changes", json=payload)
    assert response.status_code == 200, response.text
    receipt = response.json()
    new_id = receipt["analysis_round_id"]
    assert new_id != rid
    new = store.get_round(new_id)
    assert new["execution"] == "queued"
    assert new["analysis_mode"] == "deep"
    assert new["predecessor_id"] == rid
    assert new["inputs"][0]["rule"]["body"]["appendix"] == receipt["appendix"]
    snapshot = SuiteStore(store.settings, store.objects, role="api").read_snapshot(new_id, store.principal_for_fixture("alice").tenant_id)
    assert snapshot.to_dict()["targets"][0]["appendix"] == receipt["appendix"]
    assert snapshot.targets[0].verification == "unverified"
    assert new["executor"] == "ppap-checker-core"
    assert store.get_round(rid) == original
    for _ in range(3):
        assert client.post(f"/audit-rounds/{rid}/rule-changes", json=payload).json() == receipt
    view = client.get(f"/audit-rounds/{rid}/deep-feedback").json()
    assert view["current_round_id"] == new_id
    assert len(view["results"]) == 2
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE audit_round_id=%s AND kind='audit'", (new_id,)).fetchone()[0] == 1


@pytest.mark.parametrize(
    "change,error",
    [
        (lambda p: p.update(content="Changed"), "feedback_candidate_changed"),
        (lambda p: p.update(expected_revision=1), "feedback_revision_conflict"),
        (lambda p: p["scope"].update(document_ids=[str(uuid4())]), "feedback_scope_conflict"),
        (lambda p: p.update(question_id="stale"), "feedback_question_expired"),
    ],
)
def test_rejected_change_is_not_saved(feedback_app, change, error):
    _store, client, rid, payload = feedback_app
    change(payload)
    result = client.post(f"/audit-rounds/{rid}/rule-changes", json=payload)
    assert result.status_code == 409, result.text
    assert result.json()["error"] == error
    assert client.get(f"/audit-rounds/{rid}/deep-feedback").json()["revision"] == 0


def test_same_id_different_payload_and_concurrent_edits_conflict(feedback_app):
    store, client, rid, payload = feedback_app
    principal = store.principal_for_fixture("alice")
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(lambda _: store.apply_rule_change(principal, rid, payload), range(2)))
    assert replies[0] == replies[1]
    changed = {**payload, "intent": "save_and_analyze"}
    assert client.post(f"/audit-rounds/{rid}/rule-changes", json=changed).json()["error"] == "idempotency_conflict"
    competing = {**deepcopy(payload), "operation_id": str(uuid4())}
    assert client.post(f"/audit-rounds/{rid}/rule-changes", json=competing).json()["error"] == "feedback_revision_conflict"


def test_cross_tenant_cannot_read_or_apply_and_missing_receipt_stays_unknown(feedback_app):
    _store, client, rid, payload = feedback_app
    assert client.get(f"/audit-rounds/{rid}/rule-changes/{payload['operation_id']}").json() is None
    client.cookies.clear()
    assert client.post("/isolated-dev/sessions", json={"subject": "carol"}).status_code == 200
    assert client.get(f"/audit-rounds/{rid}/deep-feedback").status_code == 403
    assert client.post(f"/audit-rounds/{rid}/rule-changes", json=payload).status_code == 403


def test_enqueue_failure_preserves_confirmation_without_saving_analysis_operation(feedback_app, monkeypatch):
    store, client, rid, payload = feedback_app
    _prepare_analysis(store, client, rid, payload)

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected enqueue failure")

    monkeypatch.setattr(store, "_accept_locked", fail)
    with pytest.raises(RuntimeError, match="injected"):
        client.post(f"/audit-rounds/{rid}/rule-changes", json=payload)
    view = client.get(f"/audit-rounds/{rid}/deep-feedback").json()
    assert view["revision"] == 1
    assert len(view["appendix"]) == 1
    assert store.read_rule_change(store.principal_for_fixture("alice"), rid, payload["operation_id"]) is None


def test_saved_explanation_can_start_later_and_removal_is_a_fresh_explicit_input(feedback_app):
    store, client, rid, payload = feedback_app
    saved = client.post(f"/audit-rounds/{rid}/rule-changes", json=payload).json()
    _adopt_confirmed_appendix(store, rid, saved["appendix"])
    start = {"operation_id": str(uuid4()), "expected_revision": 1, "intent": "save_and_analyze"}
    result = client.post(f"/audit-rounds/{rid}/rule-changes", json=start)
    assert result.status_code == 200, result.text
    second = result.json()["analysis_round_id"]
    assert store.get_round(second)["inputs"][0]["rule"]["body"]["appendix"] == saved["appendix"]
    remove = {"operation_id": str(uuid4()), "expected_revision": 2, "intent": "save_only", "remove_appendix_ids": [saved["appendix"][0]["id"]]}
    removed = client.post(f"/audit-rounds/{rid}/rule-changes", json=remove)
    assert removed.status_code == 200, removed.text
    assert removed.json()["appendix"] == []
    _adopt_confirmed_appendix(store, rid, [])
    response = client.post(f"/audit-rounds/{rid}/rule-changes", json={"operation_id": str(uuid4()), "expected_revision": 3, "intent": "save_and_analyze"})
    assert response.status_code == 200, response.text
    assert response.json()["appendix"] == []
    third = response.json()["analysis_round_id"]
    assert store.get_round(third)["inputs"][0]["rule"]["body"]["appendix"] == []
    assert store.get_round(second)["inputs"][0]["rule"]["body"]["appendix"] == saved["appendix"]


def test_revoking_new_calculation_access_blocks_feedback_and_source_reads(feedback_app):
    store, client, rid, payload = feedback_app
    _prepare_analysis(store, client, rid, payload)
    new_id = client.post(f"/audit-rounds/{rid}/rule-changes", json=payload).json()["analysis_round_id"]
    principal = store.principal_for_fixture("alice")
    file = store.get_round(new_id)["inputs"][0]
    with store._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (new_id, principal.user_id))
    assert client.get(f"/audit-rounds/{rid}/deep-feedback").status_code == 403
    with pytest.raises(KeyError):
        store.read_file_version(principal, file["file_id"], file["file_version_id"], round_id=new_id)
    assert new_id not in {item["id"] for item in store.list_rounds(principal)}


def test_failure_after_admission_rolls_back_round_job_and_receipt(feedback_app, monkeypatch):
    store, client, rid, payload = feedback_app
    _prepare_analysis(store, client, rid, payload)
    accept = store._accept_locked

    def fail_after_admission(*args, **kwargs):
        accept(*args, **kwargs)
        raise RuntimeError("injected after admission")

    monkeypatch.setattr(store, "_accept_locked", fail_after_admission)
    with pytest.raises(RuntimeError, match="injected after admission"):
        client.post(f"/audit-rounds/{rid}/rule-changes", json=payload)
    view = client.get(f"/audit-rounds/{rid}/deep-feedback").json()
    assert view["revision"] == 1
    assert view["results"][0]["round_id"] == rid
    assert len(view["appendix"]) == 1
    assert store.read_rule_change(store.principal_for_fixture("alice"), rid, payload["operation_id"]) is None
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.audit_rounds WHERE predecessor_id=%s", (rid,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.checker_execution_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='audit'").fetchone()[0] == 1


def test_export_keeps_unknown_usage_and_excludes_arbitrary_runtime_fields(feedback_app):
    store, client, rid, _payload = feedback_app
    claimed = store.claim_job("contract-worker")
    assert claimed is not None
    assert store.complete_job(
        claimed,
        "contract-worker",
        {"findings": [], "coverage": {}, "usage": {"input_tokens": 7, "output_tokens": None, "secret": "fake-provider-secret", "private_reasoning": "fake-hidden-thought"}},
        "succeeded",
    )
    exported = client.get(f"/audit-rounds/{rid}/deep-feedback/export").json()
    assert exported["attempts"][0]["usage"] == {"input_tokens": 7}
    assert exported["attempts"][0]["usage_known"] is False
    assert "fake-provider-secret" not in str(exported)
    assert "fake-hidden-thought" not in str(exported)


def test_standard_core_freezes_the_explicitly_published_appendix(feedback_app):
    store, client, rid, payload = feedback_app
    source = store.get_round(rid)["inputs"][0]
    body = deepcopy(source["rule"]["body"])
    body["appendix"] = [{"id": "confirmed", "kind": "interpretation", "content": payload["content"], "scope": payload["scope"]}]
    published = store.publish_rule(store.principal_for_fixture("alice"), "Explicit appendix", body, source["file_version_id"])
    draft = client.post("/drafts").json()["id"]
    client.put(f"/drafts/{draft}", json={"analysis_mode": "standard", "items": [{"file_version_id": source["file_version_id"], "role": "target", "rule_version_id": published["id"]}]})
    result = client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "appendix-standard"})
    assert result.status_code == 201, result.text
    accepted = result.json()
    assert accepted["executor"] == "ppap-checker-core"
    assert accepted["inputs"][0]["rule"]["body"]["appendix"] == published["body"]["appendix"]
    snapshot = SuiteStore(store.settings, store.objects, role="api").read_snapshot(accepted["id"], store.principal_for_fixture("alice").tenant_id)
    assert snapshot.to_dict()["targets"][0]["appendix"] == published["body"]["appendix"]


def test_production_api_role_can_save_enqueue_and_read_back_without_migrator_rights(feedback_app):
    source_store, client, rid, payload = feedback_app
    api = build_product_store(source_store.settings, objects=source_store.objects, role="api")
    principal = source_store.principal_for_fixture("alice")
    _prepare_analysis(source_store, client, rid, payload)
    receipt = api.apply_rule_change(principal, rid, payload)
    assert receipt["analysis_round_id"]
    assert api.read_rule_change(principal, rid, payload["operation_id"]) == receipt
    assert api.read_deep_feedback(principal, rid)["current_round_id"] == receipt["analysis_round_id"]
    assert api.export_deep_feedback(principal, rid)["training_eligible"] is False


@pytest.mark.parametrize("field", ["question", "input_id", "candidates", "sources", "scope"])
def test_custom_answer_is_bound_to_the_exact_question_evidence_and_candidates(feedback_app, field):
    store, client, rid, payload = feedback_app
    payload.pop("candidate_id")
    payload["content"] = "这是用户填写的新解释"
    old = client.get(f"/audit-rounds/{rid}/deep-feedback").json()
    with store._conn() as conn:
        coverage = conn.execute("SELECT coverage FROM product.audit_rounds WHERE id=%s", (rid,)).fetchone()[0]
        question = coverage["clarifications"][0]
        if field == "candidates":
            question["candidates"][0]["explanation"] = "不同候选含义"
        elif field == "sources":
            question["sources"] = [{"document_id": payload["scope"]["document_ids"][0], "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "A1"}, "excerpt": "新的原件依据"}]
        elif field == "scope":
            question["scope"]["rule_ids"] = ["R1", "R2"]
        else:
            question[field] += " changed"
        conn.execute("UPDATE product.audit_rounds SET coverage=%s WHERE id=%s", (Jsonb(coverage), rid))
    current = client.get(f"/audit-rounds/{rid}/deep-feedback").json()
    assert old["revision"] == current["revision"] == 0
    assert old["clarifications"][0]["id"] == current["clarifications"][0]["id"]
    assert old["clarifications"][0]["fingerprint"] != current["clarifications"][0]["fingerprint"]
    response = client.post(f"/audit-rounds/{rid}/rule-changes", json=payload)
    assert response.status_code == 409
    assert response.json()["error"] == "feedback_question_changed"
    assert client.get(f"/audit-rounds/{rid}/deep-feedback").json()["revision"] == 0


def test_question_confirmation_without_displayed_fingerprint_fails_closed(feedback_app):
    _store, client, rid, payload = feedback_app
    payload.pop("expected_question_fingerprint")
    response = client.post(f"/audit-rounds/{rid}/rule-changes", json=payload)
    assert response.status_code == 409
    assert response.json()["error"] == "feedback_question_changed"


@pytest.mark.parametrize("target_scope", ["legacy", "precise", "precise_derived", "outside_question", "nonexistent"])
def test_mapping_scope_is_the_interpreted_file_not_only_the_rule_target(feedback_app, target_scope):
    store, client, rid, payload = feedback_app
    original = store.get_round(rid)["inputs"][0]
    second = client.post("/files", json={"original_filename": "other.xlsx", "content_type": "application/octet-stream", "byte_size": 4}).json()
    base = f"/files/{second['file_id']}/versions/{second['id']}"
    assert client.put(base + "/blob", content=b"test").status_code == 200
    assert client.post(base + "/finalize").status_code == 200
    second_rule = store.publish_rule(store.principal_for_fixture("alice"), "Other target rule", original["rule"]["body"], second["id"])
    draft = client.post("/drafts").json()["id"]
    files = [
        {"file_version_id": original["file_version_id"], "role": "target", "rule_version_id": original["rule_version_id"]},
        {"file_version_id": second["id"], "role": "target", "rule_version_id": second_rule["id"]},
    ]
    assert client.put(f"/drafts/{draft}", json={"analysis_mode": "deep", "items": files}).status_code == 200
    accepted = client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "two-targets"})
    assert accepted.status_code == 201, accepted.text
    source_id = accepted.json()["id"]
    with store._conn() as conn:
        coverage = conn.execute("SELECT coverage FROM product.audit_rounds WHERE id=%s", (rid,)).fetchone()[0]
        if target_scope != "legacy":
            coverage["clarifications"][0]["scope"]["rule_targets"] = [{"document_id": original["file_version_id"], "rule_id": "R1"}]
        conn.execute("UPDATE product.audit_rounds SET coverage=%s WHERE id=%s", (Jsonb(coverage), source_id))
    payload["intent"] = "save_and_analyze"
    if target_scope not in {"legacy", "precise_derived"}:
        target_id = original["file_version_id"] if target_scope == "precise" else second["id"] if target_scope == "outside_question" else str(uuid4())
        payload["scope"]["rule_targets"] = [{"document_id": target_id, "rule_id": "R1"}]
    if target_scope == "nonexistent":
        for key in ("question_id", "candidate_id"):
            payload.pop(key)
    payload["expected_question_fingerprint"] = client.get(f"/audit-rounds/{source_id}/deep-feedback").json()["clarifications"][0]["fingerprint"]
    if target_scope in {"legacy", "precise", "precise_derived"}:
        _prepare_analysis(store, client, source_id, payload)
    response = client.post(f"/audit-rounds/{source_id}/rule-changes", json=payload)
    if target_scope in {"outside_question", "nonexistent"}:
        assert response.status_code == 409, response.text
        assert response.json()["error"] == "feedback_scope_conflict"
        assert client.get(f"/audit-rounds/{source_id}/deep-feedback").json()["revision"] == 0
        return
    assert response.status_code == 200, response.text
    new = store.get_round(response.json()["analysis_round_id"])
    # Both programs may reference the interpreted source. The source scope must
    # remain CP; it must never silently expand to the second file.
    assert len(new["inputs"]) == 2
    for item in new["inputs"]:
        if target_scope in {"precise", "precise_derived"} and item["file_version_id"] == second["id"]:
            assert item["rule"]["body"]["appendix"] == []
            continue
        assert item["rule"]["body"]["appendix"][0]["scope"]["document_ids"] == [original["file_version_id"]]
        assert item["rule"]["body"]["appendix"][0]["scope"]["rule_targets"] == [{"document_id": item["file_version_id"], "rule_id": "R1"}]


def test_readonly_workspace_grant_stays_readonly_on_derived_calculation(feedback_app):
    store, client, rid, payload = feedback_app
    alice, bob = (store.principal_for_fixture(name) for name in ("alice", "bob"))
    with store._conn() as conn:
        # This is an authorization fixture, not an intake/Task workflow proof.
        # Grant-required roots use the same resource construction as real Tasks.
        conn.execute(
            "UPDATE product.audit_drafts SET task_fingerprint='auth-fixture',task_day=current_date,task_sequence=1 WHERE id=(SELECT source_draft_id FROM product.audit_rounds WHERE id=%s)", (rid,)
        )
        conn.execute("INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES (%s,%s,'read') ON CONFLICT DO NOTHING", (rid, bob.user_id))
    _prepare_analysis(store, client, rid, payload)
    receipt = store.apply_rule_change(alice, rid, payload)
    derived = receipt["analysis_round_id"]
    assert store.resource(derived).grant_required
    assert store.grants(derived, bob.user_id) == {"read"}
    assert store.read_deep_feedback(bob, rid)["current_round_id"] == derived
    assert store.read_rule_change(bob, rid, payload["operation_id"]) == receipt
    with store._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (derived, bob.user_id))
    with pytest.raises(KeyError):
        store.read_rule_change(bob, rid, payload["operation_id"])
    with pytest.raises(KeyError):
        store.read_deep_feedback(bob, rid)
    with store._conn() as conn:
        conn.execute("INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES (%s,%s,'read')", (derived, bob.user_id))
    with pytest.raises(KeyError):
        store.apply_rule_change(bob, rid, {"operation_id": str(uuid4()), "expected_revision": 2, "intent": "save_and_analyze"})
    with store._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (rid, bob.user_id))
    with pytest.raises(KeyError):
        store.read_deep_feedback(bob, rid)
    with pytest.raises(KeyError):
        store.read_rule_change(bob, rid, payload["operation_id"])


def test_late_old_worker_finishes_only_its_original_round(feedback_app):
    store, client, rid, payload = feedback_app
    original = store.claim_job("old-worker")
    assert original.round_id == rid
    _prepare_analysis(store, client, rid, payload)
    receipt = client.post(f"/audit-rounds/{rid}/rule-changes", json=payload).json()
    derived = receipt["analysis_round_id"]
    fresh = store.claim_job("new-worker")
    assert fresh.round_id == derived
    # Only worker completion/attempt publication is under test here; actual
    # parsing and findings are owned by the separate real-guest integration.
    assert store.complete_job(fresh, "new-worker", {"findings": [], "coverage": {"clarifications": [], "marker": "new"}}, "succeeded")
    assert store.complete_job(original, "old-worker", {"findings": [], "coverage": {"clarifications": [], "marker": "old"}}, "succeeded")
    view = client.get(f"/audit-rounds/{rid}/deep-feedback").json()
    assert view["current_round_id"] == derived
    assert [(result["round_id"], result["current"], result["coverage"]["marker"]) for result in view["results"]] == [(rid, False, "old"), (derived, True, "new")]
    assert client.get(f"/audit-rounds/{rid}/rule-changes/{payload['operation_id']}").json() == receipt
    assert store.complete_job(original, "old-worker", {"coverage": {"marker": "must not publish twice"}}, "succeeded") is False
    assert store.get_round(rid)["coverage"]["marker"] == "old"


def test_lost_success_reply_can_be_read_after_application_reconstruction(feedback_app, monkeypatch):
    store, client, rid, payload = feedback_app
    _prepare_analysis(store, client, rid, payload)
    apply = store.apply_rule_change

    def commit_then_lose_reply(*args, **kwargs):
        apply(*args, **kwargs)
        raise RuntimeError("injected lost success reply")

    monkeypatch.setattr(store, "apply_rule_change", commit_then_lose_reply)
    with pytest.raises(RuntimeError, match="injected lost success reply"):
        client.post(f"/audit-rounds/{rid}/rule-changes", json=payload)
    reconstructed = build_product_store(store.settings, objects=store.objects, role="api")
    principal = store.principal_for_fixture("alice")
    receipt = reconstructed.read_rule_change(principal, rid, payload["operation_id"])
    assert receipt["analysis_round_id"]
    assert reconstructed.apply_rule_change(principal, rid, payload) == receipt
    view = reconstructed.read_deep_feedback(principal, rid)
    assert len(view["operations"]) == 2 and len(view["results"]) == 2
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE audit_round_id=%s AND kind='audit'", (receipt["analysis_round_id"],)).fetchone()[0] == 1


def test_distinct_concurrent_operations_cannot_both_edit_the_same_revision(feedback_app):
    store, client, rid, payload = feedback_app
    principal = store.principal_for_fixture("alice")
    start = Barrier(2)

    def submit(operation_id):
        start.wait(timeout=5)
        try:
            return store.apply_rule_change(principal, rid, {**payload, "operation_id": operation_id})
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(submit, (str(uuid4()), str(uuid4()))))
    assert sum(isinstance(reply, dict) for reply in replies) == 1
    assert [reply for reply in replies if isinstance(reply, str)] == ["feedback_revision_conflict"]
    view = client.get(f"/audit-rounds/{rid}/deep-feedback").json()
    assert view["revision"] == 1 and len(view["operations"]) == 1


def test_feedback_cannot_start_until_the_exact_confirmed_suite_is_adopted(feedback_app):
    store, client, rid, payload = feedback_app
    original = store.get_round(rid)
    combined = client.post(f"/audit-rounds/{rid}/rule-changes", json={**payload, "intent": "save_and_analyze"})
    assert combined.status_code == 409, combined.text
    assert combined.json()["error"] == "suite_feedback_rules_changed"
    assert client.get(f"/audit-rounds/{rid}/deep-feedback").json()["revision"] == 0
    saved = client.post(f"/audit-rounds/{rid}/rule-changes", json=payload).json()
    start = {"operation_id": str(uuid4()), "expected_revision": saved["revision"], "intent": "save_and_analyze"}
    rejected = client.post(f"/audit-rounds/{rid}/rule-changes", json=start)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"] == "suite_feedback_rules_changed"
    assert store.get_round(rid) == original
    assert store.read_rule_change(store.principal_for_fixture("alice"), rid, start["operation_id"]) is None
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.audit_rounds WHERE predecessor_id=%s", (rid,)).fetchone()[0] == 0
    _adopt_confirmed_appendix(store, rid, saved["appendix"])
    accepted = client.post(f"/audit-rounds/{rid}/rule-changes", json=start)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["analysis_round_id"] != rid
