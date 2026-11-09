"""Positive session-authenticated HTTP contract on the production PostgreSQL store."""

from uuid import uuid4

import fitz
import pytest
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE

from .test_fact_answers import answer_request, freeze_fact_source, prepare_fact_round


def test_http_fact_answer_export_and_report_keep_the_original(auth_store_factory, tmp_path, monkeypatch):
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
        exported = client.get(path + "/export")
        assert exported.status_code == 200
        assert exported.json()["machine_findings"][0]["status"] == "UNABLE"
        assert exported.json()["results"] == receipt["results"]
        assert exported.json()["training_eligible"] is False
        report = client.get(path + "/report")
        assert report.status_code == 200
        assert report.headers["content-type"] == "application/pdf"
        with fitz.open(stream=report.content, filetype="pdf") as document:
            # Expand font ligatures so identifiers are compared as source text.
            text = "".join(page.get_text(flags=fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_LIGATURES) for page in document)
        assert "UNABLE" in text and "PASS" in text
        assert alice.user_id in text
        assert version["id"] in text
        assert round_id in text
    reopened = build_product_store(store.settings, objects=store.objects, role="api")
    assert reopened.get_round(round_id) == original
    assert reopened.read_fact_answer_operation(alice, round_id, request["operation_id"]) == receipt
    assert observed == ["model observation"]


@pytest.mark.parametrize("scope", ["foreign_tenant", "read_without_review", "original_revoked", "membership_revoked"])
def test_fact_answers_recheck_current_review_and_original_authorization(auth_store_factory, tmp_path, monkeypatch, scope):
    store, api, alice, rid, version, observed = prepare_fact_round(auth_store_factory, tmp_path, monkeypatch)
    request = answer_request(api, alice, rid)
    receipt = api.apply_fact_answer(alice, rid, request)
    if scope == "foreign_tenant":
        person = store.upsert_feishu_identity("another-enterprise", "eve")
    elif scope == "read_without_review":
        person = store.upsert_feishu_identity("fact-enterprise", "bob")
        draft = store.create_draft(alice)
        with store._conn() as conn:
            conn.execute("UPDATE product.audit_drafts SET task_fingerprint='private-fact-test',task_day=current_date,task_sequence=1 WHERE id=%s", (draft["id"],))
            conn.execute("UPDATE product.audit_rounds SET source_draft_id=%s WHERE id=%s", (draft["id"], rid))
            conn.execute("INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES(%s,%s,'read'),(%s,%s,'read')", (rid, person.user_id, version["file_id"], person.user_id))
    else:
        person = alice
        with store._conn() as conn:
            if scope == "original_revoked":
                conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (version["file_id"], alice.user_id))
            else:
                conn.execute("DELETE FROM product.memberships WHERE tenant_id=%s AND user_id=%s", (alice.tenant_id, alice.user_id))
    with TestClient(create_app(store.settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(person))
        path = f"/audit-rounds/{rid}/fact-answers"
        for suffix in ("", "/" + request["operation_id"], "/export", "/report"):
            assert client.get(path + suffix).status_code in {401, 403}
        assert client.post(path, json={**request, "operation_id": str(uuid4()), "expected_revision": 1}).status_code in {401, 403}
        sample = {"operation_id": str(uuid4()), "item_key": "R1", "expected_status": "PASS", "rationale": "越权不能入样本"}
        assert client.post(path + "/" + request["operation_id"] + "/samples", json=sample).status_code in {401, 403}
    with pytest.raises(KeyError):
        api.read_fact_answers(person, rid)
    with store._conn() as conn:
        assert conn.execute("SELECT receipt FROM product.original_fact_answers WHERE round_id=%s", (rid,)).fetchall() == [(receipt,)]
        assert conn.execute("SELECT count(*) FROM product.checker_feedback").fetchone()[0] == 0
    assert observed == ["model observation"]


@pytest.mark.parametrize("change,error", [("fingerprint", "fact_question_changed"), ("version", "fact_question_expired"), ("revision", "fact_revision_conflict")])
def test_http_stale_fact_answer_is_conflict_without_writing(auth_store_factory, tmp_path, monkeypatch, change, error):
    store, api, alice, rid, version, observed = prepare_fact_round(auth_store_factory, tmp_path, monkeypatch)
    request = answer_request(api, alice, rid)
    key, value = {"fingerprint": ("expected_question_fingerprint", "a" * 64), "version": ("file_version_id", str(uuid4())), "revision": ("expected_revision", 1)}[change]
    with TestClient(create_app(store.settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(alice))
        response = client.post(f"/audit-rounds/{rid}/fact-answers", json={**request, key: value})
        assert response.status_code == 409 and response.json() == {"error": error}
    assert api.read_fact_answers(alice, rid)["answers"] == []


def test_http_explicit_answer_sample_reads_back_through_existing_feedback(auth_store_factory, tmp_path, monkeypatch):
    store, api, alice, rid, version, observed = prepare_fact_round(auth_store_factory, tmp_path, monkeypatch)
    freeze_fact_source(store, alice, rid, version)
    request = answer_request(api, alice, rid, False)
    with TestClient(create_app(store.settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(alice))
        path = f"/audit-rounds/{rid}/fact-answers"
        assert client.post(path, json=request).status_code == 200
        assert client.get(f"/audit-rounds/{rid}/checker-feedback").json()["feedback"] == []
        sample = {"operation_id": str(uuid4()), "item_key": "R1", "expected_status": "NG", "rationale": "原件没有勾选SOP"}
        endpoint = path + "/" + request["operation_id"] + "/samples"
        response = client.post(endpoint, json=sample)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["original_status"] == "UNABLE" and saved["expected_status"] == "NG"
        assert client.post(endpoint, json=sample).json() == saved
        assert client.get(f"/audit-rounds/{rid}/checker-feedback").json()["feedback"] == [saved]
        conflict = client.post(endpoint, json={**sample, "expected_status": "PASS"})
        assert conflict.status_code == 409 and conflict.json()["error"] == "checker_operation_conflict"
        assert client.get(path + "/export").json()["machine_findings"][0]["status"] == "UNABLE"
    assert observed == ["model observation"]
