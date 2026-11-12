"""New customer rule publication, admission and revisions use the real PG authority."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from api.rule_routes import register_rule_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product import build_product_store
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore
from product.store.suites import SuiteStore
from product.suites import is_unimplemented_suite


def rule_body(text="必须填写批号", document_type="供应商自定义追溯记录"):
    return {
        "kind": "engine",
        "rule_profile": "v3",
        "type": document_type,
        "rules": [{"id": "R1", "category": "追溯", "priority": "P0", "rule_to_check": text, "type_of_rule": "table"}],
        "customers": ["自定义客户"],
        "models": [],
        "parts": [],
    }


@pytest.fixture
def custom(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="custom-suite-tests", usage_charge_enabled=False, git_sha="a" * 40))
    alice = store.upsert_feishu_identity("custom-enterprise", "alice", "Alice")
    bob = store.upsert_feishu_identity("custom-enterprise", "bob", "Bob")
    foreign = store.upsert_feishu_identity("foreign-enterprise", "other", "Other")
    store = build_product_store(store.settings, objects=store.objects, role="api")
    version = store.create_staging(alice, "rules.json", "application/json", 2)
    source = store.finalize_version(store.put_blob(version, b"{}"))
    suites = SuiteStore(store.settings, store.objects)
    releases = SuiteReleaseStore(store.settings, store.objects)
    return store, alice, bob, foreign, source, suites, releases


def publish(custom, *, body=None, predecessor=None, request=None):
    store, alice, _, _, source, _, _ = custom
    return store.publish_rule(alice, "企业追溯规则", body or rule_body(), source.id, predecessor, request or str(uuid.uuid4()))


def accept(custom, rule):
    store, alice, _, _, source, suites, _ = custom
    draft = store.create_draft(alice, items=[{"file_version_id": source.id, "role": "target", "rule_version_id": rule["id"]}])
    accepted = store.accept_draft(alice, draft["id"], "accept:" + draft["id"])
    return accepted, suites.read_snapshot(accepted["id"], alice.tenant_id)


def counts(custom):
    with custom[0]._conn() as conn:
        rules = conn.execute("SELECT count(*) FROM product.rule_versions WHERE body->>'suite_key' LIKE 'custom:%'").fetchone()[0]
        return (rules, *(conn.execute(f"SELECT count(*) FROM product.{table}").fetchone()[0] for table in ("suite_revisions", "tenant_suite_heads", "suite_publications")))


def test_api_new_type_publishes_baseless_suite_that_cannot_be_audited_until_generated(custom):
    store, alice, bob, _, source, suites, releases = custom
    app = FastAPI()
    register_rule_routes(app, store, lambda request: alice, None)
    client = TestClient(app)
    request = {"request_id": str(uuid.uuid4()), "title": "企业追溯规则", "body": rule_body(), "source_file_version_id": source.id}
    response = client.post("/rules", json=request)
    assert response.status_code == 201, response.text
    rule = response.json()
    assert client.post("/rules", json=request).json() == rule
    resolved = suites.resolve_suite(rule["body"]["suite_revision_id"], alice.tenant_id)
    assert resolved.revision.base_revision_id is None
    assert resolved.revision.created_by == alice.user_id
    assert resolved.revision.tenant_id == alice.tenant_id
    assert is_unimplemented_suite(resolved)
    active = releases.active(bob, resolved.revision.suite_key)
    assert active["origin"] == "enterprise"
    assert active["verification_status"] == "unverified"
    assert active["force_unverified"] is False
    assert active["validation"]["total_samples"] == 0
    assert counts(custom) == (1, 1, 1, 1)
    # The starter is never run: the rule is offered for audit only after its program is generated.
    with pytest.raises(ValueError, match="checker_not_generated"):
        accept(custom, rule)
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_validations").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0


def generated(custom, rule):
    """Stands in for the rule page's generation: a bound program adopted unverified."""
    _, alice, _, _, _, _, releases = custom
    rules = json.loads(releases.detail(alice, rule["body"]["suite_revision_id"])["files"]["rules.json"])
    rules["checker_binding"] = {"R1": "checker:check"}
    program = releases.save_candidate(alice, rule["body"]["suite_revision_id"], {"rules.json": json.dumps(rules), "checker.py": "def check(context): return []\n"})
    releases.publish(alice, rule["body"]["suite_key"], program["id"], 1, str(uuid.uuid4()), True)
    return program["id"]


