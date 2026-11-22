"""One object authorization for every route: the enterprise reads, only the creator changes, and every refusal looks alike."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from api.access import object_denied
from api.audit_routes import register_audit_routes
from api.report_routes import register_report_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product.identity import Principal, Resource

ALICE = Principal("alice", "tenant-a", "feishu", "app", "tenant-a", "alice", "member", "Alice")
BOB = Principal("bob", "tenant-a", "feishu", "app", "tenant-a", "bob", "member", "Bob")
CAROL = Principal("carol", "tenant-b", "feishu", "app", "tenant-b", "carol", "member", "Carol")


def store_with(resource):
    return SimpleNamespace(resource=lambda _object_id: resource)


def status(denied):
    return None if denied is None else (denied.status_code, denied.body)


DENIED = (403, b'{"error":"access_denied"}')
ROUND = Resource("r", "tenant-a", "audit_round", "alice")


@pytest.mark.parametrize(
    "resource,principal,modify,kind,expected",
    [
        (ROUND, ALICE, True, None, None),  # the creator changes it
        (ROUND, BOB, False, None, None),  # the enterprise reads it
        (ROUND, BOB, True, None, DENIED),  # but does not change it
        (ROUND, CAROL, False, None, DENIED),  # another enterprise
        (None, ALICE, False, None, DENIED),  # missing or malformed id
        (Resource("t", "tenant-a", "template", "alice"), ALICE, False, None, DENIED),
        (Resource("t", "tenant-a", "template", "alice"), ALICE, True, None, DENIED),
        (ROUND, ALICE, True, "file", DENIED),  # a round id where a file is required
        (Resource("f", "tenant-a", "file", "alice"), ALICE, True, "file", None),
    ],
)
def test_object_authorization_matrix(resource, principal, modify, kind, expected):
    assert status(object_denied(store_with(resource), principal, "id", modify=modify, kind=kind)) == expected


def test_draft_read_keeps_its_second_enterprise_check_and_never_fails_with_500():
    store = Mock()
    store.get_draft.return_value = {"id": "d"}
    app = FastAPI()
    register_audit_routes(app, store, lambda _request: BOB, Mock())
    client = TestClient(app)
    own = Resource("d", "tenant-a", "audit_draft", "alice")
    store.resource.side_effect = [own, own]
    assert client.get("/drafts/d").status_code == 200
    store.resource.side_effect = [own, Resource("d", "tenant-b", "audit_draft", "alice")]
    assert client.get("/drafts/d").json() == {"error": "access_denied"}
    # Deleted between the two reads.
    store.resource.side_effect = [own, None]
    assert client.get("/drafts/d").json() == {"error": "access_denied"}


def test_feishu_report_needs_a_session_and_a_round():
    store = Mock()
    store.resource.return_value = Resource("d", "tenant-a", "audit_draft", "alice")
    store.get_round.return_value = None
    session = SimpleNamespace(principal=BOB, access_token="token")
    signed_in, signed_out = FastAPI(), FastAPI()
    register_report_routes(signed_in, store, lambda _request: session, Mock(), Mock())
    register_report_routes(signed_out, store, lambda _request: None, Mock(), Mock())
    for method in ("get", "post"):
        assert getattr(TestClient(signed_out), method)("/integrations/feishu/rounds/d/report").status_code == 401
        # A draft or file id of the enterprise is no round: refused like any other object.
        refused = getattr(TestClient(signed_in), method)("/integrations/feishu/rounds/d/report")
        assert (refused.status_code, refused.json()) == (403, {"error": "access_denied"})
    store.claim_report.assert_not_called()
