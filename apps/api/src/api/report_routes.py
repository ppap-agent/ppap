"""Explicit report export; never retries an unknown external creation."""

import hashlib
import json
from typing import Any

from fastapi import Body, Depends, Request
from fastapi.responses import JSONResponse
from product.feishu_writeback import (
    FeishuWritebackError,
    append_report_blocks,
    create_report_document,
    report_blocks,
    round_return_url,
)
from product.mcp import McpError

from api import web_contract as wc
from api.access import object_denied
from api.signing import signing


def register_report_routes(app, store, session_of, http, settings):
    def prepare(round_id, request):
        session = session_of(request)
        if not session:
            return None, None, None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        if denied := object_denied(store, session.principal, round_id):
            return None, None, None, denied
        # The same projection as GET /rounds/{id}, which the confirmation captured.
        body = store.get_round(round_id, principal=session.principal)
        if body is None:
            # The id names a draft or file of the enterprise, not a round.
            return None, None, None, JSONResponse({"error": "access_denied"}, status_code=403)
        try:
            blocks = report_blocks(body, round_return_url(settings.canonical_web_url(), body))
            fingerprint = hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        except McpError as exc:
            return None, None, None, JSONResponse({"error": exc.code}, status_code=exc.status)
        except FeishuWritebackError as exc:
            return None, None, None, JSONResponse({"error": str(exc)}, status_code=409)
        return session, blocks, fingerprint, None

    @app.get("/integrations/feishu/rounds/{round_id}/report", response_model=wc.ReportEnvelope)
    def get_report(round_id: str, request: Request):
        session, blocks, fingerprint, denied = prepare(round_id, request)
        if denied is not None:
            return denied
        return {"report": store.get_report(session.principal, round_id, fingerprint), "content_fingerprint": fingerprint}

    @app.post(
        "/integrations/feishu/rounds/{round_id}/report",
        dependencies=[Depends(signing)],
        response_model=wc.ReportEnvelope,
        openapi_extra=wc.openapi_body(wc.ReportRequest),
    )
    def create_report(round_id: str, request: Request, body: Any = Body(None)):
        session, blocks, fingerprint, denied = prepare(round_id, request)
        if denied is not None:
            return denied
        # The document the person saw is the one written: a changed one is shown to them again, not sent (#348).
        expected = body.get("if_match") if isinstance(body, dict) else None
        if expected is not None and expected != fingerprint:
            return JSONResponse({"error": "content_changed"}, status_code=409)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        principal = session.principal
        ident = store.claim_report(principal, round_id, fingerprint)
        if ident is None:
            return {"report": store.get_report(principal, round_id, fingerprint)}
        try:
            document_id = create_report_document(http, session.access_token, "PPAP 审核报告 · " + round_id)
            store.settle_report(principal, ident, "writing", document_id)
            append_report_blocks(http, session.access_token, document_id, blocks, ident)
            store.settle_report(principal, ident, "ready")
        except FeishuWritebackError as exc:
            # Only a definite creation rejection can be explicitly retried.
            # A failed append may have left a real document, so it stays unknown.
            code = str(exc)
            state = "failed" if code.startswith("feishu_report_create_rejected:") else "unknown"
            store.settle_report(principal, ident, state, error_code=code)
        return {"report": store.get_report(principal, round_id, fingerprint)}
