"""Recognition tasks survive browser loss without accepting or charging an audit."""

import io
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from ppap_api import create_app
from ppap_application import build_product_store
from ppap_core.fixtures import USER_BOB
from ppap_core.settings import Settings
from ppap_worker import process_claimed


@pytest.fixture
def task_system(auth_store_factory):
    settings = Settings()
    store = auth_store_factory(settings)
    store.seed_isolated_dev()
    worker = build_product_store(settings, objects=store.objects, role="worker")
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
    yield client, worker
    client.close()


def checked_file(client, worker, name="控制计划.xlsx", document_type="控制计划"):
    stream = io.BytesIO()
    book = Workbook()
    book.active.append([document_type, "测试客户", "零件号 P-1"])
    book.save(stream)
    book.close()
    content = stream.getvalue()
    version = client.post("/files", json={"original_filename": name, "byte_size": len(content)}).json()
    path = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.put(path + "/blob", content=content).status_code == 200
    assert client.post(path + "/finalize").status_code == 200
    validation_id = str(uuid.uuid4())
    assert client.post("/file-validations", json={"request_id": validation_id, "source_file_id": version["file_id"], "source_file_version_id": version["id"]}).status_code == 202
    process_claimed(worker, worker.claim_job("precheck"), "precheck")
    return {"file_version_id": version["id"], "validation_id": validation_id, "selection": "selected", "removal_reason": None}


class MetadataAI:
    def structured_completion(self, **request):
        if request["operation"] == "document_classification":
            return "控制计划"
        return json.dumps({"customer": "测试客户", "model": "M1", "part": "零件A", "part_number": "P-1"}, ensure_ascii=False)


@pytest.fixture
def matched_task(task_system):
    client, worker = task_system
    worker.settings.usage_charge_enabled = False
    file = checked_file(client, worker)
    rule = publish_rule(client, file)
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}
    task = client.post("/audit-tasks", json=body).json()
    process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    return client, worker, f"/audit-tasks/{task['id']}", body, rule


def test_concurrent_task_confirmation_and_cross_task_key_reuse(matched_task):
    client, worker, path, body, _rule = matched_task
    commands = [{"request_id": str(uuid.uuid4()), "expected_revision": 0} for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda command: client.post(path + "/confirm", json=command), commands))
    assert sorted(reply.status_code for reply in replies) == [201, 409]
    accepted = next(reply.json() for reply in replies if reply.status_code == 201)
    winning = next(command for command in commands if command["request_id"] == accepted["acceptance"]["request_id"])
    assert client.post(path + "/confirm", json=winning).json() == accepted
    other = client.post("/audit-tasks", json={**body, "request_id": str(uuid.uuid4())}).json()
    # The existing audit job is claimed, but not completed, while the other Task matches.
    audit = worker.claim_job("one-audit")
    assert audit.round_id == accepted["round_id"]
    process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    other_path = f"/audit-tasks/{other['id']}"
    assert client.post(other_path + "/confirm", json=winning).status_code == 409
    assert client.get(other_path).json()["round_id"] is None
    assert client.get(other_path, params={"request_id": winning["request_id"]}).json()["acceptance"]["state"] == "pending"
    assert len(client.get("/rounds").json()["rounds"]) == 1
    assert worker.claim_job("no-second-audit") is None


def test_confirmation_requires_current_revision_and_explicit_no_charge_configuration(matched_task):
    client, worker, path, body, _rule = matched_task
    assert client.get(path, params={"request_id": str(uuid.uuid4())}).json()["acceptance"]["state"] == "pending"
    revised = {**body, "request_id": str(uuid.uuid4()), "task_id": body["request_id"], "expected_revision": 0}
    assert client.post("/audit-tasks", json=revised).status_code == 202
    command = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
    stale = client.post(path + "/confirm", json=command)
    assert stale.status_code == 409
    assert stale.json() == {"error": "task_revision_conflict"}
    process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    worker.settings.usage_charge_enabled = True
    denied = client.post(path + "/confirm", json={**command, "expected_revision": 1})
    assert denied.status_code == 409
    assert denied.json() == {"error": "audit_task_charging_unavailable"}
    assert client.get(path).json()["state"] == "pending_confirm"
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []
    assert worker.claim_job("no-audit") is None


def test_superseded_candidate_requires_explicit_rematching_before_confirmation(matched_task):
    client, worker, path, body, old = matched_task
    new = publish_rule(client, body["files"][0], predecessor=old["id"], title="修订要求")
    response = client.post(path + "/confirm", json={"request_id": str(uuid.uuid4()), "expected_revision": 0})
    assert response.status_code == 409
    assert response.json() == {"error": "rule_candidate_expired"}
    current = client.get(path).json()
    assert current["state"] == "pending_confirm"
    assert current["round_id"] is None
    assert current["files"][0]["match"]["rule"]["id"] == old["id"]
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []
    assert worker.claim_job("no-audit-after-refusal") is None
    revised = {**body, "request_id": str(uuid.uuid4()), "task_id": body["request_id"], "expected_revision": 0}
    assert client.post("/audit-tasks", json=revised).status_code == 202
    process_claimed(worker, worker.claim_job("rematching"), "rematching", rule_ai=MetadataAI())
    assert client.get(path).json()["files"][0]["match"]["rule"]["id"] == new["id"]
    accepted = client.post(path + "/confirm", json={"request_id": str(uuid.uuid4()), "expected_revision": 1})
    assert accepted.status_code == 201
    assert accepted.json()["files"][0]["match"]["rule"]["id"] == new["id"]


