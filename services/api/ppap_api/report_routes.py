"""Explicit report export; never retries an unknown external creation."""
import hashlib
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from ppap_core.identity import can_read
from ppap_core.feishu_writeback import (
    FeishuWritebackError, report_blocks, round_return_url,
    create_report_document, append_report_blocks,
)


def register_report_routes(app, store, session_of, http, settings):
    def prepare(round_id, request):
        session = session_of(request)
        if not session:
            return None, None, None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        principal = session.principal
        resource = store.resource(round_id)
        if resource is None or not can_read(principal, resource, store.grants(round_id, principal.user_id)):
            return None, None, None, JSONResponse({"error": "access_denied"}, status_code=403)
        body = store.get_round(round_id)
        try:
            blocks = report_blocks(body, round_return_url(settings.canonical_web_url(), round_id))
        except FeishuWritebackError as exc:
            return None, None, None, JSONResponse({"error": str(exc)}, status_code=409)
        fingerprint = hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return session, blocks, fingerprint, None

    @app.get('/integrations/feishu/rounds/{round_id}/report')
    def get_report(round_id: str, request: Request):
        session, blocks, fingerprint, denied = prepare(round_id, request)
        if denied is not None:
            return denied
        return {"report": store.get_report(session.principal, round_id, fingerprint)}

    @app.post('/integrations/feishu/rounds/{round_id}/report')
    def create_report(round_id: str, request: Request):
        session, blocks, fingerprint, denied = prepare(round_id, request)
        if denied is not None:
            return denied
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
        except FeishuWritebackError:
            store.settle_report(principal, ident, "unknown")
        return {"report": store.get_report(principal, round_id, fingerprint)}
