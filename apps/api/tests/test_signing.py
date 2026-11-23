"""Signing moments are marked once on their routes; a client's token reaches none of them (#348)."""

import re
from datetime import datetime, timezone

from api import app
from api.signing import is_signing
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from product.identity import Principal, Session
from product.mcp import Delegation

# What the product owner named as signing (#355). More may be marked; none of these may lose the mark.
SIGNING = {
    ("POST", "/audit-tasks/{task_id}/confirm"),
    ("POST", "/audit-tasks/{task_id}/reports/{report_id}/regenerations"),
    ("POST", "/integrations/feishu/rounds/{round_id}/report"),
    ("POST", "/integrations/feishu/rounds/{round_id}/writeback"),
    ("POST", "/audit-report-shares"),
    ("POST", "/rulebooks/{customer_id}/revisions"),
    ("POST", "/rulebooks/{customer_id}/{version}/{revision}/clarified"),
    ("PUT", "/rulebooks/{customer_id}/{version}/{revision}/readings"),
    ("POST", "/rule-questionnaires/cards"),
    ("POST", "/rulebooks/{customer_id}/plan"),
    # Signing off a round and withdrawing it (#357).
    ("POST", "/rounds/{round_id}/signoff"),
    ("POST", "/rounds/{round_id}/signoff/withdraw"),
}


def marked() -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute) and is_signing(route)]


def test_every_named_signing_moment_carries_the_mark():
    found = {(method, route.path) for route in marked() for method in route.methods}
    assert SIGNING <= found, SIGNING - found


def test_a_delegated_token_cannot_call_any_signing_route():
    session = Session(Principal("user", "tenant", "feishu", "app", "org", "subject", "member"), datetime.now(timezone.utc))
    delegation = Delegation("grant", "client", frozenset({"audits:execute", "reports:send", "reports:write", "rulebooks:write"}), session)

    async def delegated(scope, receive, send):
        await app({**scope, "ppap.delegation": delegation} if scope["type"] == "http" else scope, receive, send)

    client = TestClient(delegated)
    for route in marked():
        path = re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000000", route.path)
        for method in route.methods:
            response = client.request(method, path, json={})
            assert (response.status_code, response.json()) == (403, {"error": "signing_requires_person"}), (method, route.path)


def test_authorization_pages_take_every_colour_from_the_design_tokens_and_run_no_script():
    from api.mcp_pages import page

    html = page("授权连接", "<p>预计扣减 3 点。</p>").body.decode()
    style = re.search(r"<style>(.*)</style>", html, re.S)[1]
    # The only literal colours are the token values themselves, declared as variables for light and dark.
    declared = set(re.findall(r"--color-[\w-]+:(#[0-9a-f]{6})", style))
    assert declared and set(re.findall(r"#[0-9a-fA-F]{3,8}\b", style)) <= declared
    assert "--color-primary:#1456f0" in style and "prefers-color-scheme:dark" in style
    assert "<script" not in html and "http" not in style


def test_every_signing_moment_has_a_receipt_and_no_receipt_table_exists():
    """A receipt is the signing action as recorded (#361): a label for each marked route, and nothing stored apart."""
    from pathlib import Path

    from product.store import actions

    found = {(method, route.path) for route in marked() for method in route.methods}
    assert found <= set(actions.LABELS), found - set(actions.LABELS)
    migrations = (Path(__file__).resolve().parents[3] / "migrations").glob("*.sql")
    assert not [path.name for path in migrations if re.search(r"CREATE TABLE (IF NOT EXISTS )?\w+\.\w*receipt", path.read_text(encoding="utf-8"), re.I)]
