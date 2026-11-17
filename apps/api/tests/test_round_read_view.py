from types import SimpleNamespace
from unittest.mock import Mock

from api.audit_routes import register_audit_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product.identity import Resource


def client_for(*, tenant="tenant", authenticated=True, creator="reader"):
    store = Mock()
    store.resource.return_value = Resource("round", "tenant", "audit_round", created_by=creator)
    store.get_round.side_effect = lambda identity, *, read_view=False, principal=None: {"id": identity}
    store.save_review.side_effect = lambda principal, identity, *args, **kwargs: {"id": identity}
    principal = SimpleNamespace(user_id="reader", tenant_id=tenant) if authenticated else None
    app = FastAPI()
    register_audit_routes(app, store, lambda request: principal, Mock())
    return TestClient(app)


def test_the_enterprise_reads_a_round_and_only_its_creator_reviews_it():
    body = {"item_key": "coverage:version:1", "judgment": "unknown", "rationale": "需核对原件", "next_step": "", "version": 0}
    for client, expected in (
        (client_for(authenticated=False), 401),
        (client_for(tenant="other"), 403),
        (client_for(creator="colleague"), 403),
        (client_for(), 200),
    ):
        assert client.put("/rounds/round/reviews", json=body).status_code == expected
    assert client_for(creator="colleague").get("/rounds/round").status_code == 200
    assert client_for(tenant="other").get("/rounds/round").status_code == 403
