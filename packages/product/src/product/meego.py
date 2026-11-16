"""Feishu Project (Meego) OpenAPI client for the plugin host.

Every work-item call carries the plugin token plus the bound person's
``X-User-Key``, so Meego applies that person's own space and object rights.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx

from product.settings import Settings

TIMEOUT = httpx.Timeout(20.0)
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=20.0)
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


class MeegoError(Exception):
    """A Meego call failed. ``code`` is safe to return to the plugin."""

    def __init__(self, code: str, *, unknown: bool = False):
        super().__init__(code)
        self.code = code
        # True when the request may have reached Meego and its effect is not known.
        self.unknown = unknown


@dataclass(frozen=True)
class MeegoUser:
    user_key: str
    tenant_key: str
    name: str = ""
    email: str = ""
    out_id: str = ""


@dataclass(frozen=True)
class WorkItemRef:
    project_key: str
    work_item_type_key: str
    work_item_id: str

    def object_ref(self) -> str:
        return f"{self.project_key}/{self.work_item_type_key}/{self.work_item_id}"

    @classmethod
    def parse(cls, value: str) -> WorkItemRef:
        parts = str(value or "").split("/")
        if len(parts) != 3 or not all(_safe_segment(p) for p in parts) or not parts[2].isdigit():
            raise ValueError("invalid_work_item")
        return cls(*parts)


def _safe_segment(value: str) -> bool:
    return bool(value) and len(value) <= 128 and all(c.isalnum() or c in "_-" for c in value)


def work_item(project_key: str, work_item_type_key: str, work_item_id: str) -> WorkItemRef:
    return WorkItemRef.parse(f"{project_key}/{work_item_type_key}/{work_item_id}")


def _error_code(payload: dict) -> int | None:
    for key in ("err_code", "code"):
        if isinstance(payload.get(key), int):
            return payload[key]
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), int):
        return error["code"]
    return None


class MeegoClient:
    _lock = threading.Lock()
    _tokens: dict[tuple[str, str], tuple[str, float]] = {}

    def __init__(self, http: httpx.Client, settings: Settings):
        self.http = http
        self.settings = settings
        self.base = settings.meego_base_url.rstrip("/")

    def _json(self, response: httpx.Response, stage: str) -> dict:
        try:
            payload = response.json()
        except ValueError:
            raise MeegoError(f"meego_{stage}_failed", unknown=response.status_code >= 500) from None
        if not isinstance(payload, dict):
            raise MeegoError(f"meego_{stage}_failed")
        if response.status_code >= 500:
            raise MeegoError(f"meego_{stage}_failed", unknown=True)
        code = _error_code(payload)
        if response.status_code >= 400 or (code not in (None, 0)):
            if code in (10001, 30005, 1000052063, 1000050255):
                raise MeegoError("meego_access_denied")
            raise MeegoError(f"meego_{stage}_failed")
        return payload

    def plugin_token(self) -> str:
        key = (self.base, self.settings.meego_plugin_id)
        with self._lock:
            cached = self._tokens.get(key)
            if cached and cached[1] > time.monotonic() + 60:
                return cached[0]
        try:
            response = self.http.post(
                self.base + "/open_api/authen/plugin_token",
                json={"plugin_id": self.settings.meego_plugin_id, "plugin_secret": self.settings.meego_plugin_secret, "type": 0},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError:
            raise MeegoError("meego_unavailable") from None
        data = self._json(response, "plugin_token").get("data") or {}
        token = str(data.get("token") or "")
        if not token:
            raise MeegoError("meego_plugin_token_failed")
        expire = float(data.get("expire_time") or 7200)
        with self._lock:
            self._tokens[key] = (token, time.monotonic() + expire)
        return token

    def _post(self, path: str, body: dict, user_key: str, stage: str, *, timeout=TIMEOUT) -> dict:
        headers = {"X-Plugin-Token": self.plugin_token(), "X-User-Key": user_key, "Content-Type": "application/json"}
        try:
            response = self.http.post(self.base + path, json=body, headers=headers, timeout=timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise MeegoError(f"meego_{stage}_failed") from None
        except httpx.HTTPError:
            raise MeegoError(f"meego_{stage}_failed", unknown=True) from None
        return self._json(response, stage)

    def exchange_code(self, code: str) -> MeegoUser:
        try:
            response = self.http.post(
                self.base + "/open_api/authen/user_plugin_token",
                json={"code": code, "grant_type": "authorization_code"},
                headers={"X-Plugin-Token": self.plugin_token()},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError:
            raise MeegoError("meego_unavailable") from None
        data = self._json(response, "auth").get("data") or {}
        user_key = str(data.get("user_key") or "").strip()
        tenant_key = str(data.get("saas_tenant_key") or "").strip()
        if not user_key or not tenant_key:
            raise MeegoError("meego_auth_failed")
        profile = self.user(user_key)
        return MeegoUser(user_key, tenant_key, profile.get("name", ""), profile.get("email", ""), profile.get("out_id", ""))

    def user(self, user_key: str) -> dict[str, str]:
        payload = self._post("/open_api/user/query", {"user_keys": [user_key]}, user_key, "user")
        rows = payload.get("data") or []
        row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
        status = str(row.get("status") or "")
        if status and status != "activated":
            raise MeegoError("meego_user_inactive")
        raw_names = row.get("name")
        names: dict = raw_names if isinstance(raw_names, dict) else {}
        name = str(row.get("name_cn") or names.get("zh_cn") or names.get("default") or row.get("name_en") or "")
        return {"name": name, "email": str(row.get("email") or ""), "out_id": str(row.get("out_id") or "")}

    def work_item_files(self, user_key: str, item: WorkItemRef) -> dict:
        payload = self._post(
            f"/open_api/{item.project_key}/work_item/{item.work_item_type_key}/query",
            {"work_item_ids": [int(item.work_item_id)]},
            user_key,
            "work_item",
        )
        rows = payload.get("data") or []
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise MeegoError("meego_work_item_not_found")
        row = rows[0]
        files = []
        for field in row.get("fields") or []:
            if not isinstance(field, dict) or field.get("field_type_key") != "multi_file":
                continue
            for value in field.get("field_value") or []:
                if isinstance(value, dict) and value.get("uid") and value.get("name"):
                    files.append(
                        {
                            "uid": str(value["uid"]),
                            "name": str(value["name"]),
                            "size": str(value.get("size") or ""),
                            "content_type": str(value.get("type") or ""),
                            "field_key": str(field.get("field_key") or ""),
                        }
                    )
        return {"name": str(row.get("name") or ""), "files": files}

    def download(self, user_key: str, item: WorkItemRef, uid: str) -> bytes:
        headers = {"X-Plugin-Token": self.plugin_token(), "X-User-Key": user_key, "Content-Type": "application/json"}
        url = f"{self.base}/open_api/{item.project_key}/work_item/{item.work_item_type_key}/{item.work_item_id}/file/download"
        try:
            with self.http.stream("POST", url, json={"uuid": uid}, headers=headers, timeout=DOWNLOAD_TIMEOUT) as response:
                if response.status_code >= 400 or "application/json" in response.headers.get("content-type", ""):
                    response.read()
                    self._json(response, "download")
                    raise MeegoError("meego_download_failed")
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise MeegoError("file_too_large")
                    chunks.append(chunk)
        except httpx.HTTPError:
            raise MeegoError("meego_download_failed") from None
        return b"".join(chunks)

    def _comment_object(self, item: WorkItemRef) -> dict:
        return {"type": "WORKITEM", "work_item_type_key": item.work_item_type_key, "work_item_id": item.work_item_id}

    def comment_texts(self, user_key: str, item: WorkItemRef, *, pages: int = 5) -> list[str]:
        texts: list[str] = []
        cursor = ""
        for _ in range(pages):
            payload = self._post(
                "/open_api/comment/query",
                {"object": self._comment_object(item), "project_key": item.project_key, "paginator": {"direction": "DESC", "cursor": cursor, "page_size": 50}},
                user_key,
                "comment_query",
            )
            data = payload.get("data") or {}
            for comment in data.get("comments") or []:
                if not isinstance(comment, dict) or comment.get("status") == "DELETED":
                    continue
                rich = (comment.get("content") or {}).get("rich_text") or {}
                texts.append(str(rich.get("doc_text") or ""))
            cursor = str(data.get("next_cursor") or "")
            if not cursor:
                break
        return texts

    def add_comment(self, user_key: str, item: WorkItemRef, paragraphs: list[str]) -> str:
        rich = [{"type": "paragraph", "content": [{"type": "text", "text": text}]} for text in paragraphs if text]
        payload = self._post(
            "/open_api/comment/create",
            {"object": self._comment_object(item), "project_key": item.project_key, "content": {"type": "RICHTEXT", "rich_text": rich}},
            user_key,
            "comment",
        )
        return str((payload.get("data") or {}).get("comment_id") or "")
