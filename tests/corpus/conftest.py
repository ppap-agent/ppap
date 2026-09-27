"""Corpus pytest options. Defaults point at in-repo assets from ppap-agent@53a5bd2b."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DEFAULT_V2_ROOT = _REPO / "assets" / "v2.0plus"
_DEFAULT_V2_CSV = _REPO / "assets" / "全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv"
_DEFAULT_OCR_CACHE = _REPO / "tests" / "corpus" / "fixtures" / "ocr_cache"


def pytest_configure(config: Any) -> None:
    """Use the in-repo OCR conversion cache unless the caller already pointed elsewhere."""
    if not (os.environ.get("OCR_LOCAL_OSS_DIR") or "").strip():
        os.environ["OCR_LOCAL_OSS_DIR"] = str(_DEFAULT_OCR_CACHE)


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--ftype",
        action="store",
        default=None,
        choices=["excel", "pdf", "scanned"],
        help="Filter V2 cases by file type",
    )
    parser.addoption("--verbose-check", action="store_true", default=False)
    parser.addoption("--mismatch-only", action="store_true", default=False)
    parser.addoption("--bundle", action="store", default=None)
    parser.addoption("--seq", action="store", default=None)
    parser.addoption("--report", action="store", default=None)
    parser.addoption(
        "--v2-dataset-root",
        action="store",
        default=str(_DEFAULT_V2_ROOT),
        help="V2 dataset root (defaults to in-repo assets/v2.0plus)",
    )
    parser.addoption(
        "--v2-expected-csv",
        action="store",
        default=str(_DEFAULT_V2_CSV),
        help="V2 human expected CSV (defaults to in-repo 4.8 feedback table)",
    )


def pytest_collection_modifyitems(items: Any) -> None:
    for item in items:
        item._nodeid = item.nodeid


def pytest_make_parametrize_id(config: Any, val: Any, argname: Any) -> Any:
    if isinstance(val, str):
        return val
    if hasattr(val, "file_name"):
        return val.file_name
    return None
