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


def prepare(system, core_system, customer, entry, *, reference=False, photo=False, choose_customer=True, copies=1, last_reference=False):
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
    selected = []
    for index in range(copies):
        file = client.post("/files", json={"original_filename": "原件.png" if photo else f"原件{index}.xlsx", "byte_size": len(body)}).json()
        path = f"/files/{file['file_id']}/versions/{file['id']}"
        assert client.put(path + "/blob", content=body).status_code == 200
        assert client.post(path + "/finalize").status_code == 200
        validation = {"request_id": str(uuid.uuid4()), "source_file_id": file["file_id"], "source_file_version_id": file["id"]}
        if photo:
            validation["scope"] = {"customer_preference": "UAES", "preset_key": "U13"}
        assert client.post("/file-validations", json=validation).status_code == 202
        process_claimed(worker, worker.claim_job("validation"), "validation")
        selected.append(
            {
                "file_version_id": file["id"],
                "validation_id": validation["request_id"],
                "selection": "selected",
                "removal_reason": None,
                "reference_only": reference or last_reference and index == copies - 1,
            }
        )
    command = {"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": customer if choose_customer else None, "files": selected}
    response = client.post("/audit-tasks", json=command)
    assert response.status_code == 202, response.text

    class Metadata:
        def structured_completion(self, *, operation, **_kwargs):
            if operation == "document_classification":
                return preset_document_type(preset_rule(customer, entry))
            return json.dumps({"customer": customer, "part_number": "P-1"})

    for _ in range(copies):
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
    command = {"request_id": str(uuid.uuid4()), "expected_revision": before["input_revision"], "expected_confirmation_basis_digest": before["confirmation_basis_digest"]}
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
    command = {"request_id": str(uuid.uuid4()), "expected_revision": before["input_revision"], "expected_confirmation_basis_digest": before["confirmation_basis_digest"]}
    response = client.post(path + "/confirm", json=command)
    assert response.status_code == 409 and response.json() == {"error": "task_confirmation_changed"}
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []
    restored = client.get(path, params={"request_id": command["request_id"]}).json()
    assert restored["acceptance"]["state"] == "rejected"
    assert restored["confirmation_basis_digest"] == current["confirmation_basis_digest"] != before["confirmation_basis_digest"]
    # A rejected operation is never reinterpreted as a fresh confirmation.
    assert client.post(path + "/confirm", json={**command, "expected_confirmation_basis_digest": current["confirmation_basis_digest"]}).status_code == 409
    command = {**command, "request_id": str(uuid.uuid4()), "expected_confirmation_basis_digest": current["confirmation_basis_digest"]}
    response = client.post(path + "/confirm", json=command)
    assert response.status_code == 201, response.text
    accepted = response.json()
    actual = accepted["files"][0]["audit"]["rule"]
    assert actual == accepted["files"][0]["match"]["rule"] == current["files"][0]["match"]["rule"]
    assert actual != before["files"][0]["match"]["rule"]
    # Only an explicit fresh confirmation can freeze the new enterprise head.
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
    command = {"request_id": str(uuid.uuid4()), "expected_revision": 0, "expected_confirmation_basis_digest": before["confirmation_basis_digest"]}
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
    rejected = client.post(path + "/confirm", json={"request_id": str(uuid.uuid4()), "expected_revision": 0, "expected_confirmation_basis_digest": task["confirmation_basis_digest"]})
    assert rejected.status_code == 400 and rejected.json() == {"error": "invalid_task_confirmation"}
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


def confirmation(task, *, request_id=None):
    return {
        "request_id": request_id or str(uuid.uuid4()),
        "expected_revision": task["input_revision"],
        "expected_confirmation_basis_digest": task["confirmation_basis_digest"],
    }


def assert_no_admission(system):
    client, _, admin, _ = system
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []
    with admin._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='audit'").fetchone()[0] == 0


