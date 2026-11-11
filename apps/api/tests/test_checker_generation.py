"""Real PostgreSQL first-program generation for an uploaded rule.

The controlled author is not live model-quality acceptance. No provider is called.
"""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent.checker_development import DevelopmentOutcome
from agent.checker_spec import CheckerArtifact
from agent.protocol import DevelopmentEnvelope
from agent.run import ExecutionContext
from api import create_app
from audit.checker_validation import SuiteValidationReport
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from product import build_product_store
from product.files import ObjectExists, ObjectMissing
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.model_credentials import TenantModelStore
from product.store.suite_releases import SuiteReleaseStore
from product.store.suites import SuiteStore, programless_suite_keys
from product.suites import UNIMPLEMENTED_CHECKER_SOURCE
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from worker.checker_optimizer import development_handler, materialize_request

PROGRAM = b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"
CLAUSES = [
    {"id": "c1", "category": "编号", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"},
    {"id": "c2", "category": "签字", "priority": "P0", "rule_to_check": "文档要有签字", "type_of_rule": "text"},
]


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


@pytest.fixture(autouse=True)
def no_platform_model(monkeypatch):
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "OPENAI_API_KEY", "PPAP_DEEP_LLM_API_KEY", "PPAP_DEEP_LLM_BASE_URL", "PPAP_DEEP_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("documents.model_access.ISOLATED_DEV_LLM_BASE_URL", "")


def prepare(auth_store_factory, *, confirm=("c1",)):
    objects = Objects()
    settings = Settings(feishu_app_id="checker-generation", git_sha="b" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings, objects=objects)
    alice = store.upsert_feishu_identity("checker-generation", "alice")
    models = TenantModelStore(store)
    models.put(alice.tenant_id, models.candidate(alice.tenant_id, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", text_model="test-model"), operator="test")
    rule_id = str(uuid4())
    key = "custom:" + rule_id
    rules = {"type": "自定义", "title": "编号规则", "suite_key": key, "rules": CLAUSES, "checker_binding": {}}
    starter = SuiteStore(settings, objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id, suite_key=key, files={"rules.json": json.dumps(rules).encode(), "appendix.json": b"[]", "checker.py": UNIMPLEMENTED_CHECKER_SOURCE}, created_by=alice.user_id
    )
    SuiteReleaseStore(settings, objects).publish(alice, key, starter.id, 0, str(uuid4()))
    api = build_product_store(settings, objects=objects, role="api")
    worker = build_product_store(settings, objects=objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    body = {"type": "自定义", "rules": CLAUSES, "kind": "engine", "suite_key": key, "suite_revision_id": starter.id}
    context = {"registration": json.dumps({"basis": {"clauses": [{"id": clause["id"], "checked": True} for clause in CLAUSES]}}, ensure_ascii=False)}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'编号规则',%s,%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, Jsonb(context)),
        )
    for clause_id in confirm:
        digest = client.get(f"/rules/{rule_id}/formalizations/{clause_id}").json()["source_rule_digest"]
        candidate_id = str(uuid4())
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO product.rule_formalizations(
                     id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                     owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                     formal_digest,display_digest,created_by)
                   VALUES (%s,%s,%s,%s,1,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete','每一条都有编号','cnl-zh-1',%s,%s,%s)""",
                (candidate_id, alice.tenant_id, rule_id, clause_id, digest, Jsonb({}), "b" * 64, "c" * 64, alice.user_id),
            )
            conn.execute(
                """INSERT INTO product.rule_formalization_confirmations(
                     id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                     source_rule_digest,display_digest,confirmed_by,current)
                   VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,true)""",
                (str(uuid4()), str(uuid4()), alice.tenant_id, rule_id, clause_id, candidate_id, digest, "c" * 64, alice.user_id),
            )
    file_id, version_id, raw = str(uuid4()), str(uuid4()), b"example document"
    objects.data[version_id] = raw
    with store._conn() as conn:
        conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES (%s,%s,'file')", (file_id, alice.tenant_id))
        conn.execute("INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES (%s,%s,'review')", (file_id, alice.user_id))
        conn.execute("INSERT INTO product.files(id,tenant_id,created_by,original_filename) VALUES (%s,%s,%s,'example.txt')", (file_id, alice.tenant_id, alice.user_id))
        conn.execute(
            """INSERT INTO product.file_versions(id,file_id,tenant_id,original_filename,content_type,byte_size,sha256,object_key,status)
               VALUES (%s,%s,%s,'example.txt','text/plain',%s,%s,%s,'ready')""",
            (version_id, file_id, alice.tenant_id, len(raw), hashlib.sha256(raw).hexdigest(), version_id),
        )
    return SimpleNamespace(store=store, worker=worker, alice=alice, client=client, rule_id=rule_id, key=key, starter=starter, example=version_id)


class ControlledRuntime:
    def __init__(self):
        self.calls = 0
        self.request = None

    def develop_and_run(self, request, *, spec, context):
        self.calls += 1
        self.request = request
        artifact = CheckerArtifact(input_digest=request.target.digest, executor_revision=request.executor_revision, entrypoint="checker:create_audit", files={"checker.py": PROGRAM})
        proof = SuiteValidationReport(
            suite_revision_id=request.suite_revision_id,
            suite_digest=request.suite_digest,
            library_revision=request.library_revision,
            program_digest=artifact.program_digest,
            reasons=("no_independent_oracle",),
        )
        return DevelopmentOutcome(artifact, [], ["controlled fixture"], {"model_requests": 1}, validation=proof, usage_source="controlled")


def claim(f):
    job = f.worker.claim_job("generation-test")
    assert job.kind == "checker_optimization"
    return DevelopmentEnvelope(op="develop", operation_id=job.payload["operation_id"], job_id=job.id, tenant_id=job.tenant_id, attempt=job.attempt, lease_owner="generation-test")


def test_generation_writes_confirmed_clauses_against_the_example_and_adopts_unverified(auth_store_factory):
    f = prepare(auth_store_factory)
    with f.store._conn() as conn:
        assert programless_suite_keys(conn, f.alice.tenant_id, [f.key]) == {f.key}
    assert f.client.get(f"/rules/{f.rule_id}/checker-generation").json() == {
        "rule_version_id": f.rule_id,
        "suite_key": f.key,
        "program": "missing",
        "current_revision_id": f.starter.id,
        "operation": None,
    }

    body = {"operation_id": str(uuid4()), "file_version_id": f.example}
    started = f.client.post(f"/rules/{f.rule_id}/checker-generations", json=body)
    assert started.status_code == 200, started.text
    assert started.json()["state"] == "queued"
    # Replaying the same request returns the same operation; the same id cannot name another file.
    assert f.client.post(f"/rules/{f.rule_id}/checker-generations", json=body).json() == started.json()
    conflict = f.client.post(f"/rules/{f.rule_id}/checker-generations", json={**body, "file_version_id": str(uuid4())})
    assert conflict.status_code == 409 and conflict.json()["error"] == "checker_operation_conflict"
    bound = SuiteStore(f.store.settings, f.store.objects).resolve_suite(started.json()["suite_revision_id"], f.alice.tenant_id)
    # Only the confirmed clause enters the program; the other stays for people to judge.
    assert json.loads(bound.files["rules.json"])["checker_binding"] == {"c1": "checker:create_audit"}

    request = claim(f)
    from product.store.checker_development import CheckerDevelopmentStore

    loaded, _ = materialize_request(CheckerDevelopmentStore(f.worker), CheckerDevelopmentStore(f.worker).operation_for_dispatch(request))
    assert loaded.oracle == () and loaded.target.file_bytes == b"example document"
    runtime = ControlledRuntime()
    result = development_handler(f.worker, runtime)(request, ExecutionContext())
    assert result["state"] == "completed", result
    assert runtime.calls == 1
    # The first read after completion adopts the program unverified; later reads find it adopted.
    view = f.client.get(f"/rules/{f.rule_id}/checker-generation").json()
    assert f.client.get(f"/rules/{f.rule_id}/checker-generation").json() == view
    assert view["program"] == "unverified" and view["operation"]["operation_id"] == body["operation_id"]
    with f.store._conn() as conn:
        assert programless_suite_keys(conn, f.alice.tenant_id, [f.key]) == set()
        head = conn.execute("SELECT current_revision_id, force_unverified FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key=%s", (f.alice.tenant_id, f.key)).fetchone()
        assert str(head[0]) == result["candidate_revision_id"] and head[1] is True
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0
    # A later generation builds on the program in use and waits to be adopted; it never replaces it by itself.
    again = f.client.post(f"/rules/{f.rule_id}/checker-generations", json={"operation_id": str(uuid4()), "file_version_id": f.example})
    assert again.status_code == 200, again.text
    assert again.json()["suite_revision_id"] != result["candidate_revision_id"]
    rerun = ControlledRuntime()
    second = development_handler(f.worker, rerun)(claim(f), ExecutionContext())
    assert second["state"] == "completed" and rerun.request.prior.files["checker.py"] == PROGRAM
    later = f.client.get(f"/rules/{f.rule_id}/checker-generation").json()
    assert later["current_revision_id"] == result["candidate_revision_id"]
    assert later["operation"]["candidate_revision_id"] == second["candidate_revision_id"] != result["candidate_revision_id"]


def test_generation_needs_a_confirmed_clause_and_a_readable_example(auth_store_factory):
    f = prepare(auth_store_factory, confirm=())
    refused = f.client.post(f"/rules/{f.rule_id}/checker-generations", json={"operation_id": str(uuid4()), "file_version_id": f.example})
    assert refused.status_code == 409 and refused.json()["error"] == "not_confirmable"
    f = prepare(auth_store_factory)
    missing = f.client.post(f"/rules/{f.rule_id}/checker-generations", json={"operation_id": str(uuid4()), "file_version_id": str(uuid4())})
    assert missing.status_code == 404
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='checker_optimization'").fetchone()[0] == 0


def test_audits_are_claimed_before_background_preparation_and_preparation_is_capped(auth_store_factory):
    f = prepare(auth_store_factory)
    tenant = f.alice.tenant_id
    with f.store._conn() as conn:
        for _ in range(3):
            conn.execute("INSERT INTO engine.jobs(id,tenant_id,kind,payload,status,created_at) VALUES (%s,%s,'rule_formalization','{}','queued',now() - interval '1 minute')", (uuid4(), tenant))
        audit = uuid4()
        conn.execute("INSERT INTO engine.jobs(id,tenant_id,kind,payload,status) VALUES (%s,%s,'report_pdf','{}','queued')", (audit, tenant))
    first = f.worker.claim_job("w1")
    assert str(first.id) == str(audit)
    assert f.worker.claim_job("w2").kind == "rule_formalization"
    assert f.worker.claim_job("w3").kind == "rule_formalization"
    # A third preparation for the same tenant waits while two are running.
    assert f.worker.claim_job("w4") is None
