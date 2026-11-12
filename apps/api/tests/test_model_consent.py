"""HTTP admission preserves omission versus an explicitly expected model revision."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from api.audit_routes import register_audit_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize("expectation", [{}, {"expected_model_revision": None}, {"expected_model_revision": str(uuid4())}])
def test_deep_accept_forwards_exact_consent_to_atomic_admission(expectation):
    received = []
    principal = SimpleNamespace(user_id="actor", tenant_id="tenant-a")

    def accept(*args, **kwargs):
        received.append(kwargs)
        raise ValueError("model_configuration_changed")

    store = SimpleNamespace(resource=lambda identity: SimpleNamespace(tenant_id="tenant-a", kind="draft"), grants=lambda *args: {"read"}, accept_draft=accept)
    app = FastAPI()
    register_audit_routes(app, store, lambda request: principal, lambda *args: {})
    response = TestClient(app).post("/drafts/draft-a/accept", json={"idempotency_key": str(uuid4()), **expectation})
    assert response.status_code == 409
    assert response.json() == {"error": "model_configuration_changed"}
    assert {key: value for key, value in received[0].items() if key == "expected_model_revision"} == expectation


def test_deep_rejects_invalid_revision_before_admission():
    store = SimpleNamespace(resource=lambda identity: SimpleNamespace(tenant_id="tenant-a", kind="draft"), grants=lambda *args: {"read"})
    app = FastAPI()
    register_audit_routes(app, store, lambda request: SimpleNamespace(user_id="actor", tenant_id="tenant-a"), lambda *args: {})
    for invalid in ([], {}, 3, "x" * 65):
        response = TestClient(app).post("/drafts/draft-a/accept", json={"idempotency_key": str(uuid4()), "expected_model_revision": invalid})
        assert response.status_code == 409
        assert response.json() == {"error": "model_configuration_changed"}
