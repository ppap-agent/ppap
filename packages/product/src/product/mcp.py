"""MCP delegation values. Scopes restrict existing authority; they never grant ACLs."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from product.identity import Session
from product.model_endpoint import ModelEndpointError, normalize_base_url

SCOPES = {
    "identity:read": "读取当前身份与服务能力",
    "files:read": "读取飞书文件夹与已授权文件",
    "files:write": "从飞书导入文件",
    "audits:read": "读取审核任务、结果与历史",
    "audits:write": "准备审核、处理准备中的问题",
    "audits:execute": "提议开始审核或重审，由你在网页完成",
    "reviews:write": "提交人工复核、确认原件事实；提议签发，由你在网页完成",
    "reports:read": "读取已授权报告",
    "reports:write": "提议重新生成报告，由你在网页完成",
    "reports:send": "提议在飞书交付报告，由你在网页完成",
    "usage:read": "读取额度与费用记录",
    "rulebooks:read": "读取规则库修订、待确认问题与答复",
    "rulebooks:write": "准备规则澄清；发问卷、确认修订与解读由平台负责人在网页完成",
}
READ_SCOPES = tuple(s for s in SCOPES if s.endswith(":read"))
MAX_MCP_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 96 * 1024


class McpError(ValueError):
    def __init__(self, code: str, status: int = 400, missing_scope: str | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.missing_scope = missing_scope


@dataclass(frozen=True)
class Delegation:
    id: str
    client_id: str
    scopes: frozenset[str]
    session: Session = field(repr=False)

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise McpError("insufficient_scope", 403, missing_scope=scope)


# Scopes that once existed. Clients registered before their removal still ask for them; they grant nothing now and are
# dropped instead of failing the client's sign-in or refresh.
RETIRED_SCOPES = frozenset({"rules:read", "rules:write"})


def parse_scopes(value: str) -> list[str]:
    scopes = sorted(set(value.split()) - RETIRED_SCOPES)
    if not scopes or not set(scopes).issubset(SCOPES):
        raise McpError("invalid_scope")
    return scopes


def pkce_challenge(verifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise McpError("invalid_grant")
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")


def valid_redirect(uri: str) -> bool:
    try:
        p = urlsplit(uri)
        return bool(
            len(uri) <= 2048
            and p.hostname
            and not p.username
            and not p.password
            and not p.fragment
            and "\\" not in uri
            and not any(ord(c) < 33 for c in uri)
            and (p.scheme == "https" or (p.scheme == "http" and p.hostname in {"127.0.0.1", "::1", "localhost"}))
        )
    except ValueError:
        return False


GRANT_TYPES = ("authorization_code", "refresh_token")


def valid_client_metadata(metadata: Mapping) -> bool:
    """What PPAP accepts of any client, registered or named by its own document."""
    redirects = metadata.get("redirect_uris")
    name = metadata.get("client_name", "MCP client")
    grants = metadata.get("grant_types", list(GRANT_TYPES))
    return bool(
        isinstance(redirects, list)
        and 1 <= len(redirects) <= 10
        and all(isinstance(u, str) and valid_redirect(u) for u in redirects)
        and isinstance(name, str)
        and 1 <= len(name) <= 100
        and metadata.get("token_endpoint_auth_method", "none") in {"none", "client_secret_post", "client_secret_basic"}
        and isinstance(grants, list)
        and all(isinstance(g, str) for g in grants)
        and not set(grants) - set(GRANT_TYPES)
        and metadata.get("response_types", ["code"]) == ["code"]
    )


# Client ID Metadata Documents (CIMD, MCP authorization 2025-11-25): a client_id that is an HTTPS address names a JSON
# document the client publishes there. PPAP reads it itself and keeps a copy in the client's record, under this key,
# with the time it was read and until when it holds by the document's HTTP cache headers.
DOCUMENT = "ppap_document"
DOCUMENT_MAX_BYTES = 5 * 1024
DOCUMENT_TIMEOUT = 5.0
DOCUMENT_DEFAULT_TTL = 3600
DOCUMENT_MAX_TTL = 86400
NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def document_client_id(value: str) -> bool:
    """A client_id that names its own metadata document: a canonical HTTPS address with a path, held to the same rules
    as a tenant's model address (no credentials, query, fragment, dot segments, localhost or numeric aliases)."""
    if not isinstance(value, str) or not value.startswith("https://"):
        return False
    try:
        return normalize_base_url(value) == value and urlsplit(value).path not in {"", "/"}
    except ModelEndpointError:
        return False


