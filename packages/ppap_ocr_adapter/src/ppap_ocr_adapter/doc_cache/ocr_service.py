"""Minimal in-process OCR workflow ported from doc-cache.

Scope kept intentionally small (KISS):
- download/resolve PDF
- optional sqlite cache
- call Paddle layout OCR
- extract tables to xlsx
- optionally write seal metadata html
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import fitz
import openpyxl
import requests
from ppap_core.settings import get_settings
from ppap_docops.excel.loader import load_excel_workbook
from ppap_docops.files import parse_file_size

from .layout_utils import build_layout_structure
from .paddle_service import PaddleStructureService
from .processors.base_processor import ProcessingContext
from .processors.gemma_processor import GemmaProcessor
from .processors.paragraph_processor import ParagraphProcessor
from .processors.seal_processor import SealProcessor
from .processors.table_processor import TableProcessor

logger = logging.getLogger(__name__)

DEFAULT_IMG_WIDTH = 1654
DEFAULT_IMG_HEIGHT = 2339


def _coerce_table_rows(rows: object) -> list[list[Any]] | None:
    if not isinstance(rows, list):
        return None
    normalized: list[list[Any]] = []
    for row in rows:
        if not isinstance(row, list):
            return None
        normalized.append(list(row))
    return normalized


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
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()


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
    return "error" not in payload


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


def _extract_rows(pdf: fitz.Document, box: dict[str, Any], *, img_w: int, img_h: int, padding: int) -> list[list[Any]] | None:
    page_idx = int(box["page_idx"])
    x0, y0, x1, y1 = box["coordinate"]

    page = pdf[page_idx]
    scale_x = page.rect.width / img_w
    scale_y = page.rect.height / img_h

    clip = fitz.Rect(
        max(0, x0 - padding) * scale_x,
        max(0, y0 - padding) * scale_y,
        min(img_w, x1 + padding) * scale_x,
        min(img_h, y1 + padding) * scale_y,
    )

    try:
        tabs = page.find_tables(clip=clip)
        if tabs.tables:
            return _coerce_table_rows(tabs[0].extract())
        return None
    except Exception:
        logger.warning("table crop failed page=%s box=%s", page_idx, box.get("coordinate"))
        return None


def _write_tables(pdf_path: str, items: list[dict[str, Any] | list[dict[str, Any]]], output_excel: str, *, img_w: int, img_h: int) -> bool:
    workbook = openpyxl.Workbook()
    default_sheet = workbook.active

    table_count = 0
    with fitz.open(pdf_path) as pdf:
        for item in items:
            if isinstance(item, list):
                merged_rows: list[list[Any]] = []
                for box in item:
                    rows = _extract_rows(pdf, box, img_w=img_w, img_h=img_h, padding=15)
                    if rows:
                        merged_rows.extend(rows)
                rows = merged_rows
            elif isinstance(item, dict) and item.get("label") == "table":
                rows = _extract_rows(pdf, item, img_w=img_w, img_h=img_h, padding=15)
            else:
                continue

            if not rows:
                continue

            table_count += 1
            sheet = workbook.create_sheet(f"Table_{table_count}")
            for row in rows:
                sheet.append([cell if cell is not None else "" for cell in row])

    if table_count == 0:
        workbook.close()
        return False

    if default_sheet is not None:
        workbook.remove(default_sheet)
    workbook.save(output_excel)
    workbook.close()
    return True


def _write_seals_html(raw_data: dict[str, Any], items: list[dict[str, Any] | list[dict[str, Any]]], output_html: str) -> bool:
    seal_pages: set[int] = set()
    for item in items:
        if not isinstance(item, dict) or item.get("label") != "seal":
            continue
        page_idx = item.get("page_idx")
        if page_idx is not None:
            seal_pages.add(int(page_idx))
    if not seal_pages:
        return False

    results = raw_data.get("layoutParsingResults", [])
    lines: list[str] = []
    for page_idx in sorted(seal_pages):
        if page_idx >= len(results):
            lines.append(f"Page {page_idx + 1} 检测到印章 (内容未识别)")
            continue

        seal_res = results[page_idx].get("prunedResult", {}).get("seal_res_list", [])
        texts: list[str] = []
        for seal in seal_res:
            if "rec_texts" in seal:
                texts.extend(seal["rec_texts"])

        if texts:
            lines.append(f"Page {page_idx + 1} 印章: {' '.join(texts)}")
        else:
            lines.append(f"Page {page_idx + 1} 检测到印章 (内容未识别)")

    if not lines:
        return False

    html_content = '<div class="seal-section" style="color:red; border:1px solid red; padding:10px; margin:10px 0;">\n'
    html_content += "  <h4>🛑 印章/签署信息 (Seal/Signature)</h4>\n"
    html_content += "  <ul>\n"
    for line in lines:
        html_content += f"    <li>{html.escape(line, quote=True)}</li>\n"
    html_content += "  </ul>\n"
    html_content += "</div>\n"

    with open(output_html, "w", encoding="utf-8") as file:
        file.write(html_content)
    return True


def _is_valid_excel_artifact(path: str) -> bool:
    try:
        workbook = load_excel_workbook(path)
    except Exception as exc:
        logger.error("Generated OCR Excel is invalid (%s): %s", path, exc)
        return False

    close = getattr(workbook, "close", None)
    if callable(close):
        close()
    return True


def _run_pipeline(pdf_path: str, file_id: str) -> dict[str, Any]:
    """
    KISS 风格的主流程：只负责组装，不负责实现细节。
    """

    raw_data = PaddleStructureService().analyze_full_pdf(pdf_path)
    if not raw_data:
        return {"file_id": file_id, "error": "empty_paddle_result"}

    # 1. 准备路径
    output_dir = _results_dir()
    os.makedirs(output_dir, exist_ok=True)
    excel_path = os.path.join(output_dir, f"{file_id}.xlsx")
    html_path = os.path.join(output_dir, f"{file_id}.html")
    _safe_remove(excel_path)
    _safe_remove(html_path)

    # 2. 预处理数据 (Layout Structure)
    final_layout = build_layout_structure(raw_data)

    # 3. 初始化上下文
    # 获取图片尺寸信息
    page_info = (raw_data.get("dataInfo", {}).get("pages", []) or [{}])[0]
    img_w = int(page_info.get("width") or 1654)
    img_h = int(page_info.get("height") or 2339)

    ctx = ProcessingContext(
        pdf_path=pdf_path, raw_structures_data=raw_data, final_layout_structure=final_layout, output_excel_path=excel_path, output_html_path=html_path, img_w=img_w, img_h=img_h, padding=15
    )

    # 4. 初始化 HTML (清空)
    try:
        open(html_path, "w", encoding="utf-8").close()
    except Exception as e:
        logger.error(f"Failed to init HTML file: {e}")

    # 5. 定义责任链 (清晰明了)
    chain = [
        TableProcessor(),  # 负责表格 -> Excel
        SealProcessor(),  # 负责印章 -> HTML
        ParagraphProcessor(),  # 负责切图 -> 待处理列表
        GemmaProcessor(),  # 负责视觉理解 -> HTML
    ]

    # 6. 执行
    logger.info("Starting processing chain...")
    for processor in chain:
        try:
            processor.process(ctx)
        except Exception as e:
            logger.error(f"Processor {processor.__class__.__name__} failed: {e}")
            # 可以选择 continue 或 break

    # 7. 构造返回结果
    result = {"file_id": file_id}
    if os.path.exists(excel_path) and os.path.getsize(excel_path) > 0 and _is_valid_excel_artifact(excel_path):
        result["excel_path"] = excel_path
    else:
        _safe_remove(excel_path)
    if os.path.exists(html_path) and os.path.getsize(html_path) > 0:
        result["html_path"] = html_path
    else:
        _safe_remove(html_path)

    return result


def _process_pdf(pdf_path: str, source_hash: str, enable_cache: bool) -> dict[str, Any]:
    file_id = _compute_md5(pdf_path)

    if enable_cache:
        _ensure_db()
        cached = _lookup_cache(source_hash, file_id)
        if cached is not None:
            logger.info("OCR cache hit: %s", source_hash)
            return cached

    result = _run_pipeline(pdf_path, file_id)

    if enable_cache and _is_cacheable_result(result):
        _store_cache(source_hash, file_id, result)

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
