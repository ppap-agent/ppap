"""Real PostgreSQL formalization generation, confirmation and tenant boundary.

The scripted model proves the software contract. It is not model-quality acceptance.
"""

import json
import uuid

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from pydantic import SecretStr
from worker import process_claimed

NS = "https://example.test/ns#"
OWL = f"""Prefix(ex:=<{NS}>)
Prefix(xsd:=<http://www.w3.org/2001/XMLSchema#>)
Prefix(ppap:=<https://ppap.example/formal/v1#>)
Ontology(
  Declaration(Class(ex:Document))
  Declaration(DataProperty(ex:code))
  SubClassOf(Annotation(ppap:axiomId "present") ex:Document DataSomeValuesFrom(ex:code xsd:string))
)
"""


def test_formalization_problems_are_in_the_openapi_contract():
    from api import app

    spec = app.openapi()
    schema = spec["components"]["schemas"]["FormalizationProblem"]
    assert schema["properties"]["error"]["type"] == "string"
    paths = spec["paths"]
    confirm = paths["/rules/{rule_version_id}/formalizations/{clause_id}/confirmations"]["post"]["responses"]
    generate = paths["/rules/{rule_version_id}/formalizations/{clause_id}/generations"]["post"]["responses"]
    handoff = paths["/rules/{rule_version_id}/formalization-suite-candidates"]["post"]["responses"]
    read = paths["/rules/{rule_version_id}/formalizations/{clause_id}"]["get"]["responses"]
    for code in ("401", "403", "409", "422"):
        for responses in (confirm, generate, handoff):
            assert responses[code]["content"]["application/json"]["schema"]["$ref"].endswith("/FormalizationProblem")
    for code in ("401", "403", "404", "422"):
        assert read[code]["content"]["application/json"]["schema"]["$ref"].endswith("/FormalizationProblem")
    assert "503" in generate


