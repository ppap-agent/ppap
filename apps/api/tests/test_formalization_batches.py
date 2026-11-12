"""Real PostgreSQL: saving a rule version reads every clause as one batch and notifies once when all have ended.

The scripted model proves the software contract. It is not model-quality acceptance.
"""

import json
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from documents.ai import AIError, AIResult
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.formalizations import FormalizationStore
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from worker import process_claimed

CLEAR = "文档要有编号"
OPEN = "文档要有签字"
CONDITION = "当零件为外购件，且有供应商编号则通过，否则不通过"
BROKEN = "文档要有日期"


def put_tenant_key(store, principal):
    from product.store.model_credentials import TenantModelStore

    models = TenantModelStore(store)
    models.put(principal.tenant_id, models.candidate(principal.tenant_id, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", text_model="test-model"), operator="test")


def clause(clause_id: str, text: str) -> dict:
    return {"id": clause_id, "category": "条款", "priority": "P0", "rule_to_check": text, "type_of_rule": "text"}


def scripted(task, *, prompt: str, **_kwargs):
    """One answer per clause; the last clause's answer cannot be read."""
    if BROKEN in prompt:
        raise AIError("invalid_output")
    if OPEN in prompt:
        return AIResult({"sections": [{"title": "", "items": ["文档要有签字"]}], "review": ["签字须是手签还是电子签？"], "suggested_clause": "文档要有手签"}, None, None)
    if CONDITION in prompt:
        return AIResult({"sections": [{"title": "", "items": ["零件为外购件时，须有供应商编号"]}], "review": [], "suggested_clause": ""}, None, None)
    return AIResult({"sections": [{"title": "", "items": ["文档要有编号"]}], "review": [], "suggested_clause": ""}, None, None)


def test_a_saved_version_is_read_as_one_batch_and_notifies_once(auth_store_factory):
    settings = Settings(feishu_app_id="formalization-batch", git_sha="b" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-batch", "alice")
    put_tenant_key(store, alice)
    rules = {"type": "自定义", "rules": [clause("c1", CLEAR), clause("c2", OPEN), clause("c3", CONDITION), clause("c4", BROKEN), clause("c5", " ")]}
    suite = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:batch",
        files={"rules.json": json.dumps(rules, ensure_ascii=False).encode(), "checker.py": b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"},
        created_by=alice.user_id,
    )
    rule_id = str(uuid4())
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'签字规则',%s,%s)",
            (rule_id, alice.tenant_id, Jsonb({**rules, "kind": "engine", "suite_key": "custom:batch", "suite_revision_id": suite.id}), alice.user_id),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app_for(settings, api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))

    prepared = FormalizationStore(api).prepare_all(alice, rule_id)
    # A clause with no text is never sent to a model.
    assert prepared["queued"] == ["c1", "c2", "c3", "c4"] and prepared["batch_id"]
    assert FormalizationStore(api).prepare_all(alice, rule_id) == {"batch_id": None, "queued": []}
    listed = client.get(f"/rules/{rule_id}/formalizations").json()["clauses"]
    assert [item["attention"] for item in listed] == ["running", "running", "running", "running", "waiting"]

    # "开始整理" on a clause already in the batch reads back the batch's own reading.
    digest = client.get(f"/rules/{rule_id}/formalizations/c1").json()["source_rule_digest"]
    again = client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": str(uuid4()), "expected_source_digest": digest})
    assert again.status_code == 202, again.text
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.rule_formalization_operations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0] == 4

    def notifications() -> list:
        with store._conn() as conn:
            return conn.execute("SELECT payload FROM engine.jobs WHERE tenant_id=%s AND kind='feishu_notification'", (alice.tenant_id,)).fetchall()

    for index in range(4):
        claimed = worker.claim_job(f"batch-{index}")
        assert claimed is not None and claimed.kind == "rule_formalization"
        process_claimed(worker, claimed, f"batch-{index}", rule_ai=scripted)
        # Nothing is sent while any reading of the batch is still in flight.
        assert notifications() == ([] if index < 3 else [({"stage": "formalization_ready", "operation_id": prepared["batch_id"]},)])

    listed = client.get(f"/rules/{rule_id}/formalizations").json()["clauses"]
    assert {item["clause_id"]: item["attention"] for item in listed} == {"c1": "ready", "c2": "review", "c3": "question", "c4": "failed", "c5": "waiting"}

    claimed = api.claim_task_notification("notifier")
    assert claimed is not None
    job, recipient = claimed
    assert recipient.user_id == alice.user_id
    delivery = api.start_task_notification(job, "notifier", recipient)
    assert delivery is not None and delivery["receive_id"] == alice.subject
    card = json.dumps(delivery["card"], ensure_ascii=False)
    assert "签字规则" in card and "共 5 条条款" in card
    for text in ("要选择 **1** 条", "要补充标准 **1** 条", "可以直接确认 **1** 条", "没整理出来 **1** 条", "还没整理 **1** 条"):
        assert text in card
    assert f"requirement%3D{rule_id}%26reqVersion%3D{rule_id}" in card
    # Nothing a person has not decided is described as passing.
    assert "都不会判为通过" in card


