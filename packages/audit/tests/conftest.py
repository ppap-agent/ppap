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


@pytest.fixture
def run_checks():
    """Construct the company agent the rule names and run each clause through its own ``check(index)``."""
    from audit.engine.routing import resolve_engine_agent_class
    from audit.kinds import PpapDocumentType

    def run(audit, rule, group_context=None, *, count=None):
        customer = rule.customers[0] if len(rule.customers) == 1 else None
        agent_type = resolve_engine_agent_class(PpapDocumentType.from_string(rule.type or ""), audit.file_url, rule.rule_profile, preset_key=rule.preset_key, preset_customer=customer)
        assert agent_type is not None
        agent = agent_type(audit, rule, group_context=group_context)
        try:
            return [agent.check(index) for index in range(len(rule.rules) if count is None else count)]
        finally:
            close = getattr(agent, "close", None)
            if callable(close):
                close()

    return run