def put_tenant_key(store, principal):
    from product.store.model_credentials import TenantModelStore

    models = TenantModelStore(store)
    models.put(principal.tenant_id, models.candidate(principal.tenant_id, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", text_model="test-model"), operator="test")


class ScriptedFormalizer:
    """Stands in for the direct model call; it never reaches a provider."""

    def __init__(self, raw: str, before=None):
        self.raw = raw
        self.calls = 0
        self.before = before

    def __call__(self, task, **_kwargs):
        from documents.ai import AIError, AIResult

        self.calls += 1
        if self.before:
            self.before()
        usage = getattr(self, "last_usage", None)
        numbers = usage if isinstance(usage, dict) else {}

        def number(key: str) -> int | None:
            value = numbers.get(key)
            return value if type(value) is int and value >= 0 else None

        try:
            output = json.loads(self.raw) if self.raw else {}
        except ValueError:
            # The direct layer reports an unparsable answer as a code, never as text.
            raise AIError("invalid_output") from None
        return AIResult(output, number("input_tokens"), number("output_tokens"))


def _proposal(rule_id: str) -> str:
    return json.dumps(
        {
            "format_version": "1",
            "formal_profile": "owl2-dl-v1",
            "owl_content": OWL,
            "clif_content": None,
            "vocabulary": {"prefixes": {"ex": NS}, "entities": [{"iri": NS + "Document", "kind": "class", "label": "文档"}, {"iri": NS + "code", "kind": "dataProperty", "label": "编号"}]},
            "source_rule_version_id": rule_id,
            "clause_id": "c1",
            "obligations": [{"id": "o1", "source_span": "0:2", "semantic_kind": "field_presence", "formal_ref": "present"}],
            "unresolved": [],
            "completeness": "complete",
            "assumptions": [],
            "cnl_text": "规则解释已确认，全部符合",
        },
        ensure_ascii=False,
    )


def test_revoked_membership_cannot_read_or_spend_a_model_call(auth_store_factory):
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-revoke", git_sha="c" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-revoke", "alice")
    put_tenant_key(store, alice)
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert detail.status_code == 200
    operation_id = str(uuid.uuid4())
    assert (
        client.post(
            f"/rules/{rule_id}/formalizations/c1/generations",
            json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": detail.json()["source_rule_digest"]},
        ).status_code
        == 202
    )
    with store._conn() as conn:
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (alice.user_id, alice.tenant_id))
    denied = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert denied.status_code in {401, 403}
    assert "至少有一个值" not in denied.text
    assert "owl" not in denied.text.lower()
    script = ScriptedFormalizer(_proposal(rule_id))
    claimed = worker.claim_job("revoke-owner")
    assert claimed is not None and claimed.payload.get("operation_id") == operation_id
    result = process_claimed(worker, claimed, "revoke-owner", rule_ai=script)
    assert script.calls == 0
    assert result["failure"] == "access_denied"
    receipt = client.get(f"/rule-formalization-generations/{operation_id}")
    assert receipt.status_code in {401, 403}
    with store._conn() as conn:
        state = conn.execute("SELECT state FROM product.rule_formalization_operations WHERE operation_id=%s", (operation_id,)).fetchone()[0]
        assert state == "failed"
        assert conn.execute("SELECT count(*) FROM product.rule_formalizations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0] == 0


def test_known_provider_usage_is_saved_without_credentials(auth_store_factory):
    """A scripted token receipt is not model-quality acceptance."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-usage", git_sha="e" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-usage", "alice")
    put_tenant_key(store, alice)
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    digest = client.get(f"/rules/{rule_id}/formalizations/c1").json()["source_rule_digest"]
    operation_id = str(uuid.uuid4())
    assert client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": digest}).status_code == 202
    script = ScriptedFormalizer(_proposal(rule_id))
    script.last_usage = {"input_tokens": 11, "output_tokens": 7, "thinking_tokens": 2, "api_key": "secret-value", "source": "provider"}
    claimed = worker.claim_job("usage-owner")
    process_claimed(worker, claimed, "usage-owner", rule_ai=script)
    receipt = client.get(f"/rule-formalization-generations/{operation_id}")
    assert receipt.status_code == 200, receipt.text
    usage = receipt.json()["usage"]
    assert usage["uncertain"] is False
    assert usage["source"] == "provider"
    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 7
    assert "secret-value" not in receipt.text
    assert "test-only-no-provider-called" not in receipt.text


def test_refusing_model_consumption_does_not_queue_a_provider_job(auth_store_factory):
    """A generation request is not consent. No job and no candidate are created."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="rule-formalization-consent", git_sha="c" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-consent", "alice")
    put_tenant_key(store, alice)
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    digest = detail.json()["source_rule_digest"]
    for payload in (
        {"operation_id": str(uuid.uuid4()), "expected_source_digest": digest},
        {"operation_id": str(uuid.uuid4()), "expected_source_digest": digest, "consume_model": False},
    ):
        refused = client.post(f"/rules/{rule_id}/formalizations/c1/generations", json=payload)
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"] == "model_consumption_refused"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='rule_formalization'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.rule_formalization_operations").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.rule_formalizations").fetchone()[0] == 0
    assert worker.claim_job("consent-owner") is None
    allowed = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"operation_id": str(uuid.uuid4()), "expected_source_digest": digest, "consume_model": True},
    )
    assert allowed.status_code == 202, allowed.text


