"""Feishu store-app event and OAuth helpers. Application identity is not email."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from ppap_core.identity import constant_time_equal
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


@dataclass(frozen=True)
class UnauthenticatedEvent:
    pass


@dataclass(frozen=True)
class AppMismatch:
    pass


@dataclass(frozen=True)
class UserTokens:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


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


def event_token(body: dict) -> str:
    header = body.get("header") if isinstance(body.get("header"), dict) else {}
    token = header.get("token") or body.get("token") or ""
    return token.strip() if isinstance(token, str) else ""


def event_app_id(body: dict) -> str:
    header = body.get("header") if isinstance(body.get("header"), dict) else {}
    event = body.get("event") if isinstance(body.get("event"), dict) else {}
    app_id = header.get("app_id") or event.get("app_id") or ""
    return app_id.strip() if isinstance(app_id, str) else ""


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


def accept_event(
    settings: Settings, body: dict
) -> Challenge | AppTicket | EncryptedEvent | UnknownEvent | UnauthenticatedEvent | AppMismatch:
    """Feishu Verification Token plus app_id binding. Not a generic webhook framework."""
    parsed = parse_event(body)
    if isinstance(parsed, (EncryptedEvent, UnknownEvent)):
        return parsed
    expected = settings.feishu_verification_token.strip()
    got = event_token(body)
    if not expected or not got or not constant_time_equal(got, expected):
        return UnauthenticatedEvent()
    if isinstance(parsed, AppTicket):
        app_id = event_app_id(body)
        if not app_id or app_id != settings.feishu_app_id:
            return AppMismatch()
    return parsed


class FeishuError(Exception):
    pass


def _seconds(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _user_tokens(http, payload_body: dict) -> UserTokens:
    response = http.post(
        TOKEN_URL,
        json=payload_body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=20,
    )
    payload = response.json()
    token = payload.get("access_token")
    if response.status_code >= 400 or payload.get("code") not in (0, None) or not token:
        raise FeishuError("token_exchange_failed")
    now = datetime.now(timezone.utc)
    access_expires = now + timedelta(seconds=_seconds(payload.get("expires_in")))
    refresh = payload.get("refresh_token") if isinstance(payload.get("refresh_token"), str) else ""
    if refresh:
        refresh_expires = now + timedelta(seconds=_seconds(payload.get("refresh_token_expires_in")))
    else:
        refresh_expires = access_expires
    return UserTokens(str(token), refresh, access_expires, refresh_expires)


def exchange_code(http, settings: Settings, code: str) -> UserTokens:
    return _user_tokens(
        http,
        {
            "grant_type": "authorization_code",
            "client_id": settings.feishu_app_id,
            "client_secret": settings.feishu_app_secret,
            "code": code,
            "redirect_uri": callback_url(settings),
        },
    )


def refresh_user_token(http, settings: Settings, refresh_token: str) -> UserTokens:
    if not refresh_token:
        raise FeishuError("refresh_token_missing")
    return _user_tokens(
        http,
        {
            "grant_type": "refresh_token",
            "client_id": settings.feishu_app_id,
            "client_secret": settings.feishu_app_secret,
            "refresh_token": refresh_token,
        },
    )


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
