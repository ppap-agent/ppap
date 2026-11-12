"""One-time migration of actual pre-Core rows; no synthetic execution proof."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from product import build_product_store, checker_history
from product.checker_history import HistoryImportError, history_source, import_history, read_history
from product.files import ObjectMissing
from product.settings import Settings
from psycopg.types.json import Jsonb

RULE = {
    "kind": "engine",
    "rule_profile": "v3",
    "type": "控制计划",
    "customers": [],
    "models": [],
    "parts": [],
    "rules": [{"id": "R1", "category": "工序", "rule_to_check": "工序号一致", "type_of_rule": "table", "priority": "P0"}],
}
EXECUTION = {"executor": "in-repo-engine", "executor_revision": "in-repo-engine-v1", "protocol_revision": "v1"}


@pytest.fixture
def history(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="history-tests", usage_charge_enabled=False))
    alice = store.upsert_feishu_identity("history-enterprise", "alice", "Alice")
    bob = store.upsert_feishu_identity("history-enterprise", "bob", "Bob")
    foreign = store.upsert_feishu_identity("history-foreign", "mallory", "Mallory")
    return store, alice, bob, foreign


def old_round(history, *, state="completed"):
    store, alice, *_ = history
    data = b"original frozen file"
    staged = store.create_staging(alice, "control-plan.xlsx", "application/octet-stream", len(data))
    version = store.finalize_version(store.put_blob(staged, data))
    rid, rule_id, job_id = (str(uuid.uuid4()) for _ in range(3))
    with store._conn() as conn:
        conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES(%s,%s,'audit_round')", (rid, alice.tenant_id))
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'old frozen rule',%s)", (rule_id, alice.tenant_id, Jsonb(RULE)))
        conn.execute(
            """INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt)
                     VALUES(%s,%s,%s,%s,'old request',%s,1)""",
            (rid, alice.tenant_id, alice.user_id, rid, state),
        )
        conn.execute(
            """INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body,rule_execution)
                     VALUES(%s,%s,'target',%s,'old frozen rule',%s,%s)""",
            (rid, version.id, rule_id, Jsonb(RULE), Jsonb({"old_source": "original"})),
        )
        conn.execute(
            """INSERT INTO engine.jobs(id,tenant_id,audit_round_id,kind,payload,status,attempt)
                     VALUES(%s,%s,%s,'audit',%s,'succeeded',1)""",
            (job_id, alice.tenant_id, rid, Jsonb(EXECUTION)),
        )
        conn.execute(
            """INSERT INTO engine.attempts(id,job_id,attempt,worker_id,finished_at,outcome)
                     VALUES(%s,%s,1,'old worker',now(),'succeeded')""",
            (str(uuid.uuid4()), job_id),
        )
        conn.execute(
            """INSERT INTO product.findings(id,round_id,item_key,classification,item,status,locator)
                     VALUES(%s,%s,'R1','control','original result','NG',%s)""",
            (str(uuid.uuid4()), rid, Jsonb({"file_version_id": version.id, "rule_item_id": "R1"})),
        )
        conn.execute(
            """INSERT INTO product.reviews(round_id,item_key,user_id,judgment,rationale,version)
                     VALUES(%s,'R1',%s,'disagree','Human oracle: PASS',1)""",
            (rid, alice.user_id),
        )
    return rid, version


def run(history, *, apply=False, digest=None):
    store, alice, *_ = history
    return import_history(store, tenant_id=alice.tenant_id, apply=apply, expected_digest=digest)


def written(store, rid):
    with store._conn() as conn:
        return [row[0] for row in conn.execute("SELECT checker_history FROM product.round_inputs WHERE round_id=%s", (rid,)).fetchall()]


def business_state(store):
    with store._conn() as conn:
        tables = (
            "product.audit_rounds",
            "product.findings",
            "product.reviews",
            "engine.jobs",
            "engine.attempts",
            "product.usage_events",
            "product.suite_revisions",
            "product.suite_validations",
            "product.suite_publications",
            "product.checker_execution_snapshots",
        )
        return {table: conn.execute(f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM {table} t").fetchone()[0] for table in tables}


def test_empty_tenant_is_a_noop_and_unknown_tenant_is_rejected(history):
    plan = run(history)
    assert plan["rounds"] == [] and plan["complete"] and not plan["writes"]
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    with pytest.raises(HistoryImportError, match="history_tenant_missing"):
        import_history(history[0], tenant_id=str(uuid.uuid4()))


def test_old_round_dry_run_apply_retry_readback_preserve_results_and_human_oracle(history):
    store, alice, bob, foreign = history
    rid, version = old_round(history)
    before, objects = business_state(store), dict(store.objects.data)
    plan = run(history)
    assert plan["complete"] and plan["rounds"][0]["state"] == "ready"
    assert store.objects.data == objects and business_state(store) == before and written(store, rid) == [None]
    result = run(history, apply=True, digest=plan["plan_digest"])
    assert result["complete"] and result["rounds"][0]["state"] == "imported"
    api = build_product_store(store.settings, objects=store.objects, role="api")
    readback = read_history(api, alice, rid)
    assert len(readback) == 1
    assert readback[0]["verification"] == "unverified" and readback[0]["code_identity"] == "unavailable"
    assert readback[0]["file_sha256"] == version.sha256 and readback[0]["execution_source"] == [EXECUTION]
    assert run(history, apply=True, digest=plan["plan_digest"])["rounds"][0]["state"] == "verified"
    # No objects, Suites or results are created or changed; only the identity is appended.
    assert read_history(api, alice, rid) == readback and business_state(store) == before and store.objects.data == objects
    assert store.get_round(rid)["findings"][0]["review"]["rationale"] == "Human oracle: PASS"
    for denied in (bob, foreign):
        with pytest.raises(KeyError, match="missing"):
            read_history(api, denied, rid)
    with store._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (version.file_id, alice.user_id))
    with pytest.raises(KeyError, match="missing"):
        read_history(api, alice, rid)


def test_damaged_original_is_reported_and_other_round_commits_then_resumes(history):
    store = history[0]
    failed_id, version = old_round(history)
    saved = store.objects.data[version.object_key]
    store.objects.data[version.object_key] = b"corrupt original"
    good_id, _ = old_round(history)
    plan = run(history)
    assert not plan["complete"]
    states = {row["round_id"]: row for row in plan["rounds"]}
    assert states[failed_id]["reason"] == "history_original_digest_mismatch"
    result = run(history, apply=True, digest=plan["plan_digest"])
    assert not result["complete"]
    assert next(row for row in result["rounds"] if row["round_id"] == good_id)["state"] == "imported"
    assert written(store, failed_id) == [None]
    store.objects.data[version.object_key] = saved
    recovered_plan = run(history)
    recovered = run(history, apply=True, digest=recovered_plan["plan_digest"])
    assert recovered["complete"]
    assert {row["state"] for row in recovered["rounds"]} == {"imported", "verified"}


@pytest.mark.parametrize("change", ["rule", "original"])
def test_stale_plan_or_corrupt_original_never_writes(history, change):
    store = history[0]
    rid, version = old_round(history)
    plan = run(history)
    if change == "rule":
        with store._conn() as conn:
            conn.execute('UPDATE product.round_inputs SET rule_body=rule_body || \'{"title":"changed"}\' WHERE round_id=%s', (rid,))
    else:
        store.objects.data[version.object_key] = b"corrupt original"
    with pytest.raises(HistoryImportError, match="history_plan_changed_or_unconfirmed"):
        run(history, apply=True, digest=plan["plan_digest"])
    assert written(store, rid) == [None]


def test_schema_rejects_app_writes_and_mutating_imported_sources(history):
    store, alice, *_ = history
    rid, _ = old_round(history)
    plan = run(history)
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    api = build_product_store(store.settings, objects=store.objects, role="api")
    with api._conn() as conn, pytest.raises(psycopg.Error, match="immutable_checker_history"):
        conn.execute("UPDATE product.round_inputs SET checker_history=NULL WHERE round_id=%s", (rid,))
    with store._conn() as conn, pytest.raises(psycopg.Error, match="immutable_checker_history"):
        conn.execute("UPDATE product.round_inputs SET rule_title='tampered' WHERE round_id=%s", (rid,))
    pending_id, _ = old_round(history, state="queued")
    result = run(history)
    assert next(row for row in result["rounds"] if row["round_id"] == pending_id)["reason"] == "history_round_not_terminal"
    with pytest.raises(PermissionError, match="history_migrator_required"):
        import_history(api, tenant_id=alice.tenant_id, apply=True, expected_digest=plan["plan_digest"])


def test_same_tenant_rule_version_spoof_is_blocked(history):
    store, _, _, foreign = history
    rid, _ = old_round(history)
    with store._conn() as conn:
        conn.execute("UPDATE product.rule_versions SET tenant_id=%s WHERE id=(SELECT rule_version_id FROM product.round_inputs WHERE round_id=%s)", (foreign.tenant_id, rid))
    assert run(history)["rounds"][0]["reason"] == "history_rule_missing"


def test_membership_revocation_fails_readback(history):
    store, alice, *_ = history
    rid, _ = old_round(history)
    plan = run(history)
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    assert read_history(store, alice, rid)
    with store._conn() as conn:
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (alice.user_id, alice.tenant_id))
    with pytest.raises(KeyError, match="missing"):
        read_history(store, alice, rid)


def test_apply_requires_exact_reviewed_plan_and_mid_apply_original_change_fails(history, monkeypatch):
    store = history[0]
    rid, version = old_round(history)
    with pytest.raises(HistoryImportError, match="history_plan_changed_or_unconfirmed"):
        run(history, apply=True)
    plan = run(history)
    original = store.objects.get
    reads = 0

    def change_after_plan(key, **kwargs):
        nonlocal reads
        if key == version.object_key:
            reads += 1
            if reads == 2:
                return b"changed between plan and write"
        return original(key, **kwargs)

    monkeypatch.setattr(store.objects, "get", change_after_plan)
    result = run(history, apply=True, digest=plan["plan_digest"])
    assert result["rounds"][0]["reason"] == "history_original_digest_mismatch"
    assert written(store, rid) == [None]


def test_lost_commit_response_reports_unknown_once_and_retry_reads_back(history, monkeypatch):
    from contextlib import contextmanager

    store, alice, *_ = history
    rid, _ = old_round(history)
    plan = run(history)
    original = store._conn
    lost = False

    class LostCommitConnection:
        def __init__(self, conn):
            self.conn = conn
            self.writing = False

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def execute(self, query, *args):
            if query == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ":
                self.writing = True
            return self.conn.execute(query, *args)

        @contextmanager
        def transaction(self):
            nonlocal lost
            with self.conn.transaction():
                yield
            if self.writing and not lost:
                lost = True
                raise psycopg.OperationalError("lost response after commit")

    @contextmanager
    def connection():
        with original() as conn:
            yield LostCommitConnection(conn)

    monkeypatch.setattr(store, "_conn", connection)
    uncertain = run(history, apply=True, digest=plan["plan_digest"])
    assert uncertain["rounds"] == [{"round_id": rid, "state": "blocked", "reason": "history_read_or_write_failed"}]
    assert read_history(store, alice, rid)
    assert run(history, apply=True, digest=plan["plan_digest"])["rounds"][0]["state"] == "verified"


@pytest.mark.parametrize("fault", ["missing_original", "changed_original"])
def test_imported_history_never_hides_later_source_damage(history, fault):
    store, alice, *_ = history
    rid, version = old_round(history)
    plan = run(history)
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    if fault == "missing_original":
        store.objects.data.pop(version.object_key)
    else:
        store.objects.data[version.object_key] = b"changed after import"
    with pytest.raises((ObjectMissing, HistoryImportError)):
        read_history(store, alice, rid)
    damaged_plan = run(history)
    assert not damaged_plan["complete"]
    assert not run(history, apply=True, digest=damaged_plan["plan_digest"])["complete"]


def test_all_targets_and_reference_are_frozen_and_reference_revocation_blocks_readback(history):
    store, alice, *_ = history
    rid, _ = old_round(history)
    uploaded = []
    for role in ("target", "reference"):
        data = (role + " source bytes").encode()
        staging = store.create_staging(alice, role + ".xlsx", "application/octet-stream", len(data))
        version = store.finalize_version(store.put_blob(staging, data))
        uploaded.append(version)
        with store._conn() as conn:
            rule_id = conn.execute("SELECT rule_version_id FROM product.round_inputs WHERE round_id=%s", (rid,)).fetchone()[0]
            conn.execute(
                """INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body)
                         VALUES(%s,%s,%s,%s,%s,%s)""",
                (rid, version.id, role, rule_id if role == "target" else None, "old frozen rule" if role == "target" else None, Jsonb(RULE) if role == "target" else None),
            )
    plan = run(history)
    assert plan["complete"]
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    identities = read_history(store, alice, rid)
    assert len(identities) == 3 and all(identity["code_identity"] == "unavailable" for identity in identities)
    assert sum(identity["rule_digest"] is None for identity in identities) == 1
    assert len({identity["source_digest"] for identity in identities}) == 1
    with store._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (uploaded[1].file_id, alice.user_id))
    with pytest.raises(KeyError, match="missing"):
        read_history(store, alice, rid)


def test_concurrent_imports_resume_without_duplicate_round_identities(history, monkeypatch):
    store, alice, *_ = history
    rid, _ = old_round(history)
    plan = import_history(store, tenant_id=alice.tenant_id, round_ids=[rid])
    barrier = threading.Barrier(2)
    planned = threading.local()
    verify = checker_history._verify_originals

    def synchronized(objects, source):
        verify(objects, source)
        if not getattr(planned, "done", False):
            planned.done = True
            barrier.wait(timeout=10)

    monkeypatch.setattr(checker_history, "_verify_originals", synchronized)

    def apply():
        return import_history(store, tenant_id=alice.tenant_id, round_ids=[rid], apply=True, expected_digest=plan["plan_digest"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=20) for future in [pool.submit(apply) for _ in range(2)]]
    monkeypatch.setattr(checker_history, "_verify_originals", verify)
    assert all(len(result["rounds"]) == 1 for result in results)
    assert any(result["complete"] for result in results)
    assert import_history(store, tenant_id=alice.tenant_id, round_ids=[rid], apply=True, expected_digest=plan["plan_digest"])["rounds"][0]["state"] == "verified"
    assert len(read_history(store, alice, rid)) == 1
    with store._conn() as conn:
        assert history_source(conn, alice.tenant_id, rid)["inputs"][0]["sha256"] == written(store, rid)[0]["file_sha256"]
