"""Real PostgreSQL validation/publication races and immutable audit references."""

import hashlib
import json
import uuid

import pytest
from audit.checker_core import checker_digest
from audit.standard_suites import library_revision
from product.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore, current_selection
from product.store.suites import SuiteStore

FILES = {"rules.json": b'{"rules":[{"id":"R1","rule_to_check":"Required"}]}', "checker.py": b'def check(context):\n    return []\n'}


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
            CaseEvaluation(case_id=f"case-{i}", ordinal=i, input_digest=hashlib.sha256(str(i).encode()).hexdigest(),
                           split="holdout" if i % 4 == 0 else "training", coverage=True,
                           clauses=(ClauseEvaluation(clause_id=clause, expected=expected, actual=actual, evidence_locatable=True, checks_performed=1),))
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