@pytest.mark.parametrize("basis", [None, "", "a" * 63, "A" * 64, 123])
def test_public_confirmation_cannot_omit_or_fabricate_basis_format(system, core_system, basis):
    client, _, _, _ = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    task = client.get(path).json()
    command = {**confirmation(task), "expected_confirmation_basis_digest": basis}
    if basis is None:
        command.pop("expected_confirmation_basis_digest")
    refused = client.post(path + "/confirm", json=command)
    assert refused.status_code == 400 and refused.json() == {"error": "invalid_task_confirmation"}
    assert_no_admission(system)


def test_changed_code_with_identical_visible_rules_requires_fresh_confirmation(system, core_system):
    client, _, admin, principal = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    before = client.get(path).json()
    releases = SuiteReleaseStore(admin.settings, admin.objects, role="api")
    active = releases.active(principal, "UAES:U12")
    source = releases.suites.resolve_suite(active["current_revision_id"], principal.tenant_id)
    candidate = releases.save_candidate(principal, source.revision.id, {"checker.py": source.files["checker.py"].decode() + "\n# reviewed code revision\n"})
    releases.publish(principal, "UAES:U12", candidate["id"], active["revision"], str(uuid.uuid4()), force_unverified=True)
    current = client.get(path).json()
    assert current["files"][0]["match"]["rule"] == before["files"][0]["match"]["rule"]
    assert current["confirmation_basis_digest"] != before["confirmation_basis_digest"]
    refused = client.post(path + "/confirm", json=confirmation(before))
    assert refused.status_code == 409 and refused.json()["error"] == "task_confirmation_changed"
    assert_no_admission(system)


def test_related_suite_publication_changes_basis_without_rewriting_target_rules(system, core_system):
    client, _, _, _ = system
    path, _ = prepare(system, core_system, "蔚来", "N02")
    before = client.get(path).json()
    publish_edit(system, "蔚来:N05")
    current = client.get(path).json()
    assert current["input_revision"] == before["input_revision"]
    assert current["files"][0]["match"]["rule"] == before["files"][0]["match"]["rule"]
    assert current["confirmation_basis_digest"] != before["confirmation_basis_digest"]
    refused = client.post(path + "/confirm", json=confirmation(before))
    assert refused.status_code == 409 and refused.json()["error"] == "task_confirmation_changed"
    assert_no_admission(system)


def test_validation_risk_change_alone_changes_confirmation_basis(system, core_system):
    from audit.checker_core import checker_digest
    from audit.standard_suites import library_revision
    from product.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport

    client, _, admin, principal = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    before = client.get(path).json()
    releases = SuiteReleaseStore(admin.settings, admin.objects, role="api")
    active = releases.active(principal, "UAES:U12")
    suite = releases.suites.resolve_suite(active["current_revision_id"], principal.tenant_id)
    # Controlled host validation receipt only: exercise the risk-state boundary,
    # without claiming a live model/oracle validated this standard Suite.
    report = SuiteValidationReport(
        suite_revision_id=suite.revision.id,
        suite_digest=suite.revision.resolved_digest,
        program_digest=checker_digest({path: body for path, body in suite.files.items() if path in {"checker.py", "resources.py"}}),
        library_revision=library_revision(),
        oracle_revision="controlled-confirmation-risk-receipt",
        cases=tuple(
            CaseEvaluation(
                case_id=f"case-{i}",
                ordinal=i,
                input_digest=f"{i:064x}",
                split="holdout" if i == 4 else "training",
                coverage=True,
                clauses=tuple(
                    ClauseEvaluation(clause_id=row["id"], expected="PASS", actual="PASS", evidence_locatable=True, checks_performed=1) for row in json.loads(suite.files["rules.json"])["rules"]
                ),
            )
            for i in range(1, 5)
        ),
    )
    assert releases.record_validation(principal, suite.revision.id, report, expected_revision=0)["verified"] is True
    current = client.get(path).json()
    assert current["files"][0]["match"]["rule"] == before["files"][0]["match"]["rule"]
    assert current["confirmation_basis_digest"] != before["confirmation_basis_digest"]
    refused = client.post(path + "/confirm", json=confirmation(before))
    assert refused.status_code == 409 and refused.json()["error"] == "task_confirmation_changed"
    assert_no_admission(system)


