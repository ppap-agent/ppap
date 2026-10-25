"""Corpus pytest options. Defaults point at in-repo assets from ppap-agent@53a5bd2b."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DEFAULT_V2_ROOT = _REPO / "assets" / "v2.0plus"
_DEFAULT_V2_CSV = _REPO / "assets" / "全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv"
_DEFAULT_OCR_CACHE = _REPO / "tests" / "corpus" / "fixtures" / "ocr_cache"
_WORKER_CACHE_DIR: Path | None = None


def pytest_configure(config: Any) -> None:
    """Use the in-repo OCR conversion cache unless the caller already pointed elsewhere."""
    if not (os.environ.get("OCR_LOCAL_OSS_DIR") or "").strip():
        os.environ["OCR_LOCAL_OSS_DIR"] = str(_DEFAULT_OCR_CACHE)

    # With xdist, only worker processes use copies of the committed OCR fixtures.
    # The OCR adapter writes to both SQLite and generated Excel/HTML paths;
    # sharing the original cache between workers can corrupt fixture evidence.
    if os.environ.get("PPAP_CORPUS_XDIST_ISOLATE_OCR") == "1" and hasattr(config, "workerinput"):
        _isolate_worker_ocr_cache()


def _isolate_worker_ocr_cache() -> None:
    """Copy the read baseline into a unique writable directory for this worker."""
    global _WORKER_CACHE_DIR
    seed = Path(os.environ["OCR_LOCAL_OSS_DIR"]).expanduser().resolve()
    if not seed.is_dir():
        raise pytest.UsageError(f"Missing seeded OCR corpus cache: {seed}")
    temp_root = Path(tempfile.mkdtemp(prefix="ppap-corpus-ocr-"))
    worker_cache = temp_root / "ocr_cache"
    try:
        shutil.copytree(seed, worker_cache)
        override = (os.environ.get("OCR_LOCAL_SQLITE_DB") or "").strip()
        if override:
            db_source = Path(override).expanduser().resolve()
            if not db_source.is_file():
                raise pytest.UsageError(f"Missing seeded OCR SQLite database: {db_source}")
            shutil.copyfile(db_source, worker_cache / "ocr_cache.db")
        os.environ["OCR_LOCAL_OSS_DIR"] = str(worker_cache)
        os.environ["OCR_LOCAL_SQLITE_DB"] = str(worker_cache / "ocr_cache.db")
        _WORKER_CACHE_DIR = temp_root
    except BaseException:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def pytest_unconfigure(config: Any) -> None:
    global _WORKER_CACHE_DIR
    if _WORKER_CACHE_DIR is not None:
        shutil.rmtree(_WORKER_CACHE_DIR, ignore_errors=True)
        _WORKER_CACHE_DIR = None


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
    parser.addoption(
        "--cross-format",
        action="store_true",
        default=False,
        help="Run V2 PDF↔Excel pair execution (Corpus Pairs variant)",
    )


def pytest_collection_modifyitems(items: Any) -> None:
    for item in items:
        item._nodeid = item.nodeid


def pytest_make_parametrize_id(config: Any, val: Any, argname: Any) -> Any:
    if isinstance(val, str):
        return val
    if hasattr(val, "pair_id"):
        return val.pair_id
    if hasattr(val, "file_name"):
        return val.file_name
    return None


@pytest.hookimpl(trylast=True)
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Write assertion evidence immediately; canceled xdist runs lose the final summary."""
    if report.when != "call" or not report.failed:
        return
    # No raw provider response payload: only pytest's existing failure traceback.
    detail = str(report.longreprtext)
    print(f"\nCI corpus case failure: {report.nodeid}\n{detail[:12000]}\n", file=sys.stderr, flush=True)