def test_confirmation_and_rematching_serialize_on_the_same_task_revision(matched_task):
    client, _worker, path, body, _rule = matched_task
    revision = {**body, "request_id": str(uuid.uuid4()), "task_id": body["request_id"], "expected_revision": 0}
    command = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmation = pool.submit(client.post, path + "/confirm", json=command)
        recognition = pool.submit(client.post, "/audit-tasks", json=revision)
        accepted, revised = confirmation.result(), recognition.result()
    current = client.get(path).json()
    if accepted.status_code == 201:
        assert revised.status_code == 409
        assert revised.json() == {"error": "task_input_locked"}
        assert current["input_revision"] == 0
        assert current["round_id"] == accepted.json()["round_id"]
    else:
        assert accepted.status_code == 409
        assert accepted.json() == {"error": "task_revision_conflict"}
        assert revised.status_code == 202
        assert current["input_revision"] == 1
        assert current["round_id"] is None


def test_removed_file_needs_no_precheck_or_hidden_matching_work(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    excluded = client.post("/files", json={"original_filename": "已排除.xlsx", "byte_size": 3}).json()
    source = f"/files/{excluded['file_id']}/versions/{excluded['id']}"
    assert client.put(source + "/blob", content=b"bad").status_code == 200
    assert client.post(source + "/finalize").status_code == 200
    response = client.post(
        "/audit-tasks",
        json={
            "request_id": str(uuid.uuid4()),
            "analysis_mode": "standard",
            "customer_preference": None,
            "files": [file, {"file_version_id": excluded["id"], "validation_id": None, "selection": "removed", "removal_reason": "上传后已排除"}],
        },
    )
    assert response.status_code == 202, response.text
    removed = response.json()["files"][1]
    assert removed["selection"] == "removed"
    assert removed["match"] is None
    assert removed.get("validation_id") is None
    assert worker.claim_job("only-selected").payload["source_file_version_id"] == file["file_version_id"]
    assert worker.claim_job("no-excluded-work") is None


def test_confirmation_excludes_unmatched_and_unselected_files_from_the_frozen_round(task_system):
    client, worker = task_system
    worker.settings.usage_charge_enabled = False
    selected = checked_file(client, worker)
    unmatched = checked_file(client, worker, "过程流程图.xlsx", "过程流程图")
    unselected = {**checked_file(client, worker, "另一份控制计划.xlsx"), "selection": "unselected"}
    rule = publish_rule(client, selected)
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [selected, unmatched, unselected]}
    task = client.post("/audit-tasks", json=body).json()
    for _ in range(2):
        process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    response = client.post(f"/audit-tasks/{task['id']}/confirm", json={"request_id": str(uuid.uuid4()), "expected_revision": 0})
    assert response.status_code == 201, response.text
    accepted = response.json()
    assert [row["selection"] for row in accepted["files"]] == ["selected", "removed", "unselected"]
    assert accepted["files"][1]["removal_reason"]
    assert all(row.get("audit") is None for row in accepted["files"][1:])
    round = client.get(f"/rounds/{accepted['round_id']}").json()
    assert [(row["file_version_id"], row["rule_version_id"], row["role"]) for row in round["inputs"]] == [(selected["file_version_id"], rule["id"], "target")]


def test_queue_insert_failure_rolls_back_the_task_round_and_frozen_inputs(matched_task, auth_store_factory):
    client, worker, path, _body, _rule = matched_task
    migrator = auth_store_factory(worker.settings, objects=worker.objects, reset=False)
    with migrator._conn() as conn:
        conn.execute(
            """CREATE FUNCTION engine.reject_test_audit_insert() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.kind = 'audit' THEN RAISE EXCEPTION 'test_queue_unavailable'; END IF; RETURN NEW; END $$"""
        )
        conn.execute("CREATE TRIGGER reject_test_audit_insert BEFORE INSERT ON engine.jobs FOR EACH ROW EXECUTE FUNCTION engine.reject_test_audit_insert()")
    command = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="test_queue_unavailable"):
            client.post(path + "/confirm", json=command)
        restored = client.get(path).json()
        assert restored["round_id"] is None
        assert restored["state"] == "pending_confirm"
        assert client.get("/rounds").json()["rounds"] == []
        assert client.get("/usage").json()["events"] == []
        assert worker.claim_job("no-orphan-job") is None
    finally:
        with migrator._conn() as conn:
            conn.execute("DROP TRIGGER reject_test_audit_insert ON engine.jobs")
            conn.execute("DROP FUNCTION engine.reject_test_audit_insert()")
    assert client.post(path + "/confirm", json=command).status_code == 201


def test_recognition_is_persistent_and_zero_match_ends_without_an_audit(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}
    response = client.post("/audit-tasks", json=body)
    assert response.status_code == 202
    task = response.json()
    assert task["id"] == body["request_id"]
    assert re.fullmatch(r"PPAP-\d{8}-\d{3,}", task["number"])
    assert task["state"] == "matching"
    assert task["round_id"] is None
    assert task["files"][0]["match"]["state"] == "pending"
    assert client.post("/audit-tasks", json=body).json() == task
    assert client.post(f"/audit-tasks/{task['id']}/end", json={"expected_revision": 0}).status_code == 409
    process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    # New HTTP session reads server facts, not the original browser's memory.
    client.cookies.clear()
    assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
    recovered = client.get(f"/audit-tasks/{task['id']}").json()
    assert recovered["number"] == task["number"]
    assert recovered["state"] == "pending_confirm"
    assert recovered["files"][0]["match"]["state"] == "unmatched"
    assert recovered["estimated_points"] == 0
    assert client.get("/audit-tasks").json()["tasks"] == [recovered]
    assert client.get("/drafts").json()["drafts"] == []
    ended = client.post(f"/audit-tasks/{task['id']}/end", json={"expected_revision": 0})
    assert ended.status_code == 200
    assert ended.json()["state"] == "completed"
    assert ended.json()["completion_reason"] == "no_matching_rules"
    assert client.post(f"/audit-tasks/{task['id']}/end", json={"expected_revision": 0}).json() == ended.json()
    changed_end = client.post(f"/audit-tasks/{task['id']}/end", json={"expected_revision": 0, "removals": [{"file_version_id": file["file_version_id"], "removal_reason": "结束后修改"}]})
    assert changed_end.status_code == 409
    assert client.get(f"/audit-tasks/{task['id']}").json() == ended.json()
    assert client.get("/audit-tasks", params={"state": "completed", "q": "控制计划"}).json()["tasks"] == [ended.json()]
    assert client.get("/audit-tasks", params={"state": "matching"}).json()["tasks"] == []
    assert client.get("/audit-tasks", params={"q": "不存在的文件"}).json()["tasks"] == []
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []
    assert worker.claim_job("unexpected-audit") is None


