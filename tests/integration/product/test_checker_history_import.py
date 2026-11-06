"""One-time migration of actual pre-Core rows; no synthetic execution proof."""

import hashlib
import json
import runpy
import uuid
from pathlib import Path

import psycopg
import pytest
from agent.checker_program import group_context_for_spec, input_digest
from agent.checker_spec import CheckerArtifact
from agent.protocol import ExecuteRequest, FrozenFile
from product import build_product_store
from product.checker_history import HistoryImportError, history_source, import_history, read_history
from product.files import ObjectMissing
from product.settings import Settings
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb

artifact_loader = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/import_checker_history.py"))["artifact_loader"]

RULE = {
    "kind": "engine",
    "rule_profile": "v3",
    "type": "控制计划",
    "customers": [],
    "models": [],
    "parts": [],
    "rules": [{"id": "R1", "category": "工序", "rule_to_check": "工序号一致", "type_of_rule": "table", "priority": "P0"}],
}
REVISION = "a" * 64


@pytest.fixture
def history(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="history-tests", usage_charge_enabled=False))
    alice = store.upsert_feishu_identity("history-enterprise", "alice", "Alice")
    bob = store.upsert_feishu_identity("history-enterprise", "bob", "Bob")
    foreign = store.upsert_feishu_identity("history-foreign", "mallory", "Mallory")
    return store, alice, bob, foreign


