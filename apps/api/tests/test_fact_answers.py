"""HTTP contract for explicit original facts, using the real response validation."""

from types import SimpleNamespace
from unittest.mock import Mock

from api import app as product_app
from api.fact_answers import register_fact_answer_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def confirmed(value=True):
    answer = {
        "operation_id": "answer-1",
        "round_id": "round-1",
        "file_version_id": "file-1",
        "field": "stage.checked",
        "value": value,
        "answered_by": "reviewer-1",
        "answered_at": "2026-11-06T10:00:00Z",
        "locator": {"kind": "pdf_page", "page": 2},
        "prompt": "该勾选框是否选中？",
        "answer_type": "boolean",
    }
    result = {
        "item_key": "file-1:R1",
        "status": "UNABLE" if value is None else "PASS",
        "suggestions": "依据人工回答重算",
        "answer_ids": ["answer-1"],
        "confirmed_by": ["reviewer-1"],
        "confirmed_at": answer["answered_at"],
        "sources": [{key: answer[key] for key in ("file_version_id", "field", "locator", "answered_by", "answered_at", "value")}],
    }
    return {"operation_id": "answer-1", "round_id": "round-1", "revision": 1, "answer": answer, "results": [result], "model_calls": 0}


def fixture(value=True):
    principal = SimpleNamespace(tenant_id="tenant-1", user_id="reviewer-1")
    receipt = confirmed(value)
    view = {"round_id": "round-1", "revision": 1, "questions": [], "answers": [receipt["answer"]], "results": receipt["results"], "model_calls": 0}
    store = Mock()
    store.read_fact_answers.return_value = view
    store.apply_fact_answer.return_value = receipt
    store.read_fact_answer_operation.return_value = receipt
    store.export_fact_answers.return_value = {
        "schema": "original-fact-confirmations-v1",
        "training_eligible": False,
        "machine_findings": [{"item_key": "file-1:R1", "status": "UNABLE", "suggestions": "看不清", "locator": {"file_version_id": "file-1"}}],
        **view,
    }
    app = FastAPI()
    register_fact_answer_routes(app, store, lambda request: principal)
    return TestClient(app), store, principal, receipt


def test_standard_fact_answer_preserves_identity_sources_and_operation_readback():
    client, store, principal, receipt = fixture()
    response = client.get("/audit-rounds/round-1/fact-answers")
    assert response.status_code == 200
    assert response.json()["answers"] == [receipt["answer"]]
    store.read_fact_answers.assert_called_once_with(principal, "round-1")
    body = {"operation_id": "answer-1", "expected_revision": 0, "file_version_id": "file-1", "field": "stage.checked", "expected_question_fingerprint": "a" * 64, "value": True}
    saved = client.post("/audit-rounds/round-1/fact-answers", json=body)
    assert saved.status_code == 200
    assert saved.json() == receipt
    store.apply_fact_answer.assert_called_once_with(principal, "round-1", body)
    assert client.get("/audit-rounds/round-1/fact-answers/answer-1").json() == receipt
    store.read_fact_answer_operation.assert_called_once_with(principal, "round-1", "answer-1")


def test_explicit_cannot_read_answer_remains_null_in_the_wire_contract():
    client, store, principal, receipt = fixture(None)
    body = {"operation_id": "answer-1", "expected_revision": 0, "file_version_id": "file-1", "field": "stage.checked", "expected_question_fingerprint": "a" * 64, "value": None}
    response = client.post("/audit-rounds/round-1/fact-answers", json=body)
    assert response.status_code == 200
    assert response.json() == receipt
    assert response.json()["results"][0]["status"] == "UNABLE"
    assert response.json()["model_calls"] == 0
    store.apply_fact_answer.assert_called_once_with(principal, "round-1", body)


def test_export_is_a_separate_human_record_alongside_original_machine_findings():
    client, store, principal, receipt = fixture()
    response = client.get("/audit-rounds/round-1/fact-answers/export")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "original-fact-confirmations-v1"
    assert body["training_eligible"] is False
    assert body["machine_findings"][0]["status"] == "UNABLE"
    assert body["results"] == receipt["results"]
    store.export_fact_answers.assert_called_once_with(principal, "round-1")


def test_fact_answer_routes_are_registered_on_the_production_app():
    paths = product_app.openapi()["paths"]
    assert "get" in paths["/audit-rounds/{round_id}/fact-answers"]
    assert "post" in paths["/audit-rounds/{round_id}/fact-answers"]
    assert "get" in paths["/audit-rounds/{round_id}/fact-answers/{operation_id}"]
    assert "post" in paths["/audit-rounds/{round_id}/fact-answers/{operation_id}/samples"]
    assert "get" in paths["/audit-rounds/{round_id}/fact-answers/export"]
    assert "application/pdf" in paths["/audit-rounds/{round_id}/fact-answers/report"]["get"]["responses"]["200"]["content"]


def test_confirmation_pdf_preserves_the_original_and_human_conclusions():
    import fitz

    client, store, principal, receipt = fixture()
    response = client.get("/audit-rounds/round-1/fact-answers/report")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="original-fact-confirmations.pdf"'
    with fitz.open(stream=response.content, filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
    assert "UNABLE" in text
    assert "PASS" in text
    assert "reviewer-1" in text
    store.export_fact_answers.assert_called_once_with(principal, "round-1")
