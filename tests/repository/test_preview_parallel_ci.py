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


def test_pdf_product_oracle_is_the_only_parallel_corpus_slice():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    corpus = workflow.split("  corpus:\n", 1)[1].split("  integration:\n", 1)[0]
    assert 'PPAP_CORPUS_PRODUCT_WORKERS: "2"' in corpus
    assert 'PPAP_CORPUS_PRODUCT_WORKERS must be 0 or 2' in corpus
    assert "uv sync --locked --group e2e" in corpus
    assert "uv run --no-sync pytest tests/corpus/v2_assets --ftype pdf -q --durations=25 -k 'not test_v2_product_round_respects_oracle_baseline'" in corpus
    assert "PPAP_CORPUS_XDIST_ISOLATE_OCR=1" in corpus
    assert "pytest tests/corpus/v2_assets/test_product_round.py --ftype pdf -q --durations=25 -n 2 --dist=load --max-worker-restart=0" in corpus
    assert "uv run --no-sync pytest ${{ matrix.args }} --durations=25" in corpus
    assert corpus.count(" -n 2 ") == 1


def test_product_round_cases_are_separate_items_without_changing_oracle():
    source = (ROOT / "tests/corpus/v2_assets/test_product_round.py").read_text()
    assert 'metafunc.parametrize("product_case", cases, ids=lambda case: case.file_name)' in source
    assert "build_cases_from_long_feedback(" in source
    assert "def test_v2_product_round_respects_oracle_baseline(product_case:" in source
    assert "for case in cases:" not in source
    for rule in (
        "bound_type_name(",
        "execute_product_round(",
        "compare_file(",
        "classify_v2_baseline_outcomes(",
        "format_verdicts(",
    ):
        assert rule in source


def test_corpus_xdist_uses_separate_writable_ocr_caches():
    source = (ROOT / "tests/corpus/conftest.py").read_text()
    assert 'PPAP_CORPUS_XDIST_ISOLATE_OCR' in source
    assert 'hasattr(config, "workerinput")' in source
    assert 'shutil.copytree(seed, worker_cache)' in source
    assert 'os.environ["OCR_LOCAL_OSS_DIR"] = str(worker_cache)' in source
    assert 'os.environ["OCR_LOCAL_SQLITE_DB"] = str(worker_cache / "ocr_cache.db")' in source
    assert 'shutil.rmtree(_WORKER_CACHE_DIR, ignore_errors=True)' in source


def test_only_controlled_browser_report_tests_are_parallelized():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    script = (ROOT / "scripts/run-compose-tests.sh").read_text()
    assert 'PPAP_E2E_ROUND_REPORT_WORKERS: "2"' in workflow
    assert 'case "${PPAP_E2E_ROUND_REPORT_WORKERS:-0}" in' in script
    assert 'pytest "$suite" --durations=25' in script
    assert 'pytest "$suite" --ignore=tests/e2e/test_round_reports.py --durations=25 -vv' in script
    assert 'pytest tests/e2e/test_round_reports.py -n 2 --dist=load --max-worker-restart=0 --durations=25' in script
    assert 'PPAP_E2E_ROUND_REPORT_WORKERS must be 0 or 2' in script
    assert 'uv run --no-sync pytest "$suite"' in script
    controlled = (ROOT / "tests/e2e/test_round_reports.py").read_text()
    assert 'page.route("**/api/**", api)' in controlled
    assert 'with sync_playwright() as playwright:' in controlled


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
    assert cleanup.count("down -v --remove-orphans || failed=1") == 3
