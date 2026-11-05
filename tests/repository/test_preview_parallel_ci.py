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
    assert 'PPAP_PREVIEW_WORKERS: "4"' in workflow
    assert "['0', '4'].includes(workers)" in workflow
    assert "workers === '4' ? ['-n', '4', '--dist=load'] : []" in workflow
    assert "'--durations=25', ...tests" in workflow
    assert "PPAP_PREVIEW_URL: preview" in workflow
    assert "if (!tests.length) throw new Error('No preview browser contracts found')" in workflow
    assert "process.exitCode = code ?? 1" in workflow
    assert "Preview heartbeat: child pid=" in workflow
    assert "clearInterval(heartbeat)" in workflow


def test_pdf_all_oracles_share_one_bounded_xdist_pool():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    corpus = workflow.split("  corpus:\n", 1)[1].split("  integration:\n", 1)[0]
    assert 'PPAP_CORPUS_PDF_WORKERS: "2"' in corpus
    assert "PPAP_CORPUS_PDF_WORKERS must be 0 or 2" in corpus
    assert "uv sync --locked --group e2e" in corpus
    assert "pytest tests/corpus/test_cases.py tests/corpus/regressions --ftype pdf -q -n 2" in corpus
    assert "--dist=load --max-worker-restart=0 --durations=25 -vv" in corpus
    assert "python3 -m scripts.ci_heartbeat --timeout 2400" in corpus
    assert "uv run --no-sync pytest ${{ matrix.args }} --durations=25 -vv" in corpus
    assert corpus.count(" -n 2 ") == 1


def test_all_browser_e2e_items_join_one_xdist_pool_without_exclusion():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    script = (ROOT / "scripts/run-compose-tests.sh").read_text()
    integration = workflow.split("  integration:\n", 1)[1].split("  ci_gate:\n", 1)[0]
    browser = integration.split("      - name: Isolated-dev browser E2E\n", 1)[1].split("      - name: Dump Compose diagnostics\n", 1)[0]
    assert 'PPAP_E2E_WORKERS: "4"' in browser
    assert "PPAP_E2E_ROUND_REPORT_WORKERS" not in workflow
    assert 'case "${PPAP_E2E_WORKERS:-0}" in' in script
    assert "PPAP_E2E_WORKERS must be 0 or 4" in script
    assert 'uv run --no-sync pytest "$suite" --durations=25 -vv' in script
    assert 'uv run --no-sync pytest "$suite" -n 4 --dist=load --max-worker-restart=0 --durations=25 -vv' in script
    assert 'if [[ "$suite" == "tests/e2e" ]]; then' in script
    assert "--ignore=" not in script
    assert "pytest tests/e2e/test_round_reports.py" not in script
    assert 'uv run --no-sync pytest "$suite"' in script
    # Runtime tests remain serial and the same CI Gate still requires integration.
    assert "bash scripts/run-compose-tests.sh tests/integration/runtime" in integration
    assert "python3 -m scripts.ci_shards integration" in workflow
    assert "      - integration_contracts\n" in workflow
    assert "      - integration\n" in workflow


def test_browser_cleanup_is_owned_by_always_step_and_failures_are_reported():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    section = workflow.split("  integration:\n", 1)[1].split("  ci_gate:\n", 1)[0]
    e2e = section.split("      - name: Isolated-dev browser E2E\n", 1)[1].split("      - name: Dump Compose diagnostics\n", 1)[0]
    cleanup = section.split("      - name: Clean Compose stacks\n", 1)[1]
    assert "bash scripts/run-compose-tests.sh tests/e2e" in e2e
    assert "down -v --remove-orphans" not in e2e
    assert "if: always()" in cleanup
    assert 'exit "$failed"' in cleanup
    assert "down -v --remove-orphans || failed=1" in cleanup
    assert cleanup.count("down -v --remove-orphans || failed=1") == 2
