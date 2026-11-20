"""Scopes a client asks for: unknown ones are refused, retired ones are dropped."""

import pytest
from product.mcp import McpError, parse_scopes


def test_retired_scopes_from_older_clients_are_dropped():
    assert parse_scopes("identity:read rules:read rules:write audits:read") == ["audits:read", "identity:read"]


@pytest.mark.parametrize("value", ["", "rules:read", "identity:read admin:all"])
def test_nothing_usable_or_an_unknown_scope_is_refused(value):
    with pytest.raises(McpError, match="invalid_scope"):
        parse_scopes(value)
