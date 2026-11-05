"""Real PostgreSQL validation/publication races and immutable audit references."""

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from audit.checker_core import checker_digest
from audit.standard_suites import library_revision
from product.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore, _lock, current_selection
from product.store.suites import SuiteStore
from psycopg.errors import LockNotAvailable

FILES = {"rules.json": b'{"rules":[{"id":"R1","rule_to_check":"Required"}]}', "checker.py": b"def check(context):\n    return []\n"}


def seed(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="suite-transactions", usage_charge_enabled=False))
    alice = store.upsert_feishu_identity("suite-tenant", "alice", "Alice")
    bob = store.upsert_feishu_identity("suite-tenant", "bob", "Bob")
    suites = SuiteStore(store.settings, store.objects, role="migrator")
    releases = SuiteReleaseStore(store.settings, store.objects, role="api")
    return store, suites, releases, alice, bob


def proof_for(suites, principal, revision, *, related=None, version=None, expected="PASS", actual="PASS", clause="R1"):
    resolved = suites.resolve_suite(revision.id, principal.tenant_id)
    return SuiteValidationReport(
        suite_revision_id=revision.id,
        suite_digest=revision.resolved_digest,
        program_digest=checker_digest({path: body for path, body in resolved.files.items() if path in {"checker.py", "resources.py"}}),
        library_revision=version or library_revision(),
        oracle_revision="human-reviewed-oracle-1",
        related_suite_digests=related or {},
        cases=tuple(
            CaseEvaluation(
                case_id=f"case-{i}",
                ordinal=i,
                input_digest=hashlib.sha256(str(i).encode()).hexdigest(),
                split="holdout" if i % 4 == 0 else "training",
                coverage=True,
                clauses=(ClauseEvaluation(clause_id=clause, expected=expected, actual=actual, evidence_locatable=True, checks_performed=1),),
            )
            for i in range(1, 5)
        ),
    )


def test_verification_is_whole_suite_and_related_invalidation_is_only_one_hop(auth_store_factory):
    store, suites, releases, alice, bob = seed(auth_store_factory)
    first = suites.create_revision(tenant_id=None, suite_key="first", files=FILES, created_by=None)
    second = suites.create_revision(tenant_id=None, suite_key="second", files=FILES, created_by=None, related_suite_keys=("first",))
    third = suites.create_revision(tenant_id=None, suite_key="third", files=FILES, created_by=None, related_suite_keys=("second",))
    for revision, related in ((first, {}), (second, {"first": first.resolved_digest}), (third, {"second": second.resolved_digest})):
        releases.record_validation(alice, revision.id, proof_for(suites, alice, revision, related=related), expected_revision=0)
        assert releases.active(bob, revision.suite_key)["validation"]["status"] == "verified"
    # Editing the rules creates a new whole-Suite candidate with no inherited proof.
    candidate = releases.save_candidate(alice, first.id, {"rules.json": json.dumps({"rules": [{"id": "R1", "rule_to_check": "Changed wording"}]})})
    assert candidate["validation"]["status"] == "unverified"
    assert releases.active(bob, "second")["validation"]["status"] == "verified"
    releases.publish(bob, "first", candidate["id"], 0, str(uuid.uuid4()), force_unverified=True)
    assert releases.active(alice, "second")["validation"]["status"] == "stale"
    assert releases.active(alice, "third")["validation"]["status"] == "verified"
    # A late proof using the previous dependency is rejected; it cannot undo stale.
    with pytest.raises(ValueError, match="suite_validation_inputs_changed"):
        releases.record_validation(alice, second.id, proof_for(suites, alice, second, related={"first": first.resolved_digest}), expected_revision=1)
    assert releases.active(alice, "second")["validation"]["status"] == "stale"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0


def test_pass_is_not_proof_and_library_update_does_not_revoke_verified(auth_store_factory):
    _, suites, releases, alice, _ = seed(auth_store_factory)
    revision = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    # Correct NG is a passing regression sample, not a failed business audit.
    report = proof_for(suites, alice, revision, expected="NG", actual="NG", version="previous-library")
    result = releases.record_validation(alice, revision.id, report, expected_revision=0)
    assert result["verified"] is True
    assert result["passed_samples"] == result["total_samples"] == 4
    assert result["samples_not_rerun"] is True
    published = releases.publish(alice, revision.suite_key, revision.id, 0, str(uuid.uuid4()))
    assert published["verification_status"] == "verified"
    # PASS without independent expected matching is not verification.
    report = proof_for(suites, alice, revision, expected="NG", actual="PASS")
    result = releases.record_validation(alice, revision.id, report, expected_revision=1)
    assert result["verified"] is False
    assert result["passed_samples"] == 0
    with pytest.raises(ValueError, match="suite_validation_conflict"):
        releases.record_validation(alice, revision.id, report, expected_revision=1)