def test_successor_is_same_suite_and_old_admission_and_retries_never_drift(custom):
    _, alice, _, _, _, suites, releases = custom
    request = str(uuid.uuid4())
    first = publish(custom, request=request)
    program = generated(custom, first)
    accepted, original = accept(custom, first)
    second = publish(custom, body=rule_body("批号与原始记录一致"), predecessor=first["id"])
    assert first["body"]["suite_key"] == second["body"]["suite_key"]
    changed = suites.resolve_suite(second["body"]["suite_revision_id"], alice.tenant_id)
    assert changed.revision.base_revision_id == program
    assert changed.revision.revision_number == 3
    assert "checker.py" not in changed.revision.overrides
    assert publish(custom, request=request) == first
    active = releases.active(alice, changed.revision.suite_key)
    assert active["current_revision_id"] == program
    assert active["rule_update"]["clauses"][0]["kind"] == "changed"
    assert active["rule_update"]["target_revision_id"] == changed.revision.id
    assert suites.read_snapshot(accepted["id"], alice.tenant_id).to_dict() == original.to_dict()
    # New rule publication alone does not change adoption.
    _, unchanged = accept(custom, second)
    assert unchanged.targets[0].rules["rules"][0]["rule_to_check"] == "必须填写批号"
    with custom[0]._conn() as conn:
        execution = conn.execute("SELECT rule_execution FROM product.round_inputs WHERE round_id=%s", (unchanged.round_id,)).fetchone()[0]
        assert execution["rule_version_mismatch"] is True
    releases.publish(alice, changed.revision.suite_key, changed.revision.id, 2, str(uuid.uuid4()), True)
    _, current = accept(custom, first)
    assert current.targets[0].rules["rules"][0]["rule_to_check"] == "批号与原始记录一致"
    assert releases.active(alice, changed.revision.suite_key)["rule_update"] is None
    releases.publish(alice, changed.revision.suite_key, program, 3, str(uuid.uuid4()), True)
    _, rolled_back = accept(custom, second)
    assert rolled_back.targets[0].suite_revision_id == program
    assert suites.read_snapshot(accepted["id"], alice.tenant_id).to_dict() == original.to_dict()


def test_new_rule_and_suite_are_atomic_authorized_and_idempotency_checked(custom):
    store, alice, _, foreign, source, suites, releases = custom
    request = str(uuid.uuid4())
    first = publish(custom, request=request)
    before = counts(custom)
    with pytest.raises(ValueError, match="idempotency_conflict"):
        publish(custom, request=request, body=rule_body("另一个要求"))
    with pytest.raises(KeyError):
        store.publish_rule(foreign, "Foreign", rule_body(), source.id)
    with pytest.raises(KeyError):
        suites.resolve_suite(first["body"]["suite_revision_id"], foreign.tenant_id)
    with pytest.raises(KeyError):
        releases.active(foreign, first["body"]["suite_key"])
    with pytest.raises(ValueError, match="suite_document_type_changed"):
        publish(custom, predecessor=first["id"], body=rule_body(document_type="另一个类型"))
    assert counts(custom) == before
    # A genuine object-store write failure rolls back both the rule and adoption.
    original_put = store.objects.put_exclusive

    def failed_put(*args):
        raise RuntimeError("storage unavailable")

    store.objects.put_exclusive = failed_put
    try:
        with pytest.raises(RuntimeError, match="storage unavailable"):
            publish(custom, body=rule_body("新的规则字节"))
    finally:
        store.objects.put_exclusive = original_put
    assert counts(custom) == before
    assert releases.active(alice, first["body"]["suite_key"])["revision"] == 1


def test_concurrent_successors_cannot_overwrite_newer_published_rules(custom):
    first = publish(custom)

    def create(text):
        try:
            return publish(custom, body=rule_body(text), predecessor=first["id"])
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, ["要求 A", "要求 B"]))
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert "suite_head_conflict" in outcomes
    assert counts(custom) == (2, 2, 1, 1)


def test_empty_binding_alone_does_not_authorize_code_or_implicit_adoption(custom):
    _, alice, _, _, _, _, releases = custom
    first = publish(custom)
    candidate = releases.save_candidate(alice, first["body"]["suite_revision_id"], {"checker.py": "raise RuntimeError('must not run')\n"})
    with pytest.raises(ValueError, match="suite_confirmation_required"):
        releases.publish(alice, first["body"]["suite_key"], candidate["id"], 1, str(uuid.uuid4()))
    # Normal Suite publication can explicitly adopt a generated implementation.
    rules = json.loads(candidate["files"]["rules.json"])
    rules["checker_binding"] = {"R1": "checker:check"}
    executable = releases.save_candidate(alice, candidate["id"], {"rules.json": json.dumps(rules), "checker.py": "def check(context): return []\n"})
    with pytest.raises(ValueError, match="suite_confirmation_required"):
        releases.publish(alice, first["body"]["suite_key"], executable["id"], 1, str(uuid.uuid4()))
    releases.publish(alice, first["body"]["suite_key"], executable["id"], 1, str(uuid.uuid4()), True)
    updated = publish(custom, predecessor=first["id"], body=rule_body("新的追溯要求"))
    rule_candidate = releases.detail(alice, updated["body"]["suite_revision_id"])
    assert rule_candidate["base_revision_id"] == executable["id"]
    assert rule_candidate["files"]["checker.py"] == executable["files"]["checker.py"]
    assert json.loads(rule_candidate["files"]["rules.json"])["checker_binding"] == {"R1": "checker:check"}
    assert releases.active(alice, first["body"]["suite_key"])["current_revision_id"] == executable["id"]
    _, snapshot = accept(custom, first)
    assert snapshot.targets[0].binding.entrypoints == {"R1": "checker:check"}
    assert snapshot.targets[0].verification == "unverified"
