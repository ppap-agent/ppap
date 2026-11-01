"""API-owned Feishu notification dispatch; audit Workers never receive its secrets."""

from __future__ import annotations

import logging
import threading
import uuid

import httpx
from product.feishu_notifications import send_task_notification
from product.feishu_usage import FeishuUsageError, query_tenant_ai_item, tenant_access_token
from product.logs import emit
from product.settings import Settings
from product.store import AuthStore


def dispatch_task_confirmation(store: AuthStore, http, settings: Settings, worker_ident: str) -> bool:
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return False
    claimed = store.claim_task_confirmation(worker_ident)
    if claimed is None:
        return False

    def quota_for_actor(principal):
        token = tenant_access_token(http, settings.feishu_app_id, settings.feishu_app_secret, store.app_ticket(settings.feishu_app_id) or "", principal.tenant_key)
        return query_tenant_ai_item(http, token, settings.feishu_ai_item_id, subject=principal.subject)

    store.process_task_confirmation(claimed, worker_ident, usage_policy=quota_for_actor)
    return True


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


def run_task_notifications(store: AuthStore, settings: Settings, stop: threading.Event) -> None:
    worker_ident = "api-notifier-" + str(uuid.uuid4())
    with httpx.Client() as http:
        while not stop.is_set():
            worked = False
            try:
                worked = dispatch_task_confirmation(store, http, settings, worker_ident)
            except Exception:
                # The leased command owns recovery; do not create another acceptance identity.
                emit("card_confirmation_dispatch_unavailable", level=logging.WARNING)
            try:
                worked = dispatch_task_notification(store, http, settings, worker_ident) or worked
            except Exception:
                # Never requeue after an uncertain send; the persisted dispatch boundary owns recovery.
                emit("notification_dispatch_unavailable", level=logging.WARNING)
            stop.wait(0.1 if worked else 5)
