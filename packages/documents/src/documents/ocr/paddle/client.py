"""Explicit PaddleOCR PP-StructureV3 HTTP and local PDF input. No production route imports."""

from __future__ import annotations

import base64
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

import fitz
import requests

from documents.files import parse_file_size
from documents.ocr.env import max_file_size


class PaddleOCRFormatError(ValueError):
    """PaddleOCR output cannot be used as reliable document evidence."""


def validate_pdf(file_path: str | Path) -> tuple[Path, int]:
    path = Path(file_path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError("PaddleOCR input must be a local PDF file")
    if path.stat().st_size > parse_file_size(max_file_size()):
        raise ValueError("PaddleOCR input exceeds MAX_FILE_SIZE")
    with fitz.open(path) as pdf:
        if not pdf.is_pdf or pdf.needs_pass or pdf.page_count == 0:
            raise ValueError("PaddleOCR input must be a readable nonempty PDF")
        return path, pdf.page_count


def require_pages(result: object, page_count: int) -> list[dict]:
    if not isinstance(result, dict) or not isinstance(result.get("dataInfo"), dict):
        raise PaddleOCRFormatError("PaddleOCR response lacks required metadata")
    pages = result.get("layoutParsingResults")
    if not isinstance(pages, list) or len(pages) != page_count:
        raise PaddleOCRFormatError("PaddleOCR returned an incomplete PDF page set")
    if any(not isinstance(page, dict) or not isinstance(page.get("prunedResult"), dict) for page in pages):
        raise PaddleOCRFormatError("PaddleOCR returned an invalid page")
    return pages


class PaddleStructureService:
    """Historical /layout-parsing API contract, only called explicitly."""

    def __init__(self, endpoint: str, *, token: str = "", timeout: int = 300) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("PaddleOCR endpoint must be an HTTP(S) URL without credentials or fragment")
        if timeout <= 0:
            raise ValueError("PaddleOCR timeout must be positive")
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout

    def analyze_full_pdf(self, file_path: str | Path) -> dict:
        path, page_count = validate_pdf(file_path)
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        payload = {
            "file": base64.b64encode(path.read_bytes()).decode("ascii"),
            "fileType": 0,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "useChartRecognition": False,
            "useSealRecognition": True,
            "useTableRecognition": True,
        }
        try:
            response = requests.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PaddleOCRFormatError(f"PaddleOCR request failed ({type(exc).__name__})") from None
        if not isinstance(body, dict):
            raise PaddleOCRFormatError("PaddleOCR response is not an object")
        result = body.get("result")
        require_pages(result, page_count)
        return result


@contextmanager
def downloaded_pdf(url: str) -> Iterator[Path]:
    """Explicit, bounded remote-PDF input; never called by production routes."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Remote PDF URL must be plain HTTP(S) without embedded credentials")
    limit = parse_file_size(max_file_size())
    with tempfile.TemporaryDirectory(prefix="paddle-ocr-url-") as directory:
        path = Path(directory) / "input.pdf"
        try:
            with requests.get(url, stream=True, timeout=300, allow_redirects=False) as response:
                response.raise_for_status()
                if response.status_code >= 300:
                    raise PaddleOCRFormatError("Remote PDF redirects are not followed")
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > limit:
                    raise PaddleOCRFormatError("Remote PDF exceeds maximum size")
                total = 0
                with path.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=8192):
                        total += len(chunk)
                        if total > limit:
                            raise PaddleOCRFormatError("Remote PDF exceeds maximum size")
                        file.write(chunk)
        except requests.RequestException as exc:
            raise PaddleOCRFormatError(f"Remote PDF fetch failed ({type(exc).__name__})") from None
        validate_pdf(path)
        yield path
