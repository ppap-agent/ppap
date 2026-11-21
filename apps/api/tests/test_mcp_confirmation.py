"""An approved effect cannot borrow another grant, operation or route."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from api.mcp_confirmation import require_approved_source
from product.identity import Principal, Session
from product.mcp import ApprovedCall, Delegation, McpError, fingerprint
from starlette.requests import Request


def approved_request():
    session = Session(Principal("user", "tenant", "feishu", "app", "org", "subject", "member"), datetime.now(timezone.utc))
    delegation = Delegation("grant", "client", frozenset({"reports:send"}), session)
    source = {"review": {"version": 1, "rationale": "approved"}}
    proof = ApprovedCall("grant", "operation", "POST", "/send", (("/rounds/source", fingerprint(source)),))
    scope = {"type": "http", "method": "POST", "path": "/send", "ppap.delegation": delegation, "ppap.mcp_operation_id": "operation", "ppap.mcp_approval": proof}
    return scope, proof, source


@pytest.mark.parametrize("field", ["grant_id", "operation_id", "method", "path"])
def test_other_approved_call_cannot_authorize_effect(field):
    scope, proof, source = approved_request()
    scope["ppap.mcp_approval"] = replace(proof, **{field: "different"})
    with pytest.raises(McpError, match="^invalid_confirmation$") as caught:
        require_approved_source(Request(scope), "/rounds/source", source)
    assert caught.value.status == 403


@pytest.mark.parametrize("proof", [None, "approved", {"grant_id": "grant", "operation_id": "operation"}])
def test_delegated_effect_requires_internal_proof(proof):
    scope, _, source = approved_request()
    scope["ppap.mcp_approval"] = proof
    with pytest.raises(McpError, match="^invalid_confirmation$"):
        require_approved_source(Request(scope), "/rounds/source", source)


def test_same_source_required_but_web_keeps_its_existing_authority():
    scope, _, source = approved_request()
    require_approved_source(Request(scope), "/rounds/source", source)
    with pytest.raises(McpError, match="^confirmation_content_changed$") as caught:
        require_approved_source(Request(scope), "/rounds/source", {"review": {"version": 2, "rationale": "edited"}})
    assert caught.value.status == 409
    with pytest.raises(McpError, match="^confirmation_content_changed$"):
        require_approved_source(Request(scope), "/rounds/other", source)
    del scope["ppap.delegation"]
    scope["headers"] = [(b"ppap.mcp_approval", b"approved")]
    require_approved_source(Request(scope), "/rounds/source", {"review": "web"})


def test_authorization_pages_take_every_colour_from_the_design_tokens_and_run_no_script():
    import re

    from api.mcp_pages import lines, page

    html = page("确认这次操作", lines(["开始审核：共 2 份文件。", "· a.pdf", "· <b>.xlsx", "预计扣减 3 点。"])).body.decode()
    style = re.search(r"<style>(.*)</style>", html, re.S)[1]
    # The only literal colours are the token values themselves, declared as variables for light and dark.
    declared = set(re.findall(r"--color-[\w-]+:(#[0-9a-f]{6})", style))
    assert declared and set(re.findall(r"#[0-9a-fA-F]{3,8}\b", style)) <= declared
    assert "--color-primary:#1456f0" in style and "prefers-color-scheme:dark" in style
    assert "<script" not in html and "http" not in style
    assert "<ul><li>a.pdf</li><li>&lt;b&gt;.xlsx</li></ul><p>预计扣减 3 点。</p>" in html
