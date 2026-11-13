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


def _vision():
    from documents.model_access import ModelUnavailable, current_model_access

    try:
        return current_model_access("vision")
    except ModelUnavailable:
        # Versioned checkers read "not configured" as ValueError and keep evidence unknown.
        raise ValueError("Vision model is not configured") from None


# Stable reads of the vision model for versioned checkers; the key itself never leaves trusted code.
def ocr_openai_api_key() -> str:
    return _vision().api_key.get_secret_value()


def ocr_openai_base_url() -> str:
    return _vision().base_url


def ocr_model_name() -> str:
    return _vision().model


def ocr_extra_body() -> dict:
    return _vision().extra_body()


def ocr_policy_identity() -> str:
    """Hash the vision model identity, never credentials."""
    from documents.model_access import ModelUnavailable, current_model_access

    try:
        model = current_model_access("vision").identity()
    except ModelUnavailable:
        model = {"source": "unavailable"}
    value = {
        "protocol": "responses_v6",
        "model": model,
        "timeout": 20,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