def test_unauthenticated_formalization_requests_do_not_read_or_queue(auth_store_factory):
    from api import create_app

    settings = Settings(feishu_app_id="formalization-auth", git_sha="d" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    rule_id = str(uuid.uuid4())
    operation_id = str(uuid.uuid4())
    digest = "a" * 64
    responses = [
        client.get(f"/rules/{rule_id}/formalizations"),
        client.get(f"/rules/{rule_id}/formalizations/c1"),
        client.get(f"/rule-formalization-generations/{operation_id}"),
        client.post(
            f"/rules/{rule_id}/formalizations/c1/generations",
            json={"operation_id": operation_id, "expected_source_digest": digest, "consume_model": True},
        ),
        client.post(
            f"/rules/{rule_id}/formalizations/c1/confirmations",
            json={
                "operation_id": str(uuid.uuid4()),
                "candidate_id": str(uuid.uuid4()),
                "expected_candidate_revision": 1,
                "expected_source_digest": digest,
                "expected_display_digest": digest,
            },
        ),
        client.post(
            f"/rules/{rule_id}/formalization-suite-candidates",
            json={"operation_id": str(uuid.uuid4()), "suite_revision_id": str(uuid.uuid4())},
        ),
    ]
    assert [item.status_code for item in responses] == [401, 401, 401, 401, 401, 401]
    assert {item.json()["error"] for item in responses} == {"unauthenticated"}
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.rule_formalizations").fetchone()[0] == 0


def test_removing_the_model_key_before_dispatch_does_not_call_the_provider(auth_store_factory, monkeypatch):
    """A queued explanation stops when the key is gone. No provider call is model-quality acceptance."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-revoke", git_sha="9" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-revoke", "alice")
    put_tenant_key(store, alice)
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    operation_id = str(uuid.uuid4())
    created = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"operation_id": operation_id, "expected_source_digest": detail.json()["source_rule_digest"], "consume_model": True},
    )
    assert created.status_code == 202, created.text
    claimed = worker.claim_job("revoke-key-owner")
    assert claimed is not None
    from product.store.model_credentials import TenantModelStore

    TenantModelStore(store).delete(alice.tenant_id)
    calls = []

    async def provider(*args, **kwargs):
        calls.append("provider")
        raise AssertionError("a removed tenant key still called the provider")

    monkeypatch.setattr("documents.ai._run", provider)
    result = process_claimed(worker, claimed, "revoke-key-owner")
    assert calls == []
    assert result["failure"] == "model_unavailable"
    receipt = client.get(f"/rule-formalization-generations/{operation_id}")
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["state"] == "failed"
    assert receipt.json()["failure"] == "model_unavailable"
    assert receipt.json()["candidate_id"] is None
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.rule_formalizations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0] == 0


def test_an_unchecked_source_cannot_be_handed_to_a_suite(auth_store_factory):
    """An old confirmation cannot enter a suite while its saved source review is unchecked."""
    from api import create_app
    from product.store.suites import SuiteStore
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-handoff-source", git_sha="7" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-handoff-source", "alice")
    text = "文档要有编号"
    rules = {"type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": text, "type_of_rule": "text"}]}
    suite = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:source-handoff",
        files={"rules.json": json.dumps(rules).encode(), "checker.py": b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"},
        created_by=alice.user_id,
    )
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))

    def prepare(checked: bool) -> str:
        rule_id = str(uuid.uuid4())
        body = {**rules, "kind": "engine", "suite_key": "custom:source-handoff", "suite_revision_id": suite.id}
        context = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": checked}]}}, ensure_ascii=False)}
        with store._conn() as conn:
            conn.execute(
                "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'列',%s,%s,%s)",
                (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, Jsonb(context)),
            )
        detail = client.get(f"/rules/{rule_id}/formalizations/c1")
        assert detail.status_code == 200, detail.text
        candidate_id, confirmation_id = str(uuid.uuid4()), str(uuid.uuid4())
        digest = detail.json()["source_rule_digest"]
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO product.rule_formalizations(
                     id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                     owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                     formal_digest,display_digest,created_by)
                   VALUES (%s,%s,%s,'c1',1,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete','每一条都有编号','cnl-zh-1',%s,%s,%s)""",
                (candidate_id, alice.tenant_id, rule_id, digest, Jsonb({}), "b" * 64, "c" * 64, alice.user_id),
            )
            conn.execute(
                """INSERT INTO product.rule_formalization_confirmations(
                     id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                     source_rule_digest,display_digest,confirmed_by,current)
                   VALUES (%s,%s,%s,%s,'c1',%s,1,%s,%s,%s,true)""",
                (confirmation_id, str(uuid.uuid4()), alice.tenant_id, rule_id, candidate_id, digest, "c" * 64, alice.user_id),
            )
        return rule_id

    unchecked = prepare(False)
    with store._conn() as conn:
        before = conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:source-handoff'").fetchone()[0]
    refused = client.post(
        f"/rules/{unchecked}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": suite.id},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "source_unchecked"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:source-handoff'").fetchone()[0] == before

    checked = prepare(True)
    handed = client.post(
        f"/rules/{checked}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": suite.id},
    )
    assert handed.status_code == 201, handed.text
    assert handed.json()["validation"]["status"] == "unverified"


