"""File version identity. SHA-256 is content identity, not S3 ETag."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from audit.storage_errors import ObjectExists, ObjectMissing, ObjectTooLarge  # noqa: F401

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
READY = "ready"
STAGING = "staging"
FAILED = "failed"


@dataclass(frozen=True)
class ImportedFile:
    """Captured source bytes and optional provider provenance, before storage."""

    data: bytes
    filename: str
    content_type: str
    origin: dict[str, str] | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def version_object_key(tenant_id: str, file_id: str, version_id: str) -> str:
    return f"tenants/{tenant_id}/files/{file_id}/versions/{version_id}"


_MEDIA_TYPE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+/[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_ATTR_CHAR = "!#$&+-.^_`|~"


def original_filename(name: str) -> str:
    cleaned = Path(str(name).replace("\\", "/")).name
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and ord(ch) >= 32)
    cleaned = cleaned.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("invalid_filename")
    return cleaned[:255]


def safe_content_type(value: str | None) -> str:
    raw = str(value or "").split(";", 1)[0].strip()
    if raw.isascii() and _MEDIA_TYPE.fullmatch(raw):
        return raw
    return "application/octet-stream"


def _ascii_filename_fallback(name: str) -> str:
    fallback = "".join(ch if 32 < ord(ch) < 127 and ch not in {'"', "\\"} else "_" for ch in name)
    fallback = fallback.strip() or "download"
    if fallback in {".", ".."}:
        return "download"
    return fallback[:255]


def attachment_content_disposition(filename: str) -> str:
    """ASCII filename plus RFC 5987 filename*; the whole header stays latin-1."""
    try:
        name = original_filename(filename)
    except ValueError:
        name = "download"
    encoded = quote(name, safe=_ATTR_CHAR)
    return f"attachment; filename=\"{_ascii_filename_fallback(name)}\"; filename*=UTF-8''{encoded}"


@dataclass
class FileVersion:
    id: str
    file_id: str
    tenant_id: str
    original_filename: str
    content_type: str
    byte_size: int
    object_key: str
    status: str
    sha256: str | None = None

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "file_id": self.file_id,
            "original_filename": self.original_filename,
            "content_type": self.content_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "status": self.status,
        }
