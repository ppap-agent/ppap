"""Real acceptance transactions freeze adopted Suites, evidence and fencing."""

import hashlib
import json
import uuid
from copy import deepcopy

import pytest
from audit.checker_core import checker_digest
from audit.standard_suites import library_revision
from product.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport
from product.settings import Settings
from product.store.model_credentials import platform_selection
from product.store.suite_releases import SuiteReleaseStore
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb

KEY = "test:control-plan"
RULES = {
    "kind": "engine",
    "rule_profile": "v3",
    "suite_key": KEY,
    "type": "控制计划",
    "customers": ["测试客户"],
    "models": [],
    "parts": [],
    "rules": [{"id": "R1", "category": "工序", "rule_to_check": "工序编号必须一致", "type_of_rule": "table", "priority": "P0"}],
    "checker_binding": {"R1": "checker:check"},
}
FILES = {"rules.json": json.dumps(RULES, ensure_ascii=False).encode(), "checker.py": b"def check(context): return []\n", "appendix.json": b"[]"}


class Admission:
    def __init__(self, store):
        self.store = store
        self.alice = store.upsert_feishu_identity("checker-admission", "alice", "Alice")
        self.bob = store.upsert_feishu_identity("checker-admission", "bob", "Bob")
        self.suites = SuiteStore(store.settings, store.objects, role="migrator")
        self.releases = SuiteReleaseStore(store.settings, store.objects, role="api")
        self.official = self.suites.create_revision(tenant_id=None, suite_key=KEY, files=FILES, created_by=None)
        self.rule_id = str(uuid.uuid4())
        with store._conn() as conn:
            conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'Control plan',%s)", (self.rule_id, self.alice.tenant_id, Jsonb(RULES)))
        self.target = self.upload("cp.xlsx", b"frozen target")
        self.reference = self.upload("pfmea.xlsx", b"frozen reference")
        self.items = [
            {"file_version_id": self.target.id, "role": "target", "rule_version_id": self.rule_id},
            {"file_version_id": self.reference.id, "role": "reference", "rule_version_id": None},
        ]

    def upload(self, filename, body):
        version = self.store.create_staging(self.alice, filename, "application/octet-stream", len(body))
        return self.store.finalize_version(self.store.put_blob(version, body))

    def verify(self, revision=None):
        revision = revision or self.official
        resolved = self.suites.resolve_suite(revision.id, self.alice.tenant_id)
        proof = SuiteValidationReport(
            suite_revision_id=revision.id,
            suite_digest=revision.resolved_digest,
            library_revision=library_revision(),
            program_digest=checker_digest({path: body for path, body in resolved.files.items() if path in {"checker.py", "resources.py"}}),
            oracle_revision="synthetic-test-oracle",
            cases=tuple(
                CaseEvaluation(
                    case_id=f"sample-{i}",
                    ordinal=i,
                    input_digest=hashlib.sha256(str(i).encode()).hexdigest(),
                    split="holdout" if i % 4 == 0 else "training",
                    coverage=True,
                    clauses=(ClauseEvaluation(clause_id="R1", expected="NG", actual="NG", evidence_locatable=True, checks_performed=1),),
                )
                for i in range(1, 5)
            ),
        )
        platform = SuiteReleaseStore(self.store.settings, self.store.objects, role="migrator")
        result = platform.record_official_validation(revision.id, proof, expected_revision=0)
        platform.publish_official(revision.id, expected_revision=0, expected_validation_revision=result["write_revision"])
        return result

    def draft(self, mode="standard"):
        draft = self.store.create_draft(self.alice, items=self.items)
        if mode != "standard":
            self.store.save_draft(self.alice, draft["id"], items=None, analysis_mode=mode)
        return draft["id"]

    def accept(self, mode="standard"):
        draft_id = self.draft(mode)
        return self.store.accept_draft(self.alice, draft_id, "accept:" + draft_id)

    def snapshot(self, round_id):
        return self.suites.read_snapshot(round_id, self.alice.tenant_id)

    def counts(self):
        with self.store._conn() as conn:
            return tuple(
                conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in (
                    "product.audit_rounds",
                    "product.checker_execution_snapshots",
                    "engine.jobs",
                    "product.usage_events",
                    "product.deep_rule_changes",
                )
            )


