"""File version identity and remote-fetch SSRF bounds. SHA-256 is content identity, not S3 ETag."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

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
    return f'attachment; filename="{_ascii_filename_fallback(name)}"; filename*=UTF-8\'\'{encoded}'


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


def _literal_host_blocked(hostname: str) -> bool:
    host = hostname.strip(".").lower()
    return not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local")


def public_addresses(hostname: str, port: int | None) -> tuple[str, ...]:
    """Resolve once. Any private/loopback answer fails closed so a later lookup cannot rebind."""
    if _literal_host_blocked(hostname):
        raise UnsafeSource("blocked_host")
    try:
        infos = socket.getaddrinfo(hostname, port or 0, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeSource("blocked_host") from exc
    if not infos:
        raise UnsafeSource("blocked_host")
    addresses: list[str] = []
    seen: set[str] = set()
    for entry in infos:
        ip = ipaddress.ip_address(entry[4][0])
        if _ip_blocked(ip):
            raise UnsafeSource("blocked_host")
        text = str(ip)
        if text not in seen:
            seen.add(text)
            addresses.append(text)
    return tuple(addresses)


def host_blocked(hostname: str, port: int | None) -> bool:
    try:
        public_addresses(hostname, port)
    except UnsafeSource:
        return True
    return False


def _pinned_url(parts, ip: str) -> str:
    port = parts.port or (443 if parts.scheme == "https" else 80)
    host = f"[{ip}]" if ":" in ip else ip
    return urlunsplit((parts.scheme, f"{host}:{port}", parts.path or "/", parts.query, ""))


def _host_header(parts) -> str:
    default = 443 if parts.scheme == "https" else 80
    if parts.port and parts.port != default:
        return f"{parts.hostname}:{parts.port}"
    return str(parts.hostname)


def _limited_body(response, max_bytes: int) -> bytes:
    try:
        iterator = getattr(response, "iter_bytes", None)
        if callable(iterator):
            buf = bytearray()
            for chunk in iterator():
                if not chunk:
                    continue
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise UnsafeSource("too_large")
            return bytes(buf)
        body = response.content
        if len(body) > max_bytes:
            raise UnsafeSource("too_large")
        return body
    except UnsafeSource:
        close = getattr(response, "close", None)
        if callable(close):
            close()
        raise


def _request_pinned(http, url: str, headers: dict[str, str], hostname: str):
    kwargs = {
        "follow_redirects": False,
        "timeout": 20,
        "headers": headers,
        "extensions": {"sni_hostname": hostname},
    }
    stream = getattr(http, "stream", None)
    if callable(stream):
        try:
            return stream("GET", url, **kwargs)
        except TypeError:
            kwargs.pop("extensions", None)
            try:
                return stream("GET", url, **kwargs)
            except TypeError:
                return stream("GET", url)
    try:
        return http.get(url, **kwargs)
    except TypeError:
        kwargs.pop("extensions", None)
        try:
            return http.get(url, **kwargs)
        except TypeError:
            return http.get(url, follow_redirects=False, timeout=20, headers=headers)


def _finish_fetch(result, max_bytes: int, parts) -> tuple[bytes, str, str]:
    if not hasattr(result, "status_code") and hasattr(result, "__enter__") and hasattr(result, "__exit__"):
        with result as response:
            return _finish_fetch(response, max_bytes, parts)
    status = int(result.status_code)
    if 300 <= status < 400:
        raise UnsafeSource("redirect_not_allowed")
    if status >= 400:
        raise UnsafeSource("fetch_failed")
    body = _limited_body(result, max_bytes)
    content_type = safe_content_type(result.headers.get("content-type"))
    name = original_filename(unquote(Path(parts.path).name or "download"))
    return body, content_type, name


def fetch_remote(url: str, http, max_bytes: int = MAX_UPLOAD_BYTES) -> tuple[bytes, str, str]:
    """Fetch bytes for import. Redirects, credentials, and non-public hosts are rejected.

    The TCP/TLS connection uses the already-approved public address so a second DNS
    lookup cannot retarget the request. The body is read with a hard byte cap.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeSource("invalid_url") from exc
    if parts.scheme not in {"http", "https"} or parts.username or parts.password or not parts.hostname:
        raise UnsafeSource("invalid_url")
    approved = public_addresses(parts.hostname, parts.port)
    headers = {"Host": _host_header(parts), "Accept": "*/*"}
    pinned = _pinned_url(parts, approved[0])
    result = _request_pinned(http, pinned, headers, parts.hostname)
    return _finish_fetch(result, max_bytes, parts)


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
