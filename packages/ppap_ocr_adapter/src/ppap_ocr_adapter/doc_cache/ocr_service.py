"""Responses multimodal PDF to Excel/HTML conversion with content cache.

Scope kept intentionally small (KISS):
- download/resolve PDF
- optional sqlite cache
- render pages and request structured multimodal Responses outputs
- validate and write tables to xlsx, text and seals to safe HTML
"""

from __future__ import annotations

import fcntl
import hashlib
import html
import json
import logging
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

import openpyxl
import requests
from ppap_core.settings import get_settings
from ppap_docops.excel.loader import load_excel_workbook
from ppap_docops.files import parse_file_size

from .vision_service import VisionLayoutService

logger = logging.getLogger(__name__)

VISION_CACHE_VERSION = "responses_multimodal_v2"


def _source_hash(source: str) -> str:
    return hashlib.md5(source.encode("utf-8")).hexdigest()


def _repo_runtime_ocr_dir(file_path: str | None = None) -> str | None:
    current = Path(file_path or __file__).resolve()
    for candidate in current.parents:
        if (candidate / "ppap_agent" / "docker-compose.yml").exists():
            return str(candidate / ".runtime" / "dev" / "ocr-data")
    return None


def _oss_dir() -> str:
    configured = (get_settings().OCR_LOCAL_OSS_DIR or "").strip()
    if configured:
        return configured
    repo_runtime_dir = _repo_runtime_ocr_dir()
    if repo_runtime_dir:
        return repo_runtime_dir
    return os.path.join(tempfile.gettempdir(), "ppap_local_ocr")


def _db_path() -> str:
    configured = (get_settings().OCR_LOCAL_SQLITE_DB or "").strip()
    if configured:
        return configured
    return os.path.join(_oss_dir(), "ocr_cache.db")


def _results_dir() -> str:
    return os.path.join(_oss_dir(), "results")


def _max_file_size_bytes() -> int:
    return parse_file_size(get_settings().MAX_FILE_SIZE)


def _compute_md5(path: str) -> str:
    md5 = hashlib.md5()
    md5.update(VISION_CACHE_VERSION.encode())
    md5.update(get_settings().get_ocr_model_name().encode())
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()


@contextmanager
def _conversion_lock(file_id: str) -> Iterator[None]:
    """Serialize a cache key across threads and processes, including the cache recheck."""
    lock_dir = os.path.join(_oss_dir(), "locks")
    os.makedirs(lock_dir, exist_ok=True)
    with open(os.path.join(lock_dir, f"{file_id}.lock"), "a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _safe_remove(path: str | None) -> None:
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        logger.debug("cleanup failed: %s", path)


def _ensure_db() -> None:
    path = _db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS content_cache (file_id TEXT PRIMARY KEY, text TEXT)",
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS url_cache (url_hash TEXT PRIMARY KEY, file_id TEXT)",
        )