def test_customer_recovery_rematches_and_finishes_the_original_task_without_changing_files(task_system):
    client, worker = task_system
    worker.settings.usage_charge_enabled = False
    file = checked_file(client, worker)
    rule = publish_rule(client, file)

    class CustomerUnknownAI(MetadataAI):
        def structured_completion(self, **request):
            return "{}" if request["operation"] == "document_metadata_extraction" else super().structured_completion(**request)

    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}
    task = client.post("/audit-tasks", json=body).json()
    path = f"/audit-tasks/{task['id']}"
    process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=CustomerUnknownAI())
    unmatched = client.get(path).json()
    assert unmatched["files"][0]["match"]["gaps"] == ["matching_customer_required"]
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []

    revised = {**body, "request_id": str(uuid.uuid4()), "task_id": task["id"], "expected_revision": 0, "customer_preference": "测试客户"}
    assert client.post("/audit-tasks", json=revised).status_code == 202
    process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=CustomerUnknownAI())
    matched = client.get(path).json()
    assert matched["input_revision"] == 1
    assert matched["files"][0]["file"]["id"] == file["file_version_id"]
    assert matched["files"][0]["match"]["rule"]["id"] == rule["id"]
    assert matched["files"][0]["match"]["metadata"]["customer"] is None

    command = {"request_id": str(uuid.uuid4()), "expected_revision": 1}
    accepted = client.post(path + "/confirm", json=command)
    assert accepted.status_code == 201
    round_id = accepted.json()["round_id"]
    assert client.post(path + "/confirm", json=command).json()["round_id"] == round_id
    process_claimed(worker, worker.claim_job("audit"), "audit")
    client.cookies.clear()
    assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
    finished = client.get(path).json()
    assert (finished["id"], finished["round_id"], finished["state"], finished["completion_reason"]) == (task["id"], round_id, "completed", "processed")
    assert finished["files"][0]["file"]["id"] == file["file_version_id"]
    assert len(client.get("/rounds").json()["rounds"]) == 1
    assert client.get("/usage").json()["events"] == []


def test_legacy_draft_operations_cannot_replace_or_accept_a_recognition_task(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}
    task = client.post("/audit-tasks", json=body).json()
    draft = task["source_draft_id"]
    replaced = client.put(f"/drafts/{draft}", json={"items": [], "analysis_mode": "standard"})
    assert replaced.status_code == 409
    assert replaced.json() == {"error": "task_input_locked"}
    accepted = client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "legacy-bypass"})
    assert accepted.status_code == 409
    assert accepted.json() == {"error": "task_input_locked"}
    assert client.get(f"/audit-tasks/{task['id']}").json() == task
    assert client.get("/rounds").json()["rounds"] == []


def test_task_round_cannot_prepare_a_legacy_retry(matched_task):
    client, worker, path, _body, _rule = matched_task
    accepted = client.post(path + "/confirm", json={"request_id": str(uuid.uuid4()), "expected_revision": 0}).json()
    job = worker.claim_job("terminal-audit")
    assert job.round_id == accepted["round_id"]
    assert worker.complete_job(job, "terminal-audit", {"failure": {"code": "test_failure", "message": "受控执行失败", "retryable": False}}, outcome="failed")
    original = client.get(f"/rounds/{accepted['round_id']}").json()
    usage = client.get("/usage").json()
    prepared = client.post("/drafts", json={"request_id": str(uuid.uuid4()), "predecessor_id": original["id"]})
    assert prepared.status_code == 409
    assert prepared.json() == {"error": "task_input_locked"}
    assert original["preparation_allowed"] is False
    assert client.get(path).json()["files"][0]["audit"]["execution"] == "review_failed"
    assert client.get(f"/rounds/{original['id']}").json() == original
    assert client.get("/drafts").json()["drafts"] == []
    assert client.get("/usage").json() == usage
    manual = client.post("/drafts", json={"items": [{key: item[key] for key in ("file_version_id", "role", "rule_version_id")} for item in original["inputs"]]}).json()
    bypass = client.post(f"/drafts/{manual['id']}/accept", json={"idempotency_key": "task-predecessor-bypass", "predecessor_id": original["id"]})
    assert bypass.status_code == 409
    assert bypass.json() == {"error": "task_input_locked"}
    assert client.get(f"/drafts/{manual['id']}").json()["status"] == "open"
    assert [row["id"] for row in client.get("/rounds").json()["rounds"]] == [original["id"]]
    assert client.get("/usage").json() == usage


def test_concurrent_creation_coalesces_and_changed_inputs_cannot_replace_it(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    other = checked_file(client, worker, "另一份控制计划.xlsx")
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/audit-tasks", json=body), range(2)))
    assert [response.status_code for response in responses] == [202, 202]
    assert responses[0].json() == responses[1].json()
    conflict = client.post("/audit-tasks", json={**body, "files": [other]})
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "idempotency_conflict"}
    assert client.post("/audit-tasks", json={**body, "customer_preference": "另一个客户"}).status_code == 409
    task = responses[0].json()
    assert client.get(f"/audit-tasks/{task['id']}").json() == task
    assert worker.claim_job("one-recognition").payload["task_id"] == task["id"]
    assert worker.claim_job("duplicate-recognition") is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        additional = list(pool.map(lambda _: client.post("/audit-tasks", json={**body, "request_id": str(uuid.uuid4())}), range(2)))
    assert [response.status_code for response in additional] == [202, 202]
    assert {int(response.json()["number"].rsplit("-", 1)[1]) for response in additional} == {2, 3}
    page = client.get("/audit-tasks", params={"limit": 1}).json()
    assert len(page["tasks"]) == 1
    assert page["next_offset"] == 1
    assert len(client.get("/audit-tasks", params={"offset": page["next_offset"]}).json()["tasks"]) == 2


