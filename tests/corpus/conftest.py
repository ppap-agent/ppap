"""Corpus selection and bounded failure diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from corpus_contracts.oracle import DATASETS
from corpus_contracts.replay import LIVE, Recorder, install, mode

_active: list[Recorder] = []


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--ftype",
        action="store",
        default=None,
        choices=["excel", "pdf"],
        help="Filter V2 cases by file type",
    )
    parser.addoption("--dataset", action="store", default=None, choices=DATASETS, help="One oracle dataset; defaults to all")


def pytest_make_parametrize_id(config: Any, val: Any, argname: Any) -> Any:
    if isinstance(val, str):
        return val
    if hasattr(val, "file_name"):
        return val.file_name
    return None


def _replay_scope(item: pytest.Item) -> str:
    case = getattr(item, "callspec", None) and item.callspec.params.get("audit_case")
    if case is not None:
        return case.id
    test_file, _, name = item.nodeid.partition("::")
    return f"{Path(test_file).parent.name}/{Path(test_file).stem}/{name.replace('/', '_')}"


@pytest.fixture(autouse=True)
def model_replay(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Every corpus test sees recorded model answers unless the run measures the live model."""
    if mode() == LIVE:
        yield None
        return
    recorder = Recorder(_replay_scope(request.node))
    install(monkeypatch, recorder)
    _active.append(recorder)
    yield recorder
    _active.remove(recorder)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Any:
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        for recorder in _active:
            recorder.finish(keep=report.passed)


@pytest.hookimpl(trylast=True)
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Write assertion evidence immediately; canceled xdist runs lose the final summary."""
    if report.when != "call" or not report.failed:
        return
    # No raw provider response payload: only pytest's existing failure traceback.
    detail = str(report.longreprtext)
    print(f"\nCI corpus case failure: {report.nodeid}\n{detail[:12000]}\n", file=sys.stderr, flush=True)