def public_address(value: str) -> bool:
    """Only a globally routable unicast address may be fetched: never loopback, private, link-local or reserved, nor an
    IPv6 form that carries one of those (mapped, 6to4, Teredo, NAT64)."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return public_address(str(address.ipv4_mapped))
        if address.sixtofour or address.teredo or any(address in network for network in NAT64):
            return False
    return address.is_global and not address.is_multicast


def resolve_host(host: str, port: int) -> list[str]:
    try:
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except (OSError, UnicodeError):
        return []


def fresh_until(headers: Mapping[str, str], now: float) -> int:
    """Until when a read document holds, by its HTTP cache headers (RFC 9111), at most a day."""
    directives = {}
    for part in headers.get("cache-control", "").lower().split(","):
        key, _, value = part.strip().partition("=")
        directives[key] = value.strip().strip('"')
    if "no-store" in directives or "no-cache" in directives:
        return int(now)
    ttl = DOCUMENT_DEFAULT_TTL
    if "max-age" in directives:
        ttl = int(directives["max-age"]) if directives["max-age"].isdigit() else 0
    elif headers.get("expires"):
        try:
            expires = parsedate_to_datetime(headers["expires"])
            ttl = int((expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)).timestamp() - now)
        except (TypeError, ValueError):
            ttl = 0  # An Expires that cannot be read means already stale.
    return int(now + min(max(ttl, 0), DOCUMENT_MAX_TTL))


def read_client_document(url: str, *, resolve: Callable[[str, int], list[str]] = resolve_host, transport: httpx.BaseTransport | None = None) -> dict:
    """Read and check the metadata document a client_id names, and return it as the client's record.

    HTTPS only; every address the name resolves to must be public, and the connection goes to the address that was
    checked; at most 5 KB in 5 seconds; no redirects. The document must name exactly this address as its client_id,
    declare its callbacks, and hold no secret: such a client authenticates with PKCE alone."""
    if not document_client_id(url):
        raise McpError("invalid_client_id")
    parts = urlsplit(url)
    host, port = parts.hostname or "", parts.port or 443
    addresses = resolve(host, port)
    if not addresses or not all(public_address(a) for a in addresses):
        raise McpError("client_document_address")
    pinned = f"[{addresses[0]}]" if ":" in addresses[0] else addresses[0]
    deadline = time.monotonic() + DOCUMENT_TIMEOUT
    body = bytearray()
    try:
        with (
            httpx.Client(transport=transport, timeout=DOCUMENT_TIMEOUT, follow_redirects=False, trust_env=False) as client,
            client.stream(
                "GET", urlunsplit(("https", f"{pinned}:{port}", parts.path, "", "")), headers={"Host": parts.netloc, "Accept": "application/json"}, extensions={"sni_hostname": host}
            ) as response,
        ):
            if response.status_code != 200:
                raise McpError("client_document_unavailable")
            declared = response.headers.get("content-length", "0")
            if not declared.isdigit() or int(declared) > DOCUMENT_MAX_BYTES:
                raise McpError("client_document_too_large")
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > DOCUMENT_MAX_BYTES:
                    raise McpError("client_document_too_large")
                if time.monotonic() > deadline:
                    raise McpError("client_document_timeout")
            headers = dict(response.headers)
    except httpx.TimeoutException:
        raise McpError("client_document_timeout") from None
    except httpx.HTTPError:
        raise McpError("client_document_unavailable") from None
    try:
        document = json.loads(bytes(body))
    except (ValueError, UnicodeError):
        raise McpError("invalid_client_metadata") from None
    if not isinstance(document, dict) or document.get("client_id") != url:
        raise McpError("client_document_mismatch")
    record = {
        "client_id": url,
        "client_name": document.get("client_name", host),
        "redirect_uris": document.get("redirect_uris"),
        "token_endpoint_auth_method": "none",
        "grant_types": document.get("grant_types", list(GRANT_TYPES)),
        "response_types": document.get("response_types", ["code"]),
        # Like a client registered without a scope, it may ask for any; the person picks what is granted.
        "scope": " ".join(SCOPES),
    }
    if not valid_client_metadata(record) or {"client_secret", "client_secret_expires_at"} & document.keys() or document.get("token_endpoint_auth_method", "none") != "none":
        raise McpError("invalid_client_metadata")
    now = time.time()
    return {**record, DOCUMENT: {"read_at": int(now), "fresh_until": fresh_until(headers, now)}}


def document_fresh(metadata: Mapping, now: float) -> bool:
    document = metadata.get(DOCUMENT)
    return isinstance(document, dict) and document.get("fresh_until", 0) > now


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
