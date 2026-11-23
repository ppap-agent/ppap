"""API-owned Feishu notification dispatch; audit Workers never receive its secrets."""

from __future__ import annotations

import logging
import threading
import uuid

import httpx
from product.customers import find
from product.feishu_notifications import rule_answers_card, rule_cards_card, send_task_notification
from product.feishu_usage import FeishuUsageError, tenant_access_token
from product.logs import emit
from product.settings import Settings
from product.store import AuthStore, rule_questionnaires


def dispatch_task_notification(store: AuthStore, http, settings: Settings, worker_ident: str, *, task_id: str | None = None) -> bool:
    if not (settings.feishu_notifications_enabled and settings.feishu_app_id and settings.feishu_app_secret):
        return False
    claimed = store.claim_task_notification(worker_ident, task_id=task_id)
    if not claimed:
        return False
    job, recipient = claimed
    try:
        token = tenant_access_token(http, settings.feishu_app_id, settings.feishu_app_secret, store.app_ticket(settings.feishu_app_id) or "", recipient.tenant_key)
    except FeishuUsageError:
        store.defer_task_notification(job, worker_ident)
        return True
    delivery = store.start_task_notification(job, worker_ident, recipient)
    if delivery:
        receipt = send_task_notification(http, token, job.id, delivery["receive_id"], delivery["card"])
        store.finish_task_notification(job, worker_ident, receipt)
    return True


def _send_rule_notice(store: AuthStore, http, settings: Settings, claimed: dict, card_of) -> None:
    """One send for a claimed rule notice, never repeated; a token that cannot be had fails it before any send."""
    try:
        token = tenant_access_token(http, settings.feishu_app_id, settings.feishu_app_secret, store.app_ticket(settings.feishu_app_id) or "", claimed["tenant_key"])
    except FeishuUsageError:
        rule_questionnaires.finish_notification(store, claimed, {"state": "failed", "message_id": None, "reason": "feishu_connection_unavailable"})
        return
    rule_questionnaires.finish_notification(store, claimed, send_task_notification(http, token, claimed["id"], claimed["receive_id"], card_of(claimed)))


def _customer_name(customer_id: str) -> str:
    registered = find(customer_id)
    return registered.name if registered else customer_id


def dispatch_card_notification(store: AuthStore, http, settings: Settings, worker_ident: str) -> bool:
    """New rule questions, to someone who answered that rulebook version before. One send per job, never repeated."""
    if not (settings.feishu_notifications_enabled and settings.feishu_app_id and settings.feishu_app_secret):
        return False
    claimed = rule_questionnaires.claim_card_notification(store, worker_ident)
    if claimed is None:
        return False
    web = settings.canonical_web_url()
    _send_rule_notice(store, http, settings, claimed, lambda c: rule_cards_card(_customer_name(c["customer_id"]), c["customer_id"], c["rulebook"], c["added"], web, c["questionnaire_id"]))
    return True


def dispatch_answer_notification(store: AuthStore, http, settings: Settings, worker_ident: str) -> bool:
    """Customer answers on a rulebook version being clarified, to whoever prepares it (#354). One send per job, never repeated."""
    if not (settings.feishu_notifications_enabled and settings.feishu_app_id and settings.feishu_app_secret):
        return False
    claimed = rule_questionnaires.claim_answer_notification(store, worker_ident)
    if claimed is None:
        return False
    web = settings.canonical_web_url()
    _send_rule_notice(store, http, settings, claimed, lambda c: rule_answers_card(_customer_name(c["customer_id"]), c, web))
    return True


def run_task_notifications(store: AuthStore, settings: Settings, stop: threading.Event) -> None:
    worker_ident = "api-notifier-" + str(uuid.uuid4())
    with httpx.Client() as http:
        while not stop.is_set():
            worked = False
            try:
                worked = dispatch_task_notification(store, http, settings, worker_ident)
            except Exception:
                # Never requeue after an uncertain send; the persisted dispatch boundary owns recovery.
                emit("notification_dispatch_unavailable", level=logging.WARNING)
            try:
                worked = dispatch_card_notification(store, http, settings, worker_ident) or worked
            except Exception:
                emit("rule_card_notification_unavailable", level=logging.WARNING)
            try:
                worked = dispatch_answer_notification(store, http, settings, worker_ident) or worked
            except Exception:
                emit("rule_answer_notification_unavailable", level=logging.WARNING)
            stop.wait(0.1 if worked else 5)
