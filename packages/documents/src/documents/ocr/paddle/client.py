"""Explicit PaddleOCR PP-StructureV3 HTTP and local PDF input. No production route imports."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import fitz
import httpx

from documents.files import parse_file_size
from documents.ocr.env import max_file_size


class PaddleOCRFormatError(ValueError):
    """PaddleOCR output cannot be used as reliable document evidence."""


_HTTP_CHILD = "from documents.ocr.paddle.client import _http_child; _http_child()"


async def _post_page(endpoint: str, payload: dict, headers: dict, deadline: float) -> object:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError

    async def check_deadline(_request: httpx.Request) -> None:
        # JSON encoding and local client setup must not permit a late dispatch.
        if monotonic() >= deadline:
            raise TimeoutError

    # A socket's idle timeout does not bound a slowly arriving response body.
    # Cancellation closes our local HTTP resources, not the remote OCR job.
    async with asyncio.timeout(remaining):
        async with httpx.AsyncClient(timeout=remaining, follow_redirects=False, event_hooks={"request": [check_deadline]}) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
            if monotonic() >= deadline:
                raise TimeoutError
            return body


def _post_page_sync(endpoint: str, payload: dict, headers: dict, deadline: float) -> object:
    # A one-shot process also bounds DNS executor shutdown. A cancelled asyncio
    # operation alone can still wait indefinitely for libc's resolver thread.
    data = json.dumps([endpoint, payload, headers, deadline])
    if monotonic() >= deadline:
        raise TimeoutError
    process = subprocess.Popen([sys.executable, "-c", _HTTP_CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        output, _ = process.communicate(data, timeout=max(0, deadline - monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()  # Reap the child, including any DNS thread.
        raise TimeoutError from None
    except BaseException:
        process.kill()
        process.communicate()
        raise
    if process.returncode != 0:
        raise PaddleOCRFormatError("PaddleOCR request failed (TransportError)")
    result = json.loads(output)
    if not isinstance(result, dict):
        raise PaddleOCRFormatError("PaddleOCR request failed (TransportError)")
    if "error_type" in result:
        kind = result["error_type"]
        if not isinstance(kind, str) or not kind.isascii() or not kind.isidentifier() or len(kind) > 80:
            kind = "TransportError"
        raise PaddleOCRFormatError(f"PaddleOCR request failed ({kind})")
    if "body" not in result:
        raise PaddleOCRFormatError("PaddleOCR request failed (TransportError)")
    return result["body"]


def _http_child() -> None:
    """Private single-exchange entry: credentials only cross private stdio."""
    try:
        endpoint, payload, headers, deadline = json.load(sys.stdin)
        result = {"body": asyncio.run(_post_page(endpoint, payload, headers, deadline))}
    except Exception as exc:
        result = {"error_type": type(exc).__name__}
    json.dump(result, sys.stdout)


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
        deadline = monotonic() + (min(self.timeout, timeout) if timeout is not None else self.timeout)
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
        if monotonic() >= deadline:
            raise PaddleOCRFormatError("PaddleOCR request failed (TimeoutError)")
        result = self._request(payload, deadline=deadline)
        try:
            unchanged = hashlib.sha256(path.read_bytes()).digest() == before_hash
        except OSError:
            unchanged = False
        if not unchanged:
            raise PaddleOCRFormatError("PaddleOCR source changed during the request")
        require_pages(result, 1)
        page_dimensions(result, 0)
        if monotonic() >= deadline:
            raise PaddleOCRFormatError("PaddleOCR request failed (TimeoutError)")
        return result

    def analyze_full_pdf(self, file_path: str | Path) -> dict:
        """Compatibility entry for already-single-page PDFs; never send a whole multipage source."""
        deadline = monotonic() + self.timeout
        path, page_count = validate_pdf(file_path)
        if page_count != 1:
            raise ValueError("Select one original page with analyze_page")
        return self._request(path.read_bytes(), deadline=deadline)

    def _request(self, content: bytes, *, deadline: float) -> dict:
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
            body = _post_page_sync(self.endpoint, payload, headers, deadline)
        except PaddleOCRFormatError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise PaddleOCRFormatError(f"PaddleOCR request failed ({type(exc).__name__})") from None
        if not isinstance(body, dict):
            raise PaddleOCRFormatError("PaddleOCR response is not an object")
        result = body.get("result")
        if not isinstance(result, dict):
            raise PaddleOCRFormatError("PaddleOCR response lacks required metadata")
        require_pages(result, 1)
        if monotonic() >= deadline:
            raise PaddleOCRFormatError("PaddleOCR request failed (TimeoutError)")
        return result