@pytest.fixture
def admission(auth_store_factory):
    return Admission(auth_store_factory(Settings(feishu_app_id="checker-admission-tests", usage_charge_enabled=False, git_sha="a" * 40)))


def test_publication_and_rollback_only_change_new_round_snapshots(admission):
    a = admission
    a.verify()
    first = a.accept()
    original = a.snapshot(first["id"])
    candidate_rules = deepcopy(RULES)
    candidate_rules["rules"][0]["rule_to_check"] = "企业已确认的新条款"
    candidate = a.releases.save_candidate(a.alice, a.official.id, {"rules.json": json.dumps(candidate_rules, ensure_ascii=False)})
    a.releases.publish(a.bob, KEY, candidate["id"], 0, str(uuid.uuid4()), force_unverified=True)
    second = a.accept()
    changed = a.snapshot(second["id"])
    a.releases.publish(a.bob, KEY, a.official.id, 1, str(uuid.uuid4()))
    third = a.accept()
    restored = a.snapshot(third["id"])
    assert [snapshot.targets[0].suite_revision_id for snapshot in (original, changed, restored)] == [a.official.id, candidate["id"], a.official.id]
    assert [snapshot.targets[0].verification for snapshot in (original, changed, restored)] == ["verified", "unverified", "verified"]
    assert changed.targets[0].rules["rules"][0]["rule_to_check"] == "企业已确认的新条款"
    assert a.snapshot(first["id"]).to_dict() == original.to_dict()
    assert a.snapshot(second["id"]).to_dict() == changed.to_dict()
    assert original.model_identity == platform_selection().identity
    assert original.initiating_user_id == a.alice.user_id
    assert {(item.file_version_id, item.role, item.sha256) for item in original.inputs} == {
        (a.target.id, "target", a.target.sha256),
        (a.reference.id, "reference", a.reference.sha256),
    }
    assert a.releases.detail(a.alice, candidate["id"])["rounds"][0]["id"] == second["id"]
    # Replaying a prior confirmation does not select today's adopted Suite.
    replay = a.store.accept_draft(a.alice, first["source_draft_id"], "accept:" + first["source_draft_id"])
    assert replay["id"] == first["id"]
    assert a.counts()[:3] == (3, 3, 3)
    with a.store._conn() as conn:
        payload = conn.execute("SELECT payload FROM engine.jobs WHERE audit_round_id=%s", (first["id"],)).fetchone()[0]
    assert payload["checker_snapshot_digest"] == original.input_digest
    assert payload["executor_revision"] == original.runtime_revision


def test_missing_official_proof_is_atomic_and_only_explicit_force_can_run(admission):
    a = admission
    draft = a.draft()
    before = a.counts()
    with pytest.raises(KeyError, match="suite_access_denied"):
        a.store.accept_draft(a.alice, draft, "missing-proof")
    assert a.counts() == before
    assert a.store.get_draft(draft)["status"] == "open"
    with pytest.raises(ValueError, match="suite_confirmation_required"):
        a.releases.publish(a.bob, KEY, a.official.id, 0, str(uuid.uuid4()))
    a.releases.publish(a.bob, KEY, a.official.id, 0, str(uuid.uuid4()), force_unverified=True)
    accepted = a.store.accept_draft(a.alice, draft, "missing-proof")
    assert a.snapshot(accepted["id"]).targets[0].verification == "unverified"
    with a.store._conn() as conn:
        execution = conn.execute("SELECT rule_execution FROM product.round_inputs WHERE round_id=%s AND role='target'", (accepted["id"],)).fetchone()[0]
    assert execution["verification"] == "unverified"


@pytest.mark.parametrize("fault", ["object_bytes", "file_access"])
def test_admission_integrity_failures_never_queue_a_round(admission, fault):
    a = admission
    a.verify()
    draft = a.draft()
    if fault == "object_bytes":
        digest = a.official.overrides["checker.py"]
        a.store.objects.data[f"suites/official/objects/{digest}"] = b"changed outside the immutable artifact"
    else:
        with a.store._conn() as conn:
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (a.reference.file_id, a.alice.user_id))
    before = a.counts()
    with pytest.raises((KeyError, ValueError)):
        a.store.accept_draft(a.alice, draft, "invalid-input")
    assert a.counts() == before
    assert a.store.get_draft(draft)["status"] == "open"


