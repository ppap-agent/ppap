"""Every way out of an MCP call has a next step (#349): an issue that blocks offers an action, an action that needs the
user's content says what it needs, and an error is a dead end only when PPAP must refuse (permission, role, policy)."""

import ast
import re
from pathlib import Path

import pytest
from api.mcp_tools import REFUSALS, failure
from product import clarification, workflow
from product.mcp import McpError

ROOT = Path(__file__).resolve().parents[3]
SOURCES = [ROOT / "packages/product/src/product/workflow.py", ROOT / "packages/product/src/product/clarification.py"]
CODE_SOURCES = [*SOURCES, *(ROOT / "apps/api/src/api").glob("mcp_*.py"), ROOT / "apps/api/src/api/intents.py"]
# Actions that take what the person gives: they must say in what form.
NEEDS_INPUT = {"add_source", "replace_source", "submit_clauses", "submit_review", "submit_reading", "propose_text", "keep_text", "ask_further", "answer"}
NEXT = {"user_action", "call_tool", "wait", "recover", "blocked"}


def calls(path: Path, names: set[str]):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names:
            yield node


def test_every_blocking_issue_offers_a_way_on():
    for path in SOURCES:
        for node in calls(path, {"_issue", "add"}):
            args = node.args[1:] if path.name == "workflow.py" else node.args
            severity, actions = args[2], args[6]
            if isinstance(severity, ast.Constant) and severity.value == "blocking":
                # An empty list only behind a condition: no action for someone who may not act (not the creator, no role).
                assert not (isinstance(actions, ast.List) and not actions.elts), f"{path.name}:{node.lineno} blocks with nothing to do"


def test_an_action_that_takes_content_says_what_content():
    for path in SOURCES:
        for node in calls(path, {"_act"}):
            ident = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
            if ident in NEEDS_INPUT:
                assert any(keyword.arg == "input_schema" for keyword in node.keywords), f"{path.name}:{node.lineno} {ident} has no input_schema"


def known_codes() -> set[str]:
    pattern = re.compile(r'(?:Rejected|McpError|Invalid)\(\s*"([a-z_]+)"')
    return {code for path in CODE_SOURCES for code in pattern.findall(path.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("code", sorted(known_codes()))
@pytest.mark.parametrize("task_id", [None, "00000000-0000-4000-8000-000000000001"])
def test_every_error_leads_somewhere_and_only_refusals_stop(code, task_id):
    for exc in (workflow.Rejected(code), McpError(code, 409, missing_scope="files:read"), McpError(code, 403, missing_scope="files:read")):
        step = failure(exc, task_id=task_id, request_id="r").next
        assert step["kind"] in NEXT, (code, step)
        if step["kind"] == "blocked":
            assert code in REFUSALS and step.get("message"), (code, step)
        if step["kind"] == "user_action":
            assert step.get("prompt"), (code, step)


def test_unfit_prepared_work_is_fixed_and_submitted_again():
    exc = clarification.Invalid("review_rejected", ["引文不在原文里"])
    retry = {"tool": "resolve_issue", "arguments": {"task_id": "t", "issue_id": "i", "action_id": "submit_review", "expected_revision": 3}, "new_request_id": True}
    step = failure(exc, task_id="t", request_id="r", then=retry).next
    assert step["kind"] == "call_tool" and step["tool"] == "resolve_issue" and step["problems"] == ["引文不在原文里"] and step["new_request_id"]
