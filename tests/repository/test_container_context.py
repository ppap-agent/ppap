"""Check production context filtering with Docker's own ignore semantics."""

import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("local_output", [False, True], ids=["clean", "local-research-output"])
def test_production_context_keeps_runtime_files_and_excludes_research(local_output):
    with tempfile.TemporaryDirectory(prefix="ppap-context-") as directory:
        base = Path(directory)
        context = base / "context"
        context.mkdir()
        (context / ".dockerignore").write_bytes((ROOT / ".dockerignore").read_bytes())
        kept = [
            "packages/example/pyproject.toml",
            "packages/example/src/module.py",
            "packages/example/src/test_support.py",
            "packages/audit/src/audit/document_tools.py",
            "packages/agent/src/agent/skill/SKILL.md",
            "packages/agent/src/agent/skill/references/commands.md",
            "apps/api/src/module.py",
            ".env.example",
            "assets/li/templates/APQP-过程流程图.xlsx",
            "assets/nio/templates/10 控制计划.xlsx",
            "assets/uaes/templates/零件追溯检查单-模板 20250609.xlsx",
        ]
        excluded = [
            "tests/root.py",
            "packages/example/tests/test_module.py",
            "apps/api/tests/test_api.py",
            "apps/web/tests/test_web.ts",
            "packages/agent/prototypes/typed_evidence_holes/cases.py",
            "packages/agent/prototypes/typed_evidence_holes/test_local_grok_runner.py",
            "packages/agent/prototypes/typed_evidence_holes/oracle/expected.json",
            ".env",
            "assets/li/rules/08 过程流程图.docx",
            "assets/corpus/oracle.json",
            "assets/uaes/prechecks/rule.json",
        ]
        if local_output:
            excluded.extend(
                [
                    "packages/agent/prototypes/typed_evidence_holes/output/run/model-inputs.jsonl",
                    "packages/agent/prototypes/typed_evidence_holes/output/run/inputs/customer.xlsx",
                    "packages/agent/prototypes/typed_evidence_holes/output/run/code/guest.py",
                ]
            )
        for name in kept + excluded:
            path = context / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        (context / "Dockerfile").write_text("FROM scratch\nCOPY . /context/\n")
        output = base / "output"
        result = subprocess.run(
            ["docker", "buildx", "build", "--output", f"type=local,dest={output}", str(context)],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr
        for name in kept:
            assert (output / "context" / name).is_file(), name
        for name in excluded:
            assert not (output / "context" / name).exists(), name
