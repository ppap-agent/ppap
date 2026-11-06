"""Positive session-authenticated HTTP contract on the production PostgreSQL store."""

from uuid import uuid4

import fitz
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE

from .test_fact_answers import prepare_fact_round


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
            text = "".join(page.get_text() for page in document)
        assert "UNABLE" in text and "PASS" in text
        assert alice.user_id in text
    reopened = build_product_store(store.settings, objects=store.objects, role="api")
    assert reopened.get_round(round_id) == original
    assert reopened.read_fact_answer_operation(alice, round_id, request["operation_id"]) == receipt
    assert observed == ["model observation"]