def test_concurrent_same_stale_request_replays_one_definite_rejection(system, core_system):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, _, admin, principal = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    command = confirmation(client.get(path).json())
    publish_edit(system, "UAES:U12")
    barrier = Barrier(2)

    def confirm():
        barrier.wait(timeout=5)
        try:
            admin.confirm_audit_task(principal, path.rsplit("/", 1)[-1], **command, require_confirmation_basis=True)
        except ValueError as error:
            return str(error)
        pytest.fail("A stale confirmation was accepted")

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: confirm(), range(2))) == ["task_confirmation_changed"] * 2
    receipt = client.get(path, params={"request_id": command["request_id"]}).json()["acceptance"]
    assert receipt["state"] == "rejected"
    assert_no_admission(system)
    with admin._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE id=%s AND kind='task_confirmation' AND result->>'state'='rejected'", (command["request_id"],)).fetchone()[0] == 1


def prepare_card(system, path):
    from product.feishu import CardAction

    client, _, admin, principal = system
    admin.settings.feishu_verification_token = "controlled-card-verification"
    task = client.get(path).json()
    claimed, actor = admin.claim_task_notification("notification", task_id=task["id"])
    delivery = admin.start_task_notification(claimed, "notification", actor)
    callback = delivery["card"]["body"]["elements"][1]["behaviors"][0]["value"]
    assert callback == {"action": "confirm_audit_task", "notification_id": claimed.id}
    assert admin.finish_task_notification(claimed, "notification", {"state": "sent", "reason": None, "message_id": "controlled-message"})
    with admin._conn() as conn:
        stored = conn.execute("SELECT payload->'confirmation' FROM engine.jobs WHERE id=%s", (claimed.id,)).fetchone()[0]
    assert stored["expected_confirmation_basis_digest"] == task["confirmation_basis_digest"]
    return CardAction(principal.tenant_key, principal.subject, claimed.id, "controlled-message"), stored


def test_sent_card_cannot_confirm_new_suite_or_replace_its_operation_basis_via_http(system, core_system):
    client, _, admin, _ = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    action, command = prepare_card(system, path)
    pending = admin.enqueue_task_confirmation(action)
    assert pending["state"] == "pending"
    publish_edit(system, "UAES:U12")
    current = client.get(path).json()
    replaced = client.post(path + "/confirm", json={**command, "expected_confirmation_basis_digest": current["confirmation_basis_digest"]})
    assert replaced.status_code == 409 and replaced.json()["error"] == "idempotency_conflict"
    assert_no_admission(system)
    claimed = admin.claim_task_confirmation("card")
    receipt = admin.process_task_confirmation(claimed, "card", usage_policy=lambda _actor: pytest.fail("Stale confirmation must not query quota"))
    assert receipt["state"] == "rejected" and "审核依据已更新" in receipt["reason"]
    assert admin.enqueue_task_confirmation(action) == receipt
    assert client.get(path, params={"request_id": command["request_id"]}).json()["acceptance"] == receipt
    assert_no_admission(system)


def test_runtime_code_change_requires_new_basis_even_with_same_suite(system, core_system):
    client, _, admin, _ = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    before = client.get(path).json()
    admin.settings.git_sha = "b" * 40
    current = client.get(path).json()
    assert current["files"][0]["match"]["rule"] == before["files"][0]["match"]["rule"]
    assert current["confirmation_basis_digest"] != before["confirmation_basis_digest"]
    refused = client.post(path + "/confirm", json=confirmation(before))
    assert refused.status_code == 409 and refused.json()["error"] == "task_confirmation_changed"
    assert_no_admission(system)