def test_incomplete_clause_coverage_cannot_claim_verified_and_cross_tenant_validation_denied(auth_store_factory):
    store, suites, releases, alice, _ = seed(auth_store_factory)
    revision = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    report = proof_for(suites, alice, revision, clause="invented")
    result = releases.record_validation(alice, revision.id, report, expected_revision=0)
    assert result["verified"] is False
    assert "suite_clause_coverage_incomplete" in result["reasons"]
    candidate = suites.create_revision(tenant_id=alice.tenant_id, suite_key="control-plan", files={"checker.py": b"def check(c): return []\n"}, created_by=alice.user_id, base_revision_id=revision.id)
    foreign = store.upsert_feishu_identity("other-tenant", "mallory")
    with pytest.raises(KeyError):
        releases.record_validation(foreign, candidate.id, proof_for(suites, alice, candidate), expected_revision=0)


def test_creator_deletion_preserves_adopted_bytes_and_anonymizes_release(auth_store_factory):
    store, suites, releases, alice, bob = seed(auth_store_factory)
    official = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    candidate = releases.save_candidate(alice, official.id, {"checker.py": "def check(context): return []\n"})
    releases.publish(alice, "control-plan", candidate["id"], 0, str(uuid.uuid4()), True)
    with store._conn() as conn, conn.transaction():
        conn.execute("DELETE FROM product.identities WHERE user_id = %s", (alice.user_id,))
        conn.execute("DELETE FROM product.memberships WHERE user_id = %s", (alice.user_id,))
        conn.execute("DELETE FROM product.users WHERE id = %s", (alice.user_id,))
    retained = releases.detail(bob, candidate["id"])
    assert retained["created_by"] is None
    assert retained["created_by_name"] == "已删除成员"
    assert retained["files"]["checker.py"] == "def check(context): return []\n"
    history = releases.active(bob, "control-plan")["history"]
    assert history[0]["published_by"] is None
    assert history[0]["published_by_name"] == "已删除成员"
    with store._conn() as conn, conn.transaction():
        frozen = current_selection(conn, bob.tenant_id, "control-plan")
    assert frozen["revision_id"] == candidate["id"]
    assert frozen["verification_status"] == "unverified"


def test_official_proof_is_trusted_global_and_dependency_bound(auth_store_factory):
    store, suites, releases, alice, bob = seed(auth_store_factory)
    first = suites.create_revision(tenant_id=None, suite_key="first", files=FILES, created_by=None)
    second = suites.create_revision(tenant_id=None, suite_key="second", files=FILES, created_by=None, related_suite_keys=("first",))
    report = proof_for(suites, alice, second, related={"first": first.resolved_digest})
    with pytest.raises(PermissionError, match="official_validation_write_forbidden"):
        releases.record_official_validation(second.id, report, expected_revision=0)
    platform = SuiteReleaseStore(store.settings, store.objects, role="migrator")
    platform.record_official_validation(second.id, report, expected_revision=0)
    untouched = store.upsert_feishu_identity("untouched", "reader")
    assert releases.active(untouched, "second")["validation"]["verified"] is True
    candidate = releases.save_candidate(alice, first.id, {"checker.py": "def check(c): return [1]\n"})
    releases.publish(bob, "first", candidate["id"], 0, str(uuid.uuid4()), True)
    # The official proof cannot be reused for a tenant's changed direct dependency.
    assert releases.active(alice, "second")["validation"]["status"] == "stale"
    assert releases.active(untouched, "second")["validation"]["status"] == "verified"
    # SQL permission boundary also forbids writing a global proof with API credentials.
    with releases._conn() as conn, conn.transaction(), pytest.raises(Exception, match="official_validation_write_forbidden"):
        conn.execute("UPDATE product.suite_validations SET status = 'unverified' WHERE tenant_id IS NULL AND revision_id = %s", (second.id,))


def test_official_fallback_revision_is_not_tenant_validation_write_head(auth_store_factory):
    store, suites, releases, alice, bob = seed(auth_store_factory)
    revision = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    platform = SuiteReleaseStore(store.settings, store.objects, role="migrator")
    report = proof_for(suites, alice, revision)
    for expected in (0, 1):
        official = platform.record_official_validation(revision.id, report, expected_revision=expected)
        assert official["write_revision"] == expected + 1
    fallback = releases.detail(alice, revision.id)["validation"]
    assert fallback["proof_scope"] == "official"
    assert fallback["revision"] == 2
    assert fallback["write_revision"] == 0
    with pytest.raises(ValueError, match="suite_validation_conflict"):
        releases.record_validation(alice, revision.id, report, expected_revision=fallback["revision"])
    failed_report = proof_for(suites, alice, revision, expected="NG", actual="PASS")
    own = releases.record_validation(alice, revision.id, failed_report, expected_revision=fallback["write_revision"])
    assert own["proof_scope"] == "tenant"
    assert own["revision"] == own["write_revision"] == 1
    # A failing tenant proof cannot silently fall back to a successful global proof.
    assert releases.detail(bob, revision.id)["validation"]["verified"] is False
    outsider = store.upsert_feishu_identity("other-tenant", "reader")
    assert releases.detail(outsider, revision.id)["validation"]["verified"] is True


