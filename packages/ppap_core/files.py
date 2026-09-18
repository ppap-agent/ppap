"""File version identity and remote-fetch SSRF bounds. SHA-256 is content identity, not S3 ETag."""
from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
READY = "ready"
STAGING = "staging"
FAILED = "failed"


class UnsafeSource(Exception):
    pass


class ObjectExists(Exception):
    pass


class ObjectMissing(Exception):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def version_object_key(tenant_id: str, file_id: str, version_id: str) -> str:
    return f"tenants/{tenant_id}/files/{file_id}/versions/{version_id}"


def original_filename(name: str) -> str:
    cleaned = Path(str(name).replace("\\", "/")).name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("invalid_filename")
    return cleaned[:255]


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return _ip_blocked(ip.ipv4_mapped)
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def host_blocked(hostname: str, port: int | None) -> bool:
    host = hostname.strip(".").lower()
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        infos = socket.getaddrinfo(hostname, port or 0, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return True
    if not infos:
        return True
    return any(_ip_blocked(ipaddress.ip_address(entry[4][0])) for entry in infos)


def fetch_remote(url: str, http, max_bytes: int = MAX_UPLOAD_BYTES) -> tuple[bytes, str, str]:
    """Fetch bytes for import. Redirects, credentials, and non-public hosts are rejected."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeSource("invalid_url") from exc
    if parts.scheme not in {"http", "https"} or parts.username or parts.password or not parts.hostname:
        raise UnsafeSource("invalid_url")
    if host_blocked(parts.hostname, parts.port):
        raise UnsafeSource("blocked_host")
    response = http.get(url, follow_redirects=False, timeout=20, headers={"Accept": "*/*"})
    if 300 <= response.status_code < 400:
        raise UnsafeSource("redirect_not_allowed")
    if response.status_code >= 400:
        raise UnsafeSource("fetch_failed")
    body = response.content
    if len(body) > max_bytes:
        raise UnsafeSource("too_large")
    content_type = str(response.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
    name = original_filename(unquote(Path(parts.path).name or "download"))
    return body, content_type or "application/octet-stream", name


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