def test_rematching_keeps_the_task_and_ignores_a_previous_revision_result(task_system):
    client, worker = task_system
    first = checked_file(client, worker)
    replacement = checked_file(client, worker, "新版控制计划.xlsx")
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [first, replacement]}
    task = client.post("/audit-tasks", json=body).json()
    old_jobs = [worker.claim_job("old") for _ in range(2)]
    revised = {**body, "request_id": str(uuid.uuid4()), "task_id": task["id"], "expected_revision": 0, "files": [{**first, "selection": "removed", "removal_reason": "由新版替换"}, replacement]}
    response = client.post("/audit-tasks", json=revised)
    assert response.status_code == 202
    current = response.json()
    assert current["id"] == task["id"]
    assert current["number"] == task["number"]
    assert current["input_revision"] == 1
    assert current["recognition_request_id"] == revised["request_id"]
    assert current["files"][0]["match"] is None
    assert current["files"][1]["validation_id"] == replacement["validation_id"]
    for job in old_jobs:
        assert worker.complete_job(job, "old", {"source_file_version_id": job.payload["source_file_version_id"], "matching_status": "unmatched"})
    path = f"/audit-tasks/{task['id']}"
    assert client.get(path).json() == current
    process_claimed(worker, worker.claim_job("new"), "new", rule_ai=MetadataAI())
    recovered = client.get(path).json()
    assert recovered["state"] == "pending_confirm"
    assert recovered["files"][1]["match"]["state"] == "unmatched"
    assert client.post("/audit-tasks", json=revised).json() == recovered
    assert client.post("/audit-tasks", json=body).json() == recovered
    assert len(client.get("/audit-tasks").json()["tasks"]) == 1
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []


def test_recognition_receipts_survive_lost_responses_and_keep_their_original_revision(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}
    task = client.post("/audit-tasks", json=body).json()
    request_id = str(uuid.uuid4())
    path = f"/audit-tasks/{task['id']}/recognitions/"
    assert client.get(path + request_id).json() == {"request_id": request_id, "task_id": task["id"], "state": "unknown", "input_revision": None, "task": None}
    revised = {**body, "request_id": request_id, "task_id": task["id"], "expected_revision": 0, "customer_preference": "测试客户"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: client.post("/audit-tasks", json=revised), range(2)))
    assert [reply.status_code for reply in replies] == [202, 202]
    current = replies[0].json()
    assert replies[1].json() == current
    assert client.get(path + request_id).json() == {"request_id": request_id, "task_id": task["id"], "state": "accepted", "input_revision": 1, "task": current}
    assert client.get(path + body["request_id"]).json() == {"request_id": body["request_id"], "task_id": task["id"], "state": "accepted", "input_revision": 0, "task": current}
    conflict = client.post("/audit-tasks", json={**revised, "customer_preference": "修改同一个请求"})
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "idempotency_conflict"}
    recreated = client.post("/audit-tasks", json={**body, "request_id": request_id})
    assert recreated.status_code == 409
    assert recreated.json() == {"error": "idempotency_conflict"}
    assert len(client.get("/audit-tasks").json()["tasks"]) == 1


def test_competing_revisions_and_stale_zero_match_end_cannot_replace_current_input(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}
    task = client.post("/audit-tasks", json=body).json()
    process_claimed(worker, worker.claim_job("initial"), "initial", rule_ai=MetadataAI())
    revisions = [{**body, "task_id": task["id"], "expected_revision": 0, "request_id": str(uuid.uuid4()), "customer_preference": customer} for customer in ("客户A", "客户B")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda item: client.post("/audit-tasks", json=item), revisions))
    assert sorted(reply.status_code for reply in replies) == [202, 409]
    loser = next(reply for reply in replies if reply.status_code == 409)
    assert loser.json() == {"error": "task_revision_conflict"}
    process_claimed(worker, worker.claim_job("current"), "current", rule_ai=MetadataAI())
    path = f"/audit-tasks/{task['id']}"
    current = client.get(path).json()
    assert current["input_revision"] == 1
    assert current["files"][0]["match"]["state"] == "unmatched"
    stale = client.post(path + "/end", json={"expected_revision": 0})
    assert stale.status_code == 409
    assert stale.json() == {"error": "task_revision_conflict"}
    assert client.get(path).json() == current
    ended = client.post(path + "/end", json={"expected_revision": 1, "removals": [{"file_version_id": file["file_version_id"], "removal_reason": "无可用规则"}]})
    assert ended.status_code == 200
    assert ended.json()["state"] == "completed"
    assert ended.json()["files"][0]["selection"] == "removed"
    assert ended.json()["files"][0]["removal_reason"] == "无可用规则"
    assert client.post(path + "/end", json={"expected_revision": 1, "removals": [{"file_version_id": file["file_version_id"], "removal_reason": "无可用规则"}]}).json() == ended.json()


def test_superseded_queued_recognition_does_not_read_the_file_or_call_the_model(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}
    task = client.post("/audit-tasks", json=body).json()
    revised = {**body, "request_id": str(uuid.uuid4()), "task_id": task["id"], "expected_revision": 0}
    assert client.post("/audit-tasks", json=revised).status_code == 202
    old = worker.claim_job("superseded")

    class MustNotCallAI:
        def structured_completion(self, **_request):
            pytest.fail("superseded recognition must not start model work")

    assert process_claimed(worker, old, "superseded", rule_ai=MustNotCallAI()) == {"error": "recognition_superseded"}
    current = worker.claim_job("current")
    process_claimed(worker, current, "current", rule_ai=MetadataAI())
    assert client.get(f"/audit-tasks/{task['id']}").json()["state"] == "pending_confirm"


