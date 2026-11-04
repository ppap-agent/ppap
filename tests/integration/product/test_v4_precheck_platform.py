"""Frozen free prechecks through API, actual PostgreSQL roles and Worker."""

import io
import json
import uuid
from copy import deepcopy

import pytest
from api import create_app
from fastapi.testclient import TestClient
from openpyxl import Workbook
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.prechecks import blocked_targets, render_precheck_record
from product.settings import Settings
from product.standard_policy import config_digest
from worker import process_claimed


class Recognition:
    def structured_completion(self, **request):
        if request["operation"] == "document_classification":
            return "过程特殊特性清单"
        return json.dumps({"customer": "蔚来", "model": "M1", "part": "零件A", "part_number": "P-1"}, ensure_ascii=False)


@pytest.fixture
def system(auth_store_factory):
    settings = Settings(usage_charge_enabled=False, feishu_app_id="v4-precheck", git_sha="precheck-test-build")
    admin = auth_store_factory(settings)
    principal = admin.upsert_feishu_identity("precheck-" + uuid.uuid4().hex, "owner")
    api = build_product_store(settings, objects=admin.objects, role="api")
    worker = build_product_store(settings, objects=admin.objects, role="worker")
    with TestClient(create_app(settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, admin.create_session(principal))
        yield client, worker, admin, principal


def task(system, copies=1):
    client, worker, _, _ = system
    stream = io.BytesIO()
    book = Workbook()
    book.active.append(["过程特殊特性清单", "零件编号", "工序编号", "特性描述"])
    book.active.append(["P-1", "OP10", "尺寸", "CC"])
    book.save(stream)
    book.close()
    data = stream.getvalue()
    files = []
    for _ in range(copies):
        version = client.post("/files", json={"original_filename": "特殊特性清单.xlsx", "byte_size": len(data)}).json()
        path = f"/files/{version['file_id']}/versions/{version['id']}"
        assert client.put(path + "/blob", content=data).status_code == 200
        assert client.post(path + "/finalize").status_code == 200
        validation = str(uuid.uuid4())
        assert client.post("/file-validations", json={"request_id": validation, "source_file_id": version["file_id"], "source_file_version_id": version["id"]}).status_code == 202
        process_claimed(worker, worker.claim_job("validation"), "validation")
        file = {"file_version_id": version["id"], "validation_id": validation, "selection": "selected", "removal_reason": None}
        files.append(file)
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": "蔚来", "files": files}
    response = client.post("/audit-tasks", json=body)
    assert response.status_code == 202, response.text
    for _ in range(copies):
        process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=Recognition())
    path = "/audit-tasks/" + response.json()["id"]
    return path, file


def test_automatic_precheck_is_free_immutable_and_read_only(system):
    client, worker, admin, _ = system
    path, _ = task(system)
    current = client.get(path).json()["precheck"]
    assert current and current["status"] == "queued"
    snapshot_path = path + "/prechecks/" + current["id"]
    with admin._conn() as conn:
        before = conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0]
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0
    for _ in range(2):
        assert client.get(snapshot_path).json() == current
        assert len(client.get(path + "/prechecks").json()["snapshots"]) == 1
    with admin._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == before
    request = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
    assert client.post(path + "/prechecks", json=request).json()["id"] == current["id"]
    assert client.post(path + "/prechecks", json=request).json()["id"] == current["id"]
    recovered = client.get(path + "/prechecks/" + request["request_id"])
    assert recovered.status_code == 200 and recovered.json()["request_id"] == request["request_id"]
    assert recovered.json()["id"] == current["id"]
    claimed = worker.claim_job("free-precheck")
    assert claimed.kind == "task_precheck"
    result = process_claimed(worker, claimed, "free-precheck")
    saved = client.get(snapshot_path).json()
    assert saved["status"] == "succeeded" and saved["result"] == result
    assert result["input_fingerprint"] == config_digest(saved["input"])
    assert result["files"][0]["facts"]
    assert all(not fact["certified"] for fact in result["files"][0]["facts"])
    report = client.get(snapshot_path + "/report")
    assert report.status_code == 200 and "预检检查记录" in report.text
    assert client.get(snapshot_path + "/report").content == report.content
    assert render_precheck_record(saved) == report.content
    with admin._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0
    client.cookies.clear()
    assert client.get(snapshot_path).status_code == 401
    assert client.get(snapshot_path + "/report").status_code == 401


