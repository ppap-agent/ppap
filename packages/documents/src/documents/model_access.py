"""Execution-local model identity shared by text and visual clients.

A scope is authoritative, including an unavailable scope. Never consult process
credentials inside it. Resolvers run at the actual model-call boundary, so a
revocation takes effect on the next call, not only the next audit.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

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
    try:
        yield
    finally:
        _ACCESS.reset(token)
