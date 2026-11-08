"""Real PostgreSQL optimizer admission, fenced dispatch and independent read-back.

The controlled author is not live model-quality acceptance. No provider is called.
"""

import hashlib
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent.checker_development import DevelopmentOutcome
from agent.checker_spec import CheckerArtifact
from agent.protocol import DevelopmentEnvelope
from agent.run import ExecutionContext
from api import create_app
from audit.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from openpyxl import Workbook
from product import build_product_store
from product.files import ObjectExists, ObjectMissing
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.checker_development import CheckerDevelopmentStore
from product.store.suite_releases import SuiteReleaseStore
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from worker.checker_optimizer import development_handler, materialize_request


class Objects:
    def __init__(self):
        self.data = {}

    def put_exclusive(self, key, body, content_type):
        if key in self.data:
            raise ObjectExists()
        self.data[key] = body

    def get(self, key, *, max_bytes=None):
        if key not in self.data:
            raise ObjectMissing()
        data = self.data[key]
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("too_large")
        return data


PROGRAM = b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"
RULES = {"type": "自定义", "rules": [{"id": "R1", "category": "阈值", "rule_to_check": "A1 must be >=10"}], "checker_binding": {"R1": "checker:create_audit"}}


def fixture(auth_store_factory, *, count=4, resources=False):
    clause_id = RULES["rules"][0]["id"]
    objects = Objects()
    settings = Settings(feishu_app_id="checker-optimizer", git_sha="a" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings, objects=objects)
    alice = store.upsert_feishu_identity("optimizer", "alice")
    bob = store.upsert_feishu_identity("optimizer", "bob")
    other = store.upsert_feishu_identity("other", "carol")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    suite = SuiteStore(settings, objects, role="migrator").create_revision(
        tenant_id=None,
        suite_key="threshold",
        files={"rules.json": json.dumps(RULES).encode(), "checker.py": PROGRAM, "templates.json": b'[{"kind":"structure","columns":["A1"]}]', **({"resources.py": b"VALUE = 1"} if resources else {})},
        created_by=None,
    )
    api = build_product_store(settings, objects=objects, role="api")
    worker = build_product_store(settings, objects=objects, role="worker")
    clients = []
    for person in (alice, bob, other):
        client = TestClient(create_app(settings, auth_store=api))
        client.cookies.set(SESSION_COOKIE, store.create_session(person))
        clients.append(client)
    feedback = []
    for i in range(count):
        round_id, file_id, version_id, rule_id = [str(uuid4()) for _ in range(4)]
        buffer = io.BytesIO()
        book = Workbook()
        book.active["A1"] = [5, 15, 16, 6][i % 4]
        book.active["B1"] = "hidden-sentinel" if (i + 1) % 4 == 0 else str(i)
        book.save(buffer)
        book.close()
        raw = buffer.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        objects.data[version_id] = raw
        with store._conn() as conn:
            for ident, kind in ((file_id, "file"), (round_id, "audit_round")):
                conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES (%s,%s,%s)", (ident, alice.tenant_id, kind))
                conn.execute("INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES (%s,%s,'review')", (ident, alice.user_id))
            conn.execute("INSERT INTO product.files(id,tenant_id,created_by,original_filename) VALUES (%s,%s,%s,'sample.xlsx')", (file_id, alice.tenant_id, alice.user_id))
            conn.execute(
                """INSERT INTO product.file_versions(id,file_id,tenant_id,original_filename,content_type,byte_size,sha256,object_key,status)
                            VALUES (%s,%s,%s,'sample.xlsx','application/octet-stream',%s,%s,%s,'ready')""",
                (version_id, file_id, alice.tenant_id, len(raw), digest, version_id),
            )
            conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES (%s,%s,'threshold',%s)", (rule_id, alice.tenant_id, Jsonb(RULES)))
            conn.execute(
                """INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt,analysis_mode)
                            VALUES (%s,%s,%s,%s,'test','completed',1,'deep')""",
                (round_id, alice.tenant_id, alice.user_id, round_id),
            )
            conn.execute(
                """INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body)
                            VALUES (%s,%s,'target',%s,'threshold',%s)""",
                (round_id, version_id, rule_id, Jsonb(RULES)),
            )
            conn.execute(
                """INSERT INTO product.findings(id,round_id,item_key,classification,item,status,locator)
                            VALUES (%s,%s,'R1','rule','threshold','PASS',%s)""",
                (uuid4(), round_id, Jsonb({"file_version_id": version_id, "rule_item_id": clause_id})),
            )
            conn.execute(
                """INSERT INTO product.checker_round_artifacts(round_id,file_version_id,result_attempt,tenant_id,suite_revision_id,program_digest,input_digest)
                            VALUES (%s,%s,1,%s,%s,%s,%s)""",
                (round_id, version_id, alice.tenant_id, suite.id, "a" * 64, "b" * 64),
            )
        payload = {
            "operation_id": str(uuid4()),
            "file_version_id": version_id,
            "rule_version_id": rule_id,
            "rule_clause_id": clause_id,
            "result_attempt": 1,
            "original_status": "PASS",
            "expected_status": "NG" if i % 4 in (0, 3) else "PASS",
            "rationale": "User confirmed original A1",
            "evidence": [{"file_version_id": version_id, "locator": {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}, "excerpt": str(i)}],
        }
        response = clients[0].post(f"/audit-rounds/{round_id}/checker-feedback", json=payload)
        assert response.status_code == 200, response.text
        feedback.append((round_id, payload, response.json()))
    return SimpleNamespace(store=store, api=api, worker=worker, alice=alice, bob=bob, other=other, clients=clients, suite=suite, feedback=feedback)


def command(f, **changes):
    return {"operation_id": str(uuid4()), "feedback_ids": [f.feedback[0][2]["id"]], "action": "run_tests", "consume_model": True, **changes}


def test_suite_samples_span_rounds_but_recheck_current_original_permissions(auth_store_factory):
    f = fixture(auth_store_factory)
    path = f"/suite-revisions/{f.suite.id}/checker-feedback"
    listed = f.clients[0].get(path)
    assert listed.status_code == 200
    assert {row["round_id"] for row in listed.json()["feedback"]} == {row[0] for row in f.feedback}
    assert f.clients[1].get(path).json() == {"feedback": [], "groups": [], "next_cursor": None}
    assert f.clients[2].get(path).json() == {"feedback": [], "groups": [], "next_cursor": None}
    round_id, payload, _ = f.feedback[0]
    with f.store._conn() as conn:
        conn.execute(
            "DELETE FROM product.resource_grants WHERE user_id=%s AND resource_id=(SELECT file_id FROM product.file_versions WHERE id=%s)",
            (f.alice.user_id, payload["file_version_id"]),
        )
    assert len(f.clients[0].get(path).json()["feedback"]) == 3
    assert f.clients[0].get(f"/audit-rounds/{round_id}/checker-feedback").json()["feedback"] == []


