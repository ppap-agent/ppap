"""Feishu's usage reconciliation query (``ai_item_uses/search``): the paid usage this ISV reported.

Feishu calls one query address per app, and current and legacy PPAP report under the same AI item. Until legacy
retires, the answer is current's acknowledged paid usage followed by everything legacy UAC answers for the same
filters. A legacy failure fails the whole answer: a partial list would read as missing reports. The body is
JCS-canonical JSON, signed Ed25519 over its SHA-256 (``X-ISV-Signature``), as legacy UAC signs it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request
from fastapi.responses import Response
from product.feishu_usage import usage_details
from product.logs import emit
from product.settings import Settings

PATH = "/integrations/feishu/partner_ai/ai_item_uses/search"
MAX_TRACE_IDS = 1000
MAX_PAGE_SIZE = 100
LEGACY_PAGES = 100


class LegacyUnavailable(Exception):
    pass


def canonical(value: Any) -> bytes:
    """RFC 8785 for the shapes answered here: ASCII keys, strings, integers, booleans and null."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sign(body: bytes, seed: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed, validate=True))
    return base64.b64encode(key.sign(hashlib.sha256(body).digest())).decode()


def _error(code: int, message: str) -> Response:
    return Response(json.dumps({"code": code, "message": message}), status_code=code, media_type="application/json")


def _millis(value: Any) -> datetime | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("time")
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def parse(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("body")
    item = body.get("ai_item_id")
    page_no, page_size = body.get("page_no"), body.get("page_size")
    if not isinstance(item, str) or not item.strip():
        raise ValueError("ai_item_id")
    if type(page_no) is not int or page_no < 1 or type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError("page")
    tenant_key, trace_ids = body.get("tenant_key"), body.get("trace_ids")
    if tenant_key is not None and not isinstance(tenant_key, str):
        raise ValueError("tenant_key")
    if trace_ids is not None and (not isinstance(trace_ids, list) or len(trace_ids) > MAX_TRACE_IDS or not all(isinstance(t, str) for t in trace_ids)):
        raise ValueError("trace_ids")
    return {
        "ai_item_id": item,
        "start": _millis(body.get("start_time")),
        "end": _millis(body.get("end_time")),
        "start_time": body.get("start_time"),
        "end_time": body.get("end_time"),
        "tenant_key": tenant_key,
        "trace_ids": trace_ids or None,
        "page_no": page_no,
        "page_size": page_size,
    }


def own_item(row: dict, ai_item_id: str) -> dict[str, Any]:
    points = int(row["consumed_points"])
    return {
        "id": str(row["id"]),
        "ai_item_id": ai_item_id,
        "trace_id": row["trace_id"],
        "client_info": {"user_agent": "", "client_ip": ""},
        # Legacy UAC answers the AI item id here, and Feishu reconciles its records with that value.
        "feishu_app_id": ai_item_id,
        "user_id": row["subject"] or "",
        "user_id_type": "open_id",
        "tenant_key": row["tenant_key"],
        "usage_amount": {"type": "fixed", "credit": points, "minute": 0},
        # The details as reported, so Feishu reads back what it received.
        "details": usage_details(row["subject"] or "", row["created_at"], points),
    }


def legacy_items(http: httpx.Client, url: str, query: dict[str, Any]) -> list[dict]:
    filters = {key: query[key] for key in ("ai_item_id", "start_time", "end_time", "tenant_key", "trace_ids") if query[key] is not None}
    items: list[dict] = []
    for page in range(1, LEGACY_PAGES + 1):
        try:
            response = http.post(url, json={**filters, "page_no": page, "page_size": MAX_PAGE_SIZE}, timeout=10)
            body = response.json()
        except Exception as exc:
            raise LegacyUnavailable from exc
        data = body.get("data") if isinstance(body, dict) else None
        if response.status_code != 200 or body.get("code") != 0 or not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise LegacyUnavailable
        items.extend(data["list"])
        if not data.get("has_more") or not data["list"]:
            return items
    raise LegacyUnavailable


def answer(store: Any, settings: Settings, http: httpx.Client, query: dict[str, Any]) -> dict[str, Any]:
    rows = store.reported_usage(settings.feishu_app_id, query["ai_item_id"], start=query["start"], end=query["end"], tenant_key=query["tenant_key"], trace_ids=query["trace_ids"])
    own = [own_item(row, query["ai_item_id"]) for row in rows]
    legacy = legacy_items(http, settings.legacy_usage_search_url, query) if settings.legacy_usage_search_url.strip() else []
    merged = own + legacy
    offset = (query["page_no"] - 1) * query["page_size"]
    page = merged[offset : offset + query["page_size"]]
    emit("feishu_usage_search", own=len(own), legacy=len(legacy), returned=len(page))
    return {"code": 0, "data": {"list": page, "has_more": offset + len(page) < len(merged), "total": len(merged)}}


def register_usage_search_route(app: FastAPI, store: Any, settings: Settings) -> None:
    @app.post(PATH, include_in_schema=False)
    async def feishu_usage_search(request: Request) -> Response:
        seed = settings.feishu_usage_search_signing_key.strip()
        if not seed:
            return _error(503, "usage search not configured")
        expected = settings.feishu_usage_search_api_key.strip()
        if expected and request.headers.get("X-feishu-api-key", "") != expected:
            return _error(401, "Invalid API Key")
        try:
            query = parse(await request.json())
        except Exception:
            return _error(400, "invalid request")

        def run() -> dict[str, Any]:
            with httpx.Client() as http:
                return answer(store, settings, http, query)

        try:
            result = await asyncio.to_thread(run)
        except LegacyUnavailable:
            emit("feishu_usage_search_legacy_unavailable")
            return _error(500, "Internal Server Error")
        body = canonical(result)
        return Response(body, media_type="application/json;charset=UTF-8", headers={"X-ISV-Signature": sign(body, seed)})