def test_old_snapshot_cannot_become_current_after_revision_and_late_worker(system):
    client, worker, _, _ = system
    path, file = task(system)
    old = client.get(path).json()["precheck"]
    claimed = worker.claim_job("old-precheck")
    body = {"request_id": str(uuid.uuid4()), "task_id": path.split("/")[-1], "expected_revision": 0, "analysis_mode": "standard", "customer_preference": "蔚来", "files": [file]}
    revised = client.post("/audit-tasks", json=body)
    assert revised.status_code == 202, revised.text
    assert revised.json()["precheck"] is None
    process_claimed(worker, claimed, "old-precheck")
    assert client.get(path).json()["precheck"] is None
    historical = client.get(path + "/prechecks/" + old["id"]).json()
    assert historical["current"] is False and historical["input_revision"] == 0
    process_claimed(worker, worker.claim_job("new-recognition"), "new-recognition", rule_ai=Recognition())
    current = client.get(path).json()["precheck"]
    assert current["id"] != old["id"] and current["input_revision"] == 1
    assert len(client.get(path + "/prechecks").json()["snapshots"]) == 2
    assert client.post(path + "/prechecks", json={"request_id": str(uuid.uuid4()), "expected_revision": 0}).status_code == 409


def test_gate_requires_exact_certified_located_current_fact():
    payload = {
        "execution_policy": {"app_revision": "build"},
        "execution_revision": "revision",
        "files": [
            {"file": {"id": "version"}, "selection": "selected", "role": "target", "rule": {"id": "rule"}, "rule_execution": {"checker": {"class": "actual"}}, "required_structure": [{"id": "N04:S1"}]}
        ],
    }
    fact = {
        "source_id": "N04:S1",
        "status": "defect",
        "certainty": "confirmed",
        "critical": True,
        "certified": True,
        "code": "B05",
        "evidence": {"sources": [{"locator": json.dumps({"file_version_id": "version", "cell": "A1"}), "excerpt": "missing mandatory heading"}]},
    }
    result = {
        "input_fingerprint": config_digest(payload),
        "execution_revision": "revision",
        "files": [{"file_version_id": "version", "rule_version_id": "rule", "checker": {"class": "actual"}, "facts": [fact]}],
    }
    assert blocked_targets(payload, result) == {"version"}
    for key, value in [("certified", False), ("certainty", "unknown"), ("critical", False), ("status", "error"), ("code", "W01"), ("code", "S04")]:
        altered = deepcopy(result)
        altered["files"][0]["facts"][0][key] = value
        assert blocked_targets(payload, altered) == set()
    for key, value in [("input_fingerprint", "old"), ("execution_revision", "old")]:
        assert blocked_targets(payload, {**result, key: value}) == set()
    altered = deepcopy(result)
    altered["files"][0]["facts"][0]["evidence"]["sources"][0]["locator"] = json.dumps({"file_version_id": "foreign", "cell": "A1"})
    assert blocked_targets(payload, altered) == set()


def test_failed_snapshot_retries_create_new_history_without_overwriting_old(system):
    client, worker, admin, _ = system
    path, _ = task(system)
    old = client.get(path).json()["precheck"]
    claimed = worker.claim_job("failing-precheck")
    assert worker.complete_job(claimed, "failing-precheck", {"error": "read_failed"}, "failed")
    failed = client.get(path + "/prechecks/" + old["id"]).json()
    request = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
    retried = client.post(path + "/prechecks", json=request)
    assert retried.status_code == 202, retried.text
    assert retried.json()["id"] != old["id"]
    assert client.post(path + "/prechecks", json=request).json()["id"] == retried.json()["id"]
    assert client.post(path + "/prechecks", json={**request, "request_id": str(uuid.uuid4())}).json()["id"] == retried.json()["id"]
    historical = client.get(path + "/prechecks/" + old["id"]).json()
    assert historical == {**failed, "current": False}
    with admin._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='task_precheck'").fetchone()[0] == 2