def test_rematching_requires_retained_file_provenance_and_exact_prechecks(task_system):
    client, worker = task_system
    first, second = checked_file(client, worker), checked_file(client, worker, "另一份.xlsx")
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [first, second]}
    task = client.post("/audit-tasks", json=body).json()
    revise = {**body, "request_id": str(uuid.uuid4()), "task_id": task["id"], "expected_revision": 0}
    assert client.post("/audit-tasks", json={**revise, "files": [second]}).json() == {"error": "task_file_provenance_required"}
    wrong_receipt = [{**first, "validation_id": second["validation_id"]}, second]
    assert client.post("/audit-tasks", json={**revise, "files": wrong_receipt}).json() == {"error": "precheck_required"}
    assert client.get(f"/audit-tasks/{task['id']}").json() == task
    unknown_id = str(uuid.uuid4())
    assert client.get(f"/audit-tasks/{unknown_id}/recognitions/{unknown_id}").json()["state"] == "unknown"


def test_recognition_request_collision_does_not_expose_another_tenants_job(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}
    task = client.post("/audit-tasks", json=body).json()
    assert worker.claim_job("held-original") is not None
    task = client.get(f"/audit-tasks/{task['id']}").json()
    client.cookies.clear()
    assert client.post("/isolated-dev/sessions", json={"subject": "carol"}).status_code == 200
    foreign = checked_file(client, worker)
    client.cookies.clear()
    assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
    revise = {**body, "task_id": task["id"], "expected_revision": 0, "request_id": foreign["validation_id"]}
    assert client.post("/audit-tasks", json=revise).status_code == 403
    assert client.get(f"/audit-tasks/{task['id']}").json() == task


def test_unselected_and_removed_files_keep_provenance_without_recognition(task_system):
    client, worker = task_system
    files = [checked_file(client, worker, name) for name in ("控制计划.xlsx", "未选择.xlsx", "已移除.xlsx")]
    files[1]["selection"] = "unselected"
    files[2].update(selection="removed", removal_reason="本次不审核")
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": files}
    task = client.post("/audit-tasks", json=body).json()
    assert [row["selection"] for row in task["files"]] == ["selected", "unselected", "removed"]
    assert [row["file"]["id"] for row in task["files"]] == [row["file_version_id"] for row in files]
    assert task["files"][1]["match"] is None
    assert task["files"][2]["removal_reason"] == "本次不审核"
    job = worker.claim_job("recognition")
    assert job.payload["source_file_version_id"] == files[0]["file_version_id"]
    assert worker.claim_job("not-selected") is None
    process_claimed(worker, job, "recognition", rule_ai=MetadataAI())
    path = f"/audit-tasks/{task['id']}"
    assert client.get(path).json()["estimated_points"] == 0
    assert client.post(path + "/end", json={"expected_revision": 0}).status_code == 200


def publish_rule(client, file, *, predecessor=None, title="客户控制计划", document_type="控制计划"):
    body = {
        "kind": "engine",
        "type": document_type,
        "rule_profile": "v3",
        "customers": ["测试客户"],
        "models": [],
        "parts": [],
        "rules": [{"id": "1", "category": "身份", "priority": None, "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
    }
    response = client.post("/rules", json={"title": title, "body": body, "source_file_version_id": file["file_version_id"], "predecessor_id": predecessor})
    assert response.status_code == 201
    return response.json()


def test_explicit_customer_scope_and_reference_survive_acceptance_and_recovery(task_system):
    client, worker = task_system
    worker.settings.usage_charge_enabled = False
    target = checked_file(client, worker)
    reference = checked_file(client, worker, "特殊特性清单.xlsx", "过程特殊特性清单")
    first = publish_rule(client, target)
    declaration = {"title": first["title"], "body": {**first["body"], "customers": []}, "source_file_version_id": target["file_version_id"], "predecessor_id": first["id"]}
    legacy = client.post("/rules", json=declaration).json()
    assert legacy["customer_scope"] == "unconfirmed"
    source = client.get(f"/file-validations/{target['validation_id']}").json()
    version = client.get(f"/files/{source['source_file_id']}/versions/{target['file_version_id']}").json()
    units = client.get(f"/files/{source['source_file_id']}/versions/{target['file_version_id']}/rule-sources").json()["units"]
    unit, rule = units[0], legacy["body"]["rules"][0]
    clause = {
        "id": rule["id"],
        "title": rule["category"],
        "text": rule["rule_to_check"],
        "original": unit["excerpt"],
        "source": version["original_filename"],
        "location": unit["location"],
        "checked": True,
        "provenance": {**unit, "fileVersionId": version["id"]},
    }
    registration = {"basis": {"source": {"filename": version["original_filename"]}, "clauses": [clause]}, "scope": {"allCustomers": True}}
    response = client.post("/rules", json={**declaration, "predecessor_id": legacy["id"], "case_context": {"registration": json.dumps(registration)}})
    assert response.status_code == 201, response.text
    confirmed = response.json()
    assert confirmed["customer_scope"] == "all"
    assert confirmed["body"] == legacy["body"]
    assert client.get(f"/rules/{legacy['id']}").json() == legacy
    listed = {row["id"]: row for row in client.get("/rules").json()["rules"]}
    assert (listed[legacy["id"]]["customer_scope"], listed[confirmed["id"]]["customer_scope"]) == ("unconfirmed", "all")
    task = client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [target, reference]}).json()
    for _ in range(2):
        process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    path = f"/audit-tasks/{task['id']}"
    matched = client.get(path).json()
    assert [row["match"]["state"] for row in matched["files"]] == ["matched", "reference"]
    assert matched["files"][0]["match"]["rule"]["id"] == confirmed["id"]
    assert matched["estimated_points"] == 500
    command = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
    response = client.post(path + "/confirm", json=command)
    assert response.status_code == 201, response.text
    accepted = response.json()
    assert accepted["progress"] == {"completed": 0, "total": 1}
    assert [row["selection"] for row in accepted["files"]] == ["selected", "selected"]
    assert accepted["files"][1]["audit"] is None
    assert client.post(path + "/confirm", json=command).json() == accepted
    round = client.get(f"/rounds/{accepted['round_id']}").json()
    assert {row["file_version_id"]: (row["role"], (row["rule"] or {}).get("id")) for row in round["inputs"]} == {
        target["file_version_id"]: ("target", confirmed["id"]),
        reference["file_version_id"]: ("reference", None),
    }
    assert client.get("/usage").json()["events"] == []
    audit = worker.claim_job("reference-test")
    assert worker.complete_job(
        audit, "reference-test", {"findings": [], "coverage": {"rule_items": [{"file_version_id": target["file_version_id"], "id": "1", "coverage": "unsupported"}]}}, "succeeded"
    )
    finished = client.get(path).json()
    assert (finished["state"], finished["progress"]) == ("completed", {"completed": 1, "total": 1})
    assert finished["files"][1]["match"]["state"] == "reference"
    assert finished["files"][1]["audit"] is None
    assert client.get(f"/rules/{legacy['id']}").json() == legacy


