"""MCP delegation values. Scopes restrict existing authority; they never grant ACLs."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from product.identity import Session

SCOPES = {
    "identity:read": "读取当前身份与服务能力",
    "files:read": "读取飞书文件夹与已授权文件",
    "files:write": "从飞书导入文件",
    "audits:read": "读取审核任务、结果与历史",
    "audits:write": "准备审核、处理准备中的问题",
    "audits:execute": "开始审核或重审；须逐次确认",
    "reviews:write": "提交人工复核、确认原件事实",
    "reports:read": "读取已授权报告",
    "reports:write": "重新生成报告",
    "reports:send": "向飞书写回或发送报告；须逐次确认",
    "usage:read": "读取额度与费用记录",
    "rulebooks:read": "读取规则库修订、待确认问题与答复",
    "rulebooks:write": "准备规则澄清；发问卷、确认修订与解读须逐次确认",
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


@dataclass(frozen=True)
class ApprovedCall:
    """Internal approval evidence for the exact business call and source values.

    A business adapter checks its materialized source before freezing an effect.
    Later source edits do not change those local bytes. Network headers cannot
    construct this value or move it to another request/grant.
    """

    grant_id: str
    operation_id: str
    method: str
    path: str
    sources: tuple[tuple[str, str], ...]

    @classmethod
    def from_operation(cls, grant_id: str, operation: dict) -> ApprovedCall | None:
        confirmation = operation["confirmation"]
        if confirmation is None:
            return None
        arguments = operation["arguments"]
        paths, values = arguments["probes"], confirmation["sources"]
        if len(paths) != len(values) or not operation["confirmed_by"]:
            raise McpError("invalid_confirmation", 403)
        return cls(grant_id, operation["id"], arguments["method"], arguments["path"], tuple((path, fingerprint(value)) for path, value in zip(paths, values)))

    def require_source(self, path: str, value: object) -> None:
        if (path, fingerprint(value)) not in self.sources:
            raise McpError("confirmation_content_changed", 409)


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


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