def test_one_unchecked_confirmation_blocks_the_whole_suite_handoff(auth_store_factory):
    """A checked clause is not written while another confirmed clause still has an unchecked source."""
    from api import create_app
    from product.store.suites import SuiteStore
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-mixed-source", git_sha="6" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-mixed-source", "alice")
    rules = {
        "type": "自定义",
        "rules": [
            {"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"},
            {"id": "c2", "category": "列", "priority": "P0", "rule_to_check": "文档要有名称", "type_of_rule": "text"},
        ],
    }
    suite = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:mixed-source",
        files={"rules.json": json.dumps(rules).encode(), "checker.py": b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"},
        created_by=alice.user_id,
    )
    rule_id = str(uuid.uuid4())
    body = {**rules, "kind": "engine", "suite_key": "custom:mixed-source", "suite_revision_id": suite.id}
    context = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": True}, {"id": "c2", "checked": False}]}}, ensure_ascii=False)}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'列',%s,%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, Jsonb(context)),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    for clause_id, reading in (("c1", "每一条都有编号"), ("c2", "每一条都有名称")):
        detail = client.get(f"/rules/{rule_id}/formalizations/{clause_id}")
        assert detail.status_code == 200, detail.text
        digest = detail.json()["source_rule_digest"]
        candidate_id = str(uuid.uuid4())
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO product.rule_formalizations(
                     id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                     owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                     formal_digest,display_digest,created_by)
                   VALUES (%s,%s,%s,%s,1,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete',%s,'cnl-zh-1',%s,%s,%s)""",
                (candidate_id, alice.tenant_id, rule_id, clause_id, digest, Jsonb({}), reading, "b" * 64, "c" * 64, alice.user_id),
            )
            conn.execute(
                """INSERT INTO product.rule_formalization_confirmations(
                     id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                     source_rule_digest,display_digest,confirmed_by,current)
                   VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,true)""",
                (str(uuid.uuid4()), str(uuid.uuid4()), alice.tenant_id, rule_id, clause_id, candidate_id, digest, "c" * 64, alice.user_id),
            )
    with store._conn() as conn:
        before = conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:mixed-source'").fetchone()[0]
    refused = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": suite.id},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "source_unchecked"
    assert "每一条都有编号" not in refused.text
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:mixed-source'").fetchone()[0] == before


