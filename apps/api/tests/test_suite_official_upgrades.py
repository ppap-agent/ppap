"""Real PostgreSQL upgrade commands; official regression fixtures are synthetic, never live quality evidence."""

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from audit.checker_core import checker_digest
from audit.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport
from audit.standard_suites import library_revision
from product.store.suite_releases import SuiteReleaseStore
from product.store.suites import SuiteStore

from .test_suite_releases import FILES, command, prepare_fixture

KEY = "control-plan"
ACTIVE = f"/suites/{KEY}/active"
UPGRADES = f"/suites/{KEY}/upgrades"
SEEN = f"/suites/{KEY}/official-update/seen"


def publish_official(store, revision):
    """Controlled host proof only: these tests exercise publication/upgrade state, not checker accuracy."""
    platform = SuiteReleaseStore(store.settings, store.objects, role="migrator")
    resolved = platform.suites.resolve_suite(revision.id, "00000000-0000-0000-0000-000000000000")
    proof = SuiteValidationReport(
        suite_revision_id=revision.id,
        suite_digest=revision.resolved_digest,
        library_revision=library_revision(),
        program_digest=checker_digest({path: body for path, body in resolved.files.items() if path in {"checker.py", "resources.py"}}),
        oracle_revision="synthetic-upgrade-contract-oracle",
        cases=tuple(
            CaseEvaluation(
                case_id=f"case-{i}",
                ordinal=i,
                input_digest=hashlib.sha256(str(i).encode()).hexdigest(),
                split="holdout" if i % 4 == 0 else "training",
                coverage=True,
                clauses=(ClauseEvaluation(clause_id="R1", expected="NG", actual="NG", evidence_locatable=True, checks_performed=1),),
            )
            for i in range(1, 5)
        ),
    )
    validated = platform.record_official_validation(revision.id, proof, expected_revision=0)
    platform.publish_official(revision.id, expected_revision=platform.official_active(revision.suite_key)["revision"], expected_validation_revision=validated["write_revision"])


def setup_upgrade(auth_store_factory):
    store, official, alice, bob, clients = prepare_fixture(auth_store_factory)
    publish_official(store, official)
    api = SuiteStore(store.settings, store.objects, role="api")
    first = api.create_revision(tenant_id=alice.tenant_id, suite_key=KEY, files={"checker.py": b"enterprise checker"}, created_by=alice.user_id, base_revision_id=official.id)
    source = api.create_revision(tenant_id=alice.tenant_id, suite_key=KEY, files={"templates.json": b'[{"kind":"structure","name":"enterprise"}]'}, created_by=alice.user_id, base_revision_id=first.id)
    assert clients[0].put(ACTIVE, json=command(source.id)).status_code == 200
    target = SuiteStore(store.settings, store.objects, role="migrator").create_revision(
        tenant_id=None,
        suite_key=KEY,
        created_by=None,
        files={**FILES, "checker.py": b"official updated checker", "resources.py": b"NEW_OFFICIAL_RESOURCE = 1", "tests.json": b"[]"},
    )
    publish_official(store, target)
    payload = {"source_revision_id": source.id, "target_revision_id": target.id, "expected_revision": 1, "operation_id": str(uuid.uuid4())}
    return store, official, source, target, alice, bob, clients, payload


def test_upgrade_preserves_two_enterprise_deltas_and_requires_explicit_validation_and_publish(auth_store_factory):
    store, official, source, target, alice, _, (client, _, other), payload = setup_upgrade(auth_store_factory)
    before = client.get(ACTIVE).json()
    assert before["official_base_revision_id"] == official.id
    assert before["official_update_available"] and before["official_update_notice"]
    assert other.get(ACTIVE).json()["current_revision_id"] == target.id
    assert not other.get(ACTIVE).json()["official_update_available"]
    reply = client.post(UPGRADES, json=payload)
    assert reply.status_code == 201, reply.text
    candidate = reply.json()
    assert candidate["base_revision_id"] == target.id
    assert candidate["upgrade"] == payload
    assert candidate["files"]["checker.py"] == "enterprise checker"
    assert "enterprise" in candidate["files"]["templates.json"]
    assert candidate["files"]["resources.py"] == "NEW_OFFICIAL_RESOURCE = 1"
    assert candidate["files"]["tests.json"] == "[]"
    assert candidate["changed_paths"] == ["checker.py", "templates.json"]
    assert candidate["validation"]["status"] == "unverified" and candidate["validation"]["total_samples"] == 0
    assert client.get(ACTIVE).json()["current_revision_id"] == source.id
    assert client.put(ACTIVE, json=command(candidate["id"], 1, force=False)).json()["error"] == "suite_confirmation_required"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0
    # Explicit risk acknowledgment, not upgrading, is the adoption boundary.
    assert client.put(ACTIVE, json=command(candidate["id"], 1)).status_code == 200
    assert not client.get(ACTIVE).json()["official_update_available"]
    assert SuiteStore(store.settings, store.objects).resolve_suite(source.id, alice.tenant_id).files["checker.py"] == b"enterprise checker"


