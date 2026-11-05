"""Corpus pytest options. Defaults point at in-repo assets from ppap-agent@53a5bd2b."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DEFAULT_V2_ROOT = _REPO / "assets" / "v2.0plus"
_DEFAULT_V2_CSV = _REPO / "assets" / "全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv"


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
