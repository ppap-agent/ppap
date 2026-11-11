"""Exact administrator-owned destinations for tenant Responses credentials.

No user-controlled URL is an open network capability. Administrators explicitly
trust each full HTTPS base URL, including internal providers, and own its DNS,
TLS and network routing. This is not an arbitrary-URL proxy or a DNS firewall.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


class ModelEndpointError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def normalize_base_url(value: str) -> str:
    """Normalize one HTTPS prefix, without accepting hidden credentials or URLs."""
    if not isinstance(value, str) or not value or len(value) > 2048 or value != value.strip() or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ModelEndpointError("invalid_model_endpoint")
    try:
        url = urlsplit(value)
        hostname = url.hostname or ""
        port = url.port
        if (
            url.scheme != "https"
            or not hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or "?" in value
            or "#" in value
            or "\\" in value
            or "%" in value
            or hostname.endswith(".")
            or hostname == "localhost"
            or hostname.endswith(".localhost")
            or hostname in {"metadata.google.internal", "instance-data.ec2.internal"}
            or (port is not None and not 1 <= port <= 65535)
            or any(segment in {".", ".."} for segment in url.path.split("/"))
            or "//" in url.path
        ):
            raise ValueError()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname):
                raise ValueError() from None
            # Reject noncanonical numeric address aliases (integer/octal/hex).
            if all(label.isdigit() or label.startswith("0x") for label in hostname.split(".")):
                raise ValueError() from None
        else:
            if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast or address.is_reserved:
                raise ValueError()
            hostname = f"[{address.compressed}]" if address.version == 6 else address.compressed
        authority = hostname + (f":{port}" if port not in {None, 443} else "")
        return urlunsplit(("https", authority, url.path.rstrip("/"), "", ""))
    except (ValueError, UnicodeError):
        raise ModelEndpointError("invalid_model_endpoint") from None


def approved_base_url(settings, value: str) -> str:
    endpoint = normalize_base_url(value)
    approved = set()
    for candidate in settings.tenant_model_allowed_base_urls.split(","):
        if candidate.strip():
            try:
                approved.add(normalize_base_url(candidate.strip()))
            except ModelEndpointError:
                # A broken operator entry grants no network access.
                continue
    if endpoint not in approved:
        raise ModelEndpointError("model_endpoint_not_allowed")
    return endpoint


def valid_model(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value):
        raise ModelEndpointError("invalid_model_model")
    return value
