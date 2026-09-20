"""Runtime logs for locating a request, tenant, round, job, or attempt.

Log records are identifiers and outcome names only. Tokens and customer content
do not belong in this stream.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any

REQUEST_ID: ContextVar[str] = ContextVar("ppap_request_id", default="")
TENANT_ID: ContextVar[str] = ContextVar("ppap_tenant_id", default="")
ROUND_ID: ContextVar[str] = ContextVar("ppap_round_id", default="")
JOB_ID: ContextVar[str] = ContextVar("ppap_job_id", default="")
ATTEMPT: ContextVar[str] = ContextVar("ppap_attempt", default="")

_CONTEXT = {
    "request_id": REQUEST_ID,
    "tenant_id": TENANT_ID,
    "round_id": ROUND_ID,
    "job_id": JOB_ID,
    "attempt": ATTEMPT,
}
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_ROUND_PATH = re.compile(r"^/rounds/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
_SECRET_KEYS = {"access_token", "refresh_token", "authorization", "cookie", "password", "api_key", "token"}
QUIET_PATHS = {"/health", "/ready", "/version", "/public-config"}
LOGGER = logging.getLogger("ppap")


def new_request_id(raw: str = "") -> str:
    candidate = (raw or "").strip()[:128]
    if _SAFE_REQUEST_ID.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())


def round_id_from_path(path: str) -> str:
    match = _ROUND_PATH.match(path or "")
    return match.group(1) if match else ""


def bind(**fields: Any) -> list[Token]:
    tokens: list[Token] = []
    for name, value in fields.items():
        tokens.append(_CONTEXT[name].set("" if value is None else str(value)))
    return tokens


def reset(tokens: list[Token]) -> None:
    for token in reversed(tokens):
        token.var.reset(token)


def snapshot() -> dict[str, str]:
    return {name: value for name, var in _CONTEXT.items() if (value := var.get())}


class JsonFormatter(logging.Formatter):
    def __init__(self, git_sha: str = "") -> None:
        super().__init__()
        self.git_sha = git_sha

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        if self.git_sha:
            payload["git_sha"] = self.git_sha
        payload.update(snapshot())
        extra = getattr(record, "ppap", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key in _SECRET_KEYS or value in (None, ""):
                    continue
                payload[str(key)] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


class RedactQuery(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn access logs otherwise include signed file capabilities and OAuth codes.
        if isinstance(record.args, tuple) and len(record.args) == 5 and isinstance(record.args[2], str):
            values = list(record.args)
            values[2] = values[2].split('?', 1)[0]
            record.args = tuple(values)
        return True


def configure(settings: Any) -> None:
    access = logging.getLogger('uvicorn.access')
    if not any(isinstance(item, RedactQuery) for item in access.filters):
        access.addFilter(RedactQuery())
    handler = logging.StreamHandler(sys.stderr)
    fmt = str(getattr(settings, "LOG_FORMAT", "text") or "text").strip().lower()
    profile = str(getattr(settings, "profile", "") or "")
    if profile == "production" or fmt == "json":
        handler.setFormatter(JsonFormatter(str(getattr(settings, "git_sha", "") or "")))
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(getattr(logging, str(getattr(settings, "LOG_LEVEL", "INFO") or "INFO").upper(), logging.INFO))
    LOGGER.propagate = False


def emit(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    LOGGER.log(level, event, extra={"ppap": fields})