def test_once_notice_survives_reentry_is_per_member_and_returns_for_next_official_revision(auth_store_factory):
    store, _, _, target, alice, _, (client, peer, _), _ = setup_upgrade(auth_store_factory)
    assert client.post(SEEN, json={"official_revision_id": target.id}).status_code == 200
    fresh = SuiteReleaseStore(store.settings, store.objects).active(alice, KEY)
    assert fresh["official_update_available"] and not fresh["official_update_notice"]
    assert peer.get(ACTIVE).json()["official_update_notice"]
    newer = SuiteStore(store.settings, store.objects, role="migrator").create_revision(tenant_id=None, suite_key=KEY, files={**FILES, "checker.py": b"next"}, created_by=None)
    publish_official(store, newer)
    assert client.get(ACTIVE).json()["official_update_notice"]
    assert client.post(SEEN, json={"official_revision_id": target.id}).status_code == 409
    assert client.get(ACTIVE).json()["official_update_notice"]


def test_concurrent_replay_is_one_candidate_and_unknown_response_can_be_read_after_heads_change(auth_store_factory):
    store, _, source, _, _, _, (client, peer, other), payload = setup_upgrade(auth_store_factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = [future.result() for future in [pool.submit(client.post, UPGRADES, json=payload) for _ in range(2)]]
    assert [reply.status_code for reply in replies] == [201, 201]
    assert replies[0].json() == replies[1].json()
    candidate = replies[0].json()
    assert peer.put(ACTIVE, json=command(source.id, 1)).status_code == 200
    assert client.get(f"{UPGRADES}/{payload['operation_id']}").json() == candidate
    assert client.post(UPGRADES, json=payload).json() == candidate
    assert other.get(f"{UPGRADES}/{payload['operation_id']}").status_code == 404
    assert peer.post(UPGRADES, json=payload).status_code == 409
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_upgrade_operations").fetchone()[0] == 1


def test_stale_enterprise_and_official_heads_reject_without_candidate(auth_store_factory):
    store, _, source, target, _, _, (client, peer, _), payload = setup_upgrade(auth_store_factory)
    assert peer.put(ACTIVE, json=command(source.id, 1)).status_code == 200
    assert client.post(UPGRADES, json=payload).json()["error"] == "suite_head_conflict"
    newer = SuiteStore(store.settings, store.objects, role="migrator").create_revision(tenant_id=None, suite_key=KEY, files={**FILES, "checker.py": b"next"}, created_by=None)
    publish_official(store, newer)
    assert client.post(UPGRADES, json={**payload, "expected_revision": 2}).json()["error"] == "suite_official_head_conflict"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_upgrade_operations").fetchone()[0] == 0
    assert client.post(SEEN, json={"official_revision_id": target.id}).status_code == 409


def test_unpublished_selected_edits_are_preserved_and_cross_tenant_input_is_rejected(auth_store_factory):
    store, _, source, _, alice, _, (client, _, other), payload = setup_upgrade(auth_store_factory)
    edited = SuiteStore(store.settings, store.objects).create_revision(
        tenant_id=alice.tenant_id, suite_key=KEY, created_by=alice.user_id, base_revision_id=source.id, files={"checker.py": b"unpublished work"}
    )
    assert other.post(UPGRADES, json={**payload, "source_revision_id": edited.id}).status_code == 403
    response = client.post(UPGRADES, json={**payload, "source_revision_id": edited.id})
    assert response.status_code == 201, response.text
    assert response.json()["files"]["checker.py"] == "unpublished work"
    assert client.get(ACTIVE).json()["current_revision_id"] == source.id


def test_deleted_resource_tombstone_survives_and_corrupt_bytes_fail_closed(auth_store_factory):
    store, _, source, target, alice, _, (client, _, _), payload = setup_upgrade(auth_store_factory)
    api = SuiteStore(store.settings, store.objects)
    # A selected working edit may have its own inherited optional resource.
    added = api.create_revision(tenant_id=alice.tenant_id, suite_key=KEY, created_by=alice.user_id, base_revision_id=source.id, files={"resources.py": b"private helper"})
    removed = api.create_revision(tenant_id=alice.tenant_id, suite_key=KEY, created_by=alice.user_id, base_revision_id=added.id, files={}, deleted_paths=("resources.py",))
    # No net deletion against old official: a new official helper is inherited.
    response = client.post(UPGRADES, json={**payload, "source_revision_id": removed.id})
    assert response.status_code == 201
    assert response.json()["files"]["resources.py"] == "NEW_OFFICIAL_RESOURCE = 1"
    digest = target.overrides["checker.py"]
    store.objects.data[f"suites/official/objects/{digest}"] = b"corrupted"
    failed = client.post(UPGRADES, json={**payload, "operation_id": str(uuid.uuid4())})
    assert failed.status_code == 400 and failed.json()["error"] == "suite_object_digest_mismatch"


def test_upgrade_receipt_scope_immutability_and_creator_deletion(auth_store_factory):
    store, _, source, target, alice, _, (client, peer, _), payload = setup_upgrade(auth_store_factory)
    candidate = client.post(UPGRADES, json=payload).json()
    with store._conn() as conn:
        with pytest.raises(psycopg.Error, match="immutable_suite_upgrade_operation"), conn.transaction():
            conn.execute("UPDATE product.suite_upgrade_operations SET expected_revision=99 WHERE operation_id=%s", (payload["operation_id"],))
        with pytest.raises(psycopg.Error, match="suite_upgrade_scope_mismatch"), conn.transaction():
            conn.execute(
                """INSERT INTO product.suite_upgrade_operations(tenant_id,operation_id,suite_key,source_revision_id,target_revision_id,revision_id,expected_revision,request_fingerprint,created_by)
                         VALUES(%s,%s,%s,%s,%s,%s,1,%s,%s)""",
                (alice.tenant_id, str(uuid.uuid4()), "wrong", source.id, target.id, source.id, "a" * 64, alice.user_id),
            )
        for table in ("sessions", "provider_authorizations", "identities", "resource_grants", "memberships"):
            conn.execute(f"DELETE FROM product.{table} WHERE user_id=%s", (alice.user_id,))
        conn.execute("DELETE FROM product.users WHERE id=%s", (alice.user_id,))
    preserved = peer.get(f"{UPGRADES}/{payload['operation_id']}")
    assert preserved.status_code == 200, preserved.text
    assert preserved.json()["id"] == candidate["id"] and preserved.json()["created_by"] is None


@pytest.mark.parametrize("rules_changed", [False, True])
def test_upgrade_uses_existing_no_model_development_loop_and_never_inherits_old_oracle_proof(auth_store_factory, monkeypatch, rules_changed):
    from agent.run import ExecutionContext
    from worker.checker_optimizer import development_handler

    from . import test_checker_optimizations as development

    rules = {**development.DIRECT_RULES, "rules": development.DIRECT_RULES["rules"][:1], "checker_binding": {"R1": "checker:threshold"}}
    monkeypatch.setattr(development, "RULES", rules)
    monkeypatch.setattr(development, "PROGRAM", development.DIRECT_PROGRAM)
    f = development.fixture(auth_store_factory)
    publish_official(f.store, f.suite)
    releases = SuiteReleaseStore(f.store.settings, f.store.objects)
    source = releases.save_candidate(f.alice, f.suite.id, {"checker.py": development.DIRECT_PROGRAM.decode() + "# Enterprise-maintained file\n"})
    releases.publish(f.alice, "threshold", source["id"], 0, str(uuid.uuid4()), True)
    new_rules = {**rules, "rules": [{**rules["rules"][0], "rule_to_check": "A1 must be >=100"}]} if rules_changed else rules
    target = SuiteStore(f.store.settings, f.store.objects, role="migrator").create_revision(
        tenant_id=None,
        suite_key="threshold",
        files={"rules.json": json.dumps(new_rules).encode(), "checker.py": development.DIRECT_PROGRAM, "resources.py": b"OFFICIAL_RESOURCE = 2"},
        created_by=None,
    )
    publish_official(f.store, target)
    response = f.clients[0].post("/suites/threshold/upgrades", json={"source_revision_id": source["id"], "target_revision_id": target.id, "expected_revision": 1, "operation_id": str(uuid.uuid4())})
    assert response.status_code == 201, response.text
    candidate = response.json()
    run = f.clients[0].post(f"/suite-revisions/{candidate['id']}/optimizations", json=development.command(f, consume_model=False))
    assert run.status_code == 200, run.text
    runtime, guest = development.actual_core_runtime(f, monkeypatch)
    result = development_handler(f.worker, runtime)(development.claim(f), ExecutionContext())
    assert result["state"] == "completed", result
    assert guest.generation_manifests == []
    assert len(guest.fixed_manifests) >= 4
    proof = result["report"]["validation"]
    assert proof["total_samples"] == 4
    assert proof["verified"] is (not rules_changed)
    if rules_changed:
        assert "oracle_rules_changed" in proof["reasons"]
    saved = releases.detail(f.alice, result["candidate_revision_id"])
    assert "Enterprise-maintained" in saved["files"]["checker.py"]
    assert saved["files"]["resources.py"] == "OFFICIAL_RESOURCE = 2"
    assert saved["validation"]["verified"] is (not rules_changed)
    assert releases.active(f.alice, "threshold")["current_revision_id"] == source["id"]
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0


def test_enterprise_resource_removal_is_a_real_tombstone_against_new_official_base(auth_store_factory, monkeypatch):
    monkeypatch.setitem(FILES, "resources.py", b"OLD_OFFICIAL_RESOURCE = 1")
    store, _, source, target, alice, _, (client, _, _), payload = setup_upgrade(auth_store_factory)
    deleted = SuiteStore(store.settings, store.objects).create_revision(
        tenant_id=alice.tenant_id,
        suite_key=KEY,
        created_by=alice.user_id,
        base_revision_id=source.id,
        files={},
        deleted_paths=("resources.py",),
    )
    response = client.post(UPGRADES, json={**payload, "source_revision_id": deleted.id})
    assert response.status_code == 201, response.text
    candidate = response.json()
    assert candidate["base_revision_id"] == target.id
    assert "resources.py" not in candidate["files"]
    assert SuiteStore(store.settings, store.objects).get_revision(candidate["id"], alice.tenant_id).overrides["resources.py"] is None
    assert client.get(ACTIVE).json()["current_revision_id"] == source.id


def test_round_without_feedback_exposes_only_authorized_actual_suite_sources(auth_store_factory):
    from . import test_checker_optimizations as development

    f = development.fixture(auth_store_factory, count=1)
    round_id, original, sample = f.feedback[0]
    response = f.clients[0].delete(f"/audit-rounds/{round_id}/checker-feedback/{sample['id']}?expected_revision=1")
    assert response.status_code == 200
    view = f.clients[0].get(f"/audit-rounds/{round_id}/checker-feedback").json()
    assert view["feedback"] == [] and view["optimizations"] == []
    assert view["sources"] == [{"file_version_id": original["file_version_id"], "suite_revision_id": f.suite.id, "suite_key": f.suite.suite_key}]
    with f.store._conn() as conn:
        conn.execute("DELETE FROM product.resource_grants WHERE user_id=%s AND resource_id=(SELECT file_id FROM product.file_versions WHERE id=%s)", (f.alice.user_id, original["file_version_id"]))
    assert f.clients[0].get(f"/audit-rounds/{round_id}/checker-feedback").json()["sources"] == []
    assert f.clients[2].get(f"/audit-rounds/{round_id}/checker-feedback").status_code == 404