def test_expired_precheck_lease_cannot_complete_after_reclaim(system):
    client, worker, admin, _ = system
    path, _ = task(system)
    first = worker.claim_job("first")
    with admin._conn() as conn:
        conn.execute("UPDATE engine.jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (first.id,))
    assert client.get(path).json()["precheck"]["status"] == "unknown"
    assert worker.renew_job_lease(first, "first") is False
    assert worker.complete_job(first, "first", {"files": []}) is False
    second = worker.claim_job("second")
    assert first.id == second.id and second.attempt == first.attempt + 1
    assert worker.complete_job(first, "first", {"files": []}) is False
    process_claimed(worker, second, "second")
    assert client.get(path).json()["precheck"]["status"] == "succeeded"


@pytest.mark.parametrize("enforced", [False, True])
def test_gate_runs_inside_confirmation_and_unknown_does_not_block(system, enforced):
    from psycopg.types.json import Jsonb

    client, worker, admin, _ = system
    path, _ = task(system)
    receipt = client.get(path).json()["precheck"]
    process_claimed(worker, worker.claim_job("precheck"), "precheck")
    receipt = client.get(path).json()["precheck"]
    source = receipt["input"]["files"][0]
    version = source["file"]["id"]
    # This injected certified fixture tests the gate, not the customer checker's accuracy.
    result = {
        "input_fingerprint": config_digest(receipt["input"]),
        "execution_revision": receipt["input"]["execution_revision"],
        "files": [
            {
                "file_version_id": version,
                "rule_version_id": source["rule"]["id"],
                "checker": source["rule_execution"]["checker"],
                "facts": [
                    {
                        "source_id": "N04:S1",
                        "status": "defect",
                        "certainty": "confirmed",
                        "critical": True,
                        "certified": True,
                        "code": "B05",
                        "evidence": {"sources": [{"locator": json.dumps({"file_version_id": version, "cell": "A1"}), "excerpt": "independently confirmed missing identity"}]},
                    }
                ],
            }
        ],
    }
    # A known build is required as part of the certification binding.
    admin.settings.precheck_gate_enforced = enforced
    with admin._conn() as conn:
        conn.execute("UPDATE engine.jobs SET result=%s WHERE id=%s", (Jsonb(result), receipt["id"]))
    response = client.post(path + "/confirm", json={"request_id": str(uuid.uuid4()), "expected_revision": 0})
    assert response.status_code == (409 if enforced else 201), response.text
    if enforced:
        assert response.json()["error"] == "precheck_blocked"
        with admin._conn() as conn:
            assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0


def test_scoped_photo_validation_cannot_be_reused_for_another_customer(system):
    from PIL import Image

    client, worker, _, _ = system
    content = io.BytesIO()
    Image.new("RGB", (16, 12), "white").save(content, format="PNG")
    data = content.getvalue()
    version = client.post("/files", json={"original_filename": "sample.png", "byte_size": len(data)}).json()
    path = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.put(path + "/blob", content=data).status_code == 200
    assert client.post(path + "/finalize").status_code == 200
    key = str(uuid.uuid4())
    request = {"request_id": key, "source_file_id": version["file_id"], "source_file_version_id": version["id"], "scope": {"customer_preference": "UAES", "preset_key": "U13"}}
    assert client.post("/file-validations", json=request).status_code == 202
    process_claimed(worker, worker.claim_job("photo-validation"), "photo-validation")
    validation = client.get("/file-validations/" + key).json()
    assert validation["result"]["state"] == "valid"
    assert validation["scope"] == request["scope"]
    file = {"file_version_id": version["id"], "validation_id": key, "selection": "selected", "removal_reason": None}
    body = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": "蔚来", "files": [file]}
    rejected = client.post("/audit-tasks", json=body)
    assert rejected.status_code == 409 and rejected.json()["error"] == "validation_scope_mismatch"
    accepted = client.post("/audit-tasks", json={**body, "request_id": str(uuid.uuid4()), "customer_preference": "UAES"})
    assert accepted.status_code == 202, accepted.text
    process_claimed(worker, worker.claim_job("photo-recognition"), "photo-recognition")
    restored = client.get("/audit-tasks/" + accepted.json()["id"]).json()
    assert restored["files"][0]["validation_scope"] == request["scope"]
    assert restored["files"][0]["match"]["rule"]["body"]["preset_key"] == "U13"


def test_snapshot_and_export_require_same_tenant_task_and_original_access(system):
    client, worker, admin, principal = system
    path, _ = task(system)
    receipt = client.get(path).json()["precheck"]
    process_claimed(worker, worker.claim_job("precheck"), "precheck")
    snapshot_path = path + "/prechecks/" + receipt["id"]
    other = admin.upsert_feishu_identity(principal.tenant_key, "same-tenant-other")
    client.cookies.set(SESSION_COOKIE, admin.create_session(other))
    assert client.get(snapshot_path).status_code == 403
    assert client.get(snapshot_path + "/report").status_code == 403
    assert client.get(path + "/prechecks").status_code == 403
    foreign = admin.upsert_feishu_identity("foreign-" + uuid.uuid4().hex, "owner")
    client.cookies.set(SESSION_COOKIE, admin.create_session(foreign))
    assert client.get(snapshot_path).status_code == 403
    client.cookies.set(SESSION_COOKIE, admin.create_session(principal))
    with admin._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (receipt["input"]["files"][0]["file"]["file_id"], principal.user_id))
    assert client.get(snapshot_path).status_code == 403
    assert client.get(snapshot_path + "/report").status_code == 403