def _load_content(conn: sqlite3.Connection, file_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT text FROM content_cache WHERE file_id = ?", (file_id,)).fetchone()
    if not row:
        return None
    try:
        value = json.loads(str(row[0]))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        logger.warning("corrupted cache content for file_id=%s", file_id)
        return None


def _is_cacheable_result(payload: dict[str, Any]) -> bool:
    """Cache only successful OCR payloads."""
    return "error" not in payload and payload.get("vision_protocol") == VISION_CACHE_VERSION


def _resolve_cached_artifact_path(path: str) -> str | None:
    normalized = (path or "").strip()
    if not normalized:
        return None

    candidates: list[str] = []
    if os.path.isabs(normalized):
        candidates.append(normalized)
    else:
        candidates.append(os.path.abspath(normalized))
        candidates.append(os.path.abspath(os.path.join(_oss_dir(), normalized)))
        if normalized.startswith("oss/"):
            suffix = normalized.removeprefix("oss/")
            candidates.append(os.path.abspath(os.path.join(_oss_dir(), suffix)))

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def _normalize_cached_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    normalized = dict(payload)

    excel_path = normalized.get("excel_path")
    if excel_path is not None:
        resolved_excel = _resolve_cached_artifact_path(str(excel_path))
        if not resolved_excel:
            return None
        normalized["excel_path"] = resolved_excel

    html_path = normalized.get("html_path")
    if html_path is not None:
        resolved_html = _resolve_cached_artifact_path(str(html_path))
        if not resolved_html:
            return None
        normalized["html_path"] = resolved_html

    return normalized


def _lookup_cache(source_hash: str, file_id: str) -> dict[str, Any] | None:
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute("SELECT file_id FROM url_cache WHERE url_hash = ?", (source_hash,)).fetchone()
        if row:
            mapped_file_id = str(row[0])
            if mapped_file_id == file_id:
                cached = _load_content(conn, mapped_file_id)
                if cached is not None and _is_cacheable_result(cached):
                    normalized = _normalize_cached_payload(cached)
                    if normalized is not None:
                        return normalized
                    conn.execute("DELETE FROM content_cache WHERE file_id = ?", (mapped_file_id,))
                    conn.execute("DELETE FROM url_cache WHERE url_hash = ?", (source_hash,))
            else:
                # URL target changed: clear stale mapping and re-check by current file hash.
                conn.execute(
                    "DELETE FROM url_cache WHERE url_hash = ?",
                    (source_hash,),
                )

        cached = _load_content(conn, file_id)
        if cached is not None and _is_cacheable_result(cached):
            normalized = _normalize_cached_payload(cached)
            if normalized is None:
                conn.execute("DELETE FROM content_cache WHERE file_id = ?", (file_id,))
                return None
            conn.execute(
                "INSERT OR REPLACE INTO url_cache (url_hash, file_id) VALUES (?, ?)",
                (source_hash, file_id),
            )
            return normalized

    return None


def _store_cache(source_hash: str, file_id: str, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO content_cache (file_id, text) VALUES (?, ?)",
            (file_id, raw),
        )
        conn.execute(
            "INSERT OR REPLACE INTO url_cache (url_hash, file_id) VALUES (?, ?)",
            (source_hash, file_id),
        )


def _is_valid_excel_artifact(path: str) -> bool:
    try:
        workbook = load_excel_workbook(path)
    except Exception as exc:
        logger.error("Generated vision workbook is invalid (%s): %s", path, exc)
        return False
    close = getattr(workbook, "close", None)
    if callable(close):
        close()
    return True


def _run_pipeline(pdf_path: str, file_id: str) -> dict[str, Any]:
    """One Responses vision pass per PDF page; build auditable workbook/HTML."""
    pages = VisionLayoutService().analyze_full_pdf(pdf_path)
    if not pages:
        return {"file_id": file_id, "error": "vision_empty_result"}

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    html_parts: list[str] = []
    table_number = 0
    for page_idx, page in enumerate(pages, 1):
        for table in page["tables"]:
            rows = table["rows"]
            if not rows:
                continue
            table_number += 1
            sheet = workbook.create_sheet(f"Table_{table_number}")
            for row in rows:
                if not isinstance(row, list) or not row:
                    raise ValueError("Invalid multimodal table row")
                if any(not isinstance(cell, str) or len(cell) > 32767 for cell in row):
                    raise ValueError("Invalid multimodal table cells")
                sheet.append(row)
            for merge in table["merges"]:
                sheet.merge_cells(
                    start_row=merge["start_row"] + 1,
                    start_column=merge["start_col"] + 1,
                    end_row=merge["end_row"] + 1,
                    end_column=merge["end_col"] + 1,
                )
        paragraphs = page["paragraphs"]
        seals = page["seals"]
        if paragraphs or seals:
            html_parts.append(f'<section data-page="{page_idx}">')
            for text in paragraphs:
                html_parts.append(f"<p>{html.escape(text, quote=True)}</p>")
            for text in seals:
                html_parts.append(f'<p class="seal">印章/签署：{html.escape(text, quote=True)}</p>')
            html_parts.append("</section>")

    if not table_number and not html_parts:
        workbook.close()
        return {"file_id": file_id, "error": "vision_empty_result"}
    if not table_number:
        sheet = workbook.create_sheet("Text")
        for page in pages:
            for text in [*page["paragraphs"], *page["seals"]]:
                if len(text) > 32767:
                    raise ValueError("Multimodal text exceeds Excel cell limit")
                sheet.append([text])

    directory = _results_dir()
    os.makedirs(directory, exist_ok=True)
    # Each conversion owns immutable artifacts. A second producer for the same
    # file hash must never truncate a workbook already published in SQLite.
    artifact_id = f"{file_id}-{uuid.uuid4().hex}"
    excel_path = os.path.join(directory, f"{artifact_id}.xlsx")
    html_path = os.path.join(directory, f"{artifact_id}.html")
    try:
        workbook.save(excel_path)
        workbook.close()
        if not _is_valid_excel_artifact(excel_path):
            raise ValueError("Multimodal workbook failed validation")
        result: dict[str, Any] = {
            "file_id": file_id,
            "excel_path": excel_path,
            "vision_protocol": VISION_CACHE_VERSION,
            "vision_model": get_settings().get_ocr_model_name(),
        }
        if html_parts:
            with open(html_path, "w", encoding="utf-8") as target:
                target.write("\n".join(html_parts))
            result["html_path"] = html_path
        return result
    except BaseException:
        workbook.close()
        _safe_remove(excel_path)
        _safe_remove(html_path)
        raise


def _process_pdf(pdf_path: str, source_hash: str, enable_cache: bool) -> dict[str, Any]:
    file_id = _compute_md5(pdf_path)

    if not enable_cache:
        return _run_pipeline(pdf_path, file_id)

    _ensure_db()
    with _conversion_lock(file_id):
        # Recheck while holding the process-wide lock. Only the winner converts.
        cached = _lookup_cache(source_hash, file_id)
        if cached is not None:
            logger.info("OCR cache hit: %s", source_hash)
            return cached

        result = _run_pipeline(pdf_path, file_id)
        if _is_cacheable_result(result):
            try:
                _store_cache(source_hash, file_id, result)
            except BaseException:
                # An uncommitted result must not leave unreferenced artifacts.
                _safe_remove(str(result.get("excel_path", "") or ""))
                _safe_remove(str(result.get("html_path", "") or ""))
                raise
        return result


def process_local_pdf(pdf_path: str, enable_cache: bool = True) -> dict[str, Any]:
    """Run local in-process OCR for a local PDF."""
    normalized = os.path.abspath(pdf_path)
    if not os.path.exists(normalized):
        raise FileNotFoundError(f"PDF not found: {normalized}")
    return _process_pdf(normalized, _source_hash(f"file://{normalized}"), enable_cache)


def process_file_url(file_url: str, enable_cache: bool = True) -> dict[str, Any]:
    """Run local in-process OCR for a remote PDF URL."""
    decoded = unquote(file_url)
    source_hash = _source_hash(decoded)

    temp_path: str | None = None
    try:
        suffix = os.path.splitext(urlparse(decoded).path)[1] or ".pdf"
        tmp_dir = os.path.join(_oss_dir(), "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        max_size = _max_file_size_bytes()

        with tempfile.NamedTemporaryFile(dir=tmp_dir, suffix=suffix, delete=False) as tmp:
            temp_path = tmp.name
            with requests.get(decoded, timeout=300, stream=True) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_size:
                    raise ValueError(f"Downloaded file exceeds max size: {max_size} bytes")

                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > max_size:
                        raise ValueError(f"Downloaded file exceeds max size: {max_size} bytes")
                    tmp.write(chunk)

        return _process_pdf(temp_path, source_hash, enable_cache)
    finally:
        _safe_remove(temp_path)
