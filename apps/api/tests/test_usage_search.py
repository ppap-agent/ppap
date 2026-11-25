"""Feishu's usage reconciliation query: current's records, then legacy's, signed as legacy UAC signs. Mocked HTTP only."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

import httpx
from api import usage_search
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product.settings import Settings

ITEM = "OpenApp_Test"
KEY = Ed25519PrivateKey.generate()
SEED = base64.b64encode(KEY.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
LEGACY = "http://legacy.test/feishu/partner_ai/ai_item_uses/search"


class Store:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def reported_usage(self, app_id, ai_item_id, **filters):
        self.calls.append((app_id, ai_item_id, filters))
        return self.rows


def _row(n: int) -> dict:
    return {
        "id": f"00000000-0000-0000-0000-00000000000{n}",
        "trace_id": f"cur-{n}",
        "consumed_points": 500,
        "external_confirmed_at": datetime(2026, 11, 24, 10, n, tzinfo=timezone.utc),
        "tenant_key": "tk",
        "subject": f"ou_{n}",
    }


def _legacy(pages: list[list[dict]], seen: list[dict], status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        index = body["page_no"] - 1
        return httpx.Response(status, json={"code": 0, "data": {"list": pages[index], "has_more": index + 1 < len(pages), "total": sum(map(len, pages))}})

    return httpx.MockTransport(handler)


def _client(store: Store, transport: httpx.MockTransport | None = None, monkeypatch=None, **settings) -> TestClient:
    app = FastAPI()
    values = {"feishu_app_id": "cli_test", "feishu_usage_search_signing_key": SEED, "legacy_usage_search_url": LEGACY if transport else ""}
    usage_search.register_usage_search_route(app, store, Settings(**{**values, **settings}))
    if transport is not None:
        real = httpx.Client
        monkeypatch.setattr(usage_search.httpx, "Client", lambda: real(transport=transport))
    return TestClient(app)


def _query(**extra) -> dict:
    return {"ai_item_id": ITEM, "page_no": 1, "page_size": 10, **extra}


def test_answer_is_canonical_json_signed_over_its_sha256():
    response = _client(Store([_row(1)])).post(usage_search.PATH, json=_query())
    assert response.status_code == 200
    body = response.content
    assert body == usage_search.canonical(json.loads(body))
    KEY.public_key().verify(base64.b64decode(response.headers["X-ISV-Signature"]), hashlib.sha256(body).digest())
    item = json.loads(body)["data"]["list"][0]
    assert item["trace_id"] == "cur-1" and item["usage_amount"] == {"type": "fixed", "credit": 500, "minute": 0}
    assert item["user_id"] == "ou_1" and item["user_id_type"] == "open_id" and item["tenant_key"] == "tk"


def test_current_records_come_first_then_every_legacy_page_with_the_same_filters(monkeypatch):
    seen: list[dict] = []
    legacy_pages = [[{"id": "L1", "trace_id": "old-1"}, {"id": "L2", "trace_id": "old-2"}], [{"id": "L3", "trace_id": "old-3"}]]
    store = Store([_row(1), _row(2)])
    client = _client(store, _legacy(legacy_pages, seen), monkeypatch)
    filters = {"start_time": 1700000000000, "end_time": 1800000000000, "tenant_key": "tk", "trace_ids": ["cur-1", "old-3"]}
    first = client.post(usage_search.PATH, json=_query(page_size=3, **filters)).json()
    second = client.post(usage_search.PATH, json=_query(page_no=2, page_size=3, **filters)).json()
    assert [i["trace_id"] for i in first["data"]["list"]] == ["cur-1", "cur-2", "old-1"]
    assert first["data"]["has_more"] is True and first["data"]["total"] == 5
    assert [i["trace_id"] for i in second["data"]["list"]] == ["old-2", "old-3"]
    assert second["data"]["has_more"] is False
    assert [s["page_no"] for s in seen[:2]] == [1, 2] and all(s["page_size"] == usage_search.MAX_PAGE_SIZE for s in seen)
    assert {k: seen[0][k] for k in filters} == filters and seen[0]["ai_item_id"] == ITEM
    assert store.calls[0][0:2] == ("cli_test", ITEM)
    assert store.calls[0][2]["start"].timestamp() == 1700000000
    assert store.calls[0][2]["trace_ids"] == ["cur-1", "old-3"] and store.calls[0][2]["tenant_key"] == "tk"


def test_a_legacy_failure_fails_the_whole_answer_instead_of_a_partial_list(monkeypatch):
    response = _client(Store([_row(1)]), _legacy([[]], [], status=502), monkeypatch).post(usage_search.PATH, json=_query())
    assert response.status_code == 500 and "X-ISV-Signature" not in response.headers


def test_unconfigured_wrong_key_and_invalid_requests_are_refused():
    assert _client(Store([]), feishu_usage_search_signing_key="").post(usage_search.PATH, json=_query()).status_code == 503
    keyed = _client(Store([]), feishu_usage_search_api_key="agreed")
    assert keyed.post(usage_search.PATH, json=_query()).status_code == 401
    assert keyed.post(usage_search.PATH, json=_query(), headers={"X-feishu-api-key": "agreed"}).status_code == 200
    client = _client(Store([]))
    for bad in (_query(page_size=101), _query(page_no=0), _query(ai_item_id=""), _query(trace_ids=["x"] * 1001), _query(start_time="1"), [1]):
        assert client.post(usage_search.PATH, json=bad).status_code == 400
