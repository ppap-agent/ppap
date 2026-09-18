"""OCR adapter selection by mode."""

from __future__ import annotations

from urllib.parse import urlparse

from ppap_core.settings import get_settings

from .base import OcrAdapter
from .local import LocalOcrAdapter
from .remote import RemoteOcrAdapter

VALID_OCR_ADAPTER_MODES = {"auto", "local", "remote"}


def normalize_ocr_adapter_mode(mode: str | None = None) -> str:
    """Return a normalized adapter mode."""
    if mode is None:
        return get_settings().get_ocr_adapter_mode()

    normalized = mode.strip().lower()
    if normalized not in VALID_OCR_ADAPTER_MODES:
        raise ValueError(f"Invalid OCR adapter mode: {mode}")
    return normalized


def _resolve_remote_host(host: str | None) -> str:
    configured = (host or get_settings().OCR_REMOTE_URL).strip()
    if not configured:
        raise ValueError("OCR_REMOTE_URL must be set when OCR_ADAPTER_MODE=remote")

    parsed = urlparse(configured)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid OCR remote host: {configured}")
    return configured


def get_ocr_adapter(mode: str | None = None, host: str | None = None) -> OcrAdapter:
    """Return OCR adapter instance for the given mode.

    auto/local -> LocalOcrAdapter (in-process)
    remote -> RemoteOcrAdapter (HTTP)
    """
    resolved_mode = normalize_ocr_adapter_mode(mode)
    if resolved_mode == "remote":
        return RemoteOcrAdapter(host=_resolve_remote_host(host))
    return LocalOcrAdapter()
