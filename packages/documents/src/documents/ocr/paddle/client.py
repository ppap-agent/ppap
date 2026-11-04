"""Explicit PaddleOCR PP-StructureV3 HTTP and local PDF input. No production route imports."""

from __future__ import annotations

import base64
import hashlib
import math
from pathlib import Path
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
    if type(page_count) is not int or page_count < 1:
        raise PaddleOCRFormatError("PaddleOCR requested page count is invalid")
    if not isinstance(result, dict) or not isinstance(result.get("dataInfo"), dict):
        raise PaddleOCRFormatError("PaddleOCR response lacks required metadata")
    pages = result.get("layoutParsingResults")
    if not isinstance(pages, list) or len(pages) != page_count:
        raise PaddleOCRFormatError("PaddleOCR returned an incomplete PDF page set")
    if any(not isinstance(page, dict) or not isinstance(page.get("prunedResult"), dict) for page in pages):
        raise PaddleOCRFormatError("PaddleOCR returned an invalid page")
    metadata = result["dataInfo"]
    if "numPages" in metadata and (type(metadata["numPages"]) is not int or metadata["numPages"] != page_count):
        raise PaddleOCRFormatError("PaddleOCR page identity does not match the request")
    if "pages" in metadata and (not isinstance(metadata["pages"], list) or len(metadata["pages"]) != page_count):
        raise PaddleOCRFormatError("PaddleOCR page identity does not match the request")
    for index, page in enumerate(pages):
        pruned = page["prunedResult"]
        if "page_index" in pruned and (type(pruned["page_index"]) is not int or pruned["page_index"] != index):
            raise PaddleOCRFormatError("PaddleOCR page identity does not match the request")
    return pages


def page_dimensions(result: dict, index: int) -> tuple[float, float]:
    pages = result["dataInfo"].get("pages", [])
    metadata = pages[index] if isinstance(pages, list) and len(pages) > index else {}
    if not isinstance(metadata, dict):
        raise PaddleOCRFormatError("PaddleOCR page dimensions are missing")
    dims: list[float] = []
    for value in (metadata.get("width"), metadata.get("height")):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise PaddleOCRFormatError("PaddleOCR page dimensions are missing or invalid")
        dims.append(float(value))
    return dims[0], dims[1]


class PaddleStructureService:
    """Historical /layout-parsing API contract, only called explicitly."""

    def __init__(self, endpoint: str, *, token: str = "", timeout: float = 300) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("PaddleOCR endpoint must be an HTTP(S) URL without credentials or fragment")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("PaddleOCR timeout must be positive")
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout

    def analyze_page(self, file_path: str | Path, page_number: int, *, timeout: float | None = None) -> dict:
        """Send one selected original page, once. A timeout does not cancel it remotely."""
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("PaddleOCR remaining timeout must be finite and positive")
        path, _ = validate_pdf(file_path)
        original = path.read_bytes()
        before_hash = hashlib.sha256(original).digest()
        with fitz.open(stream=original, filetype="pdf") as source:
            if type(page_number) is not int or not 1 <= page_number <= len(source):
                raise ValueError("PaddleOCR page_number must select an existing original page")
            with fitz.open() as selected:
                selected.insert_pdf(source, from_page=page_number - 1, to_page=page_number - 1)
                payload = selected.tobytes(garbage=4, deflate=True)
        if len(payload) > parse_file_size(max_file_size()):
            raise ValueError("PaddleOCR selected page exceeds MAX_FILE_SIZE")
        result = self._request(payload, timeout=min(self.timeout, timeout) if timeout is not None else self.timeout)
        try:
            unchanged = hashlib.sha256(path.read_bytes()).digest() == before_hash
        except OSError:
            unchanged = False
        if not unchanged:
            raise PaddleOCRFormatError("PaddleOCR source changed during the request")
        require_pages(result, 1)
        page_dimensions(result, 0)
        return result

    def analyze_full_pdf(self, file_path: str | Path) -> dict:
        """Compatibility entry for already-single-page PDFs; never send a whole multipage source."""
        path, page_count = validate_pdf(file_path)
        if page_count != 1:
            raise ValueError("Select one original page with analyze_page")
        return self._request(path.read_bytes())

    def _request(self, content: bytes, *, timeout: float | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        payload = {
            "file": base64.b64encode(content).decode("ascii"),
            "fileType": 0,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "useChartRecognition": False,
            "useSealRecognition": True,
            "useTableRecognition": True,
        }
        try:
            response = requests.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout if timeout is None else timeout, allow_redirects=False)
            response.raise_for_status()
            if not 200 <= getattr(response, "status_code", 200) < 300:
                raise PaddleOCRFormatError("PaddleOCR returned a non-success HTTP response")
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PaddleOCRFormatError(f"PaddleOCR request failed ({type(exc).__name__})") from None
        if not isinstance(body, dict):
            raise PaddleOCRFormatError("PaddleOCR response is not an object")
        result = body.get("result")
        if not isinstance(result, dict):
            raise PaddleOCRFormatError("PaddleOCR response lacks required metadata")
        require_pages(result, 1)
        return result