def test_scope_only_publication_preserves_legacy_body_source_and_version(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    first = publish_rule(client, file)
    command = {"title": first["title"], "body": {**first["body"], "customers": []}, "source_file_version_id": first["source_file_version_id"], "predecessor_id": first["id"]}
    legacy = client.post("/rules", json=command).json()
    confirmed = client.post("/rules", json={**command, "predecessor_id": legacy["id"], "case_context": {"customer_scope": "all"}})
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["customer_scope"] == "all"
    assert confirmed.json()["case_context"] == {"customer_scope": "all"}
    assert confirmed.json()["body"] == legacy["body"]
    assert confirmed.json()["source_file_version_id"] == legacy["source_file_version_id"]
    assert client.get("/rules/" + legacy["id"]).json() == legacy
    unverified = client.post("/rules", json={**command, "case_context": {"customer_scope": "all", "registration": "{}"}})
    assert unverified.status_code == 400
    assert unverified.json()["error"] == "rule_source_unverified"
    for context, body in [({"customer_scope": True}, command["body"]), ({"customer_scope": "all"}, first["body"])]:
        response = client.post("/rules", json={**command, "body": body, "case_context": context})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_rule_scope"


def test_scope_only_version_inherits_legacy_citations_without_claiming_new_verification(task_system, auth_store_factory):
    client, worker = task_system
    file = checked_file(client, worker)
    first = publish_rule(client, file)
    store = auth_store_factory(worker.settings, objects=worker.objects, reset=False)
    legacy_context = {"registration": json.dumps({"basis": {"source": {"filename": "原件.xlsx"}, "clauses": [{"location": "原件位置待核对", "checked": False}]}})}
    legacy = store.publish_rule(store.principal_for_fixture("alice"), first["title"], {**first["body"], "customers": []}, file["file_version_id"], first["id"], case_context=legacy_context)
    command = {
        "title": legacy["title"],
        "body": legacy["body"],
        "source_file_version_id": legacy["source_file_version_id"],
        "predecessor_id": legacy["id"],
        "case_context": {**legacy_context, "customer_scope": "all"},
    }
    response = client.post("/rules", json=command)
    assert response.status_code == 201, response.text
    assert response.json()["body"] == legacy["body"]
    assert response.json()["customer_scope"] == "all"
    assert response.json()["case_context"]["registration"] == legacy_context["registration"]
    assert client.get("/rules/" + legacy["id"]).json() == legacy
    for change in ({"title": "重新命名正文版本"}, {"body": {**legacy["body"], "rules": [{**legacy["body"]["rules"][0], "rule_to_check": "改写后的条款"}]}}):
        rejected = client.post("/rules", json={**command, **change})
        assert rejected.status_code == 400
        assert rejected.json()["error"] == "rule_source_unverified"


def test_two_tasks_bind_their_own_jobs_and_candidate_versions(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    old = publish_rule(client, file)
    body = {"analysis_mode": "standard", "customer_preference": None, "files": [file]}
    first = client.post("/audit-tasks", json={**body, "request_id": str(uuid.uuid4())}).json()
    new = publish_rule(client, file, predecessor=old["id"], title="客户控制计划第二版")
    second = client.post("/audit-tasks", json={**body, "request_id": str(uuid.uuid4())}).json()
    first_job = worker.claim_job("first")
    second_job = worker.claim_job("second")
    process_claimed(worker, second_job, "second", rule_ai=MetadataAI())
    first_path, second_path = f"/audit-tasks/{first['id']}", f"/audit-tasks/{second['id']}"
    assert client.get(first_path).json()["state"] == "matching"
    second_result = client.get(second_path).json()
    assert second_result["files"][0]["match"]["rule"]["id"] == new["id"]
    process_claimed(worker, first_job, "first", rule_ai=MetadataAI())
    first_result = client.get(first_path).json()
    assert first_result["files"][0]["match"]["rule"]["id"] == old["id"]
    assert first_result["files"][0]["match"]["metadata"]["part_number"] == "P-1"
    assert first_result["estimated_points"] == 500
    assert client.get(second_path).json() == second_result
    assert client.post(first_path + "/end", json={"expected_revision": 0}).status_code == 409
    assert client.get(first_path).json() == first_result
    assert client.get(first_path.upper().replace("/AUDIT-TASKS/", "/audit-tasks/")).json() == first_result


def test_three_document_types_match_their_own_rules_without_accepting_an_audit(task_system):
    client, worker = task_system
    files, rules = [], []
    for document_type in ("PFMEA", "控制计划", "过程流程图"):
        file = checked_file(client, worker, document_type + ".xlsx", document_type)
        files.append(file)
        rules.append(publish_rule(client, file, title="客户" + document_type, document_type=document_type))
    response = client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": files})
    assert response.status_code == 202
    task = response.json()
    for _ in files:
        process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    recovered = client.get(f"/audit-tasks/{task['id']}").json()
    assert recovered["state"] == "pending_confirm"
    assert [row["match"]["rule"]["id"] for row in recovered["files"]] == [rule["id"] for rule in rules]
    assert [row["match"]["document_type"] for row in recovered["files"]] == [rule["body"]["type"] for rule in rules]
    assert recovered["estimated_points"] == 1500
    assert recovered["round_id"] is None
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []


def test_confirmation_freezes_three_matched_targets_once_and_restores_the_same_round(task_system):
    client, worker = task_system
    worker.settings.usage_charge_enabled = False
    files, rules = [], []
    for document_type in ("PFMEA", "控制计划", "过程流程图"):
        file = checked_file(client, worker, document_type + ".xlsx", document_type)
        files.append(file)
        rules.append(publish_rule(client, file, title="客户" + document_type, document_type=document_type))
    task = client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": files}).json()
    for _ in files:
        process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    path = f"/audit-tasks/{task['id']}"
    command = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
    response = client.post(path + "/confirm", json=command)
    assert response.status_code == 201, response.text
    accepted = response.json()
    assert accepted["state"] == "reviewing"
    assert accepted["acceptance"] == {"request_id": command["request_id"], "state": "accepted", "reason": None}
    assert accepted["progress"] == {"completed": 0, "total": 3}
    round = client.get(f"/rounds/{accepted['round_id']}").json()
    assert round["source_draft_id"] == task["id"]
    assert {row["file_version_id"]: row["rule_version_id"] for row in round["inputs"]} == {file["file_version_id"]: rule["id"] for file, rule in zip(files, rules)}
    assert all(row["role"] == "target" for row in round["inputs"])
    assert all(row["audit"]["round_id"] == accepted["round_id"] for row in accepted["files"])
    publish_rule(client, files[0], predecessor=rules[0]["id"], title="受理后发布的 PFMEA 规则", document_type="PFMEA")
    assert client.post(path + "/confirm", json=command).json() == accepted
    client.cookies.clear()
    assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
    assert client.get(path, params={"request_id": command["request_id"]}).json() == accepted
    assert client.get("/audit-tasks", params={"state": "reviewing"}).json()["tasks"] == [accepted]
    assert len(client.get("/rounds").json()["rounds"]) == 1
    assert client.get("/usage").json()["events"] == []
    job = worker.claim_job("one-audit")
    assert job.round_id == accepted["round_id"]
    assert worker.claim_job("no-second-audit") is None
    process_claimed(worker, job, "one-audit")
    reporting = client.get(path).json()
    assert reporting["state"] == "reviewing"
    assert [row["audit"]["report"]["state"] for row in reporting["files"]] == ["pending", "not_eligible", "pending"]
    for _ in range(2):
        report_job = worker.claim_job("file-report")
        assert report_job.kind == "report_pdf"
        process_claimed(worker, report_job, "file-report")
    finished = client.get(path).json()
    assert finished["state"] == "completed"
    assert finished["completion_reason"] == "processed"
    assert finished["progress"] == {"completed": 3, "total": 3}
    assert [row["audit"]["execution"] for row in finished["files"]] == ["completed", "review_failed", "completed"]
    assert all(row["audit"]["checks"][0]["coverage"] == "executed" for row in finished["files"])
    assert [row["audit"]["conclusion"] for row in finished["files"]] == ["NG", "UNKNOWN", "NG"]
    assert [row["audit"]["report"]["state"] for row in finished["files"]] == ["ready", "not_eligible", "ready"]
    assert client.get("/audit-tasks", params={"state": "completed"}).json()["tasks"] == [finished]


def test_private_task_cannot_be_read_through_its_round_or_original_by_a_tenant_peer(task_system):
    client, worker = task_system
    worker.settings.usage_charge_enabled = False
    file = checked_file(client, worker)
    publish_rule(client, file)
    task = client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}).json()
    process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    path = f"/audit-tasks/{task['id']}"
    command = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
    accepted = client.post(path + "/confirm", json=command).json()
    version = accepted["files"][0]["file"]
    round_path = f"/rounds/{accepted['round_id']}"
    assert client.post(round_path + "/resubmit", json={"idempotency_key": str(uuid.uuid4())}).status_code == 409
    client.cookies.clear()
    assert client.post("/isolated-dev/sessions", json={"subject": "bob"}).status_code == 200
    assert client.get(path).status_code == 403
    assert client.post(path + "/confirm", json=command).status_code == 403
    assert client.get(round_path).status_code == 403
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get(round_path + "/bundle").status_code == 403
    source = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.get(round_path + source + "/download").status_code == 403
    assert client.get(round_path + source + "/preview").status_code == 403
    assert client.get(source + "/office", params={"round_id": accepted["round_id"]}).status_code == 403
    assert client.put(round_path + "/reviews", json={"item_key": "1", "judgment": "confirm", "rationale": "peer", "next_step": "", "version": 0}).status_code == 403


