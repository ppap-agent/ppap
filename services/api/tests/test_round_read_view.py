from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from ppap_api.audit_routes import register_audit_routes
from ppap_core.identity import Resource


def client_for(*, tenant="tenant", authenticated=True, private=False, grants=()):
    store = Mock()
    store.resource.return_value = Resource("round", "tenant", "audit_round", grant_required=private)
    store.grants.return_value = set(grants)
    store.get_round.side_effect = lambda identity, *, read_view=False: {"id": identity, **({"read_view": {"files": [], "unassigned_findings": []}} if read_view else {})}
    store.save_review.side_effect = lambda principal, identity, *args: {"id": identity}
    principal = SimpleNamespace(user_id="reader", tenant_id=tenant) if authenticated else None
    app = FastAPI()
    register_audit_routes(app, store, lambda request: principal)
    return TestClient(app)


def test_round_read_view_is_an_explicit_read_of_the_existing_authorized_round():
    client = client_for()
    assert client.get("/rounds/round").json() == {"id": "round"}
    response = client.get("/rounds/round?read_view=true")
    assert response.status_code == 200
    assert response.json() == {"id": "round", "read_view": {"files": [], "unassigned_findings": []}}


def test_round_reader_keeps_tenant_and_private_task_grant_boundaries():
    for client, expected in ((client_for(authenticated=False), 401), (client_for(tenant="other"), 403), (client_for(private=True), 403), (client_for(private=True, grants=("read",)), 200)):
        response = client.get("/rounds/round?read_view=true")
        assert response.status_code == expected
        if expected != 200:
            assert "read_view" not in response.json()


def test_review_write_keeps_its_own_authority_and_never_inherits_read_access():
    body = {"item_key": "coverage:version:1", "judgment": "unknown", "rationale": "需核对原件", "next_step": "", "version": 0}
    for client, expected in (
        (client_for(authenticated=False), 401),
        (client_for(tenant="other"), 403),
        (client_for(private=True, grants=("read",)), 403),
        (client_for(private=True, grants=("read", "review")), 200),
        (client_for(), 200),
    ):
        response = client.put("/rounds/round/reviews", json=body)
        assert response.status_code == expected
