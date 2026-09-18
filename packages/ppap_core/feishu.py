"""Feishu store-app event and OAuth helpers. Application identity is not email."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from ppap_core.settings import Settings

AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"


@dataclass(frozen=True)
class Challenge:
    value: str


@dataclass(frozen=True)
class AppTicket:
    value: str


@dataclass(frozen=True)
class EncryptedEvent:
    pass


@dataclass(frozen=True)
class UnknownEvent:
    pass


def callback_url(settings: Settings) -> str:
    return settings.public_api_url.rstrip("/") + "/login/feishu/callback"


def authorize_url(settings: Settings, state: str) -> str:
    params = {
        "client_id": settings.feishu_app_id,
        "response_type": "code",
        "redirect_uri": callback_url(settings),
        "state": state,
    }
    return AUTHORIZE_URL + "?" + urlencode(params)


def parse_event(body: dict) -> Challenge | AppTicket | EncryptedEvent | UnknownEvent:
    if not isinstance(body, dict):
        return UnknownEvent()
    if body.get("type") == "url_verification" or "challenge" in body and not body.get("event"):
        challenge = body.get("challenge")
        if isinstance(challenge, str) and challenge:
            return Challenge(challenge)
    if body.get("encrypt") and not body.get("event"):
        return EncryptedEvent()
    event = body.get("event") if isinstance(body.get("event"), dict) else {}
    header = body.get("header") if isinstance(body.get("header"), dict) else {}
    ticket = event.get("app_ticket")
    event_type = header.get("event_type") or event.get("type") or body.get("type")
    if event_type == "app_ticket" and isinstance(ticket, str) and ticket.strip():
        return AppTicket(ticket.strip())
    return UnknownEvent()


class FeishuError(Exception):
    pass


def exchange_code(http, settings: Settings, code: str) -> str:
    response = http.post(
        TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "client_id": settings.feishu_app_id,
            "client_secret": settings.feishu_app_secret,
            "code": code,
            "redirect_uri": callback_url(settings),
        },
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=20,
    )
    payload = response.json()
    token = payload.get("access_token")
    if response.status_code >= 400 or payload.get("code") not in (0, None) or not token:
        raise FeishuError("token_exchange_failed")
    return token


def user_info(http, user_access_token: str) -> dict[str, str]:
    response = http.get(
        USER_INFO_URL,
        headers={"Authorization": "Bearer " + user_access_token, "Content-Type": "application/json"},
        timeout=20,
    )
    payload = response.json()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    tenant_key = str(data.get("tenant_key") or data.get("tenantKey") or "").strip()
    subject = str(data.get("open_id") or data.get("openId") or "").strip()
    if response.status_code >= 400 or not tenant_key or not subject:
        raise FeishuError("user_info_missing_tenant_or_subject")
    return {
        "tenant_key": tenant_key,
        "subject": subject,
        "display_name": str(data.get("name") or ""),
    }
