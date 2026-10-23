"""MCP delegation values. Scopes restrict existing authority; they never grant ACLs."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ppap_core.identity import Session

SCOPES = {
    "identity:read": "读取当前身份与服务能力",
    "files:read": "读取已授权文件、原件及证据",
    "files:write": "上传、导入新文件版本与预校验",
    "rules:read": "读取规则、解析结果及来源",
    "rules:write": "解析规则；发布须逐次确认",
    "audits:read": "读取任务、轮次与历史",
    "audits:write": "准备任务、匹配、调整输入及通知设置",
    "audits:execute": "受理审核或重审；须逐次确认",
    "reviews:write": "提交人工复核；须本人逐次确认",
    "reports:read": "读取和下载已授权报告",
    "reports:write": "生成、打包或恢复报告",
    "reports:send": "向飞书写回或发送报告；须逐次确认",
    "usage:read": "读取额度与费用记录",
}
READ_SCOPES = tuple(s for s in SCOPES if s.endswith(":read"))
MAX_MCP_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 96 * 1024


class McpError(ValueError):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Delegation:
    id: str
    client_id: str
    scopes: frozenset[str]
    session: Session = field(repr=False)

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise McpError("insufficient_scope", 403)


def parse_scopes(value: str) -> list[str]:
    scopes = sorted(set(value.split()))
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
