"""Dedicated OCR configuration; no inheritance from text-model settings."""

from __future__ import annotations

import hashlib
import json
import os

DEFAULT_MAX_FILE_SIZE = "100MB"
DEFAULT_ALLOWED_EXTENSIONS = ".pdf,.doc,.docx,.pptx,.xlsx,.xls,.xlsm,.jpg,.jpeg,.png,.gif,.md"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def max_file_size() -> str:
    return _env("MAX_FILE_SIZE") or DEFAULT_MAX_FILE_SIZE


def allowed_extensions() -> str:
    return _env("ALLOWED_EXTENSIONS") or DEFAULT_ALLOWED_EXTENSIONS


def ocr_openai_api_key() -> str:
    return _env("OCR_OPENAI_API_KEY")


def ocr_openai_base_url() -> str:
    return _env("OCR_OPENAI_BASE_URL")


def ocr_model_name() -> str:
    model = _env("OCR_MODEL_NAME")
    if not model:
        raise ValueError("OCR model is not configured: set OCR_MODEL_NAME")
    return model


def effective_ocr_extra_body(raw: str) -> dict:
    """One actual OCR parameter default shared by requests and policy hashes."""
    defaults = {"reasoning": {"effort": "none"}}
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


def ocr_extra_body() -> dict:
    return effective_ocr_extra_body(_env("OCR_OPENAI_EXTRA_BODY_JSON"))


def ocr_policy_identity() -> str:
    """Hash actual non-secret configuration, not credentials or text defaults."""
    value = {
        "protocol": "native_paddle_responses_v3",
        "model": _env("OCR_MODEL_NAME"),
        "endpoint_sha256": hashlib.sha256(ocr_openai_base_url().encode()).hexdigest(),
        "extra_body": ocr_extra_body(),
        "timeout": 20,
        "max_retries": 0,
        "paddle_endpoint_sha256": hashlib.sha256(_env("PADDLE_OCR_URL").encode()).hexdigest(),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
