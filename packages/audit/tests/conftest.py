"""Pytest-native fixtures for audit-owned directory checker tests."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def text_model(monkeypatch):
    """Answer direct text model calls with a stub that reads the request and returns JSON text."""

    def install(stub):
        from documents.ai import AIResult

        def run(task, *, prompt, instructions="", max_tokens=1000, timeout=900, **kwargs):
            raw = stub(operation=task, system_prompt=instructions, user_prompt=prompt, max_tokens=max_tokens, request_timeout=timeout, **kwargs)
            return AIResult(json.loads(raw) if isinstance(raw, str) else raw)

        monkeypatch.setattr("documents.ai.run_json", run)

    return install


def read_leaf_resource(source_path: str) -> dict:
    """The ``RESOURCE`` literal of an installed leaf such as ``nio/N03``, read without importing the leaf."""
    import ast

    from audit.standard_suites import _CHECKERS_ROOT

    source = (_CHECKERS_ROOT / source_path / "resources.py").read_text(encoding="utf-8")
    (resource,) = [
        ast.literal_eval(node.value) for node in ast.parse(source).body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "RESOURCE" for target in node.targets)
    ]
    return resource


@pytest.fixture
def leaf_resource():
    return read_leaf_resource


@pytest.fixture
def run_checks():
    """Construct the class a leaf's carriers name for the original's suffix and run each clause through its own ``check(index)``."""
    import importlib

    from documents.files import get_file_extension_from_url

    def run(audit, rule, group_context=None, *, leaf, count=None):
        module, name = read_leaf_resource(leaf)["carriers"][get_file_extension_from_url(audit.file_url).lower()].split(":", 1)
        agent = getattr(importlib.import_module(module), name)(audit, rule, group_context=group_context)
        try:
            return [agent.check(index) for index in range(len(rule.rules) if count is None else count)]
        finally:
            close = getattr(agent, "close", None)
            if callable(close):
                close()

    return run