def start(f, body=None):
    body = body or command(f)
    response = f.clients[0].post(f"/suite-revisions/{f.suite.id}/optimizations", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def claim(f):
    job = f.worker.claim_job("optimizer-test")
    assert job.kind == "checker_optimization"
    return DevelopmentEnvelope(op="develop", operation_id=job.payload["operation_id"], job_id=job.id, tenant_id=job.tenant_id, attempt=job.attempt, lease_owner="optimizer-test")


class ControlledRuntime:
    def __init__(self):
        self.calls = 0
        self.request = None

    def develop_and_run(self, request, *, spec, context):
        self.calls += 1
        self.request = request
        context.raise_if_cancelled()
        artifact = CheckerArtifact(
            input_digest=request.target.digest, executor_revision=request.executor_revision, entrypoint="checker:create_audit", files={"checker.py": PROGRAM + b"# fixed candidate\n"}
        )
        proof = SuiteValidationReport(
            suite_revision_id=request.suite_revision_id,
            suite_digest=request.suite_digest,
            library_revision=request.library_revision,
            program_digest=artifact.program_digest,
            oracle_revision=request.oracle_revision,
            cases=tuple(
                CaseEvaluation(
                    case_id=row.case_id,
                    ordinal=row.ordinal,
                    input_digest=row.inputs.digest,
                    split="holdout" if row.holdout else "training",
                    coverage=True,
                    clauses=(ClauseEvaluation(clause_id="R1", expected=row.expected["R1"], actual=row.expected["R1"], evidence_locatable=True, checks_performed=1),),
                )
                for row in request.oracle
            ),
        )
        return DevelopmentOutcome(artifact, [], ["controlled fixture"], {"model_requests": 1}, validation=proof, usage_source="controlled")


def test_save_only_is_real_unadopted_candidate_and_reentry_is_idempotent(auth_store_factory):
    f = fixture(auth_store_factory, count=1)
    receipt, body = start(f, command(f, action="save_only", consume_model=False))
    assert receipt["state"] == "saved" and receipt["candidate_revision_id"] != f.suite.id
    assert receipt["report"] == {"verified": False, "reasons": ["tests_not_run"]}
    assert start(f, body)[0] == receipt
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.suite_publications").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0
    assert f.clients[0].post(f"/suite-revisions/{f.suite.id}/optimizations", json={**body, "action": "run_tests", "consume_model": True}).status_code == 409


def test_feedback_freezes_labels_files_and_reports_then_controller_persists_once(auth_store_factory):
    f = fixture(auth_store_factory)
    receipt, body = start(f)
    request = claim(f)
    # Correcting an active sample cannot rewrite the accepted operation's Oracle.
    round_id, edit, sample = f.feedback[0]
    changed = {key: edit[key] for key in ("expected_status", "rationale", "evidence")}
    changed.update(expected_status="PASS", expected_revision=1)
    assert f.clients[0].put(f"/audit-rounds/{round_id}/checker-feedback/{sample['id']}", json=changed).status_code == 200
    development = CheckerDevelopmentStore(f.worker)
    frozen = development.operation_for_dispatch(request)
    loaded, _ = materialize_request(development, frozen)
    assert loaded.oracle[0].expected == {"R1": "NG"}
    assert loaded.oracle[-1].holdout
    assert b"hidden-sentinel" not in loaded.target.file_bytes
    assert loaded.oracle[-1].inputs.document_digests.isdisjoint(loaded.target.document_digests)
    runtime = ControlledRuntime()
    result = development_handler(f.worker, runtime)(request, ExecutionContext())
    assert result["state"] == "completed", result
    assert result["report"]["validation"]["verified"] is False
    assert "oracle_changed" in result["report"]["validation"]["reasons"]
    assert runtime.calls == 1
    assert start(f, body)[0] == result
    with pytest.raises(KeyError):
        development_handler(f.worker, runtime)(request, ExecutionContext())
    assert runtime.calls == 1
    # User reads the same persisted receipt after replacing the API connection.
    assert CheckerDevelopmentStore(f.api).read(f.alice, receipt["operation_id"]) == result
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.suite_publications").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM product.suite_validations WHERE revision_id=%s", (result["candidate_revision_id"],)).fetchone()[0] == "unverified"


def test_stop_before_dispatch_revocation_cross_user_and_missing_model(auth_store_factory):
    f = fixture(auth_store_factory, count=1)
    path = f"/suite-revisions/{f.suite.id}/optimizations"
    assert f.clients[1].post(path, json=command(f)).status_code == 404  # no access to the original file
    assert f.clients[2].post(path, json=command(f)).status_code == 409  # unrelated tenant has no samples
    f.store.delete_personal_model_config(f.alice)
    response = f.clients[0].post(path, json=command(f))
    assert response.status_code == 409 and response.json()["error"] == "personal_model_unavailable"
    f.store.put_personal_model_config(f.alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    receipt, _ = start(f)
    identity = claim(f)
    stopped = f.clients[0].post(f"/checker-optimizations/{receipt['operation_id']}/stop")
    assert stopped.json()["state"] == "stopped"
    assert f.clients[1].get(f"/checker-optimizations/{receipt['operation_id']}").status_code == 404
    with pytest.raises(KeyError):
        development_handler(f.worker, ControlledRuntime())(identity, ExecutionContext())
    assert f.worker.claim_job("another-worker") is None


def test_dispatch_compare_and_swap_expiry_and_late_completion_are_fenced(auth_store_factory):
    f = fixture(auth_store_factory, count=1)
    receipt, _ = start(f)
    identity = claim(f)
    development = CheckerDevelopmentStore(f.worker)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: development.dispatch(identity), range(2)))
    assert sum(row is not None for row in outcomes) == 1
    with f.store._conn() as conn:
        conn.execute("UPDATE engine.jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (identity.job_id,))
    # A lost lease is not an invitation to call a paid provider again.
    assert f.worker.claim_job("replacement") is None
    assert not f.worker.renew_job_lease(SimpleNamespace(id=identity.job_id, attempt=identity.attempt), identity.lease_owner)
    assert development.finish(identity, DevelopmentOutcome(None, [], [], {}, error_code="deep_timeout", usage_uncertain=True)) is False
    latest = development.read(f.alice, receipt["operation_id"])
    assert latest["state"] == "unknown" and latest["failure"] == "execution_outcome_unknown"
    assert latest["candidate_revision_id"] is None


def test_revocation_after_admission_is_checked_before_any_model_call(auth_store_factory):
    f = fixture(auth_store_factory, count=1)
    receipt, _ = start(f)
    identity = claim(f)
    with f.store._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=(SELECT file_id FROM product.file_versions WHERE id=%s)", (f.feedback[0][1]["file_version_id"],))
    runtime = ControlledRuntime()
    with pytest.raises(KeyError):
        development_handler(f.worker, runtime)(identity, ExecutionContext())
    assert runtime.calls == 0
    assert f.clients[0].get(f"/checker-optimizations/{receipt['operation_id']}").status_code == 404


def test_idempotent_replay_and_lists_recheck_revoked_objects(auth_store_factory):
    f = fixture(auth_store_factory, count=1)
    _, body = start(f)
    with f.store._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=(SELECT file_id FROM product.file_versions WHERE id=%s)", (f.feedback[0][1]["file_version_id"],))
    assert f.clients[0].post(f"/suite-revisions/{f.suite.id}/optimizations", json=body).status_code == 404
    listing = f.clients[0].get(f"/audit-rounds/{f.feedback[0][0]}/checker-feedback")
    assert listing.status_code == 200 and listing.json()["optimizations"] == []


def test_model_free_regression_has_no_personal_key_requirement(auth_store_factory):
    f = fixture(auth_store_factory)
    f.store.delete_personal_model_config(f.alice)
    receipt, _ = start(f, command(f, consume_model=False))
    runtime = ControlledRuntime()
    result = development_handler(f.worker, runtime)(claim(f), ExecutionContext())
    assert result["state"] == "completed"
    assert runtime.request.allow_model_calls is False
    assert runtime.request.fixed is runtime.request.prior
    assert CheckerDevelopmentStore(f.api).read(f.alice, receipt["operation_id"]) == result


def test_provider_unknown_is_persisted_without_retry_or_audit_charge(auth_store_factory):
    f = fixture(auth_store_factory, count=1)
    receipt, body = start(f)

    class InterruptedRuntime:
        calls = 0

        def develop_and_run(self, request, *, spec, context):
            self.calls += 1
            raise TimeoutError("provider-side response unknown")

    runtime = InterruptedRuntime()
    identity = claim(f)
    result = development_handler(f.worker, runtime)(identity, ExecutionContext())
    assert result["state"] == "unknown" and result["usage"]["uncertain"]
    assert start(f, body)[0] == result
    assert f.worker.claim_job("replacement") is None
    with pytest.raises(KeyError):
        development_handler(f.worker, runtime)(identity, ExecutionContext())
    assert runtime.calls == 1
    assert result["candidate_revision_id"] is None
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0
        assert conn.execute("SELECT state FROM product.checker_optimizations WHERE operation_id=%s", (receipt["operation_id"],)).fetchone()[0] == "unknown"


def test_key_revoked_before_dispatch_is_known_failure_without_a_model_call(auth_store_factory):
    f = fixture(auth_store_factory, count=1)
    receipt, _ = start(f)
    identity = claim(f)
    f.store.delete_personal_model_config(f.alice)
    runtime = ControlledRuntime()
    result = development_handler(f.worker, runtime)(identity, ExecutionContext())
    assert result["state"] == "failed" and result["failure"] == "personal_model_unavailable"
    assert result["usage"]["uncertain"] is False and runtime.calls == 0
    with f.store._conn() as conn:
        assert conn.execute("SELECT model_started_at FROM product.checker_optimizations WHERE operation_id=%s", (receipt["operation_id"],)).fetchone()[0] is None


def test_stop_running_cancels_runtime_and_cannot_create_a_candidate(auth_store_factory):
    f = fixture(auth_store_factory, count=1)
    receipt, _ = start(f)
    entered = threading.Event()

    class WaitingRuntime:
        def develop_and_run(self, request, *, spec, context):
            entered.set()
            assert context.cancelled.wait(timeout=5)
            context.raise_if_cancelled()

    identity = claim(f)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(development_handler(f.worker, WaitingRuntime()), identity, ExecutionContext())
        assert entered.wait(timeout=5)
        stopped = f.clients[0].post(f"/checker-optimizations/{receipt['operation_id']}/stop")
        assert stopped.status_code == 200 and stopped.json()["cancel_requested"]
        result = future.result(timeout=5)
    assert result["state"] == "stopped" and result["candidate_revision_id"] is None


def test_expiry_during_object_write_rolls_back_candidate_and_proof(auth_store_factory, monkeypatch):
    f = fixture(auth_store_factory)
    receipt, _ = start(f)
    identity = claim(f)
    development = CheckerDevelopmentStore(f.worker)
    row = development.operation_for_dispatch(identity)
    request, spec = materialize_request(development, row)
    assert development.dispatch(identity)
    outcome = ControlledRuntime().develop_and_run(request, spec=spec, context=ExecutionContext())
    put = f.worker.objects.put_exclusive

    def slow_put(*args, **kwargs):
        time.sleep(0.3)
        return put(*args, **kwargs)

    monkeypatch.setattr(f.worker.objects, "put_exclusive", slow_put)
    with f.store._conn() as conn:
        conn.execute("UPDATE engine.jobs SET lease_until=clock_timestamp()+interval '0.15 seconds' WHERE id=%s", (identity.job_id,))
    with pytest.raises(ValueError, match="checker_lease_lost"):
        development.finish(identity, outcome)
    result = development.read(f.alice, receipt["operation_id"])
    assert result["state"] == "unknown" and result["usage"]["uncertain"]
    assert result["candidate_revision_id"] is None
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE tenant_id=%s", (f.alice.tenant_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.suite_validations").fetchone()[0] == 0


