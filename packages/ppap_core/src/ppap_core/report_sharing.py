"""Feishu report delivery. A missing receipt never authorizes another send."""

import json
from collections.abc import Mapping
from time import monotonic
from urllib.parse import quote

import httpx

BASE = "https://open.feishu.cn/open-apis"
# Documented HTTP-400 rejections, not '230049: message is being sent'. Unknown
# codes, malformed success and transport failures retain delivery responsibility.
REJECTED = {
    230001,
    230002,
    230006,
    230013,
    230015,
    230017,
    230018,
    230019,
    230020,
    230022,
    230025,
    230027,
    230028,
    230029,
    230034,
    230035,
    230036,
    230038,
    230053,
    230054,
    230055,
    230075,
    230099,
    232009,
}


class ReportSharingError(Exception):
    pass


class ReportDirectoryHttp:
    """One directory read shares a budget; it never changes delivery policy.

    HTTPX phase timeouts do not cancel a transport at a hard wall-clock limit.
    Check again after it returns so a late response cannot verify a recipient.
    """

    def __init__(self, http):
        self.http = http
        self.deadline = monotonic() + 30.0
        self.timed_out = False

    def check(self):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            self.timed_out = True
            raise ReportSharingError("share_directory_timeout")
        return remaining

    def _request(self, method, url, kwargs):
        remaining = self.check()
        kwargs["timeout"] = min(kwargs.get("timeout", remaining), remaining)
        try:
            response = getattr(self.http, method)(url, **kwargs)
        except Exception:
            self.check()
            raise
        self.check()
        return response

    def get(self, url, **kwargs):
        return self._request("get", url, kwargs)

    def post(self, url, **kwargs):
        return self._request("post", url, kwargs)


def _get(http, token, path, params=None):
    try:
        response = http.get(BASE + path, headers={"Authorization": "Bearer " + token}, params=params, timeout=20)
        body = response.json()
        if isinstance(http, ReportDirectoryHttp):
            http.check()
        if response.status_code == 400 and isinstance(body, dict) and body.get("code") == 99991672:
            raise ReportSharingError("share_directory_permission_missing")
        if not 200 <= response.status_code < 300 or not isinstance(body, dict) or type(body.get("code")) is not int or body["code"] != 0 or not isinstance(body.get("data"), dict):
            raise ReportSharingError("share_directory_unavailable")
        return body["data"]
    except (httpx.HTTPError, ValueError) as error:
        raise ReportSharingError("share_directory_unavailable") from error


def resolve_report_target(http, token: str, organization_id: str, reference: Mapping) -> dict:
    target = {**reference, "display_name": reference["id"], "organization_id": organization_id, "eligibility": "unknown", "reason": "暂时无法核对接收对象的组织及可用状态。"}
    try:
        if reference["kind"] == "group":
            group = _get(http, token, "/im/v1/chats/" + quote(reference["id"], safe=""))
            if isinstance(group.get("name"), str) and group["name"].strip():
                target["display_name"] = group["name"]
            if group.get("external") is True or isinstance(group.get("tenant_key"), str) and group["tenant_key"] != organization_id:
                target.update(eligibility="blocked", reason="只能分享给当前组织的内部群。")
            elif group.get("external") is False and group.get("tenant_key") == organization_id and group.get("chat_mode") in {"group", "topic"}:
                target.update(eligibility="eligible", reason=None)
        else:
            user = _get(http, token, "/contact/v3/users/" + quote(reference["id"], safe=""), {"user_id_type": "open_id"}).get("user")
            if not isinstance(user, dict) or user.get("open_id") != reference["id"]:
                return target
            target["reason"] = "未取得完整的成员姓名或账号状态，请检查通讯录字段权限与数据范围。"
            if not isinstance(user.get("name"), str) or not user["name"].strip():
                return target
            target["display_name"] = user["name"]
            status = user.get("status")
            if not isinstance(status, dict):
                return target
            blocked = ("is_resigned", "is_frozen", "is_exited", "is_unjoin")
            if any(status.get(key) is True for key in blocked) or status.get("is_activated") is False:
                target.update(eligibility="blocked", reason="该用户当前不可接收报告。")
            elif status.get("is_activated") is True and all(status.get(key) is False for key in blocked):
                target.update(eligibility="eligible", reason=None)
    except ReportSharingError as error:
        if str(error) == "share_directory_permission_missing":
            target["reason"] = "当前组织尚未授权所需的飞书目录权限，接收资格未确认。"
        elif str(error) == "share_directory_timeout":
            target["reason"] = "读取接收对象超时，接收资格尚未确认，请重新核对。"
    return target


def report_share_profile(http, token: str, principal) -> dict:
    profile = _get(http, token, "/authen/v1/user_info")
    if profile.get("tenant_key") != principal.tenant_key or profile.get("open_id") != principal.subject:
        raise ReportSharingError("share_identity_mismatch")
    return profile


