"""Process environment for physical reads.

This module does not import product settings. It reads the same environment
names and applies the same OCR fallbacks as product settings.
"""

from __future__ import annotations

import os

ISOLATED_DEV_LLM_BASE_URL = "https://api.trustedtunnel.app/v1"  # pragma: allowlist secret
ISOLATED_DEV_LLM_MODEL = "gpt-5.6-luna"  # pragma: allowlist secret
DEFAULT_OCR_OPENAI_BASE_URL = "https://gateway.ai.cloudflare.com/v1/b1a6ed92ee3fad00dcbb1e3e57327c02/openrouter/openrouter/v1"
DEFAULT_MAX_FILE_SIZE = "100MB"
DEFAULT_ALLOWED_EXTENSIONS = ".pdf,.doc,.docx,.pptx,.xlsx,.xls,.xlsm,.jpg,.jpeg,.png,.gif,.md"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _profile() -> str:
    return _env("PPAP_PROFILE") or "isolated-dev"


def _llm_base_url() -> str:
    value = _env("LLM_BASE_URL")
    if value:
        return value
    if _profile() == "isolated-dev":
        return ISOLATED_DEV_LLM_BASE_URL
    return ""


def _llm_model() -> str:
    value = _env("LLM_MODEL")
    if value:
        return value
    if _profile() == "isolated-dev":
        return ISOLATED_DEV_LLM_MODEL
    return ""


def _openai_api_key() -> str:
    return _env("OPENAI_API_KEY") or _env("LLM_API_KEY")


def _openai_base_url() -> str:
    return _env("OPENAI_BASE_URL") or _llm_base_url()


def _model_name() -> str:
    return _env("MODEL_NAME") or _llm_model()


def max_file_size() -> str:
    return _env("MAX_FILE_SIZE") or DEFAULT_MAX_FILE_SIZE


def allowed_extensions() -> str:
    return _env("ALLOWED_EXTENSIONS") or DEFAULT_ALLOWED_EXTENSIONS


def paddle_evidence_enabled() -> bool:
    """Production Paddle consumption requires an explicit opt-in."""
    value = _env("PADDLE_EVIDENCE_ENABLED").lower()
    if value in {"", "0", "false", "off", "no"}:
        return False
    if value in {"1", "true", "on", "yes"}:
        return True
    raise ValueError("PADDLE_EVIDENCE_ENABLED must be a boolean")


def ocr_remote_url() -> str:
    return _env("OCR_REMOTE_URL")


def ocr_local_oss_dir() -> str:
    return _env("OCR_LOCAL_OSS_DIR")


def ocr_local_sqlite_db() -> str:
    return _env("OCR_LOCAL_SQLITE_DB")


def ocr_adapter_mode() -> str:
    mode = (_env("OCR_ADAPTER_MODE") or "auto").lower()
    if mode in {"auto", "local", "remote"}:
        return mode
    raise ValueError(f"Invalid OCR_ADAPTER_MODE: {os.environ.get('OCR_ADAPTER_MODE', '')}")


def ocr_openai_api_key() -> str:
    return _env("OCR_OPENAI_API_KEY") or _openai_api_key()


def ocr_openai_base_url() -> str:
    return _env("OCR_OPENAI_BASE_URL") or _openai_base_url() or DEFAULT_OCR_OPENAI_BASE_URL


def ocr_model_name() -> str:
    model = _env("OCR_MODEL_NAME") or _model_name()
    if not model:
        raise ValueError("OCR model is not configured: set OCR_MODEL_NAME, MODEL_NAME, or LLM_MODEL")
    return model
