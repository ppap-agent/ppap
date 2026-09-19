"""Feishu Drive/Docs fetch plan and download/export. Not a Feishu-only file ledger."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable
from urllib.parse import unquote

from ppap_core.files import MAX_UPLOAD_BYTES, original_filename, safe_content_type

OPEN_API = "https://open.feishu.cn/open-apis"
META_TYPES = ("doc", "docx", "sheet", "bitable", "mindnote", "file", "folder")
MAX_DOWNLOAD_ATTEMPTS = 3
RETRY_SLEEP_MS = 2000
EXPORT_POLL_ATTEMPTS = 30
EXPORT_POLL_SLEEP_MS = 2000

Sleeper = Callable[[int], None]


class FetchMode(Enum):
    DRIVE_FILE = "drive_file"
    EXPORT = "export"
    EXPORT_WITH_DOCX_LINKS = "export_with_docx_links"


class UnsupportedDocType(ValueError):
    pass


class FeishuMetaError(Exception):
    pass


class FeishuExportError(Exception):
    pass


class PermanentFailure(Exception):
    pass


@dataclass(frozen=True)
class FetchPlan:
    doc_type: str
    mode: FetchMode

    @classmethod
    def for_doc_type(cls, doc_type: str | None) -> FetchPlan:
        if doc_type is None or not str(doc_type).strip():
            raise UnsupportedDocType("docType is required from Feishu meta")
        kind = str(doc_type).strip()
        if kind == "file":
            return cls(kind, FetchMode.DRIVE_FILE)
        if kind in {"sheet", "bitable", "doc"}:
            return cls(kind, FetchMode.EXPORT)
        if kind == "docx":
            return cls(kind, FetchMode.EXPORT_WITH_DOCX_LINKS)
        raise UnsupportedDocType("unsupported docType: " + kind)

    def scans_docx_links(self) -> bool:
        return self.mode is FetchMode.EXPORT_WITH_DOCX_LINKS

    def downloads_drive_file(self) -> bool:
        return self.mode is FetchMode.DRIVE_FILE

    def main_fetch_urls(self, file_token: str) -> list[str]:
        urls: list[str] = []
        if self.scans_docx_links():
            urls.append(docx_blocks_url(file_token))
        if self.downloads_drive_file():
            urls.append(files_download_url(file_token))
        else:
            urls.append(export_tasks_url())
        return urls


def files_download_url(file_token: str) -> str:
    return OPEN_API + "/drive/v1/files/" + file_token + "/download"


def export_tasks_url() -> str:
    return OPEN_API + "/drive/v1/export_tasks"


def export_task_url(ticket: str, file_token: str) -> str:
    return OPEN_API + "/drive/v1/export_tasks/" + ticket + "?token=" + file_token


def export_file_download_url(exported_file_token: str) -> str:
    return OPEN_API + "/drive/v1/export_tasks/file/" + exported_file_token + "/download"


def docx_blocks_url(file_token: str) -> str:
    return OPEN_API + "/docx/v1/documents/" + file_token + "/blocks"


def metas_batch_query_url() -> str:
    return OPEN_API + "/drive/v1/metas/batch_query"


def export_extension(doc_type: str) -> str:
    if doc_type in {"sheet", "bitable"}:
        return "xlsx"
    return "docx"


def retryable_status(status: int) -> bool:
    return status == 408 or status == 429 or status >= 500


def format_failure(status: int, body: str | None) -> str:
    snippet = re.sub(r"\s+", " ", body or "").strip()
    if len(snippet) > 200:
        snippet = snippet[:200]
    if not snippet:
        return "飞书下载失败 HTTP " + str(status)
    return "飞书下载失败 HTTP " + str(status) + ": " + snippet


def format_io_failure(message: str | None) -> str:
    snippet = (message or "").strip()
    if not snippet:
        return "飞书下载失败 IO"
    return "飞书下载失败 IO: " + snippet


@dataclass(frozen=True)
class Attempt:
    value: object | None = None
    status: int = 0
    body: str | None = None
    io_error: bool = False
    io_message: str | None = None

    @classmethod
    def ok(cls, value: object) -> Attempt:
        return cls(value=value, status=200)

    @classmethod
    def http(cls, status: int, body: str | None) -> Attempt:
        return cls(status=status, body=body)

    @classmethod
    def io(cls, message: str | None) -> Attempt:
        return cls(io_error=True, io_message=message)

    def success(self) -> bool:
        return self.value is not None

    def retryable(self) -> bool:
        if self.success():
            return False
        if self.io_error:
            return True
        return retryable_status(self.status)

    def failure_message(self) -> str:
        if self.io_error:
            return format_io_failure(self.io_message)
        return format_failure(self.status, self.body)


def retry(factory, max_attempts: int, sleep_ms: int, sleeper: Sleeper | None):
    if max_attempts < 1:
        raise ValueError("maxAttempts")
    last: Attempt | None = None
    sleep = sleeper or (lambda millis: time.sleep(millis / 1000.0))
    for attempt in range(1, max_attempts + 1):
        try:
            last = factory(attempt)
        except Exception as exc:
            last = Attempt.io(str(exc) or None)
        if last is not None and last.success():
            return last.value
        more = attempt < max_attempts and last is not None and last.retryable()
        if not more:
            raise PermanentFailure(format_io_failure(None) if last is None else last.failure_message())
        sleep(sleep_ms)
    raise PermanentFailure(format_io_failure(None) if last is None else last.failure_message())


def filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    marker = "filename*=UTF-8''"
    start = header.find(marker)
    if start != -1:
        encoded = header[start + len(marker) :]
        try:
            return unquote(encoded)
        except Exception:
            return encoded
    start = header.find('filename="')
    if start != -1:
        begin = start + 10
        end = header.find('"', begin)
        if end != -1:
            return header[begin:end]
    return None


@dataclass(frozen=True)
class DriveMeta:
    doc_type: str
    name: str
    token: str
    size: int | None = None


@dataclass(frozen=True)
class ImportedMaterial:
    data: bytes
    filename: str
    content_type: str
    doc_type: str
    file_token: str


def _auth_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer " + access_token,
        "Content-Type": "application/json; charset=utf-8",
    }


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
    body = getattr(response, "content", b"") or b""
    if body:
        try:
            payload = json.loads(body)
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _response_text(response) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    body = getattr(response, "content", b"") or b""
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace")
    return str(body or "")


def _response_headers(response) -> dict[str, str]:
    headers = getattr(response, "headers", None) or {}
    try:
        return {str(key).lower(): str(value) for key, value in headers.items()}
    except Exception:
        return {}


def _response_bytes(response) -> bytes:
    body = getattr(response, "content", None)
    if body is None:
        read = getattr(response, "read", None)
        if callable(read):
            body = read()
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    return b""


def try_get_file_meta(http, access_token: str, file_token: str, doc_type: str) -> DriveMeta | None:
    payload = {
        "request_docs": [{"doc_token": file_token, "doc_type": doc_type}],
        "with_url": False,
    }
    try:
        response = http.post(
            metas_batch_query_url(),
            json=payload,
            headers=_auth_headers(access_token),
            timeout=20,
        )
        body = _response_json(response)
    except Exception:
        return None
    if body.get("code") not in (0, None):
        return None
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    failed = data.get("failed_list") if isinstance(data.get("failed_list"), list) else []
    for item in failed:
        if isinstance(item, dict) and item.get("doc_token") == file_token:
            return None
    metas = data.get("metas") if isinstance(data.get("metas"), list) else []
    if not metas or not isinstance(metas[0], dict):
        return None
    row = metas[0]
    kind = str(row.get("doc_type") or "").strip()
    name = str(row.get("title") or "").strip()
    token = str(row.get("doc_token") or file_token).strip()
    if not kind:
        return None
    size = row.get("size")
    try:
        parsed_size = int(size) if size is not None else None
    except (TypeError, ValueError):
        parsed_size = None
    return DriveMeta(kind, name or token, token, parsed_size)


def get_file_meta(http, access_token: str, file_token: str, doc_type: str | None = None) -> DriveMeta:
    order: list[str] = []
    hinted = (doc_type or "").strip()
    if hinted:
        order.append(hinted)
    for kind in META_TYPES:
        if kind not in order:
            order.append(kind)
    for kind in order:
        meta = try_get_file_meta(http, access_token, file_token, kind)
        if meta is not None:
            return meta
    raise FeishuMetaError("feishu_meta_failed")


def _choose_filename(header_name: str | None, meta_name: str, doc_type: str, fallback: str) -> str:
    candidate = (header_name or "").strip() or (meta_name or "").strip() or fallback
    if not candidate:
        candidate = fallback
    if "." not in candidate and not FetchPlan.for_doc_type(doc_type).downloads_drive_file():
        candidate = candidate + "." + export_extension(doc_type)
    try:
        return original_filename(candidate)
    except ValueError:
        return original_filename(fallback)


def _download_attempt(http, url: str, access_token: str, attempt: int) -> Attempt:
    del attempt
    try:
        response = http.get(url, headers={"Authorization": "Bearer " + access_token}, timeout=20)
    except Exception as exc:
        return Attempt.io(str(exc) or None)
    status = int(getattr(response, "status_code", 0) or 0)
    if 200 <= status < 300:
        data = _response_bytes(response)
        if len(data) > MAX_UPLOAD_BYTES:
            return Attempt.http(413, "too_large")
        headers = _response_headers(response)
        name = filename_from_content_disposition(headers.get("content-disposition"))
        content_type = safe_content_type(headers.get("content-type"))
        return Attempt.ok((data, name, content_type))
    return Attempt.http(status, _response_text(response))


def download_drive_file(
    http,
    access_token: str,
    file_token: str,
    *,
    sleeper: Sleeper | None = None,
    sleep_ms: int = RETRY_SLEEP_MS,
    max_attempts: int = MAX_DOWNLOAD_ATTEMPTS,
) -> tuple[bytes, str | None, str]:
    return retry(
        lambda attempt: _download_attempt(http, files_download_url(file_token), access_token, attempt),
        max_attempts,
        sleep_ms,
        sleeper,
    )


def create_export_task(http, access_token: str, file_token: str, doc_type: str) -> str:
    if not doc_type.strip():
        raise FeishuExportError("无法创建导出任务，缺少 meta doc_type")
    try:
        response = http.post(
            export_tasks_url(),
            json={
                "file_extension": export_extension(doc_type),
                "token": file_token,
                "type": doc_type,
            },
            headers=_auth_headers(access_token),
            timeout=20,
        )
        body = _response_json(response)
    except Exception as exc:
        raise FeishuExportError("创建导出任务失败, fileType=" + doc_type) from exc
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    ticket = data.get("ticket") if body.get("code") in (0, None) else None
    if not isinstance(ticket, str) or not ticket.strip():
        raise FeishuExportError("创建导出任务失败, fileType=" + doc_type)
    return ticket.strip()


def poll_export_task(
    http,
    access_token: str,
    ticket: str,
    file_token: str,
    *,
    sleeper: Sleeper | None = None,
    sleep_ms: int = EXPORT_POLL_SLEEP_MS,
    max_attempts: int = EXPORT_POLL_ATTEMPTS,
) -> str:
    sleep = sleeper or (lambda millis: time.sleep(millis / 1000.0))
    url = export_task_url(ticket, file_token)
    for _ in range(max_attempts):
        try:
            response = http.get(url, headers={"Authorization": "Bearer " + access_token}, timeout=20)
        except Exception:
            sleep(sleep_ms)
            continue
        status = int(getattr(response, "status_code", 0) or 0)
        if status < 200 or status >= 300:
            raise FeishuExportError("查询导出任务失败:fileToken: " + file_token + "  HTTP " + str(status))
        body = _response_json(response)
        if body.get("code") not in (0, None):
            raise FeishuExportError("查询导出任务失败: " + str(body.get("msg") or ""))
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        job_status = data.get("job_status")
        try:
            job_status = int(job_status)
        except (TypeError, ValueError):
            job_status = -1
        if job_status == 0:
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            exported = str(result.get("file_token") or "").strip()
            if exported:
                return exported
            sleep(sleep_ms)
            continue
        if job_status == 1:
            sleep(sleep_ms)
            continue
        raise FeishuExportError("导出任务失败, status=" + str(job_status))
    raise FeishuExportError("导出任务超时，请稍后重试")


def download_exported_file(http, access_token: str, exported_file_token: str) -> tuple[bytes, str | None, str]:
    try:
        response = http.get(
            export_file_download_url(exported_file_token),
            headers={"Authorization": "Bearer " + access_token},
            timeout=20,
        )
    except Exception as exc:
        raise FeishuExportError("无法下载导出文件, exportedFileToken=" + exported_file_token) from exc
    status = int(getattr(response, "status_code", 0) or 0)
    if status < 200 or status >= 300:
        raise FeishuExportError("无法下载导出文件, exportedFileToken=" + exported_file_token)
    data = _response_bytes(response)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("too_large")
    headers = _response_headers(response)
    return data, filename_from_content_disposition(headers.get("content-disposition")), safe_content_type(
        headers.get("content-type")
    )


def import_drive_material(
    http,
    access_token: str,
    file_token: str,
    doc_type: str | None = None,
    *,
    sleeper: Sleeper | None = None,
    sleep_ms: int = RETRY_SLEEP_MS,
    export_polls: int = EXPORT_POLL_ATTEMPTS,
    export_sleep_ms: int = EXPORT_POLL_SLEEP_MS,
) -> ImportedMaterial:
    token = (file_token or "").strip()
    if not token:
        raise ValueError("invalid_file_token")
    meta = get_file_meta(http, access_token, token, doc_type)
    plan = FetchPlan.for_doc_type(meta.doc_type)
    if plan.downloads_drive_file():
        data, header_name, content_type = download_drive_file(
            http, access_token, meta.token, sleeper=sleeper, sleep_ms=sleep_ms
        )
    else:
        ticket = create_export_task(http, access_token, meta.token, plan.doc_type)
        exported = poll_export_task(
            http,
            access_token,
            ticket,
            meta.token,
            sleeper=sleeper,
            sleep_ms=export_sleep_ms,
            max_attempts=export_polls,
        )
        data, header_name, content_type = download_exported_file(http, access_token, exported)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("too_large")
    filename = _choose_filename(header_name, meta.name, plan.doc_type, "drive_" + meta.token)
    return ImportedMaterial(data, filename, content_type, plan.doc_type, meta.token)