def test_confirmation_holds_publication_lock_until_frozen_round_commits(system, core_system, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from product.store.suite_releases import _lock
    from psycopg.errors import LockNotAvailable

    client, _, admin, principal = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    before = client.get(path).json()
    command = confirmation(before)
    entered, resume = Event(), Event()
    check = admin._check_usage_policy

    def pause_before_admission(*args, **kwargs):
        entered.set()
        assert resume.wait(timeout=10), "Test did not release admission"
        return check(*args, **kwargs)

    monkeypatch.setattr(admin, "_check_usage_policy", pause_before_admission)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(admin.confirm_audit_task, principal, before["id"], **command, require_confirmation_basis=True)
        try:
            assert entered.wait(timeout=10)
            with admin._conn() as conn, conn.transaction():
                conn.execute("SET LOCAL lock_timeout='100ms'")
                with pytest.raises(LockNotAvailable):
                    _lock(conn, principal.tenant_id)
        finally:
            resume.set()
        accepted = future.result(timeout=10)
    assert accepted["files"][0]["audit"]["rule"] == before["files"][0]["match"]["rule"]
    publish_edit(system, "UAES:U12")
    # Accepted operation recovery never needs a currently valid basis and never
    # calls quota again. Legacy clients may recover without the new field.
    replay = client.post(path + "/confirm", json={"request_id": command["request_id"], "expected_revision": command["expected_revision"]})
    assert replay.status_code == 201
    for key in ("round_id", "files", "acceptance", "confirmation_scope"):
        assert replay.json()[key] == accepted[key]


def certify_precheck_block(system, receipt, version_id):
    from product.standard_policy import config_digest
    from psycopg.types.json import Jsonb

    _, _, admin, _ = system
    source = next(row for row in receipt["input"]["files"] if row["file"]["id"] == version_id)
    # Controlled certified evidence tests admission only, not checker accuracy.
    result = {
        "input_fingerprint": config_digest(receipt["input"]),
        "execution_revision": receipt["input"]["execution_revision"],
        "files": [
            {
                "file_version_id": version_id,
                "rule_version_id": source["rule"]["id"],
                "suite_execution": source["rule_execution"],
                "facts": [
                    {
                        "source_id": "N04:S1",
                        "status": "defect",
                        "certainty": "confirmed",
                        "critical": True,
                        "certified": True,
                        "code": "B05",
                        "evidence": {"sources": [{"locator": json.dumps({"file_version_id": version_id, "cell": "A1"}), "excerpt": "independently confirmed missing identity"}]},
                    }
                ],
            }
        ],
    }
    with admin._conn() as conn:
        conn.execute("UPDATE engine.jobs SET result=%s WHERE id=%s AND status='succeeded'", (Jsonb(result), receipt["id"]))


@pytest.mark.parametrize("all_blocked", [False, True])
def test_scope_keeps_references_and_excludes_only_certified_targets(system, core_system, all_blocked):
    client, worker, admin, _ = system
    admin.settings.precheck_gate_enforced = True
    path, _ = prepare(system, core_system, "蔚来", "N04", copies=2 if all_blocked else 3, last_reference=True)
    before = client.get(path).json()
    assert before["confirmation_basis_digest"]
    process_claimed(worker, worker.claim_job("precheck"), "precheck")
    receipt = client.get(path).json()["precheck"]
    bad = before["files"][0]["file"]["id"]
    certify_precheck_block(system, receipt, bad)
    current = client.get(path).json()
    scope = current["confirmation_scope"]
    assert current["input_revision"] == before["input_revision"]
    assert current["confirmation_basis_digest"] != before["confirmation_basis_digest"]
    assert all(file["selection"] == "selected" for file in current["files"])
    assert scope["reference_file_version_ids"] == [before["files"][-1]["file"]["id"]]
    assert scope["excluded_files"] == [{"file_version_id": bad, "reason": "预检已确认的阻断缺陷，需修正后重新提交"}]
    assert scope["target_file_version_ids"] == ([] if all_blocked else [before["files"][1]["file"]["id"]])
    assert current["estimated_points"] == (0 if all_blocked else 500)
    refused = client.post(path + "/confirm", json=confirmation(before))
    assert refused.status_code == 409 and refused.json()["error"] == "task_confirmation_changed"
    assert_no_admission(system)
    command = confirmation(current)
    response = client.post(path + "/confirm", json=command)
    if all_blocked:
        assert response.status_code == 409 and response.json()["error"] == "precheck_blocked"
        assert_no_admission(system)
        return
    assert response.status_code == 201, response.text
    accepted = response.json()
    assert accepted["confirmation_scope"] == scope
    assert accepted["estimated_points"] == 500
    excluded = next(file for file in accepted["files"] if file["file"]["id"] == bad)
    assert excluded["selection"] == "removed" and excluded["removal_reason"] == scope["excluded_files"][0]["reason"] and excluded["audit"] is None
    assert len([file for file in accepted["files"] if file["audit"]]) == 1
    assert accepted["files"][-1]["match"]["state"] == "reference" and accepted["files"][-1]["audit"] is None
    assert client.get("/usage").json()["events"] == []
    # Later mutable preparation cannot rewrite the accepted range.
    with admin._conn() as conn:
        conn.execute("UPDATE engine.jobs SET result='{}'::jsonb WHERE id=%s", (receipt["id"],))
    replay = client.post(path + "/confirm", json=command)
    assert replay.status_code == 201 and replay.json()["confirmation_scope"] == scope
    assert replay.json()["files"] == accepted["files"]


def test_http_acceptance_and_card_enqueue_serialize_one_original_intent(system, core_system, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from psycopg.errors import LockNotAvailable

    client, _, admin, _ = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    action, command = prepare_card(system, path)
    # The API app closes over this instance; patch its class boundary so the
    # real HTTP route is paused only after its locks and basis comparison.
    entered, resume = Event(), Event()
    cls = type(admin)
    original = cls._check_usage_policy

    def pause(self, *args, **kwargs):
        entered.set()
        assert resume.wait(timeout=10)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(cls, "_check_usage_policy", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        submitted = pool.submit(client.post, path + "/confirm", json=command)
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(LockNotAvailable):
                admin.enqueue_task_confirmation(action)
        finally:
            resume.set()
        response = submitted.result(timeout=10)
    assert response.status_code == 201, response.text
    accepted = response.json()
    assert admin.enqueue_task_confirmation(action) == accepted["acceptance"]
    assert client.post(path + "/confirm", json=command).json()["round_id"] == accepted["round_id"]
    with admin._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='audit'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE id=%s AND kind='task_confirmation'", (command["request_id"],)).fetchone()[0] == 1
    assert client.get("/usage").json()["events"] == []


def test_sent_card_intent_is_immutable_even_before_callback_is_enqueued(system, core_system):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, _, admin, _ = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    action, command = prepare_card(system, path)
    publish_edit(system, "UAES:U12")
    current = client.get(path).json()
    barrier = Barrier(2)

    def confirm_changed():
        barrier.wait(timeout=5)
        return client.post(path + "/confirm", json={**command, "expected_confirmation_basis_digest": current["confirmation_basis_digest"]})

    def enqueue_original():
        barrier.wait(timeout=5)
        return admin.enqueue_task_confirmation(action)

    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(confirm_changed)
        queued = pool.submit(enqueue_original)
        refused, receipt = request.result(timeout=10), queued.result(timeout=10)
    assert refused.status_code == 409 and refused.json()["error"] == "idempotency_conflict"
    assert receipt["state"] == "pending"
    assert_no_admission(system)
    rejected = admin.process_task_confirmation(admin.claim_task_confirmation("card"), "card")
    assert rejected["state"] == "rejected" and "审核依据已更新" in rejected["reason"]
    assert_no_admission(system)


def test_legacy_accepted_operation_without_receipt_job_replays_without_new_writes(system, core_system):
    client, _, admin, principal = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    before = client.get(path).json()
    key = str(uuid.uuid4())
    accepted = admin.confirm_audit_task(principal, before["id"], key, before["input_revision"])
    publish_edit(system, "UAES:U12")
    with admin._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE id=%s", (key,)).fetchone()[0] == 0
        counts = conn.execute("SELECT (SELECT count(*) FROM product.audit_rounds), (SELECT count(*) FROM engine.jobs), (SELECT count(*) FROM product.usage_events)").fetchone()
    replay = client.post(path + "/confirm", json={"request_id": key, "expected_revision": before["input_revision"]})
    assert replay.status_code == 201, replay.text
    for field in ("round_id", "files", "confirmation_scope", "acceptance"):
        assert replay.json()[field] == accepted[field]
    with admin._conn() as conn:
        assert conn.execute("SELECT (SELECT count(*) FROM product.audit_rounds), (SELECT count(*) FROM engine.jobs), (SELECT count(*) FROM product.usage_events)").fetchone() == counts


@pytest.mark.parametrize(("customer", "entry"), [("UAES", "U12"), ("理想", "L12"), ("蔚来", "N04"), ("蔚来", "N02")])
def test_background_worker_preserves_real_guest_clause_deadlines(system, core_system, customer, entry):
    from concurrent.futures import ThreadPoolExecutor

    client, worker, admin, _ = system
    core_system.install(admin, guest_process=True)
    path, _ = prepare(system, core_system, customer, entry)
    task = client.get(path).json()
    accepted = client.post(path + "/confirm", json=confirmation(task))
    assert accepted.status_code == 201, accepted.text

    def work():
        while True:
            job = worker.claim_job("background-core")
            assert job is not None
            process_claimed(worker, job, "background-core")
            if job.kind == "audit":
                return

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(work).result(timeout=30)
    result = client.get("/rounds/" + accepted.json()["round_id"]).json()
    assert result["execution"] == "completed", result
    assert result["executor"] == "ppap-checker-core", result
    assert result["findings"] and all(row["status"] != "ERR" for row in result["findings"]), result["findings"]
    # This deliberately sparse workbook proves deadline execution, not a
    # supplier-document oracle. Missing content stays NG/UNABLE, never PASS.
    assert any(row["status"] in {"NG", "UNABLE"} for row in result["findings"]), result["findings"]
    assert len(core_system.audit_jobs) == 1
    assert core_system.audit_jobs[0].allow_model_calls is False


def verify_current_suite(system, key):
    """Controlled passing host proof for the admission/recovery product journey."""
    from audit.checker_core import checker_digest
    from audit.standard_suites import library_revision
    from product.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport
    from product.store.suite_releases import current_selection

    _, _, admin, principal = system
    releases = SuiteReleaseStore(admin.settings, admin.objects, role="api")
    active = releases.active(principal, key)
    suite = releases.suites.resolve_suite(active["current_revision_id"], principal.tenant_id)
    with admin._conn() as conn:
        selected = current_selection(conn, principal.tenant_id, key)
        related = {key: releases.suites.resolve_suite(revision, principal.tenant_id, conn=conn).revision.resolved_digest for key, revision in selected["related_revision_ids"].items()}
    report = SuiteValidationReport(
        suite_revision_id=suite.revision.id,
        suite_digest=suite.revision.resolved_digest,
        program_digest=checker_digest({path: body for path, body in suite.files.items() if path in {"checker.py", "resources.py"}}),
        library_revision=library_revision(),
        related_suite_digests=related,
        oracle_revision="controlled-admission-recovery-proof",
        cases=tuple(
            CaseEvaluation(
                case_id=f"case-{i}",
                ordinal=i,
                input_digest=f"{i:064x}",
                split="holdout" if i == 4 else "training",
                coverage=True,
                clauses=tuple(
                    ClauseEvaluation(clause_id=row["id"], expected="PASS", actual="PASS", evidence_locatable=True, checks_performed=1) for row in json.loads(suite.files["rules.json"])["rules"]
                ),
            )
            for i in range(1, 5)
        ),
    )
    proof = releases.record_validation(principal, suite.revision.id, report, expected_revision=selected["validation"]["write_revision"])
    assert proof["verified"] is True
    return releases, active


@pytest.mark.parametrize("recovery", ["revalidate", "explicit_risk_adoption"])
def test_operator_recovers_stale_suite_preparation_and_original_confirmation_receipt(system, core_system, recovery):
    client, _, admin, principal = system
    path, _ = prepare(system, core_system, "蔚来", "N02")
    releases, active = verify_current_suite(system, "蔚来:N02")
    releases.publish(principal, "蔚来:N02", active["current_revision_id"], active["revision"], str(uuid.uuid4()), force_unverified=False)
    verified = client.get(path).json()
    assert verified["files"][0]["suite_admission"]["state"] == "ready"
    publish_edit(system, "蔚来:N05")
    current = client.get(path).json()
    file = current["files"][0]
    assert file["match"] == verified["files"][0]["match"]
    assert file["suite_admission"] == {
        "state": "validation_required",
        "suite_key": "蔚来:N02",
        "suite_revision_id": active["current_revision_id"],
        "verification_status": "stale",
        "force_unverified": False,
        "samples_not_rerun": False,
    }
    assert current["confirmation_scope"] == verified["confirmation_scope"]
    preparation = client.post(path + "/prechecks", json={"request_id": str(uuid.uuid4()), "expected_revision": current["input_revision"]})
    assert preparation.status_code == 202, preparation.text
    assert preparation.json()["input"]["files"][0]["rule"] == file["match"]["rule"]
    command = confirmation(current)
    # Simulate losing the refusal response: only an independent read is used to
    # settle the original operation, and the journey ends with a fresh admission.
    client.post(path + "/confirm", json=command)
    with TestClient(create_app(admin.settings, auth_store=build_product_store(admin.settings, objects=admin.objects, role="api"))) as reopened:
        reopened.cookies.set(SESSION_COOKIE, admin.create_session(principal))
        restored = reopened.get(path, params={"request_id": command["request_id"]}).json()
        assert restored["acceptance"]["request_id"] == command["request_id"]
        assert restored["acceptance"]["state"] == "rejected"
        assert "重新验证" in restored["acceptance"]["reason"]
        assert_no_admission(system)
        if recovery == "revalidate":
            verify_current_suite(system, "蔚来:N02")
        else:
            active = releases.active(principal, "蔚来:N02")
            releases.publish(principal, "蔚来:N02", active["current_revision_id"], active["revision"], str(uuid.uuid4()), force_unverified=True)
        ready = reopened.get(path).json()
        assert ready["files"][0]["suite_admission"]["state"] == "ready"
        assert ready["files"][0]["match"] == file["match"]
        assert reopened.get(path, params={"request_id": command["request_id"]}).json()["acceptance"] == restored["acceptance"]
        request = confirmation(ready)
        accepted = reopened.post(path + "/confirm", json=request)
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["acceptance"]["state"] == "accepted"
        assert accepted.json()["files"][0]["audit"]["rule"] == file["match"]["rule"]
        publish_edit(system, "蔚来:N05")
        replayed = reopened.post(path + "/confirm", json=request).json()
        assert {key: replayed[key] for key in ("round_id", "acceptance", "confirmation_scope", "files")} == {
            key: accepted.json()[key] for key in ("round_id", "acceptance", "confirmation_scope", "files")
        }
        with admin._conn() as conn:
            assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='audit'").fetchone()[0] == 1


def test_library_update_keeps_verified_suite_available_with_samples_notice(system, core_system, monkeypatch):
    client, _, _, principal = system
    path, _ = prepare(system, core_system, "UAES", "U12")
    releases, active = verify_current_suite(system, "UAES:U12")
    releases.publish(principal, "UAES:U12", active["current_revision_id"], active["revision"], str(uuid.uuid4()), force_unverified=False)
    monkeypatch.setattr("audit.standard_suites.library_revision", lambda: "updated-shared-library")
    current = client.get(path).json()
    assert current["files"][0]["suite_admission"] == {
        "state": "ready",
        "suite_key": "UAES:U12",
        "suite_revision_id": active["current_revision_id"],
        "verification_status": "verified",
        "force_unverified": False,
        "samples_not_rerun": True,
    }
    accepted = client.post(path + "/confirm", json=confirmation(current))
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["acceptance"]["state"] == "accepted"
