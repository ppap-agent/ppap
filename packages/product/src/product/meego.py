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
# A space opts in to a conclusion field by creating a single-select field with this alias or name.
CONCLUSION_FIELD_ALIAS = "ppap_conclusion"
CONCLUSION_FIELD_NAME = "PPAP 审核结论"
# Report PDFs go to their own attachment field, never to the field the package is read from.
REPORT_FIELD_ALIAS = "ppap_reports"
REPORT_FIELD_NAME = "PPAP 审核报告"
UPLOAD_TIMEOUT = httpx.Timeout(60.0, connect=20.0)


def _is_report_field(field: dict) -> bool:
    return field.get("field_alias") == REPORT_FIELD_ALIAS or field.get("field_name") == REPORT_FIELD_NAME


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

    def __init__(self, http: httpx.Client, settings: Settings, *, ai_node: bool = False):
        self.http = http
        self.settings = settings
        self.base = settings.meego_base_url.rstrip("/")
        # The AI node app is a separate plugin; only its own token may change its nodes.
        self.ai_node = ai_node

    @property
    def plugin_id(self) -> str:
        return self.settings.meego_ai_node_plugin_id if self.ai_node else self.settings.meego_plugin_id

    @property
    def plugin_secret(self) -> str:
        return self.settings.meego_ai_node_plugin_secret if self.ai_node else self.settings.meego_plugin_secret

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
            # Another space, a wrong space key or no rights all read as no access, never as a service fault.
            if code in (10001, 30005, 1000052062, 1000052063, 1000050255):
                raise MeegoError("meego_access_denied")
            raise MeegoError(f"meego_{stage}_failed")
        return payload

    def plugin_token(self) -> str:
        key = (self.base, self.plugin_id)
        with self._lock:
            cached = self._tokens.get(key)
            if cached and cached[1] > time.monotonic() + 60:
                return cached[0]
        try:
            response = self.http.post(
                self.base + "/open_api/authen/plugin_token",
                json={"plugin_id": self.plugin_id, "plugin_secret": self.plugin_secret, "type": 0},
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

    def exchange_code(self, code: str, profile: MeegoClient | None = None) -> MeegoUser:
        """Exchange a host auth code issued to this client's app; ``profile`` reads the person's profile when this app cannot."""
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
        person = (profile or self).user(user_key)
        return MeegoUser(user_key, tenant_key, person.get("name", ""), person.get("email", ""), person.get("out_id", ""))

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
            # Reports PPAP wrote back are results, not material to audit.
            if not isinstance(field, dict) or field.get("field_type_key") != "multi_file" or _is_report_field(field):
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

    def _put(self, path: str, body: dict, user_key: str, stage: str) -> dict:
        headers = {"X-Plugin-Token": self.plugin_token(), "X-User-Key": user_key, "Content-Type": "application/json"}
        try:
            response = self.http.put(self.base + path, json=body, headers=headers, timeout=TIMEOUT)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise MeegoError(f"meego_{stage}_failed") from None
        except httpx.HTTPError:
            raise MeegoError(f"meego_{stage}_failed", unknown=True) from None
        return self._json(response, stage)

    def conclusion_field(self, user_key: str, item: WorkItemRef) -> tuple[str, dict[str, str]] | None:
        """The space's single-select field for the PPAP conclusion and its option ids by label.

        A space opts in by creating it; it is found by its integration alias, else its name.
        """
        payload = self._post(f"/open_api/{item.project_key}/field/all", {"work_item_type_key": item.work_item_type_key}, user_key, "field")
        fields = [field for field in payload.get("data") or [] if isinstance(field, dict) and field.get("field_type_key") == "select"]
        chosen = next((f for f in fields if f.get("field_alias") == CONCLUSION_FIELD_ALIAS), None) or next((f for f in fields if f.get("field_name") == CONCLUSION_FIELD_NAME), None)
        if not chosen or not chosen.get("field_key"):
            return None
        options = {str(o.get("label") or ""): str(o.get("value") or "") for o in chosen.get("options") or [] if isinstance(o, dict) and o.get("is_disabled") != 1}
        return str(chosen["field_key"]), {label: value for label, value in options.items() if label and value}

    def field_option(self, user_key: str, item: WorkItemRef, field_key: str) -> str | None:
        """The option id a single-select field holds on this work item now, or None when empty."""
        payload = self._post(
            f"/open_api/{item.project_key}/work_item/{item.work_item_type_key}/query",
            {"work_item_ids": [int(item.work_item_id)], "fields": [field_key]},
            user_key,
            "work_item",
        )
        rows = payload.get("data") or []
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise MeegoError("meego_work_item_not_found")
        for field in rows[0].get("fields") or []:
            if isinstance(field, dict) and field.get("field_key") == field_key:
                value = field.get("field_value")
                if isinstance(value, dict):
                    value = value.get("value")
                return str(value) if value not in (None, "") else None
        return None

    def set_field(self, user_key: str, item: WorkItemRef, field_key: str, value: str, label: str) -> None:
        # Verified on a real space: a bare option id is rejected (20006 "field is illegal"); the option object is accepted.
        self._put(
            f"/open_api/{item.project_key}/work_item/{item.work_item_type_key}/{item.work_item_id}",
            {"update_fields": [{"field_key": field_key, "field_value": {"label": label, "value": value}}]},
            user_key,
            "field_update",
        )

    def report_field(self, user_key: str, item: WorkItemRef) -> str | None:
        """The space's attachment field for written-back reports; a space opts in by creating it."""
        payload = self._post(f"/open_api/{item.project_key}/field/all", {"work_item_type_key": item.work_item_type_key}, user_key, "field")
        fields = [field for field in payload.get("data") or [] if isinstance(field, dict) and field.get("field_type_key") == "multi_file"]
        chosen = next((f for f in fields if f.get("field_alias") == REPORT_FIELD_ALIAS), None) or next((f for f in fields if f.get("field_name") == REPORT_FIELD_NAME), None)
        return str(chosen["field_key"]) if chosen and chosen.get("field_key") else None

    def field_file_names(self, user_key: str, item: WorkItemRef, field_key: str) -> set[str]:
        """Names of the files an attachment field holds now."""
        payload = self._post(
            f"/open_api/{item.project_key}/work_item/{item.work_item_type_key}/query",
            {"work_item_ids": [int(item.work_item_id)], "fields": [field_key]},
            user_key,
            "work_item",
        )
        rows = payload.get("data") or []
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise MeegoError("meego_work_item_not_found")
        names: set[str] = set()
        for field in rows[0].get("fields") or []:
            if isinstance(field, dict) and field.get("field_key") == field_key:
                names.update(str(value.get("name")) for value in field.get("field_value") or [] if isinstance(value, dict) and value.get("name"))
        return names

    def upload_file(self, user_key: str, item: WorkItemRef, field_key: str, name: str, content: bytes, content_type: str) -> None:
        """Append one file to an attachment field."""
        headers = {"X-Plugin-Token": self.plugin_token(), "X-User-Key": user_key}
        url = f"{self.base}/open_api/{item.project_key}/work_item/{item.work_item_type_key}/{item.work_item_id}/file/upload"
        try:
            response = self.http.post(url, headers=headers, data={"field_key": field_key}, files={"file": (name, content, content_type)}, timeout=UPLOAD_TIMEOUT)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise MeegoError("meego_upload_failed") from None
        except httpx.HTTPError:
            raise MeegoError("meego_upload_failed", unknown=True) from None
        self._json(response, "upload")

    # AI node (only with the AI node app's client) ------------------------

    def ai_node_state(self, user_key: str, item: WorkItemRef, state_key: str) -> tuple[str, str]:
        """The node's flow state (e.g. DOING, PASSED) and its AI status (idle, running, done, stopped)."""
        body = {"project_key": item.project_key, "work_item_id": int(item.work_item_id), "state_key": state_key}
        data = self._post("/open_api/ai_node/query", body, user_key, "ai_node_query").get("data") or {}
        info = data.get("ai_info")
        status = info.get("status") if isinstance(info, dict) else ""
        return str(data.get("node_state") or ""), str(status or "")

    def ai_node_done(self, user_key: str, item: WorkItemRef, state_key: str) -> None:
        """Mark the node's AI work done; Meego accepts this only while it is running."""
        body = {"project_key": item.project_key, "work_item_id": int(item.work_item_id), "state_key": state_key, "status": "done"}
        self._post("/open_api/ai_node/edit", body, user_key, "ai_node_edit")

    def ai_node_complete(self, user_key: str, item: WorkItemRef, state_key: str) -> None:
        """Complete the node so the flow moves on; Meego accepts this only after the AI status is done."""
        body = {"project_key": item.project_key, "work_item_id": int(item.work_item_id), "state_key": state_key}
        self._post("/open_api/ai_node/confirm", body, user_key, "ai_node_confirm")

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