def old_round(history, *, mode="deep", artifact=True, state="completed"):
    store, alice, *_ = history
    data = b"original frozen file"
    staged = store.create_staging(alice, "control-plan.xlsx", "application/octet-stream", len(data))
    version = store.finalize_version(store.put_blob(staged, data))
    rid, rule_id, job_id = (str(uuid.uuid4()) for _ in range(3))
    with store._conn() as conn:
        conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES(%s,%s,'audit_round')", (rid, alice.tenant_id))
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'old frozen rule',%s)", (rule_id, alice.tenant_id, Jsonb(RULE)))
        conn.execute(
            """INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt,analysis_mode)
                     VALUES(%s,%s,%s,%s,'old request',%s,1,%s)""",
            (rid, alice.tenant_id, alice.user_id, rid, state, mode),
        )
        conn.execute(
            """INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body,rule_execution)
                     VALUES(%s,%s,'target',%s,'old frozen rule',%s,%s)""",
            (rid, version.id, rule_id, Jsonb(RULE), Jsonb({"old_source": "original"})),
        )
        conn.execute(
            """INSERT INTO engine.jobs(id,tenant_id,audit_round_id,kind,payload,status,attempt)
                     VALUES(%s,%s,%s,'audit',%s,'succeeded',1)""",
            (job_id, alice.tenant_id, rid, Jsonb({"executor": "ppap-deep", "executor_revision": REVISION, "protocol_revision": "v1"})),
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
        source = history_source(conn, alice.tenant_id, rid)
    key = None
    if artifact:
        item = source["inputs"][0]
        frozen = FrozenFile(
            file_version_id=version.id,
            role="target",
            filename=item["original_filename"],
            sha256=version.sha256,
            rule_version_id=rule_id,
            rule_body=RULE,
            rule_title="old frozen rule",
            rule_execution=item["rule_execution"],
        )
        request = ExecuteRequest(protocol_revision="v1", round_id=rid, tenant_id=alice.tenant_id, files=[frozen], executor="ppap-deep", executor_revision=REVISION, job_id=job_id)
        group = group_context_for_spec(request)
        digest = input_digest(group.by_audit_id[version.id], group.rules_by_id[version.id], file_sha256=version.sha256, role="target", group_context=group)
        saved = CheckerArtifact(
            input_digest=digest, executor_revision=REVISION, entrypoint="checker:create_audit", files={"checker.py": b"def create_audit(*args): raise RuntimeError('must never execute')\n"}
        )
        key = f"deep/{hashlib.sha256(alice.tenant_id.encode()).hexdigest()}/{REVISION}/rounds/{rid}/{hashlib.sha256(job_id.encode()).hexdigest()}-1/checkers/{digest}.json"
        store.objects.data[key] = saved.model_dump_json().encode()
    return rid, version, key


def run(history, *, apply=False, digest=None, loader=None):
    store, alice, *_ = history
    return import_history(store, tenant_id=alice.tenant_id, artifact_loader=loader or artifact_loader(store.objects), apply=apply, expected_digest=digest)


def business_state(store):
    with store._conn() as conn:
        tables = (
            "product.audit_rounds",
            "product.findings",
            "product.reviews",
            "engine.jobs",
            "engine.attempts",
            "product.usage_events",
            "product.suite_validations",
            "product.suite_publications",
            "product.checker_execution_snapshots",
            "product.checker_round_artifacts",
        )
        return {table: conn.execute(f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM {table} t").fetchone()[0] for table in tables}


def test_empty_tenant_is_a_noop_and_unknown_tenant_is_rejected(history):
    plan = run(history)
    assert plan["rounds"] == [] and plan["complete"] and not plan["writes"]
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    with pytest.raises(HistoryImportError, match="history_tenant_missing"):
        import_history(history[0], tenant_id=str(uuid.uuid4()), artifact_loader=lambda _: {})


def test_old_round_dry_run_apply_retry_readback_preserve_results_and_human_oracle(history):
    store, alice, bob, foreign = history
    rid, version, _ = old_round(history)
    before, objects = business_state(store), dict(store.objects.data)
    plan = run(history)
    assert plan["complete"] and plan["rounds"][0]["state"] == "ready"
    assert store.objects.data == objects and business_state(store) == before
    result = run(history, apply=True, digest=plan["plan_digest"])
    assert result["complete"] and result["rounds"][0]["state"] == "imported"
    api = build_product_store(store.settings, objects=store.objects, role="api")
    readback = read_history(api, alice, rid)
    assert readback[0]["verification"] == "unverified"
    assert readback[0]["artifact"]["state"] == "saved_not_execution_proof"
    assert "object_key" not in readback[0]["artifact"]
    suite_id = readback[0]["artifact"]["suite_revision_id"]
    suite = SuiteStore(store.settings, store.objects).resolve_suite(suite_id, alice.tenant_id, include_history=True)
    from product.store.suite_releases import SuiteReleaseStore

    releases = SuiteReleaseStore(store.settings, store.objects)
    assert SuiteStore(store.settings, store.objects).get_revision(suite_id, alice.tenant_id) is None
    with pytest.raises(KeyError, match="suite_missing"):
        SuiteStore(store.settings, store.objects).resolve_suite(suite_id, alice.tenant_id)
    assert releases.list_suites(bob) == {"suites": []}
    with pytest.raises(KeyError, match="suite_access_denied"):
        releases.detail(bob, suite_id)
    with pytest.raises(KeyError, match="suite_access_denied"):
        releases.publish(alice, suite.revision.suite_key, suite_id, 0, str(uuid.uuid4()), force_unverified=True)
    with pytest.raises(KeyError, match="suite_access_denied"):
        releases.upgrade_official(alice, suite.revision.suite_key, suite_id, str(uuid.uuid4()), 0, str(uuid.uuid4()))
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_upgrade_operations").fetchone()[0] == 0
    assert suite.files["checker.py"] == b"def create_audit(*args): raise RuntimeError('must never execute')\n"
    assert run(history, apply=True, digest=plan["plan_digest"])["rounds"][0]["state"] == "verified"
    assert read_history(api, alice, rid) == readback and business_state(store) == before
    assert store.get_round(rid)["findings"][0]["review"]["rationale"] == "Human oracle: PASS"
    for denied in (bob, foreign):
        with pytest.raises(KeyError, match="missing"):
            read_history(api, denied, rid)
    with store._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (version.file_id, alice.user_id))
    with pytest.raises(KeyError, match="missing"):
        read_history(api, alice, rid)


def test_missing_artifact_is_reported_and_other_round_commits_then_resumes(history):
    store = history[0]
    failed_id, _, key = old_round(history)
    saved = store.objects.data.pop(key)
    good_id, _, _ = old_round(history, mode="standard", artifact=False)
    plan = run(history)
    assert not plan["complete"]
    states = {row["round_id"]: row for row in plan["rounds"]}
    assert states[failed_id]["reason"] == "history_artifact_missing"
    result = run(history, apply=True, digest=plan["plan_digest"])
    assert not result["complete"]
    assert next(row for row in result["rounds"] if row["round_id"] == good_id)["state"] == "imported"
    store.objects.data[key] = saved
    recovered_plan = run(history)
    recovered = run(history, apply=True, digest=recovered_plan["plan_digest"])
    assert recovered["complete"]
    assert {row["state"] for row in recovered["rounds"]} == {"imported", "verified"}


def test_partial_object_write_rolls_back_round_and_retry_reuses_orphans(history, monkeypatch):
    store, alice, *_ = history
    rid, _, _ = old_round(history)
    plan = run(history)
    original = store.objects.put_exclusive
    writes = 0

    def fail_second(key, body, content_type):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("secret database URL must not be printed")
        return original(key, body, content_type)

    monkeypatch.setattr(store.objects, "put_exclusive", fail_second)
    result = run(history, apply=True, digest=plan["plan_digest"])
    assert result["rounds"][0]["reason"] == "history_read_or_write_failed"
    with store._conn() as conn:
        assert conn.execute("SELECT checker_history FROM product.round_inputs WHERE round_id=%s", (rid,)).fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM product.suite_revisions").fetchone()[0] == 0
    monkeypatch.setattr(store.objects, "put_exclusive", original)
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    assert read_history(store, alice, rid)


@pytest.mark.parametrize("change", ["rule", "artifact", "original"])
def test_stale_plan_or_corrupt_original_never_writes(history, change):
    store = history[0]
    rid, version, key = old_round(history)
    plan = run(history)
    if change == "rule":
        with store._conn() as conn:
            conn.execute('UPDATE product.round_inputs SET rule_body=rule_body || \'{"title":"changed"}\' WHERE round_id=%s', (rid,))
    elif change == "artifact":
        raw = json.loads(store.objects.data[key])
        raw["files"]["checker.py"] = "def create_audit(): pass\n"
        raw["program_digest"] = ""
        store.objects.data[key] = CheckerArtifact.model_validate(raw).model_dump_json().encode()
    else:
        store.objects.data[version.object_key] = b"corrupt original"
    with pytest.raises(HistoryImportError, match="history_plan_changed_or_unconfirmed"):
        run(history, apply=True, digest=plan["plan_digest"])
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions").fetchone()[0] == 0


def test_schema_rejects_app_writes_and_mutating_imported_sources(history):
    store, alice, *_ = history
    rid, _, _ = old_round(history, mode="standard", artifact=False)
    plan = run(history)
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    api = build_product_store(store.settings, objects=store.objects, role="api")
    with api._conn() as conn, pytest.raises(psycopg.Error, match="immutable_checker_history"):
        conn.execute("UPDATE product.round_inputs SET checker_history=NULL WHERE round_id=%s", (rid,))
    with store._conn() as conn, pytest.raises(psycopg.Error, match="immutable_checker_history"):
        conn.execute("UPDATE product.round_inputs SET rule_title='tampered' WHERE round_id=%s", (rid,))
    pending_id, _, _ = old_round(history, mode="standard", artifact=False, state="queued")
    result = run(history)
    assert next(row for row in result["rounds"] if row["round_id"] == pending_id)["reason"] == "history_round_not_terminal"
    with pytest.raises(PermissionError, match="history_migrator_required"):
        import_history(api, tenant_id=alice.tenant_id, artifact_loader=lambda _: {}, apply=True, expected_digest=plan["plan_digest"])


def test_same_tenant_rule_version_spoof_and_wrong_attempt_are_blocked(history):
    store, alice, _, foreign = history
    rid, _, _ = old_round(history)
    with store._conn() as conn:
        conn.execute("UPDATE product.rule_versions SET tenant_id=%s WHERE id=(SELECT rule_version_id FROM product.round_inputs WHERE round_id=%s)", (foreign.tenant_id, rid))
    assert run(history)["rounds"][0]["reason"] == "history_rule_missing"
    with store._conn() as conn:
        conn.execute("UPDATE product.rule_versions SET tenant_id=%s WHERE id=(SELECT rule_version_id FROM product.round_inputs WHERE round_id=%s)", (alice.tenant_id, rid))
        conn.execute("UPDATE engine.jobs SET attempt=2 WHERE audit_round_id=%s", (rid,))
    assert run(history)["rounds"][0]["reason"] == "history_execution_identity_unknown"


def test_membership_revocation_and_suite_tampering_fail_readback(history):
    store, alice, *_ = history
    rid, _, _ = old_round(history)
    plan = run(history)
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    identity = read_history(store, alice, rid)[0]
    suite = SuiteStore(store.settings, store.objects).resolve_suite(identity["artifact"]["suite_revision_id"], alice.tenant_id, include_history=True)
    key = f"suites/{alice.tenant_id}/objects/{suite.object_digests['checker.py']}"
    original = store.objects.data[key]
    store.objects.data[key] = b"tampered"
    with pytest.raises(ValueError, match="digest_mismatch"):
        read_history(store, alice, rid)
    store.objects.data[key] = original
    with store._conn() as conn:
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (alice.user_id, alice.tenant_id))
    with pytest.raises(KeyError, match="missing"):
        read_history(store, alice, rid)


def test_apply_requires_exact_reviewed_plan_and_mid_apply_object_change_fails(history):
    store = history[0]
    rid, _, key = old_round(history)
    with pytest.raises(HistoryImportError, match="history_plan_changed_or_unconfirmed"):
        run(history, apply=True)
    plan = run(history)
    loader = artifact_loader(store.objects)
    calls = 0

    def change_after_plan(source):
        nonlocal calls
        calls += 1
        if calls == 2:
            raw = json.loads(store.objects.data[key])
            raw["files"]["checker.py"] = "def create_audit(): pass\n"
            raw["program_digest"] = ""
            store.objects.data[key] = CheckerArtifact.model_validate(raw).model_dump_json().encode()
        return loader(source)

    result = run(history, apply=True, digest=plan["plan_digest"], loader=change_after_plan)
    assert result["rounds"][0]["reason"] == "history_artifact_changed"
    with store._conn() as conn:
        assert conn.execute("SELECT checker_history FROM product.round_inputs WHERE round_id=%s", (rid,)).fetchone()[0] is None


def test_lost_commit_response_reports_unknown_once_and_retry_reads_back(history, monkeypatch):
    from contextlib import contextmanager

    store, alice, *_ = history
    rid, _, _ = old_round(history, mode="standard", artifact=False)
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


@pytest.mark.parametrize("fault", ["missing_original", "changed_original", "missing_artifact", "changed_artifact"])
def test_imported_history_never_hides_later_source_damage(history, fault):
    store, alice, *_ = history
    rid, version, key = old_round(history)
    plan = run(history)
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    target = version.object_key if "original" in fault else key
    if fault.startswith("missing"):
        store.objects.data.pop(target)
    else:
        store.objects.data[target] = b"changed after import"
    with pytest.raises((ObjectMissing, HistoryImportError)):
        read_history(store, alice, rid)
    damaged_plan = run(history)
    assert not damaged_plan["complete"]
    assert not run(history, apply=True, digest=damaged_plan["plan_digest"])["complete"]


def test_archive_cannot_be_published_or_validated_by_direct_api_sql(history):
    store, alice, *_ = history
    rid, _, _ = old_round(history)
    plan = run(history)
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    suite_id = read_history(store, alice, rid)[0]["artifact"]["suite_revision_id"]
    suite = SuiteStore(store.settings, store.objects).resolve_suite(suite_id, alice.tenant_id, include_history=True)
    api = build_product_store(store.settings, objects=store.objects, role="api")
    with api._conn() as conn, pytest.raises(psycopg.Error, match="history_suite_not_executable"):
        conn.execute(
            """INSERT INTO product.tenant_suite_heads(tenant_id,suite_key,current_revision_id,revision,force_unverified,published_by)
                     VALUES(%s,%s,%s,1,true,%s)""",
            (alice.tenant_id, suite.revision.suite_key, suite_id, alice.user_id),
        )
    with api._conn() as conn, pytest.raises(psycopg.Error, match="history_suite_not_executable"):
        conn.execute(
            """INSERT INTO product.suite_validations(tenant_id,revision_id,status,revision,suite_digest,proof,recorded_by)
                     VALUES(%s,%s,'verified',1,%s,'{}',%s)""",
            (alice.tenant_id, suite_id, suite.revision.resolved_digest, alice.user_id),
        )
    with api._conn() as conn, pytest.raises(psycopg.Error, match="history_suite_not_executable"):
        conn.execute(
            """INSERT INTO product.suite_publications(tenant_id,operation_id,suite_key,revision_id,expected_revision,revision,
                     request_fingerprint,verification_status,force_unverified,published_by)
                     VALUES(%s,%s,%s,%s,0,1,'forged','unverified',true,%s)""",
            (alice.tenant_id, str(uuid.uuid4()), suite.revision.suite_key, suite_id, alice.user_id),
        )


@pytest.mark.parametrize("location", ["reusable_cache", "previous_attempt"])
def test_only_exact_result_attempt_artifact_can_be_imported(history, location):
    from agent.checker_program import checker_program_key

    store, alice, *_ = history
    rid, _, key = old_round(history)
    if location == "reusable_cache":
        raw = store.objects.data.pop(key)
        artifact = CheckerArtifact.model_validate_json(raw)
        store.objects.data[checker_program_key(alice.tenant_id, REVISION, artifact.input_digest)] = raw
    else:
        with store._conn() as conn:
            conn.execute("UPDATE product.audit_rounds SET result_attempt=2 WHERE id=%s", (rid,))
            conn.execute("UPDATE engine.jobs SET attempt=2 WHERE audit_round_id=%s", (rid,))
            conn.execute(
                """INSERT INTO engine.attempts(id,job_id,attempt,worker_id,finished_at,outcome)
                         SELECT %s,id,2,'new worker',now(),'succeeded' FROM engine.jobs WHERE audit_round_id=%s""",
                (str(uuid.uuid4()), rid),
            )
    plan = run(history)
    assert not plan["complete"] and plan["rounds"][0]["reason"] == "history_artifact_missing"
    assert not run(history, apply=True, digest=plan["plan_digest"])["complete"]
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions").fetchone()[0] == 0


def test_all_targets_and_reference_are_frozen_and_reference_revocation_blocks_readback(history):
    store, alice, *_ = history
    rid, _, _ = old_round(history, artifact=False)
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
    with store._conn() as conn:
        source = history_source(conn, alice.tenant_id, rid)
    frozen = [
        FrozenFile(
            file_version_id=item["file_version_id"],
            role=item["role"],
            filename=item["original_filename"],
            sha256=item["sha256"],
            rule_version_id=item["rule_version_id"],
            rule_body=item["rule_body"],
            rule_title=item["rule_title"],
            rule_execution=item["rule_execution"],
        )
        for item in source["inputs"]
    ]
    job_id = source["jobs"][0]["id"]
    request = ExecuteRequest(protocol_revision="v1", round_id=rid, tenant_id=alice.tenant_id, files=frozen, executor="ppap-deep", executor_revision=REVISION, job_id=job_id)
    group = group_context_for_spec(request)
    for item in frozen:
        if item.role != "target":
            continue
        digest = input_digest(group.by_audit_id[item.file_version_id], group.rules_by_id[item.file_version_id], file_sha256=item.sha256, role=item.role, group_context=group)
        saved = CheckerArtifact(input_digest=digest, executor_revision=REVISION, entrypoint="checker:create_audit", files={"checker.py": b"def create_audit(): pass\n"})
        key = f"deep/{hashlib.sha256(alice.tenant_id.encode()).hexdigest()}/{REVISION}/rounds/{rid}/{hashlib.sha256(job_id.encode()).hexdigest()}-1/checkers/{digest}.json"
        store.objects.data[key] = saved.model_dump_json().encode()
    plan = run(history)
    assert plan["complete"] and plan["rounds"][0]["saved_artifacts"] == 2
    assert run(history, apply=True, digest=plan["plan_digest"])["complete"]
    identities = read_history(store, alice, rid)
    assert len(identities) == 3 and sum(identity["artifact"] is not None for identity in identities) == 2
    assert len({identity["source_digest"] for identity in identities}) == 1
    with store._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (uploaded[1].file_id, alice.user_id))
    with pytest.raises(KeyError, match="missing"):
        read_history(store, alice, rid)