def test_removed_optional_module_does_not_reappear_from_base_overlay(auth_store_factory):
    f = fixture(auth_store_factory, resources=True)
    start(f)
    result = development_handler(f.worker, ControlledRuntime())(claim(f), ExecutionContext())
    assert result["state"] == "completed" and result["report"]["validation"]["verified"]
    candidate = SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(result["candidate_revision_id"], f.alice.tenant_id)
    assert candidate.revision.base_revision_id is None
    assert "resources.py" not in candidate.revision.overrides
    assert "resources.py" not in candidate.files
    assert candidate.files["templates.json"] == b'[{"kind":"structure","columns":["A1"]}]'
    assert candidate.revision.overrides["templates.json"]
    original = SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(f.suite.id, f.alice.tenant_id)
    assert original.files["resources.py"] == b"VALUE = 1"
    assert original.files["checker.py"] == PROGRAM
    detail = SuiteReleaseStore(f.store.settings, f.store.objects).detail(f.alice, candidate.revision.id)
    assert detail["base_revision_id"] is None and "resources.py" not in detail["files"]
    assert detail["validation"]["verified"]


def test_changed_rules_run_current_candidate_but_old_labels_cannot_verify(auth_store_factory):
    f = fixture(auth_store_factory)
    revised_rules = {**RULES, "rules": [{**RULES["rules"][0], "rule_to_check": "A1 must be >=20"}]}
    revised = SuiteStore(f.store.settings, f.store.objects, role="migrator").create_revision(
        tenant_id=f.alice.tenant_id,
        suite_key="threshold",
        base_revision_id=f.suite.id,
        files={"rules.json": json.dumps(revised_rules).encode()},
        created_by=f.alice.user_id,
    )
    response = f.clients[0].post(f"/suite-revisions/{revised.id}/optimizations", json=command(f))
    assert response.status_code == 200, response.text
    runtime = ControlledRuntime()
    result = development_handler(f.worker, runtime)(claim(f), ExecutionContext())
    # This controlled adapter deliberately omits the loop's stale-label guard.
    # The store must independently protect the candidate's persisted proof.
    assert all(sample.inputs.rule.rules == revised_rules["rules"] for sample in runtime.request.oracle)
    assert all(not sample.expectations_current for sample in runtime.request.oracle)
    assert result["report"]["validation"]["verified"] is False
    assert "oracle_rules_changed" in result["report"]["validation"]["reasons"]