def test_concurrent_first_tenant_validation_uses_cas(auth_store_factory):
    _, suites, releases, alice, bob = seed(auth_store_factory)
    revision = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    report = proof_for(suites, alice, revision)

    def record(person):
        try:
            return releases.record_validation(person, revision.id, report, expected_revision=0)["write_revision"]
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(record, (alice, bob)))
    assert results.count(1) == results.count("suite_validation_conflict") == 1


@pytest.mark.parametrize("field,value", [("suite_revision_id", str(uuid.uuid4())), ("suite_digest", "0" * 64), ("program_digest", "0" * 64)])
def test_validation_rejects_any_mismatched_content_identity(auth_store_factory, field, value):
    _, suites, releases, alice, _ = seed(auth_store_factory)
    revision = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    report = proof_for(suites, alice, revision).model_copy(update={field: value})
    with pytest.raises(ValueError, match="suite_validation_identity_mismatch"):
        releases.record_validation(alice, revision.id, report, expected_revision=0)
    assert releases.detail(alice, revision.id)["validation"]["write_revision"] == 0


@pytest.mark.parametrize(
    "path,body",
    [
        ("rules.json", '{"rules":[{"id":"R1","rule_to_check":"Changed semantics"}]}'),
        ("checker.py", "def check(context): return [1]\n"),
        ("resources.py", "LAYOUT = {'number': 'B7'}\n"),
        ("templates.json", '[{"kind":"structure","columns":["Number"]}]'),
    ],
)
def test_every_suite_content_change_requires_new_bound_proof(auth_store_factory, path, body):
    _, suites, releases, alice, _ = seed(auth_store_factory)
    official = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    report = proof_for(suites, alice, official)
    releases.record_validation(alice, official.id, report, expected_revision=0)
    candidate = releases.save_candidate(alice, official.id, {path: body})
    assert candidate["validation"]["verified"] is False
    # Retargeting only the immutable ID cannot claim that changed bytes were tested.
    with pytest.raises(ValueError, match="suite_validation_identity_mismatch"):
        releases.record_validation(alice, candidate["id"], report.model_copy(update={"suite_revision_id": candidate["id"]}), expected_revision=0)
    with pytest.raises(ValueError, match="suite_confirmation_required"):
        releases.publish(alice, "control-plan", candidate["id"], 0, str(uuid.uuid4()))
    receipt = releases.publish(alice, "control-plan", candidate["id"], 0, str(uuid.uuid4()), True)
    assert receipt["verification_status"] == "unverified"


def test_latest_official_order_is_revision_not_transaction_timestamp_or_uuid(auth_store_factory):
    store, suites, releases, alice, _ = seed(auth_store_factory)
    with store._conn() as conn, conn.transaction():
        revisions = [suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None, conn=conn) for _ in range(8)]
        assert conn.execute("SELECT count(DISTINCT created_at) FROM product.suite_revisions").fetchone()[0] == 1
    assert releases.active(alice, "control-plan")["current_revision_id"] == revisions[-1].id


def test_api_cannot_move_official_validation_into_tenant_scope(auth_store_factory):
    store, suites, releases, alice, _ = seed(auth_store_factory)
    revision = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    platform = SuiteReleaseStore(store.settings, store.objects, role="migrator")
    platform.record_official_validation(revision.id, proof_for(suites, alice, revision), expected_revision=0)
    with releases._conn() as conn, conn.transaction(), pytest.raises(Exception, match="immutable_suite_validation_scope"):
        conn.execute("UPDATE product.suite_validations SET tenant_id = %s WHERE tenant_id IS NULL AND revision_id = %s", (alice.tenant_id, revision.id))
    assert releases.detail(alice, revision.id)["validation"]["proof_scope"] == "official"