def test_task_creation_requires_the_exact_completed_precheck_receipt(task_system):
    client, worker = task_system
    first = checked_file(client, worker)
    other = checked_file(client, worker, "另一份.xlsx")
    request = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [{**first, "validation_id": other["validation_id"]}]}
    assert client.post("/audit-tasks", json=request).status_code == 409
    assert client.get("/audit-tasks").json()["tasks"] == []
    assert worker.claim_job("not-accepted") is None
    # Upload readiness alone is not a content check. A queued receipt cannot qualify.
    version = client.get(f"/file-validations/{first['validation_id']}").json()
    queued = {"request_id": str(uuid.uuid4()), "source_file_id": version["source_file_id"], "source_file_version_id": first["file_version_id"]}
    assert client.post("/file-validations", json=queued).status_code == 202
    assert client.post("/audit-tasks", json={**request, "files": [{**first, "validation_id": queued["request_id"]}]}).status_code == 409
    assert client.get("/audit-tasks").json()["tasks"] == []


def test_ambiguous_rules_are_not_reported_as_definitive_zero_match(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    first = publish_rule(client, file)
    second = publish_rule(client, file, title="另一套同时生效的客户控制计划")
    task = client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}).json()
    process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    path = f"/audit-tasks/{task['id']}"
    recovered = client.get(path).json()
    assert recovered["files"][0]["match"]["state"] == "failed"
    assert recovered["files"][0]["match"]["gaps"] == ["ambiguous_available_rules"]
    assert {row["id"]: row["title"] for row in recovered["files"][0]["match"]["candidates"]} == {rule["id"]: rule["title"] for rule in (first, second)}
    assert recovered["estimated_points"] is None
    assert client.post(path + "/end", json={"expected_revision": 0}).status_code == 409
    assert client.get("/rounds").json()["rounds"] == []


