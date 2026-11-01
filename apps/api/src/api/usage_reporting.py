"""Report pending obligations with the original user's live authorization.

A provider receipt is not a debit order. Unknown reports are never selected
again; reconciliation needs a separately verified provider readback contract.
"""

import logging
import threading

import httpx
from product.feishu_usage import FeishuUsageError, apply_report_credential, query_tenant_ai_item, report_usage_record, tenant_access_token
from product.logs import emit


def report_usage(store, http, settings, session, event_id):
    principal = session.principal
    row = store.usage_event(principal, event_id)
    if row is None:
        return {"error": "access_denied"}, 403
    public = store.usage_event_public(row)
    if str(row["user_id"]) != principal.user_id:
        return {"error": "usage_report_owner_required"}, 409
    if row["is_free_trial"]:
        return {**public, "skipped": True, "skip_reason": "消费在赠送额度内"}, 200
    if public["external_status"] == "confirmed":
        return {**public, "skipped": False}, 200
    if public["external_status"] != "pending":
        return public, 409
    if not settings.usage_charge_enabled:
        return {"error": "usage_charging_disabled"}, 409
    if not session.access_token:
        return {"error": "feishu_user_token_missing"}, 409
    if not row.get("trace_id"):
        return {"error": "usage_trace_missing"}, 409
    ticket = store.app_ticket(settings.feishu_app_id)
    if not ticket:
        return {"error": "feishu_app_ticket_missing"}, 409
    # All preparation is before the non-idempotent report boundary. Lack of
    # authorization or quota leaves the same obligation pending, not re-billed.
    try:
        partner = query_tenant_ai_item(http, session.access_token, row["ai_item_id"])
        if not partner.enabled or partner.user_quota <= 0:
            return {"error": "usage_balance_empty" if partner.enabled else "usage_ai_disabled"}, 409
        token = tenant_access_token(http, settings.feishu_app_id, settings.feishu_app_secret, ticket, principal.tenant_key)
        credential = apply_report_credential(http, session.access_token, row["ai_item_id"], row["trace_id"])
    except FeishuUsageError:
        return {"error": "feishu_usage_prepare_failed"}, 502
    claim = store.claim_usage_report(principal, event_id)
    if claim is None:
        return store.usage_event_public(store.usage_event(principal, event_id)), 409
    try:
        record_id = report_usage_record(http, token, credential, row["ai_item_id"], row["trace_id"], row["consumed_points"])
    except FeishuUsageError:
        store.settle_usage_external(principal, event_id, claim, "unknown")
        return {"error": "feishu_usage_report_unknown"}, 502
    settled = store.settle_usage_external(principal, event_id, claim, "confirmed", record_id)
    return {**store.usage_event_public(settled), "skipped": False}, 200


def dispatch_pending_usage(store, http, settings, *, stop: threading.Event | None = None):
    if not (settings.usage_charge_enabled and settings.feishu_app_id and settings.feishu_app_secret):
        return False
    worked = False
    for event_id, session in store.pending_usage_sessions(settings.feishu_app_id):
        if stop is not None and stop.is_set():
            break
        if not settings.tenant_allowed(session.principal.tenant_key):
            continue
        body, status = report_usage(store, http, settings, session, event_id)
        worked |= status == 200 or body.get("error") == "feishu_usage_report_unknown"
    return worked


def run_pending_usage(store, settings, stop: threading.Event):
    with httpx.Client() as http:
        while not stop.is_set():
            try:
                dispatch_pending_usage(store, http, settings, stop=stop)
            except Exception:
                emit("usage_reporting_unavailable", level=logging.WARNING)
            stop.wait(30)