def own_report_target(principal, profile: dict) -> dict:
    # A fresh user_info response proves the caller's own organization without
    # requiring access to the whole tenant directory merely to send to self.
    if profile.get("tenant_key") != principal.tenant_key or profile.get("open_id") != principal.subject:
        raise ReportSharingError("share_identity_mismatch")
    return {"id": principal.subject, "kind": "user", "display_name": profile.get("name") or principal.display_name, "organization_id": principal.tenant_key, "eligibility": "eligible", "reason": None}


def list_report_targets(http, user_token: str, tenant_token: str, principal, profile: dict, kind: str, query: str, page_token: str | None) -> dict:
    params: dict[str, str | int] = {"page_size": 20}
    if page_token:
        params["page_token"] = page_token
    if query:
        params["query"] = query
    if kind == "user" and not query:
        # Search requires a nonempty query. Only the freshly verified caller can
        # be offered without a directory search; no guessed organization list.
        targets = [own_report_target(principal, profile)]
        next_page = None
    else:
        path = "/search/v1/user" if kind == "user" else "/im/v1/chats/search" if query else "/im/v1/chats"
        # A bot can deliver only to chats it belongs to. Do not request access
        # to the caller's chats just to offer groups where the bot cannot send.
        data = _get(http, user_token if kind == "user" else tenant_token, path, params)
        rows = data.get("users" if kind == "user" else "items")
        if not isinstance(rows, list) or len(rows) > 20 or type(data.get("has_more")) is not bool:
            raise ReportSharingError("share_directory_unavailable")
        ids = [row.get("open_id" if kind == "user" else "chat_id") for row in rows if isinstance(row, dict)]
        if len(ids) != len(rows) or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in ids) or len(set(ids)) != len(ids):
            raise ReportSharingError("share_directory_unavailable")
        targets = [resolve_report_target(http, tenant_token, principal.tenant_key, {"id": value, "kind": kind}) for value in ids]
        next_page = data.get("page_token") if data["has_more"] else None
        if data["has_more"] and (not isinstance(next_page, str) or not next_page.strip()):
            raise ReportSharingError("share_directory_unavailable")
    return {"organization_id": principal.tenant_key, "organization_name": "当前飞书组织", "targets": targets, "next_page_token": next_page}


def upload_report_message_file(http, token: str, artifact: dict, body: bytes) -> str:
    if not body or len(body) > 30 * 1024 * 1024:
        raise ReportSharingError("share_file_too_large")
    try:
        response = http.post(
            BASE + "/im/v1/files",
            headers={"Authorization": "Bearer " + token},
            data={"file_type": "pdf" if artifact["content_type"] == "application/pdf" else "stream", "file_name": artifact["filename"]},
            files={"file": (artifact["filename"], body, artifact["content_type"])},
            timeout=60,
        )
        data = response.json()
        if response.status_code == 400 and isinstance(data, dict) and data.get("code") == 99991672:
            raise ReportSharingError("share_upload_permission_missing")
        key = data.get("data", {}).get("file_key") if isinstance(data, dict) and isinstance(data.get("data"), dict) else None
        if not 200 <= response.status_code < 300 or not isinstance(data, dict) or type(data.get("code")) is not int or data["code"] != 0 or not isinstance(key, str) or not key.strip():
            raise ReportSharingError("share_upload_unconfirmed")
        return key
    except (httpx.HTTPError, ValueError) as error:
        raise ReportSharingError("share_upload_unconfirmed") from error


def send_report_message(http, token: str, target: dict, message_uuid: str, message_type: str, content: dict) -> dict:
    unknown = {"phase": "unknown", "reason": "发送结果尚未确认，请勿重新发送。", "message_id": None}
    try:
        response = http.post(
            BASE + "/im/v1/messages",
            headers={"Authorization": "Bearer " + token},
            params={"receive_id_type": "open_id" if target["kind"] == "user" else "chat_id"},
            json={"receive_id": target["id"], "msg_type": message_type, "content": json.dumps(content, ensure_ascii=False), "uuid": message_uuid},
            timeout=30,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return unknown
    if not isinstance(body, dict) or type(body.get("code")) is not int:
        return unknown
    data = body.get("data")
    message_id = data.get("message_id") if isinstance(data, dict) else None
    if 200 <= response.status_code < 300 and body["code"] == 0 and isinstance(message_id, str) and message_id.strip():
        return {"phase": "sent", "reason": None, "message_id": message_id}
    if response.status_code == 400 and body["code"] in REJECTED:
        return {"phase": "failed", "reason": "飞书拒绝了本次发送，请检查机器人能力、可用范围及接收对象。", "message_id": None}
    return unknown
