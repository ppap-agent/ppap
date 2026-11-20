"""Feishu web-app JSAPI authentication (h5sdk.config): the server half.

Feishu signs nothing for us: we fetch a per-tenant jsapi_ticket with the tenant token and sign
`jsapi_ticket=…&noncestr=…&timestamp=…&url=…` with SHA-1 (open.feishu.cn, 网页应用鉴权). The ticket
stays on the server; only the signature leaves it.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

JSAPI_TICKET_URL = "https://open.feishu.cn/open-apis/jssdk/ticket/get"
# Feishu returns the same ticket while more than 30 minutes remain; refresh a little before expiry.
TICKET_MARGIN_SECONDS = 300


class FeishuJsapiError(Exception):
    pass


def jsapi_signature(ticket: str, nonce: str, timestamp_ms: int, url: str) -> str:
    """Fields in ASCII order, joined as key=value with &, unescaped; SHA-1, lowercase hex."""
    plain = f"jsapi_ticket={ticket}&noncestr={nonce}&timestamp={timestamp_ms}&url={url}"
    return hashlib.sha1(plain.encode("utf-8")).hexdigest()


def signed_page(url: str, origin: str) -> str | None:
    """The page URL Feishu checks: same origin as our web app, query kept, fragment dropped."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    page, expected = urlsplit(url.split("#", 1)[0]), urlsplit(origin)
    if page.scheme != expected.scheme or page.netloc != expected.netloc or page.username or page.password:
        return None
    return page.geturl()


class JsapiTickets:
    """One jsapi_ticket per tenant, cached in the process until shortly before it expires."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._tickets: dict[str, tuple[str, float]] = {}

    def ticket(self, http, tenant_key: str, tenant_token: Callable[[], str]) -> str:
        with self._lock:
            cached = self._tickets.get(tenant_key)
            if cached and cached[1] > self._clock():
                return cached[0]
            try:
                response = http.post(JSAPI_TICKET_URL, headers={"Authorization": "Bearer " + tenant_token(), "Content-Type": "application/json; charset=utf-8"}, timeout=20)
                body = response.json()
            except Exception as exc:
                raise FeishuJsapiError("feishu_jsapi_unavailable") from exc
            data = body.get("data") if isinstance(body, dict) else None
            ticket = data.get("ticket") if isinstance(data, dict) else None
            expire_in = data.get("expire_in") if isinstance(data, dict) else None
            if response.status_code != 200 or body.get("code") != 0 or not isinstance(ticket, str) or not ticket.strip() or not isinstance(expire_in, int):
                raise FeishuJsapiError("feishu_jsapi_unavailable")
            self._tickets[tenant_key] = (ticket.strip(), self._clock() + max(expire_in - TICKET_MARGIN_SECONDS, 60))
            return ticket.strip()


def jsapi_config(app_id: str, ticket: str, url: str, *, now_ms: int | None = None, nonce: str | None = None) -> dict:
    timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
    nonce_str = nonce or secrets.token_urlsafe(12)
    return {"app_id": app_id, "timestamp": timestamp, "nonce_str": nonce_str, "signature": jsapi_signature(ticket, nonce_str, timestamp, url)}
