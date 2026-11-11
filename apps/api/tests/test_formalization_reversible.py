"""Real PostgreSQL: confirmations can be withdrawn and unchanged clauses carry their confirmation to a new version."""

import json
from uuid import uuid4

from api import create_app
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from product import build_product_store
from product.formalization.text import accept_text_points
from product.identity import SESSION_COOKIE
from product.settings import Settings
from psycopg.types.json import Jsonb
from pydantic import SecretStr


def clause(clause_id: str, text: str) -> dict:
    return {"id": clause_id, "category": "条款", "priority": "P0", "rule_to_check": text, "type_of_rule": "text"}


def test_withdraw_confirmation_and_carry_unchanged_clauses(auth_store_factory):
    settings = Settings(feishu_app_id="formalization-reversible", git_sha="c" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-reversible", "alice")
    client = TestClient(create_app(settings, auth_store=build_product_store(settings, objects=store.objects, role="api")))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    checked = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": True}, {"id": "c2", "checked": True}]}}, ensure_ascii=False)}
    first, second = str(uuid4()), str(uuid4())
    v1 = {"kind": "engine", "type": "自定义", "rules": [clause("c1", "文档要有编号"), clause("c2", "文档要有签字")]}
    v2 = {**v1, "rules": [clause("c1", "文档要有编号"), clause("c2", "文档要有两处签字")]}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'规则',%s,%s,%s)", (first, alice.tenant_id, Jsonb(v1), alice.user_id, Jsonb(checked))
        )
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context,predecessor_id) VALUES (%s,%s,'规则',%s,%s,%s,%s)",
            (second, alice.tenant_id, Jsonb(v2), alice.user_id, Jsonb(checked), first),
        )

    def confirm(rule_id: str, clause_id: str, text: str) -> dict:
        detail = client.get(f"/rules/{rule_id}/formalizations/{clause_id}").json()
        reading = accept_text_points(json.dumps({"points": [text], "review": []}, ensure_ascii=False), source_rule_version_id=rule_id, clause_id=clause_id, clause_text=text)
        candidate_id = str(uuid4())
        spec = reading["spec"]
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO product.rule_formalizations(
                     id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,owl_content,clif_content,
                     vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,formal_digest,display_digest,created_by)
                   VALUES (%s,%s,%s,%s,1,%s,%s,NULL,NULL,%s,%s,%s,%s,'complete',%s,%s,%s,%s,%s)""",
                (
                    candidate_id,
                    alice.tenant_id,
                    rule_id,
                    clause_id,
                    detail["source_rule_digest"],
                    spec["formal_profile"],
                    Jsonb({}),
                    Jsonb(spec["obligations"]),
                    Jsonb(spec["assumptions"]),
                    Jsonb([]),
                    reading["cnl_text"],
                    reading["cnl_renderer_version"],
                    reading["formal_digest"],
                    reading["display_digest"],
                    alice.user_id,
                ),
            )
        response = client.post(
            f"/rules/{rule_id}/formalizations/{clause_id}/confirmations",
            json={
                "operation_id": str(uuid4()),
                "candidate_id": candidate_id,
                "expected_candidate_revision": 1,
                "expected_source_digest": detail["source_rule_digest"],
                "expected_display_digest": reading["display_digest"],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    confirmation = confirm(first, "c1", "文档要有编号")
    confirm(first, "c2", "文档要有签字")

    # Only the clause stated exactly as before can carry its confirmation.
    assert client.get(f"/rules/{second}/formalizations").json()["carryable"] == ["c1"]
    carried = client.post(f"/rules/{second}/formalization-carry-overs", json={"operation_id": str(uuid4())})
    assert carried.status_code == 200, carried.text
    assert carried.json()["carried"] == ["c1"]
    states = {row["clause_id"]: row["state"] for row in client.get(f"/rules/{second}/formalizations").json()["clauses"]}
    assert states == {"c1": "confirmed", "c2": "not_started"}
    assert client.get(f"/rules/{second}/formalizations").json()["carryable"] == []

    # Withdrawing leaves the record as history and returns the clause to the person.
    withdrawn = client.post(f"/rules/{first}/formalizations/c1/confirmations/{confirmation['id']}/revocation")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["current"] is False and withdrawn.json()["revoked_at"]
    assert client.get(f"/rules/{first}/formalizations/c1").json()["state"] == "candidate"
    again = client.post(f"/rules/{first}/formalizations/c1/confirmations/{confirmation['id']}/revocation")
    assert again.status_code == 200 and again.json()["current"] is False