@pytest.mark.parametrize("dispatched", [False, True])
@pytest.mark.parametrize("consume_model", [False, True])
def test_membership_removal_atomically_revokes_optimizer_fence_and_key(auth_store_factory, dispatched, consume_model):
    from documents.model_access import ModelUnavailable

    f = fixture(auth_store_factory, count=1)
    receipt, _ = start(f, command(f, consume_model=consume_model))
    identity = claim(f)
    development = CheckerDevelopmentStore(f.worker)
    if dispatched:
        assert development.dispatch(identity)
        assert development.active(identity)
    with f.store._conn() as conn, conn.transaction():
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (f.alice.user_id, f.alice.tenant_id))
    with pytest.raises(ModelUnavailable):
        f.worker.personal_model_access(f.alice.tenant_id, f.alice.user_id)
    assert development.active(identity) is False
    assert development.finish(identity, DevelopmentOutcome(None, [], [], {}, error_code="late-result")) is False
    runtime = ControlledRuntime()
    with pytest.raises(KeyError):
        development_handler(f.worker, runtime)(identity, ExecutionContext())
    assert runtime.calls == 0
    assert f.worker.claim_job("replacement") is None
    assert f.clients[0].get(f"/checker-optimizations/{receipt['operation_id']}").status_code == 401
    with f.store._conn() as conn:
        state, cancelled, candidate, usage, failure = conn.execute(
            "SELECT state,cancel_requested,candidate_revision_id,usage,failure FROM product.checker_optimizations WHERE operation_id=%s", (receipt["operation_id"],)
        ).fetchone()
        assert state == ("unknown" if dispatched and consume_model else "stopped")
        assert cancelled and candidate is None and failure == "checker_operator_removed"
        assert bool(usage.get("uncertain")) is (dispatched and consume_model)
        assert conn.execute("SELECT status,lease_until FROM engine.jobs WHERE id=%s", (identity.job_id,)).fetchone() == ("cancelled", None)
        assert conn.execute("SELECT outcome,finished_at IS NOT NULL FROM engine.attempts WHERE job_id=%s", (identity.job_id,)).fetchone() == ("cancelled", True)
        assert conn.execute("SELECT count(*) FROM product.personal_model_credentials WHERE user_id=%s", (f.alice.user_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.checker_feedback").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 1
    # Rejoining never reactivates the previous job or restores the removed key.
    with f.store._conn() as conn:
        conn.execute("INSERT INTO product.memberships(user_id,tenant_id,role) VALUES (%s,%s,'member')", (f.alice.user_id, f.alice.tenant_id))
    assert f.worker.claim_job("rejoined") is None
    with pytest.raises(ModelUnavailable):
        f.worker.personal_model_access(f.alice.tenant_id, f.alice.user_id)


def test_running_membership_revocation_cancels_controller_and_blocks_next_model_access(auth_store_factory):
    from documents.model_access import ModelUnavailable

    f = fixture(auth_store_factory, count=1)
    receipt, _ = start(f)
    entered = threading.Event()
    continue_call = threading.Event()
    context = ExecutionContext()

    class WaitingRuntime:
        def develop_and_run(self, request, *, spec, context):
            # This boundary reads the same live credential accessor as production,
            # but sends no model request. Never cache this access for later calls.
            f.worker.personal_model_access(spec.tenant_id, spec.initiating_user_id)
            entered.set()
            assert continue_call.wait(timeout=5)
            with pytest.raises(ModelUnavailable):
                f.worker.personal_model_access(spec.tenant_id, spec.initiating_user_id)
            assert context.cancelled.wait(timeout=5)
            context.raise_if_cancelled()

    identity = claim(f)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(development_handler(f.worker, WaitingRuntime()), identity, context)
        assert entered.wait(timeout=5)
        with f.store._conn() as conn:
            conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (f.alice.user_id, f.alice.tenant_id))
        continue_call.set()
        with pytest.raises(KeyError):
            future.result(timeout=5)
    with f.store._conn() as conn:
        assert conn.execute("SELECT state,candidate_revision_id FROM product.checker_optimizations WHERE operation_id=%s", (receipt["operation_id"],)).fetchone() == ("unknown", None)
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE tenant_id=%s", (f.alice.tenant_id,)).fetchone()[0] == 0


def test_deleted_operator_preserves_feedback_completed_receipt_and_published_checker(auth_store_factory):
    from documents.model_access import ModelUnavailable

    f = fixture(auth_store_factory, count=1)
    round_id, original, _ = f.feedback[0]
    with f.store._conn() as conn:
        conn.execute(
            "INSERT INTO product.resource_grants(resource_id,user_id,capability) SELECT resource_id,%s,capability FROM product.resource_grants WHERE user_id=%s",
            (f.bob.user_id, f.alice.user_id),
        )
    # Bob operates on Alice's historical round: the older audit owner FKs remain
    # unchanged. This proves the new Checker tables do not add deletion blockers.
    response = f.clients[1].post(f"/audit-rounds/{round_id}/checker-feedback", json={**original, "operation_id": str(uuid4())})
    assert response.status_code == 200, response.text
    sample = response.json()
    body = command(f, feedback_ids=[sample["id"]], consume_model=False)
    response = f.clients[1].post(f"/suite-revisions/{f.suite.id}/optimizations", json=body)
    assert response.status_code == 200, response.text
    receipt = development_handler(f.worker, ControlledRuntime())(claim(f), ExecutionContext())
    assert receipt["state"] == "completed"
    releases = SuiteReleaseStore(f.store.settings, f.store.objects, role="api")
    releases.publish(f.bob, "threshold", receipt["candidate_revision_id"], 0, str(uuid4()), True)
    f.store.put_personal_model_config(f.bob, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    with f.store._conn() as conn, conn.transaction():
        conn.execute("DELETE FROM product.sessions WHERE user_id=%s", (f.bob.user_id,))
        conn.execute("DELETE FROM product.provider_authorizations WHERE user_id=%s", (f.bob.user_id,))
        conn.execute("DELETE FROM product.identities WHERE user_id=%s", (f.bob.user_id,))
        conn.execute("DELETE FROM product.resource_grants WHERE user_id=%s", (f.bob.user_id,))
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s", (f.bob.user_id,))
        conn.execute("DELETE FROM product.users WHERE id=%s", (f.bob.user_id,))
    assert f.api.claim_task_notification("deleted-notifier") is None
    with f.store._conn() as conn:
        assert (
            conn.execute("SELECT result->>'reason' FROM engine.jobs WHERE kind='feishu_notification' AND payload->>'operation_id'=%s", (body["operation_id"],)).fetchone()[0] == "recipient_unavailable"
        )
    with pytest.raises(ModelUnavailable):
        f.worker.personal_model_access(f.bob.tenant_id, f.bob.user_id)
    retained = releases.detail(f.alice, receipt["candidate_revision_id"])
    assert retained["created_by"] is None and retained["created_by_name"] == "已删除成员"
    assert retained["files"]["checker.py"] == PROGRAM.decode() + "# fixed candidate\n"
    assert releases.active(f.alice, "threshold")["history"][0]["published_by"] is None
    with f.store._conn() as conn:
        assert conn.execute("SELECT created_by,expected_status FROM product.checker_feedback WHERE id=%s", (sample["id"],)).fetchone() == (None, "NG")
        assert conn.execute("SELECT initiating_user_id,state,report FROM product.checker_optimizations WHERE operation_id=%s", (body["operation_id"],)).fetchone() == (
            None,
            "completed",
            receipt["report"],
        )
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM product.findings").fetchone()[0] == 1
    # Retained human confirmation remains usable by a currently authorized member,
    # without inventing a live operator or attempting to borrow the former key.
    response = f.clients[0].post(f"/suite-revisions/{f.suite.id}/optimizations", json=command(f, feedback_ids=[sample["id"]], consume_model=False))
    assert response.status_code == 200, response.text
    identity = claim(f)
    row = CheckerDevelopmentStore(f.worker).operation_for_dispatch(identity)
    loaded, _ = materialize_request(CheckerDevelopmentStore(f.worker), row)
    grouped = next(item for item in loaded.oracle if sample["id"] in item.feedback_ids)
    assert grouped.expected == {"R1": "NG"}
    retained_answer = next(item for item in row["frozen"]["samples"] if item["id"] == sample["id"])
    assert retained_answer["confirmed_by"] == "deleted-member"
    assert loaded.initiating_user_id == f.alice.user_id


def test_membership_retirement_serializes_with_atomic_admission(auth_store_factory, monkeypatch):
    from psycopg.errors import LockNotAvailable

    f = fixture(auth_store_factory, count=1)
    entered, release = threading.Event(), threading.Event()
    access = f.api.personal_model_access

    def paused_access(*args):
        entered.set()
        assert release.wait(timeout=5)
        return access(*args)

    monkeypatch.setattr(f.api, "personal_model_access", paused_access)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(CheckerDevelopmentStore(f.api).start, f.alice, f.suite.id, command(f))
        try:
            assert entered.wait(timeout=5)
            with f.store._conn() as conn, conn.transaction(), pytest.raises(LockNotAvailable):
                conn.execute("SET LOCAL lock_timeout='100ms'")
                conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (f.alice.user_id, f.alice.tenant_id))
        finally:
            release.set()
        receipt = future.result(timeout=5)
    with f.store._conn() as conn:
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (f.alice.user_id, f.alice.tenant_id))
        assert conn.execute("SELECT state FROM product.checker_optimizations WHERE operation_id=%s", (receipt["operation_id"],)).fetchone() == ("stopped",)
    assert f.worker.claim_job("retirement-race") is None


def _completed_notification(f):
    receipt, _ = start(f)
    request = claim(f)
    result = development_handler(f.worker, ControlledRuntime())(request, ExecutionContext())
    assert result["state"] == "completed"
    with f.store._conn() as conn:
        rows = conn.execute("SELECT id,payload FROM engine.jobs WHERE kind='feishu_notification' AND payload->>'operation_id'=%s", (receipt["operation_id"],)).fetchall()
    assert len(rows) == 1
    assert rows[0][1]["stage"] == "optimization_completed"
    return receipt["operation_id"], str(rows[0][0])