def test_a_source_uncheck_during_confirmation_is_not_saved(auth_store_factory):
    """The confirmation lock reads the rule again. A concurrent uncheck is not confirmed."""
    import threading
    import time

    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-race", git_sha="5" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-race", "alice")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    checked = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": True}]}}, ensure_ascii=False)}
    unchecked = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": False}]}}, ensure_ascii=False)}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'列',%s,%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, Jsonb(checked)),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    digest = detail.json()["source_rule_digest"]
    candidate_id = str(uuid.uuid4())
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO product.rule_formalizations(
                 id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by)
               VALUES (%s,%s,%s,'c1',1,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete','每一条都有编号','cnl-zh-1',%s,%s,%s)""",
            (candidate_id, alice.tenant_id, rule_id, digest, Jsonb({}), "b" * 64, "c" * 64, alice.user_id),
        )
    held = threading.Event()
    release = threading.Event()

    def editor():
        with store._conn() as conn, conn.transaction():
            conn.execute("SELECT id FROM product.rule_versions WHERE id=%s FOR UPDATE", (rule_id,))
            held.set()
            assert release.wait(5)
            conn.execute("UPDATE product.rule_versions SET case_context=%s WHERE id=%s", (Jsonb(unchecked), rule_id))

    threading.Thread(target=editor).start()
    assert held.wait(5)
    result = {}

    def confirmer():
        result["response"] = client.post(
            f"/rules/{rule_id}/formalizations/c1/confirmations",
            json={
                "operation_id": str(uuid.uuid4()),
                "candidate_id": candidate_id,
                "expected_candidate_revision": 1,
                "expected_source_digest": digest,
                "expected_display_digest": "c" * 64,
            },
        )

    worker_thread = threading.Thread(target=confirmer)
    worker_thread.start()
    time.sleep(0.3)
    release.set()
    worker_thread.join(5)
    assert "response" in result
    assert result["response"].status_code == 409, result["response"].text
    assert result["response"].json()["error"] == "source_changed"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.rule_formalization_confirmations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0] == 0


