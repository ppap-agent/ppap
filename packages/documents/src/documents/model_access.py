"""Which model an execution uses, resolved at each call and never stored in frozen inputs.

The platform's configured models are the default. A tenant may supply its own key
(tenant BYOK); the product layer then installs a scope that resolves to it. A scope is
authoritative, including an unavailable scope: inside it the process configuration is
never consulted. Resolvers run at the actual model-call boundary, so a key rotation or
removal takes effect on the next call.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import SecretStr

MODEL_PROVIDER = "responses-compatible"
# Uses of a model. Coding is the Deep agent; text and vision are direct calls.
ModelPurpose = Literal["text", "vision", "coding"]
PURPOSES: tuple[ModelPurpose, ...] = ("text", "vision", "coding")
ISOLATED_DEV_LLM_BASE_URL = "https://api.trustedtunnel.app/v1"  # pragma: allowlist secret
ISOLATED_DEV_LLM_MODEL = "gpt-5.6-luna"  # pragma: allowlist secret


class ModelUnavailable(RuntimeError):
    """No usable model for this call; no provider payload or secret in errors."""

    def __init__(self, code: str = "model_unavailable") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ModelAccess:
    api_key: SecretStr = field(repr=False)
    model: str
    base_url: str
    provider: str = MODEL_PROVIDER
    # "platform" or "tenant": who owns the key this call spends.
    source: str = "platform"
    revision: str = ""
    reasoning_effort: str = ""
    extra_body_json: str = field(default="", repr=False)

    def extra_body(self) -> dict[str, Any]:
        """Provider-specific request options owned by whoever configured this model."""
        if not self.extra_body_json:
            return {}
        try:
            value = json.loads(self.extra_body_json)
        except ValueError:
            raise ValueError("Model options must contain one JSON object") from None
        if not isinstance(value, dict):
            raise ValueError("Model options must contain one JSON object")
        return value

    def identity(self) -> dict[str, str]:
        """Opaque execution identity: never a credential or a private endpoint URL."""
        options = self.extra_body()
        revision = self.revision
        if options or self.reasoning_effort:
            revision = hashlib.sha256(json.dumps([revision, options, self.reasoning_effort], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "revision": revision,
            "deployment": hashlib.sha256(self.base_url.encode()).hexdigest(),
        }


ModelResolver = Callable[[ModelPurpose], ModelAccess]
_ACCESS: ContextVar[ModelResolver | None] = ContextVar("model_resolver", default=None)


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def effective_vision_extra_body(raw: str) -> dict[str, Any]:
    """The platform OCR parameter default shared by requests and policy hashes."""
    defaults: dict[str, Any] = {"reasoning": {"effort": "none"}}
    raw = raw.strip()
    if not raw:
        return defaults
    try:
        value = json.loads(raw)
    except ValueError:
        raise ValueError("OCR_OPENAI_EXTRA_BODY_JSON must contain one JSON object") from None
    if not isinstance(value, dict):
        raise ValueError("OCR_OPENAI_EXTRA_BODY_JSON must contain one JSON object")
    return {**defaults, **value}


def platform_model_access(purpose: ModelPurpose) -> ModelAccess:
    """The platform model for one use, read from this process's deployment configuration."""
    isolated = (_env("PPAP_PROFILE") or "isolated-dev") == "isolated-dev"
    text_url = _env("LLM_BASE_URL") or (ISOLATED_DEV_LLM_BASE_URL if isolated else "")
    text_model = _env("LLM_MODEL") or (ISOLATED_DEV_LLM_MODEL if isolated else "")
    if purpose == "text":
        base_url, model, key, effort, extra = text_url, text_model, _env("LLM_API_KEY"), _env("OPENAI_REASONING_EFFORT"), ""
    elif purpose == "vision":
        base_url, model, key, effort = _env("OCR_OPENAI_BASE_URL"), _env("OCR_MODEL_NAME"), _env("OCR_OPENAI_API_KEY"), ""
        extra = json.dumps(effective_vision_extra_body(_env("OCR_OPENAI_EXTRA_BODY_JSON")), sort_keys=True)
    elif purpose == "coding":
        overridden = bool(_env("PPAP_DEEP_LLM_BASE_URL") or _env("PPAP_DEEP_LLM_MODEL"))
        base_url = _env("PPAP_DEEP_LLM_BASE_URL") if overridden else text_url
        model = _env("PPAP_DEEP_LLM_MODEL") if overridden else text_model
        key = _env("PPAP_DEEP_LLM_API_KEY") if overridden else _env("LLM_API_KEY")
        effort, extra = _env("PPAP_DEEP_REASONING_EFFORT"), ""
    else:
        raise ModelUnavailable()
    # The identity depends only on non-secret configuration, so every process agrees on it
    # even when only some hold the key. A missing key fails at call time instead.
    if not base_url or not model:
        raise ModelUnavailable("platform_model_unconfigured")
    revision = hashlib.sha256(json.dumps({"purpose": purpose, "model": model, "base_url": base_url}, sort_keys=True).encode()).hexdigest()
    return ModelAccess(api_key=SecretStr(key), model=model, base_url=base_url, source="platform", revision=revision, reasoning_effort=effort, extra_body_json=extra)


def model_access_scoped() -> bool:
    return _ACCESS.get() is not None


def current_model_access(purpose: ModelPurpose = "text") -> ModelAccess:
    resolver = _ACCESS.get()
    access = resolver(purpose) if resolver is not None else platform_model_access(purpose)
    if not access.api_key.get_secret_value():
        raise ModelUnavailable("platform_model_unconfigured" if access.source == "platform" else "model_unavailable")
    return access


@contextmanager
def model_access_scope(access: ModelAccess | ModelResolver | None) -> Iterator[None]:
    """Bind this execution's model. None means no model call is allowed here."""

    def resolve(purpose: ModelPurpose) -> ModelAccess:
        if access is None:
            raise ModelUnavailable()
        return access(purpose) if callable(access) else access

    token = _ACCESS.set(resolve)
    try:
        yield
    finally:
        _ACCESS.reset(token)