def test_confirmed_feedback_appendix_cannot_be_silently_replaced_by_adopted_suite(admission):
    a = admission
    a.verify()
    source = a.accept("deep")
    original = a.snapshot(source["id"]).to_dict()
    payload = {
        "operation_id": str(uuid.uuid4()),
        "expected_revision": 0,
        "kind": "interpretation",
        "content": "确认后的编号解释",
        "scope": {"document_ids": [a.target.id], "rule_ids": ["R1"]},
        "intent": "save_only",
        "confirmation_source": "synthetic",
    }
    saved = a.store.apply_rule_change(a.alice, source["id"], payload)
    before = a.counts()
    followup = {"operation_id": str(uuid.uuid4()), "expected_revision": saved["revision"], "intent": "save_and_analyze"}
    with pytest.raises(ValueError, match="suite_feedback_rules_changed"):
        a.store.apply_rule_change(a.alice, source["id"], followup)
    assert a.counts() == before
    assert a.snapshot(source["id"]).to_dict() == original
    view = a.store.read_deep_feedback(a.alice, source["id"])
    assert view["appendix"][0]["content"] == payload["content"]
    assert view["current_round_id"] == source["id"]
    # Explicitly adopting matching appendix bytes creates a distinct unverified
    # Suite; feedback then freezes those exact bytes, never its old verified proof.
    candidate = a.releases.save_candidate(a.alice, a.official.id, {"appendix.json": json.dumps(saved["appendix"], ensure_ascii=False)})
    a.releases.publish(a.bob, KEY, candidate["id"], 0, str(uuid.uuid4()), force_unverified=True)
    new = a.store.apply_rule_change(a.alice, source["id"], followup)
    frozen = a.snapshot(new["analysis_round_id"])
    assert frozen.targets[0].verification == "unverified"
    assert frozen.to_dict()["targets"][0]["appendix"] == saved["appendix"]


@pytest.mark.parametrize("mode", ["standard", "deep"])
@pytest.mark.parametrize("boundary", ["expired", "null"])
def test_all_audits_reject_expired_renewal_progress_and_completion(admission, mode, boundary):
    a = admission
    a.verify()
    source = a.accept(mode)
    first = a.store.claim_job("old-worker")
    assert first.round_id == source["id"]
    with a.store._conn() as conn:
        expiry = "clock_timestamp() - interval '1 second'" if boundary == "expired" else "NULL"
        conn.execute(f"UPDATE engine.jobs SET lease_until={expiry} WHERE id=%s", (first.id,))
    assert a.store.renew_job_lease(first, "old-worker") is False
    assert a.store.audit_progress(first, "old-worker", {"step": "too late"}) is False
    assert a.store.complete_job(first, "old-worker", {"findings": [], "coverage": {}}, "failed") is False
    with a.store._conn() as conn:
        assert conn.execute("SELECT result_attempt FROM product.audit_rounds WHERE id=%s", (source["id"],)).fetchone()[0] is None
        assert conn.execute("SELECT finished_at FROM engine.attempts WHERE job_id=%s", (first.id,)).fetchone()[0] is None
        if boundary == "null":
            conn.execute("UPDATE engine.jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s", (first.id,))
    second = a.store.claim_job("new-worker")
    assert second.id == first.id and second.attempt == first.attempt + 1
    assert a.store.complete_job(first, "old-worker", {"findings": [], "coverage": {}}, "failed") is False
    assert a.store.complete_job(second, "new-worker", {"findings": [], "coverage": {}}, "failed") is True
    assert a.store.get_round(source["id"])["result_attempt"] == second.attempt