def test_a_source_uncheck_during_handoff_is_not_saved(auth_store_factory):
    """The handoff lock reads the rule again. A concurrent uncheck writes no Suite candidate."""
    import threading
    import time

    from api import create_app
    from product.store.suites import SuiteStore
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-handoff-race", git_sha="7" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-handoff-race", "alice")
    rules = {"type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    suite = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:handoff-race",
        files={"rules.json": json.dumps(rules).encode(), "checker.py": b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"},
        created_by=alice.user_id,
    )
    rule_id = str(uuid.uuid4())
    body = {**rules, "kind": "engine", "suite_key": "custom:handoff-race", "suite_revision_id": suite.id}
    checked = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": True}]}}, ensure_ascii=False)}
    unchecked = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": False}]}}, ensure_ascii=False)}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'列',%s,%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, Jsonb(checked)),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert detail.status_code == 200, detail.text
    digest = detail.json()["source_rule_digest"]
    candidate_id = str(uuid.uuid4())
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO product.rule_formalizations(
                 id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by)
               VALUES (%s,%s,%s,'c1',1,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete','每一条都有编号','cnl-zh-1',%s,%s,%s)""",
            (candidate_id, alice.tenant_id, rule_id, digest, Jsonb({}), "b" * 64, "c" * 64, alice.user_id),
        )
        conn.execute(
            """INSERT INTO product.rule_formalization_confirmations(
                 id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                 source_rule_digest,display_digest,confirmed_by,current)
               VALUES (%s,%s,%s,%s,'c1',%s,1,%s,%s,%s,true)""",
            (str(uuid.uuid4()), str(uuid.uuid4()), alice.tenant_id, rule_id, candidate_id, digest, "c" * 64, alice.user_id),
        )
        before = conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:handoff-race'").fetchone()[0]
    held = threading.Event()
    release = threading.Event()

    def editor():
        with store._conn() as conn, conn.transaction():
            conn.execute("SELECT id FROM product.rule_versions WHERE id=%s FOR UPDATE", (rule_id,))
            held.set()
            assert release.wait(5)
            conn.execute("UPDATE product.rule_versions SET case_context=%s WHERE id=%s", (Jsonb(unchecked), rule_id))

    threading.Thread(target=editor).start()
    assert held.wait(5)
    result = {}

    def handoff():
        result["response"] = client.post(
            f"/rules/{rule_id}/formalization-suite-candidates",
            json={"operation_id": str(uuid.uuid4()), "suite_revision_id": suite.id},
        )

    worker_thread = threading.Thread(target=handoff)
    worker_thread.start()
    time.sleep(0.3)
    release.set()
    worker_thread.join(5)
    assert "response" in result
    assert result["response"].status_code == 409, result["response"].text
    assert result["response"].json()["error"] == "source_changed"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:handoff-race'").fetchone()[0] == before


def test_two_candidates_cannot_both_become_current(auth_store_factory):
    """Two concurrent confirmations of one clause leave one current pointer."""
    import threading

    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-two-candidates", git_sha="8" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-two-candidates", "alice")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    digest = client.get(f"/rules/{rule_id}/formalizations/c1").json()["source_rule_digest"]
    older_id, newer_id = str(uuid.uuid4()), str(uuid.uuid4())
    with store._conn() as conn:
        for candidate_id, revision, display in ((older_id, 1, "a" * 64), (newer_id, 2, "b" * 64)):
            conn.execute(
                """INSERT INTO product.rule_formalizations(
                     id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                     owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                     formal_digest,display_digest,created_by)
                   VALUES (%s,%s,%s,'c1',%s,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete',%s,'cnl-zh-1',%s,%s,%s)""",
                (candidate_id, alice.tenant_id, rule_id, revision, digest, Jsonb({}), f"理解{revision}", "c" * 64, display, alice.user_id),
            )
    started = threading.Barrier(2)
    results = {}

    def confirm(name, candidate_id, revision, display):
        started.wait(5)
        results[name] = client.post(
            f"/rules/{rule_id}/formalizations/c1/confirmations",
            json={
                "operation_id": str(uuid.uuid4()),
                "candidate_id": candidate_id,
                "expected_candidate_revision": revision,
                "expected_source_digest": digest,
                "expected_display_digest": display,
            },
        )

    threads = [
        threading.Thread(target=confirm, args=("older", older_id, 1, "a" * 64)),
        threading.Thread(target=confirm, args=("newer", newer_id, 2, "b" * 64)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert results["newer"].status_code == 201, results["newer"].text
    assert results["older"].status_code == 409, results["older"].text
    assert results["older"].json()["error"] in {"candidate_stale", "conflicting_confirmation"}
    fresh = TestClient(create_app(settings, auth_store=api))
    fresh.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = fresh.get(f"/rules/{rule_id}/formalizations/c1")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert [item["id"] for item in payload["candidates"]] == [older_id, newer_id]
    current = [item for item in payload["confirmations"] if item["current"]]
    assert len(payload["confirmations"]) == 1
    assert current[0]["id"] == results["newer"].json()["id"]
    assert current[0]["candidate_id"] == newer_id
    assert "Ontology()" not in results["older"].text


def test_a_rule_and_its_original_are_shared_with_the_tenant_but_not_beyond(auth_store_factory):
    """A published rule is a tenant asset, and so is the exact original it cites. Other tenants see neither."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-file-acl", git_sha="9" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-file-acl", "alice")
    bob = store.upsert_feishu_identity("formalization-file-acl", "bob")
    carol = store.upsert_feishu_identity("formalization-file-acl-other", "carol")
    assert bob.tenant_id == alice.tenant_id and carol.tenant_id != alice.tenant_id
    rule_id, file_id, version_id = (str(uuid.uuid4()) for _ in range(3))
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES (%s,%s,'file')", (file_id, alice.tenant_id))
        conn.execute("INSERT INTO product.files(id,tenant_id,created_by,original_filename) VALUES (%s,%s,%s,'rule.xlsx')", (file_id, alice.tenant_id, alice.user_id))
        conn.execute(
            """INSERT INTO product.file_versions(id,file_id,tenant_id,original_filename,content_type,byte_size,sha256,object_key,status)
               VALUES (%s,%s,%s,'rule.xlsx','application/octet-stream',3,%s,'rule-object','ready')""",
            (version_id, file_id, alice.tenant_id, "d" * 64),
        )
        conn.execute("INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES (%s,%s,'read')", (file_id, alice.user_id))
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,source_file_version_id) VALUES (%s,%s,'列',%s,%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, version_id),
        )
        conn.execute(
            """INSERT INTO product.rule_formalizations(
                 id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by)
               VALUES (%s,%s,%s,'c1',1,%s,'owl2-dl-v1','Ontology(SecretAxiom)',%s,'[]','[]','[]','complete','秘密回译','cnl-zh-1',%s,%s,%s)""",
            (str(uuid.uuid4()), alice.tenant_id, rule_id, "e" * 64, Jsonb({}), "f" * 64, "1" * 64, alice.user_id),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    owner = TestClient(create_app(settings, auth_store=api))
    owner.cookies.set(SESSION_COOKIE, store.create_session(alice))
    visible = owner.get(f"/rules/{rule_id}/formalizations/c1")
    assert visible.status_code == 200, visible.text
    assert "秘密回译" in visible.text
    colleague = TestClient(create_app(settings, auth_store=api))
    colleague.cookies.set(SESSION_COOKIE, store.create_session(bob))
    shared = colleague.get(f"/rules/{rule_id}/formalizations/c1")
    assert shared.status_code == 200, shared.text
    assert "秘密回译" in shared.text
    assert api.read_file_version(bob, file_id, version_id).id == version_id
    stranger = TestClient(create_app(settings, auth_store=api))
    stranger.cookies.set(SESSION_COOKIE, store.create_session(carol))
    denied = stranger.get(f"/rules/{rule_id}/formalizations/c1")
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"] == "access_denied"
    assert "Ontology" not in denied.text
    assert "秘密回译" not in denied.text
    assert "SecretAxiom" not in denied.text
    try:
        api.read_file_version(carol, file_id, version_id)
    except KeyError:
        pass
    else:
        raise AssertionError("another tenant read the rule original")
    # An original that is really gone still leaves the rule and its readings readable; only new readings need it.
    lost_rule, lost_version = str(uuid.uuid4()), str(uuid.uuid4())
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO product.file_versions(id,file_id,tenant_id,original_filename,content_type,byte_size,sha256,object_key,status)
               VALUES (%s,%s,%s,'rule.xlsx','application/octet-stream',3,%s,'rule-object-lost','failed')""",
            (lost_version, file_id, alice.tenant_id, "c" * 64),
        )
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,source_file_version_id) VALUES (%s,%s,'列',%s,%s,%s)",
            (lost_rule, alice.tenant_id, Jsonb(body), alice.user_id, lost_version),
        )
    readable = colleague.get(f"/rules/{lost_rule}/formalizations/c1")
    assert readable.status_code == 200, readable.text
    refused = colleague.post(
        f"/rules/{lost_rule}/formalizations/c1/generations",
        json={"consume_model": True, "operation_id": str(uuid.uuid4()), "expected_source_digest": readable.json()["source_rule_digest"]},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "source_unreadable"


def test_audits_are_claimed_before_background_preparation_and_preparation_is_capped(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="background-claims", git_sha="b" * 40, usage_charge_enabled=False))
    tenant = store.upsert_feishu_identity("background-claims", "alice").tenant_id
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    with store._conn() as conn:
        for _ in range(5):
            conn.execute("INSERT INTO engine.jobs(id,tenant_id,kind,payload,status,created_at) VALUES (%s,%s,'rule_formalization','{}','queued',now() - interval '1 minute')", (uuid.uuid4(), tenant))
        audit = uuid.uuid4()
        conn.execute("INSERT INTO engine.jobs(id,tenant_id,kind,payload,status) VALUES (%s,%s,'report_pdf','{}','queued')", (audit, tenant))
    first = worker.claim_job("w1")
    assert str(first.id) == str(audit)
    for index in range(4):
        assert worker.claim_job(f"w{index + 2}").kind == "rule_formalization"
    # A fifth check-point job for the same tenant waits while four are running.
    assert worker.claim_job("w6") is None
