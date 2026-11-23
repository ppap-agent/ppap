"""Explicit report export; never retries an unknown external creation.

A delivered document is set read-only for others in the enterprise and attaches the round's frozen report PDFs (#359).
Either step failing leaves the document delivered and says so; its deliverer retries the step, which is no signing
moment: it finishes the delivery they signed and publishes nothing new.
"""

import hashlib
import json
from typing import Any

from audit.audits import TARGET
from fastapi import Body, Depends, Request
from fastapi.responses import JSONResponse
from product.feishu_writeback import (
    FeishuWritebackError,
    append_report_blocks,
    attach_report_pdfs,
    create_report_document,
    make_read_only,
    pending_attachments,
    report_blocks,
    report_page_url,
    round_return_url,
    writeback_state,
)
from product.mcp import McpError

from api import web_contract as wc
from api.access import object_denied
from api.signing import signing

RETRY_STEPS = ("read_only", "attachments")


def register_report_routes(app, store, session_of, http, settings):
    def originals(principal, round_id: str, body: dict) -> list[dict]:
        """Each audited file's frozen report PDF, in the round's order; the document stands on all of them."""
        pdfs = store.round_report_pdfs(principal, round_id)
        targets = [str(item.get("file_version_id") or "") for item in body.get("inputs") or [] if isinstance(item, dict) and item.get("role") == TARGET]
        if not targets or any(version not in pdfs for version in targets):
            raise FeishuWritebackError("report_pdf_not_ready")
        return [{**pdfs[version], "url": report_page_url(settings.canonical_web_url(), body, version)} for version in targets]

    def prepare(round_id, request):
        session = session_of(request)
        if not session:
            return None, None, None, None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        if denied := object_denied(store, session.principal, round_id):
            return None, None, None, None, denied
        # The same projection as GET /rounds/{id}, which the confirmation captured.
        body = store.get_round(round_id, principal=session.principal)
        if body is None:
            # The id names a draft or file of the enterprise, not a round.
            return None, None, None, None, JSONResponse({"error": "access_denied"}, status_code=403)
        try:
            if (state := writeback_state(body)) != "ready":
                raise FeishuWritebackError(state)
            pdfs = originals(session.principal, round_id, body)
            blocks = report_blocks(body, round_return_url(settings.canonical_web_url(), body), store.round_customer(round_id), pdfs)
            fingerprint = hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        except McpError as exc:
            return None, None, None, None, JSONResponse({"error": exc.code}, status_code=exc.status)
        except FeishuWritebackError as exc:
            return None, None, None, None, JSONResponse({"error": str(exc)}, status_code=409)
        return session, blocks, pdfs, fingerprint, None

    def attach(principal, token: str, document_id: str, ident: str, lease: str, items: list[dict]) -> None:
        by_id = {item["report_id"]: item["fingerprint"] for item in items}
        attach_report_pdfs(
            http,
            token,
            document_id,
            items,
            lambda report_id: store.report_pdf_body(principal, report_id, by_id[report_id]),
            lambda current: store.save_report_step(principal, ident, lease, attachments=current),
        )

    @app.get("/integrations/feishu/rounds/{round_id}/report", response_model=wc.ReportEnvelope)
    def get_report(round_id: str, request: Request):
        session, _blocks, _pdfs, fingerprint, denied = prepare(round_id, request)
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
        session, blocks, pdfs, fingerprint, denied = prepare(round_id, request)
        if denied is not None:
            return denied
        # The document the person saw is the one written: a changed one is shown to them again, not sent (#348).
        expected = body.get("if_match") if isinstance(body, dict) else None
        if expected is not None and expected != fingerprint:
            return JSONResponse({"error": "content_changed"}, status_code=409)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        principal, token = session.principal, session.access_token
        items = pending_attachments(pdfs)
        ident = store.claim_report(principal, round_id, fingerprint, action_id=getattr(request.state, "action_id", None), attachments=items)
        if ident is None:
            return {"report": store.get_report(principal, round_id, fingerprint)}
        try:
            document_id = create_report_document(http, token, "PPAP 审核报告 · " + round_id)
            store.settle_report(principal, ident, "writing", document_id)
            # Read-only for others before anything is in it; failing that, the document is still delivered and says so.
            store.save_report_step(principal, ident, ident, read_only=make_read_only(http, token, document_id))
            append_report_blocks(http, token, document_id, blocks, ident)
            store.settle_report(principal, ident, "ready")
        except FeishuWritebackError as exc:
            # Only a definite creation rejection can be explicitly retried.
            # A failed append may have left a real document, so it stays unknown.
            code = str(exc)
            state = "failed" if code.startswith("feishu_report_create_rejected:") else "unknown"
            store.settle_report(principal, ident, state, error_code=code)
            store.release_report(principal, ident, ident)
            return {"report": store.get_report(principal, round_id, fingerprint)}
        try:
            attach(principal, token, document_id, ident, ident, items)
        finally:
            store.release_report(principal, ident, ident)
        return {"report": store.get_report(principal, round_id, fingerprint)}

    @app.post("/integrations/feishu/rounds/{round_id}/report/retry", response_model=wc.ReportEnvelope, openapi_extra=wc.openapi_body(wc.ReportRetryRequest))
    def retry_report(round_id: str, request: Request, body: Any = Body(None)):
        """Retry setting a delivered document read-only, or attaching its report PDFs (#359). Only its deliverer does."""
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if denied := object_denied(store, session.principal, round_id):
            return denied
        ident, step = (body.get("report_id"), body.get("step")) if isinstance(body, dict) else (None, None)
        if not isinstance(ident, str) or step not in RETRY_STEPS:
            return JSONResponse({"error": "invalid_report_retry"}, status_code=400)
        principal = session.principal
        if store.report_export(principal, round_id, ident) is None:
            # Another person's delivery, or none: each person retries only their own.
            return JSONResponse({"error": "access_denied"}, status_code=403)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        held = store.lease_report(principal, round_id, ident)
        if held is None:
            return JSONResponse({"error": "report_retry_unavailable"}, status_code=409)
        lease, document_id = held["lease"], held["document_id"]
        try:
            if step == "read_only" and held["read_only"] != "set":
                store.save_report_step(principal, ident, lease, read_only=make_read_only(http, session.access_token, document_id))
            elif step == "attachments" and held["status"] == "ready":
                attach(principal, session.access_token, document_id, ident, lease, held["attachments"])
        finally:
            store.release_report(principal, ident, lease)
        return {"report": store.report_export(principal, round_id, ident)}
