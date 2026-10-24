"""Guard CI checkout isolation across the co-located self-hosted runner instances."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Every scheduled job/matrix cell has its own tracked files, uv environment,
# JS dependencies and generated artifacts even if two runners share workspace.
CHECKOUTS = (
    ("web", "repository", "web"),
    ("repository", "distribution_closure", "repository"),
    ("distribution_closure", "python_modules", "distribution-${{ matrix.shard }}"),
    ("python_modules", "schema", "modules-${{ matrix.shard }}"),
    ("schema", "corpus", "schema"),
    ("corpus", "integration", "corpus-${{ matrix.name }}"),
    ("integration", "ci_gate", "integration"),
)


def test_all_owner_jobs_use_independent_checkouts_and_default_workdirs():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    for job, following_job, checkout_suffix in CHECKOUTS:
        section = workflow.split(f"  {job}:\n", 1)[1].split(f"  {following_job}:\n", 1)[0]
        checkout = ".ci-checkouts/${{ github.run_id }}-${{ github.run_attempt }}/" + checkout_suffix
        assert f"path: {checkout}" in section, job
        assert f"working-directory: ${{{{ github.workspace }}}}/{checkout}" in section, job
        assert section.count("persist-credentials: false") == 1, job
        assert section.count("uses: actions/checkout@") == 1, job
        assert "cache-dependency-glob: uv.lock" in section, job
        assert section.count("run: bash scripts/ci_assert_worktree.sh") == 1, job
        assert "uses: ./.github/actions/assert-docker" not in section, job
    assert "  ci_gate:\n" in workflow


def test_node_paths_and_preview_stay_inside_unique_checkout():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    web = workflow.split("  web:\n", 1)[1].split("  repository:\n", 1)[0]
    repository = workflow.split("  repository:\n", 1)[1].split("  distribution_closure:\n", 1)[0]
    checkout = ".ci-checkouts/${{ github.run_id }}-${{ github.run_attempt }}/web"
    assert f"node-version-file: {checkout}/.node-version" in web
    assert f"working-directory: ${{{{ github.workspace }}}}/{checkout}/apps/web" in web
    assert web.count("/apps/web") == 2
    assert "COREPACK_HOME=$RUNNER_TEMP/corepack/${GITHUB_RUN_ID}/${GITHUB_JOB}" in web
    repository_node_version = ".ci-checkouts/${{ github.run_id }}-${{ github.run_attempt }}/repository/.node-version"
    assert f"node-version-file: {repository_node_version}" in repository


def test_preflight_runs_from_each_isolated_checkout():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    script = (ROOT / "scripts/ci_assert_docker.sh").read_text()
    assert "set -euo pipefail" in script
    assert "docker info --format" in script
    assert "docker compose version" in script
    assert "docker buildx version" in script
    assert workflow.count("run: bash scripts/ci_assert_docker.sh") == 6
    assert "uses: ./.github/actions/assert-docker" not in workflow
