"""The accepted audit has one execution owner, not a compatibility dispatcher."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_parallel_round_executor_and_result_projection_are_not_reintroduced():
    assert not (ROOT / "packages/product/src/product/rounds.py").exists()
    retired = {"run_round_from_bytes", "run_standard_round", "builtin_rule_input", "evaluate_round", "evaluate_presence", "findings_from_engine_results", "findings_from_rule_execution"}
    for owner in ("packages/product/src", "packages/audit/src", "packages/agent/src", "apps/worker/src"):
        for path in (ROOT / owner).rglob("*.py"):
            tree = ast.parse(path.read_text())
            definitions = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
            assert not retired & (definitions | imports), path.relative_to(ROOT)