def test_reference_only_task_can_end_without_a_round_report_or_charge(task_system):
    client, worker = task_system
    file = checked_file(client, worker, "特殊特性清单.xlsx", "过程特殊特性清单")
    task = client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}).json()
    process_claimed(worker, worker.claim_job("reference-only"), "reference-only", rule_ai=MetadataAI())
    path = "/audit-tasks/" + task["id"]
    recognized = client.get(path).json()
    assert recognized["files"][0]["match"]["state"] == "reference"
    assert recognized["estimated_points"] == 0
    assert client.post(path + "/confirm", json={"request_id": str(uuid.uuid4()), "expected_revision": 0}).status_code == 409
    ended = client.post(path + "/end", json={"expected_revision": 0})
    assert ended.status_code == 200, ended.text
    assert ended.json()["state"] == "completed"
    assert ended.json()["round_id"] is None
    assert ended.json()["files"][0]["selection"] == "selected"
    assert client.get("/usage").json()["events"] == []
    assert client.get("/rounds").json()["rounds"] == []


def test_task_reads_and_job_reads_require_the_private_draft_owner(task_system, auth_store_factory):
    client, worker = task_system
    file = checked_file(client, worker)
    source_id = client.get(f"/file-validations/{file['validation_id']}").json()["source_file_id"]
    standalone = client.post("/file-analyses", json={"request_id": str(uuid.uuid4()), "source_file_id": source_id, "source_file_version_id": file["file_version_id"]}).json()
    process_claimed(worker, worker.claim_job("standalone"), "standalone", rule_ai=MetadataAI())
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}
    task = client.post("/audit-tasks", json=body).json()
    job = worker.claim_job("recognition")
    migrator = auth_store_factory(worker.settings, objects=worker.objects, reset=False)
    # A file grant is not a private Task grant. Seed this policy boundary directly.
    with migrator._conn() as conn:
        conn.execute("INSERT INTO product.resource_grants (resource_id, user_id, capability) VALUES (%s, %s, 'read')", (source_id, USER_BOB))
    for subject in ("bob", "carol"):
        client.cookies.clear()
        assert client.post("/isolated-dev/sessions", json={"subject": subject}).status_code == 200
        if subject == "bob":
            assert client.get(f"/files/{source_id}/versions/{file['file_version_id']}").status_code == 200
            assert client.get(f"/file-analyses/{standalone['id']}").status_code == 200
            latest = client.get(f"/files/{source_id}/versions/{file['file_version_id']}/analysis")
            assert latest.status_code == 200
            assert latest.json()["analysis"]["id"] == standalone["id"]
        assert client.get("/audit-tasks").json()["tasks"] == []
        assert client.get(f"/audit-tasks/{task['id']}").status_code == 403
        assert client.get(f"/audit-tasks/{task['id']}/recognitions/{task['id']}").status_code == 403
        assert client.post(f"/audit-tasks/{task['id']}/end", json={"expected_revision": 0}).status_code == 403
        assert client.post("/audit-tasks", json=body).status_code == 403
        assert client.get(f"/file-analyses/{job.id}").status_code == 403
    client.cookies.clear()
    assert client.get(f"/audit-tasks/{task['id']}").status_code == 401


def test_failed_and_unknown_recognition_are_not_definitive_zero_matches(task_system):
    client, worker = task_system
    file = checked_file(client, worker)
    task = client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}).json()
    job = worker.claim_job("original")
    # Inject a lost heartbeat through the database boundary; no replacement worker.
    with worker._conn() as conn:
        conn.execute("UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s", (job.id,))
    path = f"/audit-tasks/{task['id']}"
    unknown = client.get(path).json()
    assert unknown["state"] == "matching"
    assert unknown["files"][0]["match"]["gaps"] == ["matching_result_unknown"]
    assert client.post(path + "/end", json={"expected_revision": 0}).status_code == 409
    assert worker.claim_job("replacement") is None
    assert not worker.complete_job(job, "replacement", {"matching_status": "unmatched"})

    class UnavailableAI:
        def structured_completion(self, **_request):
            raise RuntimeError("private provider failure")

    process_claimed(worker, job, "original", rule_ai=UnavailableAI())
    failed = client.get(path).json()
    assert failed["state"] == "pending_confirm"
    assert failed["files"][0]["match"]["state"] == "failed"
    assert failed["estimated_points"] is None
    assert "private provider" not in json.dumps(failed)
    assert client.post(path + "/end", json={"expected_revision": 0}).status_code == 409
    assert client.get("/rounds").json()["rounds"] == []