def test_certified_gate_excludes_only_bad_target_and_replays_same_acceptance(system):
    from psycopg.types.json import Jsonb

    client, worker, admin, _ = system
    path, _ = task(system, copies=2)
    process_claimed(worker, worker.claim_job("precheck"), "precheck")
    receipt = client.get(path).json()["precheck"]
    bad = receipt["input"]["files"][0]
    good = receipt["input"]["files"][1]
    result = {
        "input_fingerprint": config_digest(receipt["input"]),
        "execution_revision": receipt["input"]["execution_revision"],
        "files": [
            {
                "file_version_id": bad["file"]["id"],
                "rule_version_id": bad["rule"]["id"],
                "checker": bad["rule_execution"]["checker"],
                "facts": [
                    {
                        "source_id": "N04:S1",
                        "status": "defect",
                        "certainty": "confirmed",
                        "critical": True,
                        "certified": True,
                        "code": "B05",
                        "evidence": {"sources": [{"locator": json.dumps({"file_version_id": bad["file"]["id"], "cell": "A1"}), "excerpt": "independently confirmed missing identity"}]},
                    }
                ],
            }
        ],
    }
    admin.settings.precheck_gate_enforced = True
    with admin._conn() as conn:
        conn.execute("UPDATE engine.jobs SET result=%s WHERE id=%s", (Jsonb(result), receipt["id"]))
    request = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
    accepted = client.post(path + "/confirm", json=request)
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["estimated_points"] == 500
    with admin._conn() as conn:
        targets = conn.execute("SELECT file_version_id FROM product.round_inputs WHERE round_id=%s AND role='target'", (accepted.json()["round_id"],)).fetchall()
        assert targets == [(uuid.UUID(good["file"]["id"]),)]
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 1
    replayed = client.post(path + "/confirm", json=request)
    assert replayed.status_code == 201 and replayed.json()["round_id"] == accepted.json()["round_id"]
