"""Remote OCR adapter implementation."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote, urlparse

import requests

from documents.contracts import (
    FileRef,
    OcrArtifact,
)
from documents.ocr.env import ocr_remote_url

log = logging.getLogger(__name__)


class RemoteOcrAdapter:
    """Remote OCR implementation backed by HTTP API."""

    def __init__(self, host: Optional[str] = None) -> None:
        configured_host = (host or ocr_remote_url()).strip()
        parsed = urlparse(configured_host)
        if not configured_host or not parsed.scheme or not parsed.netloc:
            raise ValueError("RemoteOcrAdapter requires a valid OCR remote host URL")
        self.host = configured_host

    @staticmethod
    def _to_file_ref(file_ref: FileRef | str) -> FileRef:
        if isinstance(file_ref, FileRef):
            return file_ref
        return FileRef(location=str(file_ref))

    @staticmethod
    def _candidate_hosts(host: str) -> list[str]:
        return [host]

    @staticmethod
    def _require_local_path(ref: FileRef) -> Path:
        local_path = Path(ref.location).expanduser()
        if not local_path.exists():
            raise FileNotFoundError(f"local file not found: {ref.location}")
        return local_path

    def _request_process(
        self,
        candidate_host: str,
        ref: FileRef,
        *,
        enable_cache: Optional[bool],
    ) -> requests.Response:
        if ref.is_remote:
            payload: dict[str, object] = {"file_url": ref.location}
            if enable_cache is not None:
                payload["enable_cache"] = enable_cache
            return requests.post(
                f"{candidate_host}/process",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=300,
            )

        local_path = self._require_local_path(ref)
        data: dict[str, str] = {}
        if enable_cache is not None:
            data["enable_cache"] = "true" if enable_cache else "false"

        with open(local_path, "rb") as fp:
            return requests.post(
                f"{candidate_host}/process-upload",
                data=data or None,
                files={"file": (local_path.name, fp, "application/octet-stream")},
                timeout=300,
            )

    def ocr_pdf_to_excel(self, file_ref: FileRef | str, enable_cache: bool = True) -> OcrArtifact:
        ref = self._to_file_ref(file_ref)
        data: dict = {}
        active_host: Optional[str] = None
        last_error: Optional[Exception] = None

        for candidate_host in self._candidate_hosts(self.host):
            try:
                log.info("OCR process: %s", unquote(ref.location.split("/")[-1]))
                process_resp = self._request_process(
                    candidate_host,
                    ref,
                    enable_cache=enable_cache,
                )
                process_resp.raise_for_status()
                raw_data = process_resp.json()
                data = raw_data if isinstance(raw_data, dict) else {"raw": raw_data}
                active_host = candidate_host
                break
            except Exception as exc:
                last_error = exc
                log.warning("OCR process failed via %s: %s", candidate_host, exc)

        if active_host is None:
            log.error("OCR process failed: %s", last_error)
            return OcrArtifact(provider="remote_ocr", raw_meta={"error": str(last_error or "unknown error")})

        excel_path = data.get("excel_path")
        if not excel_path:
            return OcrArtifact(
                provider="remote_ocr",
                raw_meta=data,
            )

        excel_path_str = str(excel_path)

        def _candidate_download_urls(path: str) -> list[str]:
            normalized = path.replace("\\", "/").strip()
            if not normalized:
                return []

            normalized = normalized.lstrip("./")

            for prefix in ("app/", "srv/"):
                if normalized.startswith(prefix):
                    normalized = normalized[len(prefix) :]
                    break

            variants: list[str] = [normalized]
            if normalized.startswith("oss/"):
                variants.append(normalized[len("oss/") :])

            deduped_variants: list[str] = []
            seen_variants: set[str] = set()
            for item in variants:
                key = item.strip()
                if not key or key in seen_variants:
                    continue
                seen_variants.add(key)
                deduped_variants.append(key)

            urls: list[str] = []
            seen_urls: set[str] = set()
            for item in deduped_variants:
                encoded = quote(item, safe="/")
                for candidate in (
                    f"{active_host}/file/{encoded}",
                    f"{active_host}/file//{encoded}",
                ):
                    if candidate in seen_urls:
                        continue
                    seen_urls.add(candidate)
                    urls.append(candidate)
            return urls

        try:
            xlsx_bytes = b""
            last_download_error: Optional[Exception] = None
            for download_url in _candidate_download_urls(excel_path_str):
                try:
                    download_resp = requests.get(download_url, timeout=120)
                    download_resp.raise_for_status()
                    xlsx_bytes = download_resp.content
                    if xlsx_bytes:
                        break
                except Exception as exc:
                    last_download_error = exc

            if not xlsx_bytes:
                if last_download_error is not None:
                    raise last_download_error
                raise RuntimeError(f"empty excel artifact for path: {excel_path_str}")
        except Exception as exc:
            log.error("OCR artifact download failed (%s): %s", excel_path_str, exc)
            return OcrArtifact(
                provider="remote_ocr",
                raw_meta=data,
            )

        fd, local_path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        with open(local_path, "wb") as fp:
            fp.write(xlsx_bytes)

        log.info(
            "OCR PDF->Excel completed: file_id=%s path=%s",
            data.get("file_id", "unknown"),
            local_path,
        )
        return OcrArtifact(
            provider="remote_ocr",
            excel_path=local_path,
            html_path=str(data.get("html_path")) if data.get("html_path") else None,
            raw_meta=data,
        )
