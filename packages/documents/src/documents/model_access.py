"""Execution-local model identity shared by text and visual clients.

A scope is authoritative, including an unavailable scope. Never consult process
credentials inside it. Resolvers run at the actual model-call boundary, so a
revocation takes effect on the next call, not only the next audit.
"""

from __future__ import annotations

import hashlib
import json
import threading
from _thread import LockType
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

DEEPSEEK_PROVIDER = "deepseek"
DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class ModelUnavailable(RuntimeError):
    """No usable personal credential; no provider payload or secret in errors."""

    def __init__(self, code: str = "personal_model_unavailable") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ModelAccess:
    api_key: SecretStr = field(repr=False)
    provider: str = DEEPSEEK_PROVIDER
    model: str = DEEPSEEK_MODEL
    base_url: str = DEEPSEEK_BASE_URL


ModelResolver = Callable[[], ModelAccess]
_ACCESS: ContextVar[ModelResolver | None] = ContextVar("personal_model_resolver", default=None)


@dataclass
class _PendingRequests:
    # Only request digests live here, never source text, images, or credentials.
    unresolved: set[str] = field(default_factory=set)
    lock: LockType = field(default_factory=threading.Lock)


_REQUESTS: ContextVar[_PendingRequests | None] = ContextVar("personal_model_requests", default=None)


@contextmanager
def model_request_scope(endpoint: str, request: dict[str, Any]) -> Iterator[Callable[[Any], None]]:
    """Do not replay a pending/uncertain inference elsewhere in the same run.

    SDK retry settings alone do not protect a second clause that requests the
    same evidence after the first request timed out. A known token receipt can
    settle the request; timeout, cancellation, or missing usage cannot.
    """
    pending = _REQUESTS.get()
    if pending is None:
        yield lambda response: None
        return
    # Changing only the local timeout does not make a new paid inference.
    body = {key: value for key, value in request.items() if key != "timeout"}
    digest = hashlib.sha256(json.dumps([endpoint, body], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    with pending.lock:
        if digest in pending.unresolved:
            raise ModelUnavailable("personal_model_request_uncertain")
        pending.unresolved.add(digest)

    def settle(response: Any) -> None:
        usage = getattr(response, "usage", None)
        if all(type(value := getattr(usage, name, None)) is int and value >= 0 for name in ("input_tokens", "output_tokens")):
            with pending.lock:
                pending.unresolved.discard(digest)

    yield settle


def model_access_scoped() -> bool:
    return _ACCESS.get() is not None


def current_model_access() -> ModelAccess:
    resolver = _ACCESS.get()
    if resolver is None:
        raise ModelUnavailable()
    access = resolver()
    if not access.api_key.get_secret_value():
        raise ModelUnavailable()
    return access


@contextmanager
def model_access_scope(access: ModelAccess | ModelResolver | None) -> Iterator[None]:
    def resolve() -> ModelAccess:
        if access is None:
            raise ModelUnavailable()
        return access() if callable(access) else access

    token = _ACCESS.set(resolve)
    requests_token = _REQUESTS.set(_PendingRequests())
    try:
        yield
    finally:
        _REQUESTS.reset(requests_token)
        _ACCESS.reset(token)
