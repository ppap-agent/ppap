"""Positive session-authenticated HTTP contract on the production PostgreSQL store."""

from uuid import uuid4

import fitz
import pytest
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE

from .test_fact_answers import answer_request, prepare_fact_round


def test_http_fact_answer_and_report_keep_the_original(auth_store_factory, tmp_path, monkeypatch):
    store, api, alice, round_id, version, observed = prepare_fact_round(auth_store_factory, tmp_path, monkeypatch)
    original = api.get_round(round_id)
    with TestClient(create_app(store.settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(alice))
        path = f"/audit-rounds/{round_id}/fact-answers"
        response = client.get(path)
        assert response.status_code == 200
        view = response.json()
        question = view["questions"][0]
        assert question["file_version_id"] == version["id"]
        request = {
            "operation_id": str(uuid4()),
            "expected_revision": view["revision"],
            "file_version_id": version["id"],
            "field": question["field"],
            "expected_question_fingerprint": question["fingerprint"],
            "value": True,
        }
        response = client.post(path, json=request)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["answer"]["answered_by"] == alice.user_id
        assert receipt["answer"]["locator"] == question["locator"]
        assert receipt["results"][0]["status"] == "PASS"
        assert receipt["model_calls"] == 0
        assert client.get(path + "/" + request["operation_id"]).json() == receipt
        assert client.post(path, json=request).json() == receipt
        assert len(client.get(path).json()["answers"]) == 1
        report = client.get(path + "/report")
        assert report.status_code == 200
        assert report.headers["content-type"] == "application/pdf"
        with fitz.open(stream=report.content, filetype="pdf") as document:
            # Expand font ligatures so identifiers are compared as source text.
            text = "".join(page.get_text(flags=fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_LIGATURES) for page in document)
        assert "UNABLE" in text and "PASS" in text
        # The delivered report names people and files as users know them, never by internal identifiers.
        assert alice.display_name in text and "original.pdf" in text
        assert alice.user_id not in text and version["id"] not in text and round_id not in text
    reopened = build_product_store(store.settings, objects=store.objects, role="api")
    assert reopened.get_round(round_id) == original
    assert reopened.read_fact_answer_operation(alice, round_id, request["operation_id"]) == receipt
    assert observed == ["model observation"]


@pytest.mark.parametrize("scope", ["foreign_tenant", "other_member", "membership_revoked"])
def test_fact_answers_are_read_by_the_enterprise_and_answered_only_by_the_round_creator(auth_store_factory, tmp_path, monkeypatch, scope):
    store, api, alice, rid, version, observed = prepare_fact_round(auth_store_factory, tmp_path, monkeypatch)
    request = answer_request(api, alice, rid)
    receipt = api.apply_fact_answer(alice, rid, request)
    if scope == "foreign_tenant":
        person = store.upsert_feishu_identity("another-enterprise", "eve")
    elif scope == "other_member":
        person = store.upsert_feishu_identity("fact-enterprise", "bob")
    else:
        person = alice
        with store._conn() as conn:
            conn.execute("DELETE FROM product.memberships WHERE tenant_id=%s AND user_id=%s", (alice.tenant_id, alice.user_id))
    readable = scope == "other_member"
    with TestClient(create_app(store.settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(person))
        path = f"/audit-rounds/{rid}/fact-answers"
        for suffix in ("", "/" + request["operation_id"], "/report"):
            assert client.get(path + suffix).status_code in ({200} if readable else {401, 403})
        assert client.post(path, json={**request, "operation_id": str(uuid4()), "expected_revision": 1}).status_code in {401, 403}
    if readable:
        assert api.read_fact_answers(person, rid)["answers"] == api.read_fact_answers(alice, rid)["answers"]
        with pytest.raises(KeyError):
            api.apply_fact_answer(person, rid, {**request, "operation_id": str(uuid4()), "expected_revision": 1})
    else:
        with pytest.raises(KeyError):
            api.read_fact_answers(person, rid)
    with store._conn() as conn:
        assert conn.execute("SELECT receipt FROM product.original_fact_answers WHERE round_id=%s", (rid,)).fetchall() == [(receipt,)]
    assert observed == ["model observation"]


def test_http_stale_fact_answer_is_conflict_without_writing(auth_store_factory, tmp_path, monkeypatch):
    """The store owns every stale case; HTTP adds only the 409 mapping."""
    store, api, alice, rid, version, observed = prepare_fact_round(auth_store_factory, tmp_path, monkeypatch)
    request = answer_request(api, alice, rid)
    with TestClient(create_app(store.settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(alice))
        response = client.post(f"/audit-rounds/{rid}/fact-answers", json={**request, "expected_question_fingerprint": "a" * 64})
        assert response.status_code == 409 and response.json() == {"error": "fact_question_changed"}
    assert api.read_fact_answers(alice, rid)["answers"] == []


def test_a_signed_round_takes_no_fact_answer_until_its_signoff_is_withdrawn(auth_store_factory, tmp_path, monkeypatch):
    """#357: a signed round's original facts stand as signed; its signer withdraws the signoff first."""
    store, api, alice, rid, version, observed = prepare_fact_round(auth_store_factory, tmp_path, monkeypatch)
    request = answer_request(api, alice, rid)
    action = str(uuid4())
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO product.actions (id, tenant_id, user_id, channel, method, route, path, outcome)
               VALUES (%s, %s, %s, 'web', 'POST', '/rounds/{round_id}/signoff', %s, 'succeeded')""",
            (action, alice.tenant_id, alice.user_id, f"/rounds/{rid}/signoff"),
        )
        conn.execute("UPDATE product.audit_rounds SET signed_at = now(), signoff = '{}'::jsonb, signoff_action_id = %s WHERE id = %s", (action, rid))
    with TestClient(create_app(store.settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(alice))
        response = client.post(f"/audit-rounds/{rid}/fact-answers", json=request)
        assert response.status_code == 409 and response.json() == {"error": "round_signed"}
    assert api.read_fact_answers(alice, rid)["answers"] == []
