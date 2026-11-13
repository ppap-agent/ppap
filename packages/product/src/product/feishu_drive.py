"""Feishu Drive/Docs fetch plan and download/export. Not a Feishu-only file ledger."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, cast
from urllib.parse import unquote

from product.files import MAX_UPLOAD_BYTES, original_filename, safe_content_type

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


class FeishuDocLinksError(Exception):
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

    def downloads_drive_file(self) -> bool:
        return self.mode is FetchMode.DRIVE_FILE


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


def list_drive_folder(http, access_token: str, folder_token: str = "", page_token: str = "") -> dict:
    if folder_token and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", folder_token):
        raise ValueError("invalid_folder_token")
    if len(page_token) > 4096:
        raise ValueError("invalid_page_token")
    params: dict[str, str | int] = {"page_size": 50}
    if folder_token:
        params["folder_token"] = folder_token
    if page_token:
        params["page_token"] = page_token
    try:
        response = http.get(OPEN_API + "/drive/v1/files", params=params, headers=_auth_headers(access_token), timeout=20)
        body = _response_json(response)
        if response.status_code != 200 or body.get("code") != 0:
            raise FeishuMetaError("feishu_folder_failed")
        data = body.get("data") or {}
        files = data.get("files")
        if not isinstance(files, list):
            raise FeishuMetaError("feishu_folder_failed")
        next_page = data.get("next_page_token") if data.get("has_more") else None
        if data.get("has_more") and (not isinstance(next_page, str) or not next_page):
            raise FeishuMetaError("feishu_folder_failed")
        return {
            "files": [
                {"token": item["token"], "name": item["name"], "type": item["type"]}
                for item in files
                if isinstance(item, dict) and all(isinstance(item.get(k), str) for k in ("token", "name", "type"))
            ],
            "next_page_token": next_page,
        }
    except FeishuMetaError:
        raise
    except Exception:
        raise FeishuMetaError("feishu_folder_failed") from None


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
    data_value = body.get("data")
    data = data_value if isinstance(data_value, dict) else {}
    failed_value = data.get("failed_list")
    failed = failed_value if isinstance(failed_value, list) else []
    for item in failed:
        if isinstance(item, dict) and item.get("doc_token") == file_token:
            return None
    metas_value = data.get("metas")
    metas = metas_value if isinstance(metas_value, list) else []
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
    data_value = body.get("data")
    data = data_value if isinstance(data_value, dict) else {}
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
        data_value = body.get("data")
        data = data_value if isinstance(data_value, dict) else {}
        job_status = data.get("job_status")
        try:
            job_status = int(cast(Any, job_status))
        except (TypeError, ValueError):
            job_status = -1
        if job_status == 0:
            result_value = data.get("result")
            result = result_value if isinstance(result_value, dict) else {}
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
    return data, filename_from_content_disposition(headers.get("content-disposition")), safe_content_type(headers.get("content-type"))


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
        data, header_name, content_type = download_drive_file(http, access_token, meta.token, sleeper=sleeper, sleep_ms=sleep_ms)
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


_FEISHU_HOST = re.compile(r"(?:feishu\.cn|larksuite\.com)", re.I)
_DOC_URL_TOKEN = re.compile(
    r"/(docx|sheet|bitable|file|doc)/([^/?#]+)",
    re.I,
)
_TEXT_KEYS = (
    "text",
    "heading1",
    "heading2",
    "heading3",
    "heading4",
    "heading5",
    "heading6",
    "heading7",
    "heading8",
    "heading9",
    "bullet",
    "ordered",
    "quote",
)


def extract_doc_token_from_url(url: str) -> tuple[str, str] | None:
    raw = (url or "").strip()
    if not raw or not _FEISHU_HOST.search(raw):
        return None
    match = _DOC_URL_TOKEN.search(raw)
    if not match:
        return None
    return match.group(2), match.group(1).lower()


def _block_elements(block: dict) -> list:
    for key in _TEXT_KEYS:
        node = block.get(key)
        if isinstance(node, dict) and isinstance(node.get("elements"), list):
            return node["elements"]
    return []


def _element_link_url(element: dict) -> str:
    link = element.get("link")
    if isinstance(link, dict):
        href = str(link.get("url") or "").strip()
        if href:
            return href
    text_run = element.get("text_run")
    if isinstance(text_run, dict):
        style = text_run.get("text_element_style")
        if isinstance(style, dict) and isinstance(style.get("link"), dict):
            href = str(style["link"].get("url") or "").strip()
            if href:
                return href
        href = str(text_run.get("url") or "").strip()
        if href:
            return href
    return str(element.get("url") or "").strip()


def _collect_block_links(block: dict) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    file_node = block.get("file")
    if isinstance(file_node, dict):
        token = str(file_node.get("token") or "").strip()
        if token:
            found.append(
                {
                    "token": token,
                    "name": str(file_node.get("name") or "").strip(),
                    "url": str(file_node.get("url") or "").strip(),
                    "doc_type": "file",
                }
            )
    for element in _block_elements(block):
        if not isinstance(element, dict):
            continue
        mention = element.get("mention_doc")
        if isinstance(mention, dict):
            token = str(mention.get("token") or "").strip()
            url = str(mention.get("url") or "").strip()
            if token and _FEISHU_HOST.search(url):
                hinted = extract_doc_token_from_url(url)
                found.append(
                    {
                        "token": token,
                        "name": str(mention.get("title") or mention.get("name") or "").strip(),
                        "url": url,
                        "doc_type": (hinted[1] if hinted else "") or str(mention.get("obj_type") or mention.get("type") or "").strip(),
                    }
                )
            continue
        href = _element_link_url(element)
        extracted = extract_doc_token_from_url(href)
        if extracted:
            token, doc_type = extracted
            found.append({"token": token, "name": "", "url": href, "doc_type": doc_type})
    return found


def _resolve_link_type(http, access_token: str, item: dict[str, str]) -> dict[str, str]:
    hinted = (item.get("doc_type") or "").strip()
    try:
        meta = get_file_meta(http, access_token, item["token"], hinted or None)
    except FeishuMetaError:
        if hinted and hinted != "folder":
            item["doc_type"] = hinted
        return item
    if meta.doc_type == "folder":
        item["doc_type"] = "folder"
        return item
    item["doc_type"] = meta.doc_type
    if not item.get("name"):
        item["name"] = meta.name
    return item


def _fetch_docx_blocks(http, access_token: str, file_token: str) -> list:
    items: list = []
    page_token = ""
    for _ in range(20):
        params: dict[str, str | int] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        try:
            response = http.get(
                docx_blocks_url(file_token),
                params=params,
                headers=_auth_headers(access_token),
                timeout=20,
            )
            body = _response_json(response)
        except Exception as exc:
            raise FeishuDocLinksError("feishu_doc_links_failed") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status < 200 or status >= 300:
            raise FeishuDocLinksError("feishu_doc_links_failed")
        if body.get("code") not in (0, None):
            return items
        data_value = body.get("data")
        data = data_value if isinstance(data_value, dict) else {}
        raw_items = data.get("items")
        page_items = raw_items if isinstance(raw_items, list) else []
        items.extend(page_items)
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token") or "").strip()
        if not page_token:
            break
    return items


def list_doc_links(http, access_token: str, file_token: str) -> list[dict[str, str]]:
    token = (file_token or "").strip()
    if not token or "/" in token or ".." in token:
        raise ValueError("invalid_file_token")
    items = _fetch_docx_blocks(http, access_token, token)
    collected: list[dict[str, str]] = []
    seen: set[str] = set()
    for block in items:
        if not isinstance(block, dict):
            continue
        for link in _collect_block_links(block):
            if link["token"] in seen:
                continue
            seen.add(link["token"])
            resolved = _resolve_link_type(http, access_token, link)
            if resolved.get("doc_type") == "folder":
                continue
            collected.append(
                {
                    "token": resolved["token"],
                    "name": resolved.get("name") or resolved["token"],
                    "url": resolved.get("url") or "",
                    "doc_type": resolved.get("doc_type") or "",
                }
            )
    return collected