def test_official_validation_cannot_use_tenant_dependency_content(auth_store_factory):
    store, suites, releases, alice, _ = seed(auth_store_factory)
    first = suites.create_revision(tenant_id=None, suite_key="first", files=FILES, created_by=None)
    second = suites.create_revision(tenant_id=None, suite_key="second", files=FILES, created_by=None, related_suite_keys=("first",))
    candidate = releases.save_candidate(alice, first.id, {"resources.py": "LOCATION = 'C1'\n"})
    releases.publish(alice, "first", candidate["id"], 0, str(uuid.uuid4()), True)
    tenant_report = proof_for(suites, alice, second, related={"first": candidate["resolved_digest"]})
    assert releases.record_validation(alice, second.id, tenant_report, expected_revision=0)["verified"] is True
    platform = SuiteReleaseStore(store.settings, store.objects, role="migrator")
    with pytest.raises(ValueError, match="suite_validation_inputs_changed"):
        platform.record_official_validation(second.id, tenant_report, expected_revision=0)
    with pytest.raises(KeyError):
        platform.record_official_validation(candidate["id"], tenant_report, expected_revision=0)


def test_release_migration_replay_preserves_adoption_proof_and_lineage(auth_store_factory):
    store, suites, releases, alice, bob = seed(auth_store_factory)
    official = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    candidate = suites.create_revision(tenant_id=alice.tenant_id, suite_key="control-plan", files={"resources.py": b"LOCATION = 'B2'\n"}, created_by=alice.user_id, base_revision_id=official.id)
    releases.record_validation(alice, candidate.id, proof_for(suites, alice, candidate), expected_revision=0)
    operation = str(uuid.uuid4())
    before = releases.publish(bob, "control-plan", candidate.id, 0, operation)
    detail = releases.detail(bob, candidate.id)
    migration = Path(__file__).resolve().parents[3] / "migrations/0041_suite_releases.sql"
    for _ in range(2):
        with store._conn() as conn:
            conn.execute(migration.read_text())
    assert releases.detail(bob, candidate.id) == detail
    assert releases.active(bob, "control-plan")["history"] == [before]
    assert releases.publish(bob, "control-plan", candidate.id, 0, operation) == before
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.schema_migrations WHERE version='0041_suite_releases.sql'").fetchone()[0] == 1
    with store._conn() as conn, conn.transaction(), pytest.raises(Exception, match="immutable_suite_publication"):
        conn.execute("UPDATE product.suite_publications SET revision_id = %s WHERE operation_id = %s", (official.id, operation))


@pytest.mark.parametrize("action", ["official_revision", "official_validation", "tenant_publication", "tenant_validation"])
def test_admission_freeze_excludes_input_changes_until_commit(auth_store_factory, monkeypatch, action):
    store, suites, releases, alice, _ = seed(auth_store_factory)
    official = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    platform = SuiteReleaseStore(store.settings, store.objects, role="migrator")
    report = proof_for(suites, alice, official)
    for target in (suites, releases, platform):
        connect = target._conn

        def bounded_connection(connect=connect):
            conn = connect()
            conn.execute("SET lock_timeout = '100ms'")
            return conn

        monkeypatch.setattr(target, "_conn", bounded_connection)
    actions = {
        "official_revision": lambda: suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None),
        "official_validation": lambda: platform.record_official_validation(official.id, report, expected_revision=0),
        "tenant_publication": lambda: releases.publish(alice, "control-plan", official.id, 0, str(uuid.uuid4()), True),
        "tenant_validation": lambda: releases.record_validation(alice, official.id, report, expected_revision=0),
    }
    with store._conn() as conn, conn.transaction():
        frozen = current_selection(conn, alice.tenant_id, "control-plan")
        with pytest.raises(LockNotAvailable):
            actions[action]()
        assert current_selection(conn, alice.tenant_id, "control-plan") == frozen
    # Once the acceptance transaction commits the operation is allowed normally.
    actions[action]()


def test_worker_can_record_tenant_proof_but_never_official(auth_store_factory):
    store, suites, _, alice, _ = seed(auth_store_factory)
    official = suites.create_revision(tenant_id=None, suite_key="control-plan", files=FILES, created_by=None)
    report = proof_for(suites, alice, official)
    worker = SuiteReleaseStore(store.settings, store.objects, role="worker")
    # The optimizer verifies its frozen job/attempt/user fence before entering
    # this host-only completion transaction, rather than using a user HTTP route.
    with worker._conn() as conn, conn.transaction():
        _lock(conn, alice.tenant_id)
        recorded = worker._record_validation(conn, alice.tenant_id, alice.user_id, official.id, report, 0)
    assert recorded["verified"] is True
    assert recorded["proof_scope"] == "tenant"
    with pytest.raises(PermissionError, match="official_validation_write_forbidden"):
        worker.record_official_validation(official.id, report, expected_revision=0)
    platform = SuiteReleaseStore(store.settings, store.objects, role="migrator")
    platform.record_official_validation(official.id, report, expected_revision=0)
    with worker._conn() as conn, conn.transaction(), pytest.raises(Exception, match="official_validation_write_forbidden"):
        conn.execute("UPDATE product.suite_validations SET status = 'unverified' WHERE tenant_id IS NULL")
