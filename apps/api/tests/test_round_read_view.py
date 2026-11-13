from types import SimpleNamespace
from unittest.mock import Mock

from api.audit_routes import register_audit_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product.identity import Resource


def client_for(*, tenant="tenant", authenticated=True, private=False, grants=()):
    store = Mock()
    store.resource.return_value = Resource("round", "tenant", "audit_round", grant_required=private)
    store.grants.return_value = set(grants)
    store.get_round.side_effect = lambda identity, *, read_view=False, principal=None: {
        "id": identity,
        **(
            {
                "read_view": {
                    "files": [],
                    "unassigned_findings": [],
                    "report_source": {"task_id": "private-task" if principal else None, "access": {"state": "allowed" if principal else "denied", "reason": None}},
                }
            }
            if read_view
            else {}
        ),
    }
    store.save_review.side_effect = lambda principal, identity, *args, **kwargs: {"id": identity}
    principal = SimpleNamespace(user_id="reader", tenant_id=tenant) if authenticated else None
    app = FastAPI()
    register_audit_routes(app, store, lambda request: principal, Mock())
    return TestClient(app)


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
