"""Canonical import path for API, worker, packages, and tests."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PYTHONPATH_PARTS = (
    ROOT / "packages",
    ROOT / "packages" / "ppap_docops" / "src",
    ROOT / "packages" / "ppap_excel_audit_engine" / "src",
    ROOT / "packages" / "ppap_ocr_adapter" / "src",
    ROOT / "packages" / "ppap_pdf_to_excel" / "src",
    ROOT / "packages" / "ppap_vision" / "src",
    ROOT / "services" / "api",
    ROOT / "services" / "worker",
)


def pythonpath(existing: str | None = None) -> str:
    parts = [str(path) for path in PYTHONPATH_PARTS]
    extra = existing if existing is not None else os.environ.get("PYTHONPATH", "")
    if extra:
        parts.append(extra)
    return os.pathsep.join(parts)


def git_sha() -> str:
    forced = os.environ.get("PPAP_BUILD_SHA", "").strip()
    if forced:
        return forced
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""
