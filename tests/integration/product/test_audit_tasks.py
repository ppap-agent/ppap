"""Recognition tasks survive browser loss without accepting or charging an audit."""

import io
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

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
    assert client.post(f"/audit-tasks/{task['id']}/end").status_code == 409
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
    ended = client.post(f"/audit-tasks/{task['id']}/end")
    assert ended.status_code == 200
    assert ended.json()["state"] == "completed"
    assert ended.json()["completion_reason"] == "no_matching_rules"
    assert client.post(f"/audit-tasks/{task['id']}/end").json() == ended.json()
    assert client.get(f"/audit-tasks/{task['id']}").json() == ended.json()
    assert client.get("/audit-tasks", params={"state": "completed", "q": "控制计划"}).json()["tasks"] == [ended.json()]
    assert client.get("/audit-tasks", params={"state": "matching"}).json()["tasks"] == []
    assert client.get("/audit-tasks", params={"q": "不存在的文件"}).json()["tasks"] == []
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []
    assert worker.claim_job("unexpected-audit") is None


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
    assert client.post(path + "/end").status_code == 200


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
    assert client.post(first_path + "/end").status_code == 409
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
    publish_rule(client, file)
    publish_rule(client, file, title="另一套同时生效的客户控制计划")
    task = client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": None, "files": [file]}).json()
    process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=MetadataAI())
    path = f"/audit-tasks/{task['id']}"
    recovered = client.get(path).json()
    assert recovered["files"][0]["match"]["state"] == "failed"
    assert recovered["files"][0]["match"]["gaps"] == ["ambiguous_available_rules"]
    assert recovered["estimated_points"] is None
    assert client.post(path + "/end").status_code == 409
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
        assert client.post(f"/audit-tasks/{task['id']}/end").status_code == 403
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
    assert client.post(path + "/end").status_code == 409
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
    assert client.post(path + "/end").status_code == 409
    assert client.get("/rounds").json()["rounds"] == []