@pytest.mark.parametrize("shared_suite", [False, True])
def test_concurrent_imports_resume_without_duplicate_round_identities_or_archive_suites(history, shared_suite):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    store, alice, *_ = history
    first, _, first_key = old_round(history)
    second = first
    if shared_suite:
        second, job_id = str(uuid.uuid4()), str(uuid.uuid4())
        with store._conn() as conn:
            old_job = str(conn.execute("SELECT id FROM engine.jobs WHERE audit_round_id=%s", (first,)).fetchone()[0])
            conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES(%s,%s,'audit_round')", (second, alice.tenant_id))
            conn.execute(
                """INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt,analysis_mode)
                         SELECT %s,tenant_id,created_by,%s,request_fingerprint,execution,result_attempt,analysis_mode FROM product.audit_rounds WHERE id=%s""",
                (second, second, first),
            )
            conn.execute(
                """INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body,rule_execution)
                         SELECT %s,file_version_id,role,rule_version_id,rule_title,rule_body,rule_execution FROM product.round_inputs WHERE round_id=%s""",
                (second, first),
            )
            conn.execute(
                """INSERT INTO engine.jobs(id,tenant_id,audit_round_id,kind,payload,status,attempt)
                         SELECT %s,tenant_id,%s,kind,payload,status,attempt FROM engine.jobs WHERE id=%s""",
                (job_id, second, old_job),
            )
            conn.execute(
                """INSERT INTO engine.attempts(id,job_id,attempt,worker_id,finished_at,outcome)
                         SELECT %s,%s,attempt,worker_id,finished_at,outcome FROM engine.attempts WHERE job_id=%s""",
                (str(uuid.uuid4()), job_id, old_job),
            )
        second_key = first_key.replace(first, second).replace(hashlib.sha256(old_job.encode()).hexdigest(), hashlib.sha256(job_id.encode()).hexdigest())
        store.objects.data[second_key] = store.objects.data[first_key]
    loader = artifact_loader(store.objects)
    plans = [import_history(store, tenant_id=alice.tenant_id, round_ids=[rid], artifact_loader=loader) for rid in (first, second)]
    barrier = threading.Barrier(2)

    def apply(rid, plan):
        first_load = True

        def synchronized(source):
            nonlocal first_load
            value = loader(source)
            if first_load:
                first_load = False
                barrier.wait(timeout=10)
            return value

        return import_history(store, tenant_id=alice.tenant_id, round_ids=[rid], artifact_loader=synchronized, apply=True, expected_digest=plan["plan_digest"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(apply, rid, plan) for rid, plan in zip((first, second), plans)]
        results = [future.result(timeout=20) for future in pending]
    assert all(len(result["rounds"]) == 1 for result in results)
    assert any(result["complete"] for result in results)
    for rid, plan in zip((first, second), plans):
        assert import_history(store, tenant_id=alice.tenant_id, round_ids=[rid], artifact_loader=loader, apply=True, expected_digest=plan["plan_digest"])["complete"]
    assert read_history(store, alice, first)[0]["artifact"]["suite_revision_id"] == read_history(store, alice, second)[0]["artifact"]["suite_revision_id"]
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE history_only").fetchone()[0] == 1