@pytest.mark.parametrize("operation", ["renew", "complete"])
def test_waiting_audit_cannot_use_old_transaction_time_to_revive_lease(admission, operation):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    a = admission
    a.verify()
    a.accept()
    claimed = a.store.claim_job("waiting-worker")
    with ThreadPoolExecutor(max_workers=1) as pool, a.store._conn() as holder, a.store._conn() as observer:
        holder.autocommit = False
        holder.execute("UPDATE engine.jobs SET lease_until=clock_timestamp()+interval '1 second' WHERE id=%s", (claimed.id,))
        if operation == "renew":
            future = pool.submit(a.store.renew_job_lease, claimed, "waiting-worker")
        else:
            future = pool.submit(a.store.complete_job, claimed, "waiting-worker", {"findings": [], "coverage": {}}, "failed")
        deadline = monotonic() + 3
        while not observer.execute("SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%%UPDATE engine.jobs%%'").fetchone():
            assert monotonic() < deadline, "audit did not reach the held row lock"
            sleep(0.01)
        # The blocked transaction has started before its lease expires. Its
        # statement must compare wall-clock time again after it gets the lock.
        holder.execute("SELECT pg_sleep(1.05)")
        holder.commit()
        assert future.result(timeout=3) is False


def test_first_standard_adoption_uses_tenant_heads_and_freezes_related_checks(auth_store_factory):
    from api import create_app
    from audit.standard_suites import predefined_directory_suites
    from fastapi.testclient import TestClient
    from product import build_product_store
    from product.identity import SESSION_COOKIE

    store = auth_store_factory(Settings(feishu_app_id="first-standard-adoption", usage_charge_enabled=False, git_sha="a" * 40))
    principal = store.upsert_feishu_identity("first-standard", "owner")
    suites = SuiteStore(store.settings, store.objects, role="migrator")
    ordered = ("UAES:U12", "UAES:U16", "UAES:U07", "UAES:U08")
    baselines = {suite.suite_key: suite for suite in predefined_directory_suites() if suite.suite_key in ordered}
    revisions = {key: suites.create_revision(tenant_id=None, suite_key=key, files=dict(baselines[key].files), created_by=None, related_suite_keys=baselines[key].related_suite_keys) for key in ordered}
    api = build_product_store(store.settings, objects=store.objects, role="api")
    with TestClient(create_app(store.settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(principal))
        catalog = client.get("/suites").json()["suites"]
        assert {row["suite_key"] for row in catalog} == set(ordered)
        assert all(row["selection_mode"] == "candidate_only" and row["current_revision_id"] is None for row in catalog)
        for key in ordered:
            path = f"/suites/{key}/active"
            before = client.get(path).json()
            candidate = client.get(f"/suite-revisions/{before['candidate_revision_id']}").json()
            assert candidate["id"] == revisions[key].id
            assert candidate["unadopted_related_suite_keys"] == []
            command = {"revision_id": candidate["id"], "expected_revision": before["revision"], "operation_id": str(uuid.uuid4()), "force_unverified": True}
            reply = client.put(path, json=command)
            assert reply.status_code == 200, reply.text
            receipt = reply.json()
            assert receipt["previous_revision_id"] is None and receipt["force_unverified"] is True
            assert receipt["verification_status"] == "unverified" and receipt["revision"] == 1
            assert client.put(path, json=command).json() == receipt
            assert client.get(path).json()["history"] == [receipt]
        with store._conn() as conn:
            assert conn.execute("SELECT count(*) FROM product.official_suite_heads").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0
            rule_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'UAES 控制计划',%s)",
                (rule_id, principal.tenant_id, Jsonb({**json.loads(baselines["UAES:U08"].files["rules.json"]), "suite_key": "UAES:U08"})),
            )
        raw = b"frozen original for admission"
        staged = store.create_staging(principal, "control-plan.xlsx", "application/octet-stream", len(raw))
        original = store.finalize_version(store.put_blob(staged, raw))
        draft = client.post("/drafts", json={"items": [{"file_version_id": original.id, "role": "target", "rule_version_id": rule_id}]}).json()
        accepted = client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": "first-standard-adoption"})
        assert accepted.status_code == 201, accepted.text
        frozen = suites.read_snapshot(accepted.json()["id"], principal.tenant_id)
        assert frozen.targets[0].suite_revision_id == revisions["UAES:U08"].id
        assert frozen.targets[0].verification == "unverified"
        assert frozen.targets[0].related_suite_digests == {key: revisions[key].resolved_digest for key in ("UAES:U07", "UAES:U16")}
        assert client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": "first-standard-adoption"}).json()["id"] == accepted.json()["id"]
        assert suites.read_snapshot(accepted.json()["id"], principal.tenant_id).to_dict() == frozen.to_dict()
