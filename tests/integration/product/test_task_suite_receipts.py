"""Task confirmation receipts expose the actual adopted and frozen Suite rules."""

import io
import json
import uuid

import pytest
from api import create_app
from audit.presets import preset_document_type, preset_rule
from fastapi.testclient import TestClient
from openpyxl import Workbook
from PIL import Image
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore
from worker import process_claimed


@pytest.fixture
def system(auth_store_factory, core_system):
    settings = Settings(usage_charge_enabled=False, feishu_app_id="suite-receipts", git_sha="a" * 40)
    admin = auth_store_factory(settings)
    principal = admin.upsert_feishu_identity("suite-receipt", "owner")
    core_system.install(admin)
    worker = build_product_store(settings, objects=admin.objects, role="worker")
    api = build_product_store(settings, objects=admin.objects, role="api")
    with TestClient(create_app(settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, admin.create_session(principal))
        yield client, worker, admin, principal


def prepare(system, core_system, customer, entry, *, reference=False, photo=False, choose_customer=True):
    client, worker, admin, principal = system
    core_system.adopt_standard(admin, principal, f"{customer}:{entry}")
    stream = io.BytesIO()
    if photo:
        Image.new("RGB", (16, 12), "white").save(stream, format="PNG")
    else:
        book = Workbook()
        book.active.append(["客户", customer, "零件号", "P-1"])
        book.save(stream)
        book.close()
    body = stream.getvalue()
    file = client.post("/files", json={"original_filename": "原件.png" if photo else "原件.xlsx", "byte_size": len(body)}).json()
    path = f"/files/{file['file_id']}/versions/{file['id']}"
    assert client.put(path + "/blob", content=body).status_code == 200
    assert client.post(path + "/finalize").status_code == 200
    validation = {"request_id": str(uuid.uuid4()), "source_file_id": file["file_id"], "source_file_version_id": file["id"]}
    if photo:
        validation["scope"] = {"customer_preference": "UAES", "preset_key": "U13"}
    assert client.post("/file-validations", json=validation).status_code == 202
    process_claimed(worker, worker.claim_job("validation"), "validation")
    selected = {"file_version_id": file["id"], "validation_id": validation["request_id"], "selection": "selected", "removal_reason": None, "reference_only": reference}
    command = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": customer if choose_customer else None, "files": [selected]}
    response = client.post("/audit-tasks", json=command)
    assert response.status_code == 202, response.text

    class Metadata:
        def structured_completion(self, *, operation, **_kwargs):
            if operation == "document_classification":
                return preset_document_type(preset_rule(customer, entry))
            return json.dumps({"customer": customer, "part_number": "P-1"})

    process_claimed(worker, worker.claim_job("recognition"), "recognition", rule_ai=Metadata())
    return "/audit-tasks/" + response.json()["id"], command


def publish_edit(system, key):
    _, _, admin, principal = system
    releases = SuiteReleaseStore(admin.settings, admin.objects, role="api")
    active = releases.active(principal, key)
    suite = releases.suites.resolve_suite(active["current_revision_id"], principal.tenant_id)
    rules = json.loads(suite.files["rules.json"])
    rules["rules"][0]["rule_to_check"] += "（企业已采用修订）"
    candidate = releases.save_candidate(principal, active["current_revision_id"], {"rules.json": json.dumps(rules, ensure_ascii=False)})
    releases.publish(principal, key, candidate["id"], active["revision"], str(uuid.uuid4()), force_unverified=True)
    return candidate


@pytest.mark.parametrize(("customer", "entry"), [("UAES", "U12"), ("理想", "L12"), ("蔚来", "N04"), ("蔚来", "N02")])
def test_confirm_and_request_readback_share_exact_current_suite_rule(system, core_system, customer, entry):
    client, _, _, _ = system
    path, _ = prepare(system, core_system, customer, entry)
    before = client.get(path).json()
    expected = before["files"][0]["match"]["rule"]
    assert expected["body"]["suite_key"] == f"{customer}:{entry}"
    assert expected["body"]["checker_binding"] and "rule_profile" not in expected["body"]
    command = {"request_id": str(uuid.uuid4()), "expected_revision": before["input_revision"]}
    response = client.post(path + "/confirm", json=command)
    assert response.status_code == 201, response.text
    accepted = response.json()
    assert accepted["state"] == "reviewing" and accepted["round_id"]
    assert accepted["acceptance"] == {"request_id": command["request_id"], "state": "accepted", "reason": None}
    file = accepted["files"][0]
    assert file["file"] == before["files"][0]["file"]
    assert file["match"] == before["files"][0]["match"]
    assert file["audit"]["rule"] == expected
    assert file["audit"]["file_version_id"] == file["file"]["id"]
    assert file["audit"]["round_id"] == accepted["round_id"]
    assert client.get(path, params={"request_id": command["request_id"]}).json() == accepted
    assert client.post(path + "/confirm", json=command).json() == accepted
    original_round = client.get("/rounds/" + accepted["round_id"]).json()
    publish_edit(system, f"{customer}:{entry}")
    restored = client.get(path).json()
    assert restored["files"][0]["match"]["rule"] == restored["files"][0]["audit"]["rule"] == expected
    assert client.get("/rounds/" + accepted["round_id"]).json() == original_round


def test_publish_after_preview_does_not_misrepresent_new_round_as_old_rules(system, core_system):
    client, _, admin, _ = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    before = client.get(path).json()
    published = publish_edit(system, "UAES:U12")
    current = client.get(path).json()
    assert current["input_revision"] == before["input_revision"]
    assert current["files"][0]["match"]["rule"] != before["files"][0]["match"]["rule"]
    command = {"request_id": str(uuid.uuid4()), "expected_revision": before["input_revision"]}
    response = client.post(path + "/confirm", json=command)
    assert response.status_code == 201, response.text
    accepted = response.json()
    actual = accepted["files"][0]["audit"]["rule"]
    assert actual == accepted["files"][0]["match"]["rule"] == current["files"][0]["match"]["rule"]
    assert actual != before["files"][0]["match"]["rule"]
    # The existing client correctly refuses to claim that this different body
    # was the old preview. A Suite CAS is not part of the current request API.
    frozen = client.get("/rounds/" + accepted["round_id"]).json()
    with admin._conn() as conn:
        execution = conn.execute("SELECT rule_execution FROM product.round_inputs WHERE round_id=%s AND role='target'", (frozen["id"],)).fetchone()[0]
    assert execution["suite_revision_id"] == published["id"]
    assert client.get(path, params={"request_id": command["request_id"]}).json() == accepted


def test_scoped_photo_keeps_customer_scope_and_reference_boundaries(system, core_system):
    client, _, admin, _ = system
    path, original = prepare(system, core_system, "UAES", "U13", photo=True)
    other = client.post("/audit-tasks", json={**original, "request_id": str(uuid.uuid4()), "customer_preference": "蔚来"})
    assert other.status_code == 409 and other.json()["error"] == "validation_scope_mismatch"
    before = client.get(path).json()
    assert before["files"][0]["match"]["rule"]["body"]["suite_key"] == "UAES:U13"
    command = {"request_id": str(uuid.uuid4()), "expected_revision": 0}
    # Verify the acceptance guard independently of the upload guard: persisted
    # customer state cannot make an already scoped photo valid for another scope.
    with admin._conn() as conn:
        conn.execute("UPDATE product.audit_drafts SET customer_preference='蔚来' WHERE id=%s", (before["id"],))
    refused = client.post(path + "/confirm", json=command)
    assert refused.status_code == 409 and refused.json()["error"] == "customer_scope_conflict"
    assert client.get("/rounds").json()["rounds"] == []
    with admin._conn() as conn:
        conn.execute("UPDATE product.audit_drafts SET customer_preference='UAES' WHERE id=%s", (before["id"],))
    accepted = client.post(path + "/confirm", json=command)
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["files"][0]["audit"]["rule"] == before["files"][0]["match"]["rule"]


def test_reference_only_task_cannot_become_a_paid_target_from_suite_projection(system, core_system):
    client, _, _, _ = system
    path, _ = prepare(system, core_system, "蔚来", "N04", reference=True)
    task = client.get(path).json()
    assert task["files"][0]["match"]["state"] == "reference"
    assert task["files"][0]["match"]["rule"] is None and task["estimated_points"] == 0
    rejected = client.post(path + "/confirm", json={"request_id": str(uuid.uuid4()), "expected_revision": 0})
    assert rejected.status_code == 409
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []


def test_explicit_precheck_uses_projected_suite_without_legacy_profile(system, core_system):
    client, worker, _, _ = system
    path, _ = prepare(system, core_system, "蔚来", "N04", choose_customer=False)
    task = client.get(path).json()
    assert task["customer_preference"] is None
    assert task["files"][0]["match"]["rule"]["body"]["suite_key"] == "蔚来:N04"
    assert "rule_profile" not in task["files"][0]["match"]["rule"]["body"]
    response = client.post(path + "/prechecks", json={"request_id": str(uuid.uuid4()), "expected_revision": 0})
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["input"]["files"][0]["rule"] == task["files"][0]["match"]["rule"]
    process_claimed(worker, worker.claim_job("explicit-precheck"), "explicit-precheck")
    loaded = client.get(path + "/prechecks/" + receipt["id"]).json()
    assert loaded["status"] == "succeeded", loaded
    assert client.get("/rounds").json()["rounds"] == []
