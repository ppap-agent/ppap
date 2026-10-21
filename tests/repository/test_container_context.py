"""Check production context filtering with Docker's own ignore semantics."""

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_context_keeps_runtime_files_and_excludes_nested_tests():
    with tempfile.TemporaryDirectory(prefix="ppap-context-") as directory:
        base = Path(directory)
        context = base / "context"
        context.mkdir()
        (context / ".dockerignore").write_bytes((ROOT / ".dockerignore").read_bytes())
        kept = ["packages/example/src/module.py", "packages/example/src/skills/SKILL.md", "services/api/src/module.py", ".env.example"]
        excluded = ["tests/root.py", "packages/example/tests/test_module.py", "services/api/tests/test_api.py", "apps/web/tests/test_web.ts", ".env"]
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