def test_publishing_a_rule_starts_the_batch_and_never_fails_because_of_it(auth_store_factory, monkeypatch):
    settings = Settings(feishu_app_id="formalization-batch-route", git_sha="a" * 40, usage_charge_enabled=False)
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-batch-route", "alice")
    api = build_product_store(settings, objects=store.objects, role="api")
    calls = []
    monkeypatch.setattr(type(api), "publish_rule", lambda self, principal, *args: {"id": "rule-1"})

    def prepare(self, principal, rule_version_id):
        calls.append((principal.user_id, rule_version_id))
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(FormalizationStore, "prepare_all", prepare)
    client = TestClient(create_app_for(settings, api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    response = client.post("/rules", json={"title": "规则", "body": {"type": "自定义", "rules": []}, "source_file_version_id": str(uuid4())})
    assert response.status_code == 201 and response.json() == {"id": "rule-1"}
    assert calls == [(alice.user_id, "rule-1")]


def create_app_for(settings, api):
    from api import create_app

    return create_app(settings, auth_store=api)


def test_audits_go_first_and_a_full_reading_pool_still_takes_them(auth_store_factory):
    settings = Settings(feishu_app_id="formalization-claims", git_sha="c" * 40, usage_charge_enabled=False)
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-claims", "alice")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    queued = {}
    with store._conn() as conn:
        # Oldest first: two batch readings, a person's own reading, then an audit.
        for minutes, name, kind, payload in (
            (4, "batch-1", "rule_formalization", {"batch": "b"}),
            (3, "batch-2", "rule_formalization", {"batch": "b"}),
            (2, "manual", "rule_formalization", {}),
            (1, "audit", "audit", {}),
        ):
            queued[name] = str(uuid4())
            conn.execute(
                "INSERT INTO engine.jobs(id,tenant_id,kind,payload,status,created_at) VALUES (%s,%s,%s,%s,'queued',now() - make_interval(mins => %s))",
                (queued[name], alice.tenant_id, kind, Jsonb(payload), minutes),
            )
    names = {value: key for key, value in queued.items()}

    def claim(room: str = "any"):
        claimed = worker.claim_job("claims", formalizations=room)
        return None if claimed is None else names[claimed.id]

    # The audit was queued last and still goes first.
    assert claim() == "audit"
    # A person's own reading goes before a batch queued earlier.
    assert claim() == "manual"
    # A worker whose pool holds its share of batch readings takes no more of them; a full pool takes none.
    assert claim("manual") is None
    assert claim("none") is None
    assert claim() == "batch-1"
    with pytest.raises(ValueError):
        worker.claim_job("claims", formalizations="all")