def test_optimization_notification_is_atomic_with_terminal_receipt_and_deduplicated(auth_store_factory, monkeypatch):
    f = fixture(auth_store_factory)
    receipt, body = start(f)
    request = claim(f)
    development = CheckerDevelopmentStore(f.worker)
    loaded, spec = materialize_request(development, development.operation_for_dispatch(request))
    assert development.dispatch(request)
    outcome = ControlledRuntime().develop_and_run(loaded, spec=spec, context=ExecutionContext())
    enqueue = f.worker._queue_task_notification

    def disconnected_after_queue(*args, **kwargs):
        enqueue(*args, **kwargs)
        raise RuntimeError("transaction interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(f.worker, "_queue_task_notification", disconnected_after_queue)
        with pytest.raises(RuntimeError, match="transaction interrupted"):
            development.finish(request, outcome)
    with f.store._conn() as conn:
        assert conn.execute("SELECT state FROM product.checker_optimizations WHERE operation_id=%s", (receipt["operation_id"],)).fetchone()[0] == "running"
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='feishu_notification'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE tenant_id=%s", (f.alice.tenant_id,)).fetchone()[0] == 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = list(pool.map(lambda _: development.finish(request, outcome), range(2)))
    assert sorted(completed) == [False, True]
    assert development.finish(request, outcome) is False
    assert start(f, body)[0]["state"] == "completed"
    with f.store._conn() as conn:
        # Replaying intent creation cannot schedule a second notification either.
        f.store._queue_task_notification(conn, None, "optimization_completed", f.feedback[0][0], operation_id=receipt["operation_id"])
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='feishu_notification'").fetchone()[0] == 1
    assert f.worker.claim_job("audit-worker") is None  # API, not audit Worker, owns sending.


def test_notifications_survive_closed_browser_and_target_initiator_not_task_owner(auth_store_factory, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    import httpx
    from api.notifications import dispatch_task_notification

    f = fixture(auth_store_factory)
    draft = f.store.create_draft(f.bob)
    with f.store._conn() as conn:
        conn.execute("UPDATE product.audit_rounds SET source_draft_id=%s WHERE id=%s", (draft["id"], f.feedback[0][0]))
    first, _ = _completed_notification(f)
    second, _ = _completed_notification(f)
    assert first != second
    for client in f.clients:
        f.store.revoke_session(client.cookies.get(SESSION_COOKIE))
        client.close()
    f.api.settings.feishu_notifications_enabled = True
    f.api.settings.feishu_app_secret = "test-only-no-network"
    monkeypatch.setattr("api.notifications.tenant_access_token", lambda *args: "test-token")
    requests = []

    def send(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_" + str(len(requests))}})

    # No browser polling is involved; both intents reach the existing API sender.
    with httpx.Client(transport=httpx.MockTransport(send)) as http:
        assert dispatch_task_notification(f.api, http, f.api.settings, "notifier", task_id=draft["id"])
        assert dispatch_task_notification(f.api, http, f.api.settings, "notifier", task_id=draft["id"])
        assert not dispatch_task_notification(f.api, http, f.api.settings, "notifier", task_id=draft["id"])
    assert len(requests) == 2
    recovered = set()
    for request in requests:
        assert request["receive_id"] == f.alice.subject and request["receive_id"] != f.bob.subject
        card = json.loads(request["content"])
        assert "未自动发布" in card["body"]["elements"][0]["content"]
        link = card["body"]["elements"][1]["behaviors"][0]["default_url"]
        assert urlsplit(link).path == "/enter-current"
        target = parse_qs(urlsplit(link).query)["return_to"][0]
        params = parse_qs(urlsplit(target).query)
        assert params["round"] == [f.feedback[0][0]] and params["file"] == [f.feedback[0][1]["file_version_id"]]
        assert params["clause"] == ["R1"] and params["basis"] == ["1"]
        assert params["checkerFeedbackClause"] == ["R1"]
        assert params["checkerFeedbackScope"] == [f"{f.feedback[0][0]}:{f.feedback[0][1]['file_version_id']}:R1"]
        recovered.add(params["checkerOptimization"][0])
    assert recovered == {first, second}
    serialized = json.dumps(requests)
    assert "test-only-no-provider-called" not in serialized and "hidden-sentinel" not in serialized


@pytest.mark.parametrize("revocation", ["membership", "identity", "source_round", "regression_original", "tenant", "different_user", "different_tenant"])
def test_optimization_notification_rechecks_recipient_and_all_frozen_access(auth_store_factory, revocation):
    f = fixture(auth_store_factory)
    if revocation == "source_round":
        draft = f.store.create_draft(f.alice)
        with f.store._conn() as conn:
            conn.execute("UPDATE product.audit_drafts SET task_fingerprint='test',task_day='2026-11-06',task_sequence=1 WHERE id=%s", (draft["id"],))
            conn.execute("UPDATE product.audit_rounds SET source_draft_id=%s WHERE id=%s", (draft["id"], f.feedback[-1][0]))
    _, intent = _completed_notification(f)
    job, recipient = f.api.claim_task_notification("notifier")
    with f.store._conn() as conn:
        if revocation == "membership":
            conn.execute("DELETE FROM product.memberships WHERE tenant_id=%s AND user_id=%s", (f.alice.tenant_id, f.alice.user_id))
        elif revocation == "identity":
            conn.execute("DELETE FROM product.identities WHERE user_id=%s", (f.alice.user_id,))
        elif revocation == "source_round":
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (f.feedback[-1][0], f.alice.user_id))
        elif revocation == "regression_original":
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id=(SELECT file_id FROM product.file_versions WHERE id=%s)", (f.feedback[-1][1]["file_version_id"],))
        elif revocation == "tenant":
            f.api.settings.current_tenant_allowlist = "another-tenant"
        elif revocation == "different_user":
            recipient = f.bob
        else:
            recipient = f.other
    assert f.api.start_task_notification(job, "notifier", recipient) is None
    with f.store._conn() as conn:
        status, result = conn.execute("SELECT status,result FROM engine.jobs WHERE id=%s", (intent,)).fetchone()
    assert status == "cancelled" and result["state"] == "suppressed"
    assert f.api.claim_task_notification("again") is None


def test_removed_recipient_is_suppressed_before_claim(auth_store_factory):
    f = fixture(auth_store_factory)
    _, intent = _completed_notification(f)
    with f.store._conn() as conn:
        conn.execute("DELETE FROM product.memberships WHERE tenant_id=%s AND user_id=%s", (f.alice.tenant_id, f.alice.user_id))
    assert f.api.claim_task_notification("notifier") is None
    with f.store._conn() as conn:
        assert conn.execute("SELECT result->>'reason' FROM engine.jobs WHERE id=%s", (intent,)).fetchone()[0] == "recipient_unavailable"


def test_optimization_notification_reclaims_only_before_dispatch_and_fences_late_receipts(auth_store_factory):
    f = fixture(auth_store_factory)
    _, intent = _completed_notification(f)
    old, recipient = f.api.claim_task_notification("old")
    with f.store._conn() as conn:
        conn.execute("UPDATE engine.jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (intent,))
    current, recipient = f.api.claim_task_notification("current")
    assert current.attempt == old.attempt + 1
    assert f.api.start_task_notification(old, "old", recipient) is None
    assert f.api.start_task_notification(current, "current", recipient)
    assert f.api.finish_task_notification(old, "old", {"state": "sent", "message_id": "om_stale"}) is False
    with f.store._conn() as conn:
        conn.execute("UPDATE engine.jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (intent,))
    assert f.api.claim_task_notification("must-not-retry") is None
    with f.store._conn() as conn:
        assert conn.execute("SELECT result->>'state' FROM engine.jobs WHERE id=%s", (intent,)).fetchone()[0] == "unknown"
    assert f.api.finish_task_notification(current, "current", {"state": "sent", "message_id": "om_late"})
    assert f.api.finish_task_notification(current, "current", {"state": "sent", "message_id": "om_duplicate"}) is False
    assert f.api.claim_task_notification("still-no-retry") is None


def test_optimization_notification_connection_failure_defers_but_unknown_send_never_retries(auth_store_factory, monkeypatch):
    import httpx
    from api.notifications import dispatch_task_notification
    from product.feishu_usage import FeishuUsageError

    f = fixture(auth_store_factory)
    _, intent = _completed_notification(f)
    f.api.settings.feishu_notifications_enabled = True
    f.api.settings.feishu_app_secret = "test-only-no-network"
    attempts = []

    def no_connection(*args):
        raise FeishuUsageError("offline")

    def uncertain_send(request):
        attempts.append(request)
        raise httpx.ReadTimeout("receipt lost", request=request)

    with httpx.Client(transport=httpx.MockTransport(uncertain_send)) as http:
        monkeypatch.setattr("api.notifications.tenant_access_token", no_connection)
        assert dispatch_task_notification(f.api, http, f.api.settings, "notifier")
        assert attempts == []
        with f.store._conn() as conn:
            assert conn.execute("SELECT result->>'state' FROM engine.jobs WHERE id=%s", (intent,)).fetchone()[0] == "queued"
            conn.execute("UPDATE engine.jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (intent,))
        monkeypatch.setattr("api.notifications.tenant_access_token", lambda *args: "test-token")
        assert dispatch_task_notification(f.api, http, f.api.settings, "notifier")
        assert not dispatch_task_notification(f.api, http, f.api.settings, "notifier")
    assert len(attempts) == 1
    with f.store._conn() as conn:
        assert conn.execute("SELECT result->>'state' FROM engine.jobs WHERE id=%s", (intent,)).fetchone()[0] == "unknown"


@pytest.mark.parametrize("state", ["failed", "unknown", "stopped"])
def test_non_success_optimization_terminal_notices_keep_the_original_outcome(auth_store_factory, state):
    f = fixture(auth_store_factory, count=1)
    receipt, _ = start(f)
    request = claim(f)
    development = CheckerDevelopmentStore(f.worker)
    assert development.dispatch(request)
    if state == "stopped":
        assert development.stop(f.alice, receipt["operation_id"])["cancel_requested"]
    outcome = DevelopmentOutcome(None, [], [], {}, error_code="checker_execution_failed", usage_uncertain=state == "unknown")
    assert development.finish(request, outcome)
    assert development.read(f.alice, receipt["operation_id"])["state"] == state
    notification, recipient = f.api.claim_task_notification("notifier")
    delivery = f.api.start_task_notification(notification, "notifier", recipient)
    assert delivery
    content = delivery["card"]["body"]["elements"][0]["content"]
    assert "验证通过" not in content
    assert {"unknown": "未知", "failed": "尚未得到完整验证结果", "stopped": "已停止"}[state] in content
    assert f.worker.claim_job("no-model-retry") is None


DIRECT_RULES = {
    "type": "自定义",
    "rules": [{"id": "R1", "category": "阈值", "rule_to_check": "A1 must be >=10"}, {"id": "R2", "category": "原件", "rule_to_check": "A1 must be numeric"}],
    "checker_binding": {"R1": "checker:threshold", "R2": "resources:numeric"},
}
DIRECT_PROGRAM = b"""from openpyxl import load_workbook
from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

def threshold(context):
    book = load_workbook(context.audit_file.file_url, read_only=True)
    value = book.active['A1'].value
    book.close()
    return AuditResultItem(classification='rule', item='Threshold', priority='P1', clause_id=context.clause_id,
        status='PASS' if value >= 10 else 'NG', suggestions='Compared original cell', checks_performed=1,
        evidence=AuditEvidence(required='structure', used='structure', source_format='xlsx', prepared_format='xlsx',
            sources=(EvidenceSource(kind='structure', locator='{"kind":"xlsx_cell","sheet":"Sheet","cell":"A1"}'),)))
"""
DIRECT_RESOURCE = b"""from openpyxl import load_workbook
from audit.contracts import AuditResultItem

def numeric(context):
    book = load_workbook(context.audit_file.file_url, read_only=True)
    value = book.active['A1'].value
    book.close()
    return AuditResultItem(classification='rule', item='Native number', priority='P1', clause_id=context.clause_id,
        status='PASS' if isinstance(value, (int, float)) else 'NG', suggestions='Read the original number', checks_performed=1)
"""
GENERATED_FACTORY = b"""from types import SimpleNamespace
from openpyxl import load_workbook
from audit.contracts import AuditResultItem

def create_audit(audit_file, rule, group_context=None):
    def check(clause):
        book = load_workbook(audit_file.file_url, read_only=True)
        value = book.active['A1'].value
        book.close()
        status = ('PASS' if value >= 10 else 'NG') if clause == 'R1' else 'PASS'
        return AuditResultItem(classification='rule', item=clause, priority='P1', clause_id=clause,
            status=status, suggestions='Generated native comparison', checks_performed=1)
    return SimpleNamespace(checks=[SimpleNamespace(rule_item=SimpleNamespace(id=row['id']), check=lambda clause=row['id']: check(clause)) for row in rule.rules])
"""


def actual_core_runtime(f, monkeypatch, *, generated=None):
    """Run actual Core manifests; only the author, when requested, is controlled."""
    from agent.checker_program import run_adapt_manifest
    from agent.core_program import AuditJobSpec, CoreRuntime, run_audit_manifest

    def forbidden(*args, **kwargs):
        raise AssertionError("Fixed evaluation must not call a model or Paddle")

    monkeypatch.setattr("documents.ocr.paddle.reader.read_pdf_page_evidence", forbidden)
    monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", forbidden)

    class Author:
        input_tokens = output_tokens = 0

        def run(self, work, prompt, schema, deadline, **kwargs):
            assert generated is not None
            return schema(entrypoint="checker:create_audit", files={"checker.py": generated.decode()})

    recorded = SimpleNamespace(fixed_manifests=[], generation_manifests=[])

    class Guest:
        usage_source = "controlled"

        def __init__(self, model_identity):
            self.model_identity = dict(model_identity)

        def engine_revision(self):
            return f.store.settings.deep_execution_revision(self.model_identity)

        def run(self, job, inputs, work, deadline, cancelled, **kwargs):
            manifest = json.loads((inputs / "manifest.json").read_text())
            if isinstance(job, AuditJobSpec):
                assert job.allow_model_calls is False
                assert manifest["snapshot"]["model_identity"] == self.model_identity
                recorded.fixed_manifests.append(manifest)
                run_audit_manifest(inputs, work, work / "no-model-grant")
            else:
                assert job.operation == "adapt-checker" and generated is not None
                recorded.generation_manifests.append(manifest)
                run_adapt_manifest(inputs, work, Author(), deadline)

    class Runtime:
        def develop_and_run(self, request, *, spec, context):
            # Mirror the production per-request runtime. Use the accepted
            # identity from the materialized operation, not today's settings.
            core = CoreRuntime(Guest(spec.model_identity), lambda _: None, lambda *_: None, objects=f.store.objects)
            assert core.execution_revision() == request.executor_revision == spec.executor_revision
            return core.develop_and_run(request, spec=spec, context=context)

    return Runtime(), recorded


def direct_suite_fixture(auth_store_factory, monkeypatch):
    monkeypatch.setitem(globals(), "RULES", DIRECT_RULES)
    monkeypatch.setitem(globals(), "PROGRAM", DIRECT_PROGRAM)
    f = fixture(auth_store_factory, count=1)
    releases = SuiteReleaseStore(f.store.settings, f.store.objects)
    candidate = releases.save_candidate(f.alice, f.suite.id, {"resources.py": DIRECT_RESOURCE.decode()})
    return f, candidate


def test_fixed_suite_preserves_multiple_explicit_entrypoints_through_saved_candidate(auth_store_factory, monkeypatch):
    f, candidate = direct_suite_fixture(auth_store_factory, monkeypatch)
    runtime, guest = actual_core_runtime(f, monkeypatch)
    revision = candidate["id"]
    for _ in range(2):
        response = f.clients[0].post(f"/suite-revisions/{revision}/optimizations", json=command(f, consume_model=False))
        assert response.status_code == 200, response.text
        receipt = development_handler(f.worker, runtime)(claim(f), ExecutionContext())
        assert receipt["state"] == "completed", receipt
        assert [(row["clause_id"], row["status"]) for row in receipt["report"]["results"]] == [("R1", "NG"), ("R2", "PASS")]
        assert receipt["report"]["validation"]["verified"] is False
        revision = receipt["candidate_revision_id"]
        saved = SuiteReleaseStore(f.store.settings, f.store.objects).detail(f.alice, revision)
        assert json.loads(saved["files"]["rules.json"])["checker_binding"] == DIRECT_RULES["checker_binding"]
        assert saved["files"]["resources.py"] == DIRECT_RESOURCE.decode()
    assert guest.generation_manifests == []
    assert all(row["snapshot"]["targets"][0]["binding"]["entrypoints"] == DIRECT_RULES["checker_binding"] for row in guest.fixed_manifests)


def test_native_standard_suite_can_be_validated_without_generating_a_factory(auth_store_factory, monkeypatch):
    from audit.standard_suites import standard_suites

    standard = standard_suites()[0]
    monkeypatch.setitem(globals(), "RULES", json.loads(standard.files["rules.json"]))
    monkeypatch.setitem(globals(), "PROGRAM", standard.files["checker.py"])
    f = fixture(auth_store_factory, count=1)
    start(f, command(f, consume_model=False))
    runtime, guest = actual_core_runtime(f, monkeypatch)
    receipt = development_handler(f.worker, runtime)(claim(f), ExecutionContext())
    assert receipt["state"] == "completed", receipt
    assert {row["clause_id"] for row in receipt["report"]["results"]} == set(standard.entrypoints)
    saved = SuiteReleaseStore(f.store.settings, f.store.objects).detail(f.alice, receipt["candidate_revision_id"])
    assert json.loads(saved["files"]["rules.json"])["checker_binding"] == dict(standard.entrypoints)
    assert guest.generation_manifests == []


def test_native_baseline_then_generated_factory_keeps_its_own_binding_and_tombstone(auth_store_factory, monkeypatch):
    f, candidate = direct_suite_fixture(auth_store_factory, monkeypatch)
    response = f.clients[0].post(f"/suite-revisions/{candidate['id']}/optimizations", json=command(f, consume_model=True))
    assert response.status_code == 200, response.text
    runtime, guest = actual_core_runtime(f, monkeypatch, generated=GENERATED_FACTORY)
    receipt = development_handler(f.worker, runtime)(claim(f), ExecutionContext())
    assert receipt["state"] == "completed", receipt
    assert guest.fixed_manifests[0]["snapshot"]["targets"][0]["binding"]["entrypoints"] == DIRECT_RULES["checker_binding"]
    assert len(guest.generation_manifests) == 1
    assert guest.generation_manifests[0]["prior_files"] == {"checker.py": DIRECT_PROGRAM.decode(), "resources.py": DIRECT_RESOURCE.decode()}
    saved = SuiteReleaseStore(f.store.settings, f.store.objects).detail(f.alice, receipt["candidate_revision_id"])
    generated_binding = {clause: "checker:create_audit" for clause in ("R1", "R2")}
    assert json.loads(saved["files"]["rules.json"])["checker_binding"] == generated_binding
    assert "resources.py" not in saved["files"]
    assert saved["base_revision_id"] is None
    response = f.clients[0].post(f"/suite-revisions/{saved['id']}/optimizations", json=command(f, consume_model=False))
    assert response.status_code == 200, response.text
    second = development_handler(f.worker, runtime)(claim(f), ExecutionContext())
    assert second["state"] == "completed", second
    assert guest.fixed_manifests[-1]["snapshot"]["targets"][0]["binding"]["entrypoints"] == generated_binding
    assert len(guest.generation_manifests) == 1


def test_generated_candidate_keeps_an_independent_factory_binding_and_optimizes_again(auth_store_factory, monkeypatch):
    from urllib.parse import quote

    from audit.checker_core import checker_digest

    f, candidate = direct_suite_fixture(auth_store_factory, monkeypatch)
    suites = SuiteStore(f.store.settings, f.store.objects, role="migrator")
    source_before = suites.resolve_suite(candidate["id"], f.alice.tenant_id)

    def snapshot(sql):
        with f.store._conn() as conn:
            return conn.execute(sql).fetchall()

    labels, rounds = (
        snapshot("SELECT id::text, expected_status, revision FROM product.checker_feedback ORDER BY id"),
        snapshot("SELECT id::text, execution, result_attempt FROM product.audit_rounds ORDER BY id"),
    )
    response = f.clients[0].post(f"/suite-revisions/{candidate['id']}/optimizations", json=command(f, consume_model=True))
    assert response.status_code == 200, response.text
    runtime, guest = actual_core_runtime(f, monkeypatch, generated=GENERATED_FACTORY)
    receipt = development_handler(f.worker, runtime)(claim(f), ExecutionContext())
    assert receipt["state"] == "completed", receipt
    generated_binding = {clause: "checker:create_audit" for clause in ("R1", "R2")}
    saved = SuiteReleaseStore(f.store.settings, f.store.objects).detail(f.alice, receipt["candidate_revision_id"])
    assert json.loads(saved["files"]["rules.json"])["checker_binding"] == generated_binding
    assert saved["files"]["checker.py"] == GENERATED_FACTORY.decode()
    assert "resources.py" not in saved["files"]
    assert saved["base_revision_id"] is None
    assert receipt["report"]["validation"]["program_digest"] == checker_digest({"checker.py": GENERATED_FACTORY})
    assert [(row["clause_id"], row["status"]) for row in receipt["report"]["results"]] == [("R1", "NG"), ("R2", "PASS")]
    assert receipt["report"]["validation"]["verified"] is False
    source_after = suites.resolve_suite(candidate["id"], f.alice.tenant_id)
    assert source_after.files == source_before.files
    assert source_after.revision.resolved_digest == source_before.revision.resolved_digest
    assert suites.resolve_suite(f.suite.id, f.alice.tenant_id).files["checker.py"] == DIRECT_PROGRAM
    response = f.clients[0].post(f"/suite-revisions/{saved['id']}/optimizations", json=command(f, consume_model=False))
    assert response.status_code == 200, response.text
    second = development_handler(f.worker, runtime)(claim(f), ExecutionContext())
    assert second["state"] == "completed", second
    assert guest.fixed_manifests[-1]["snapshot"]["targets"][0]["binding"]["entrypoints"] == generated_binding
    assert len(guest.generation_manifests) == 1
    again = SuiteReleaseStore(f.store.settings, f.store.objects).detail(f.alice, second["candidate_revision_id"])
    assert json.loads(again["files"]["rules.json"])["checker_binding"] == generated_binding
    assert again["base_revision_id"] is None
    assert again["files"]["checker.py"] == GENERATED_FACTORY.decode()
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key='threshold'", (f.alice.tenant_id,)).fetchone()[0] == 0
    unconfirmed = f.clients[0].put(
        "/suites/" + quote("threshold", safe="") + "/active",
        json={"revision_id": saved["id"], "expected_revision": 0, "operation_id": str(uuid4()), "force_unverified": False},
    )
    assert unconfirmed.status_code == 409 and unconfirmed.json()["error"] == "suite_confirmation_required"
    adopted = f.clients[0].put(
        "/suites/" + quote("threshold", safe="") + "/active",
        json={"revision_id": saved["id"], "expected_revision": 0, "operation_id": str(uuid4()), "force_unverified": True},
    )
    assert adopted.status_code == 200, adopted.text
    active = suites.resolve_suite(saved["id"], f.alice.tenant_id)
    assert json.loads(active.files["rules.json"])["checker_binding"] == generated_binding
    assert active.files["checker.py"] == GENERATED_FACTORY
    rolled = f.clients[0].put(
        "/suites/" + quote("threshold", safe="") + "/active",
        json={"revision_id": candidate["id"], "expected_revision": 1, "operation_id": str(uuid4()), "force_unverified": True},
    )
    assert rolled.status_code == 200, rolled.text
    restored = suites.resolve_suite(candidate["id"], f.alice.tenant_id)
    assert restored.files == source_before.files
    assert snapshot("SELECT id::text, expected_status, revision FROM product.checker_feedback ORDER BY id") == labels
    assert snapshot("SELECT id::text, execution, result_attempt FROM product.audit_rounds ORDER BY id") == rounds


def test_fixed_rule_text_change_cannot_reuse_old_labels_as_verified(auth_store_factory, monkeypatch):
    rules = {**DIRECT_RULES, "rules": DIRECT_RULES["rules"][:1], "checker_binding": {"R1": "checker:threshold"}}
    monkeypatch.setitem(globals(), "RULES", rules)
    monkeypatch.setitem(globals(), "PROGRAM", DIRECT_PROGRAM)
    f = fixture(auth_store_factory)
    changed = {**rules, "rules": [{**rules["rules"][0], "rule_to_check": "A1 must be >=100"}]}
    candidate = SuiteReleaseStore(f.store.settings, f.store.objects).save_candidate(f.alice, f.suite.id, {"rules.json": json.dumps(changed)})
    response = f.clients[0].post(f"/suite-revisions/{candidate['id']}/optimizations", json=command(f, consume_model=False))
    assert response.status_code == 200, response.text
    runtime, guest = actual_core_runtime(f, monkeypatch)
    receipt = development_handler(f.worker, runtime)(claim(f), ExecutionContext())
    assert receipt["state"] == "completed", receipt
    proof = receipt["report"]["validation"]
    assert proof["passed_samples"] == proof["total_samples"] == 4
    assert proof["verified"] is False and "oracle_rules_changed" in proof["reasons"]
    saved = SuiteReleaseStore(f.store.settings, f.store.objects).detail(f.alice, receipt["candidate_revision_id"])
    assert saved["validation"]["verified"] is False
    assert json.loads(saved["files"]["rules.json"])["checker_binding"] == rules["checker_binding"]
    assert guest.generation_manifests == []


@pytest.mark.parametrize("expectation", ["changed", "null"])
@pytest.mark.parametrize("consume_model", [False, True])
def test_model_revision_consent_rejects_before_optimization_admission(auth_store_factory, monkeypatch, expectation, consume_model):
    f = fixture(auth_store_factory, count=1)
    observed = f.api.personal_model_config(f.alice)["revision"]
    if expectation == "changed":
        f.store.put_personal_model_config(f.alice, SecretStr("replacement-test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
        assert f.api.personal_model_config(f.alice)["revision"] != observed
    body = command(f, consume_model=consume_model, expected_model_revision=observed if expectation == "changed" else None)

    def forbidden(*args, **kwargs):
        pytest.fail("A rejected consent must not resolve credentials or call a provider")

    monkeypatch.setattr(f.api, "personal_model_access", forbidden)
    with f.store._conn() as conn:
        suites_before = conn.execute("SELECT count(*) FROM product.suite_revisions").fetchone()[0]
    response = f.clients[0].post(f"/suite-revisions/{f.suite.id}/optimizations", json=body)
    assert response.status_code == 409 and response.json()["error"] == "personal_model_configuration_changed", response.text
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.checker_optimizations WHERE operation_id=%s", (body["operation_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE payload->>'operation_id'=%s", (body["operation_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.suite_revisions").fetchone()[0] == suites_before
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0
    assert f.worker.claim_job("rejected-consent") is None


def test_matching_model_consent_freezes_identity_and_replay_preserves_original_receipt(auth_store_factory):
    f = fixture(auth_store_factory, count=1)
    accepted_identity = f.api.personal_model_identity(f.alice.tenant_id, f.alice.user_id)
    receipt, body = start(f, command(f, expected_model_revision=accepted_identity["revision"]))
    assert receipt["state"] == "queued"
    f.store.put_personal_model_config(f.alice, SecretStr("replacement-test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    current_identity = f.api.personal_model_identity(f.alice.tenant_id, f.alice.user_id)
    assert current_identity != accepted_identity
    # A lost response is a read-back of the accepted operation, not consent to
    # use today's provider or another opportunity to enqueue paid work.
    assert start(f, body)[0] == receipt
    with f.store._conn() as conn:
        frozen = conn.execute("SELECT frozen FROM product.checker_optimizations WHERE operation_id=%s", (receipt["operation_id"],)).fetchone()[0]
        assert frozen["model_identity"] == accepted_identity
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE payload->>'operation_id'=%s", (body["operation_id"],)).fetchone()[0] == 1
    runtime = ControlledRuntime()
    result = development_handler(f.worker, runtime)(claim(f), ExecutionContext())
    assert result["state"] == "failed" and result["failure"] == "personal_model_unavailable"
    assert result["usage"]["uncertain"] is False and runtime.calls == 0
    with f.store._conn() as conn:
        assert conn.execute("SELECT model_started_at FROM product.checker_optimizations WHERE operation_id=%s", (receipt["operation_id"],)).fetchone()[0] is None


def test_model_change_after_dispatch_before_runtime_is_known_zero_send(auth_store_factory, monkeypatch):
    from contextlib import contextmanager

    from agent.execution import harness

    f = fixture(auth_store_factory, count=1)
    receipt, body = start(f)
    identity = claim(f)
    dispatch = CheckerDevelopmentStore.dispatch
    dispatches, runtimes = [], []

    def change_after_dispatch(development, request):
        accepted = dispatch(development, request)
        assert accepted is not None
        f.store.put_personal_model_config(f.alice, SecretStr("changed-after-dispatch-no-provider-called"), base_url="https://models.example.test", model="test-model")
        dispatches.append(request.operation_id)
        return accepted

    @contextmanager
    def gateway(path, **kwargs):
        yield SimpleNamespace(socket_path=path, http=SimpleNamespace(grants=SimpleNamespace(revoke_all=lambda: None)))

    class Bootstrap:
        degraded = False

        def __init__(self, config, **kwargs):
            runtimes.append(config)

        def recover(self):
            pass

        def ready(self):
            return True

    def forbidden(*args, **kwargs):
        pytest.fail("Runtime pre-start rejection must not construct a guest, resolve a key or send a model request")

    monkeypatch.setenv("PPAP_DEEP_RUNTIME", "container-only")
    monkeypatch.setenv("PPAP_DEEP_SANDBOX_PROFILE", "off")
    monkeypatch.setattr(CheckerDevelopmentStore, "dispatch", change_after_dispatch)
    monkeypatch.setattr(harness, "local_gateway", gateway)
    monkeypatch.setattr(harness, "ContainerRuntime", Bootstrap)
    monkeypatch.setattr(harness, "CoreRuntime", forbidden)
    with harness.runtime_from_env(
        f.worker.settings,
        f.worker.objects,
        model_access_for_request=forbidden,
        model_identity_for_request=lambda spec: f.worker.personal_model_identity(spec.tenant_id, spec.initiating_user_id),
    ) as runtime:
        result = development_handler(f.worker, runtime)(identity, ExecutionContext())
    assert dispatches == [receipt["operation_id"]] and len(runtimes) == 1
    assert result["state"] == "failed" and result["failure"] == "personal_model_unavailable"
    assert result["usage"] == {"model_requests": 0, "input_tokens": 0, "output_tokens": 0, "source": "controlled", "uncertain": False}
    assert result["candidate_revision_id"] is None
    assert start(f, body)[0] == result
    assert f.worker.claim_job("no-automatic-retry") is None
    with f.store._conn() as conn:
        assert conn.execute("SELECT model_started_at IS NOT NULL, state FROM product.checker_optimizations WHERE operation_id=%s", (receipt["operation_id"],)).fetchone() == (True, "failed")
        assert conn.execute("SELECT status FROM engine.jobs WHERE id=%s", (identity.job_id,)).fetchone()[0] == "failed"
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0


@pytest.mark.parametrize("error_type", ["model_unavailable", "deep_error"])
def test_generic_runtime_model_error_without_prestart_proof_remains_uncertain(auth_store_factory, error_type):
    from agent.client import DeepError
    from documents.model_access import ModelUnavailable

    f = fixture(auth_store_factory, count=1)
    receipt, body = start(f)

    class Runtime:
        def develop_and_run(self, request, *, spec, context):
            # Ordinary runtime errors do not prove that earlier turns were free.
            if error_type == "model_unavailable":
                raise ModelUnavailable()
            raise DeepError("personal_model_unavailable")

    result = development_handler(f.worker, Runtime())(claim(f), ExecutionContext())
    assert result["state"] == "unknown" and result["failure"] == "personal_model_unavailable"
    assert result["usage"]["uncertain"] is True
    assert start(f, body)[0] == result
    assert f.worker.claim_job("no-unknown-replay") is None


def test_feedback_granted_reference_sources_are_authorized_on_each_readback(auth_store_factory):
    """Saving, replaying, editing and listing retain the same explicit source scope."""
    from unittest.mock import patch

    f = fixture(auth_store_factory, count=2)
    round_id, original, _ = f.feedback[0]
    reference = f.feedback[1][1]["file_version_id"]
    with f.store._conn() as conn:
        conn.execute("INSERT INTO product.round_inputs(round_id,file_version_id,role) VALUES (%s,%s,'reference')", (round_id, reference))
    evidence = [*original["evidence"], {"file_version_id": reference, "locator": {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "B1"}, "excerpt": "Confirmed reference"}]
    payload = {**original, "operation_id": str(uuid4()), "evidence": evidence}
    path = f"/audit-rounds/{round_id}/checker-feedback"
    expected_sources = {original["file_version_id"], reference}

    def checked_sources(authorize):
        assert any({row["file_version_id"] for row in call.args[2]} == expected_sources for call in authorize.call_args_list)
        authorize.reset_mock()

    with patch.object(f.api, "_prepared_items", wraps=f.api._prepared_items) as authorize:
        saved = f.clients[0].post(path, json=payload)
        assert saved.status_code == 200 and saved.json()["evidence"] == evidence
        checked_sources(authorize)
        assert f.clients[0].post(path, json=payload).json() == saved.json()
        checked_sources(authorize)
        edited = f.clients[0].put(path + "/" + saved.json()["id"], json={"expected_revision": 1, "expected_status": "NG", "rationale": "Reconfirmed target and reference", "evidence": evidence})
        assert edited.status_code == 200 and edited.json()["evidence"] == evidence
        checked_sources(authorize)
        listed = f.clients[0].get(f"/suite-revisions/{f.suite.id}/checker-feedback?limit=1&after=2")
        assert listed.status_code == 200 and [row["id"] for row in listed.json()["feedback"]] == [saved.json()["id"]]
        assert listed.json()["next_cursor"] is None
        checked_sources(authorize)
