"""Feishu Partner AI quota check and usage observations. Not a billing product."""
from __future__ import annotations

import json
from dataclasses import dataclass

from ppap_core.feishu_drive import OPEN_API

DEFAULT_FREE_TRIAL_QUOTA_POINTS = 10000
TRIAL_UNREPORTED = "none"
PARTNER_PENDING = "pending"
PARTNER_CONFIRMED = "confirmed"
PARTNER_FAILED = "failed"


class FeishuUsageError(Exception):
    pass


def quota_sufficient(enabled: bool | None, user_quota: int | None) -> bool:
    return bool(enabled) and int(user_quota or 0) > 0


def remaining_trial_points(free_quota: int, consumed: int) -> int:
    return max(0, int(free_quota) - max(0, int(consumed)))


def quota_messages(
    enabled: bool | None, user_quota: int | None, ai_package_purchased: bool | None, sufficient: bool
) -> tuple[str, str]:
    purchased = bool(ai_package_purchased)
    on = bool(enabled)
    quota = int(user_quota or 0)
    has_quota = quota > 0
    if sufficient:
        return (
            "AI额度充足",
            "功能已启用，当前可用额度为 %d 点，可以正常使用AI功能" % quota,
        )
    if not purchased:
        return ("未购买AI包", "未采购飞书AI包，请联系管理员")
    if not on:
        return ("AI功能未启用", "飞书AI包开关已关闭，请联系管理员启用AI功能后使用")
    if not has_quota:
        return ("AI额度已用完", "飞书AI包额度不足，请联系管理员")
    return ("AI额度异常", "AI功能状态异常，请联系管理员检查")


def tenant_ai_item_url(ai_item_id: str) -> str:
    return OPEN_API + "/partner_ai/v1/tenant_ai_items/" + ai_item_id


@dataclass(frozen=True)
class PartnerAiItem:
    enabled: bool
    user_quota: int
    ai_package_purchased: bool


def _response_json(response) -> dict:
    parser = getattr(response, "json", None)
    if callable(parser):
        try:
            payload = parser()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            return payload
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        try:
            payload = json.loads(text)
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def query_tenant_ai_item(http, access_token: str, ai_item_id: str) -> PartnerAiItem:
    item_id = (ai_item_id or "").strip()
    if not item_id:
        raise FeishuUsageError("feishu_ai_item_missing")
    try:
        response = http.get(
            tenant_ai_item_url(item_id),
            headers={
                "Authorization": "Bearer " + access_token,
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=20,
        )
        body = _response_json(response)
    except Exception as exc:
        raise FeishuUsageError("feishu_ai_item_failed") from exc
    status = int(getattr(response, "status_code", 0) or 0)
    if status < 200 or status >= 300 or body.get("code") not in (0, None):
        raise FeishuUsageError("feishu_ai_item_failed")
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    item = data.get("tenant_ai_item") if isinstance(data.get("tenant_ai_item"), dict) else {}
    if not item:
        raise FeishuUsageError("feishu_ai_item_failed")
    try:
        quota = int(item.get("user_quota") or 0)
    except (TypeError, ValueError):
        quota = 0
    return PartnerAiItem(bool(item.get("enabled")), quota, bool(item.get("ai_package_purchased")))


def quota_payload(item: PartnerAiItem, remaining_trial: int) -> dict[str, object]:
    sufficient = quota_sufficient(item.enabled, item.user_quota)
    message, detailed = quota_messages(
        item.enabled, item.user_quota, item.ai_package_purchased, sufficient
    )
    return {
        "enabled": item.enabled,
        "user_quota": item.user_quota,
        "ai_package_purchased": item.ai_package_purchased,
        "quota_sufficient": sufficient,
        "message": message,
        "detailed_message": detailed,
        "remaining_trial_ai_points": remaining_trial,
    }


def quota_unavailable(message: str, detailed: str) -> dict[str, object]:
    return {
        "enabled": False,
        "user_quota": 0,
        "ai_package_purchased": False,
        "quota_sufficient": False,
        "message": message,
        "detailed_message": detailed,
        "remaining_trial_ai_points": 0,
    }


TENANT_ACCESS_TOKEN_URL = OPEN_API + "/auth/v3/tenant_access_token"
CREDENTIAL_APPLY_URL = OPEN_API + "/partner_ai/v1/credential/apply"
USAGE_REPORT_URL = OPEN_API + "/partner_ai/v1/usage_records/report"


def _ok(response) -> bool:
    status = int(getattr(response, "status_code", 0) or 0)
    if status < 200 or status >= 300:
        return False
    body = _response_json(response)
    return body.get("code") in (0, None)


def tenant_access_token(http, app_id: str, app_secret: str, app_ticket: str) -> str:
    ticket = (app_ticket or "").strip()
    if not ticket:
        raise FeishuUsageError("feishu_app_ticket_missing")
    try:
        response = http.post(
            TENANT_ACCESS_TOKEN_URL,
            json={"app_id": app_id, "app_secret": app_secret, "app_ticket": ticket},
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=20,
        )
        body = _response_json(response)
    except Exception as exc:
        raise FeishuUsageError("feishu_tenant_token_failed") from exc
    token = str(body.get("tenant_access_token") or "").strip()
    if not _ok(response) or not token:
        raise FeishuUsageError("feishu_tenant_token_failed")
    return token


def apply_report_credential(http, user_token: str, ai_item_id: str, trace_id: str) -> str:
    try:
        response = http.post(
            CREDENTIAL_APPLY_URL,
            json={"ai_item_id": ai_item_id, "trace_id": trace_id},
            headers={
                "Authorization": "Bearer " + user_token,
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=20,
        )
        body = _response_json(response)
    except Exception as exc:
        raise FeishuUsageError("feishu_usage_report_failed") from exc
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    token = str(data.get("token") or data.get("report_token") or "").strip()
    if not _ok(response) or not token:
        raise FeishuUsageError("feishu_usage_report_failed")
    return token


def report_usage_record(
    http, tenant_token: str, report_token: str, ai_item_id: str, trace_id: str, points: int
) -> str:
    payload = {
        "report_token": report_token,
        "record": {
            "ai_item_id": ai_item_id,
            "trace_id": trace_id,
            "usage_amount": {"type": "fixed", "credit": int(points)},
        },
    }
    try:
        response = http.post(
            USAGE_REPORT_URL,
            json=payload,
            headers={
                "Authorization": "Bearer " + tenant_token,
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=20,
        )
        body = _response_json(response)
    except Exception as exc:
        raise FeishuUsageError("feishu_usage_report_failed") from exc
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    record_id = str(data.get("id") or "").strip()
    if not _ok(response) or not record_id:
        raise FeishuUsageError("feishu_usage_report_failed")
    return record_id


def report_partner_usage(http, user_token: str, tenant_token: str, event: dict) -> str:
    trace_id = str(event.get("trace_id") or "").strip()
    if not trace_id:
        raise FeishuUsageError("invalid_trace_id")
    report_token = apply_report_credential(http, user_token, event["ai_item_id"], trace_id)
    return report_usage_record(
        http,
        tenant_token,
        report_token,
        event["ai_item_id"],
        trace_id,
        int(event["consumed_points"]),
    )
