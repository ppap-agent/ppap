"""Ensure bounded preview concurrency preserves locked dependencies and CI gates."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_preview_parallel_dependency_is_locked_in_e2e_group():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert "pytest-xdist==3.8.0" in project["dependency-groups"]["e2e"]
    packages = {package["name"]: package for package in lock["package"]}
    assert {"name": "pytest-xdist"} in packages["ppap"]["dev-dependencies"]["e2e"]
    expected = {"name": "pytest-xdist", "specifier": "==3.8.0"}
    assert expected in packages["ppap"]["metadata"]["requires-dev"]["e2e"]
    assert packages["pytest-xdist"]["version"] == "3.8.0"
    assert {dependency["name"] for dependency in packages["pytest-xdist"]["dependencies"]} == {"execnet", "pytest"}
    assert packages["execnet"]["version"] == "2.1.2"
    for name in ("pytest-xdist", "execnet"):
        wheel = packages[name]["wheels"][0]
        assert wheel["url"].startswith("https://files.pythonhosted.org/packages/")
        assert wheel["hash"].startswith("sha256:")


def test_preview_workers_are_bounded_with_serial_rollback():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    assert 'PPAP_PREVIEW_WORKERS: "2"' in workflow
    assert "['0', '2'].includes(workers)" in workflow
    assert "workers === '2' ? ['-n', '2', '--dist=load'] : []" in workflow
    assert "'--durations=25', ...tests" in workflow
    assert "PPAP_PREVIEW_URL: preview" in workflow
    assert "if (!tests.length) throw new Error('No preview browser contracts found')" in workflow
    assert "process.exitCode = code ?? 1" in workflow


def test_corpus_profiling_preserves_serial_execution():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    corpus = workflow.split("  corpus:\n", 1)[1].split("  integration:\n", 1)[0]
    assert "uv run --no-sync pytest ${{ matrix.args }} --durations=25" in corpus
    assert " -n " not in corpus
