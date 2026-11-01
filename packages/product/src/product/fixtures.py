"""Isolated-dev fixtures for object-authorization tests. Not an S02 OAuth proof."""

from __future__ import annotations

TENANT_A = "00000000-0000-4000-8000-00000000000a"
TENANT_B = "00000000-0000-4000-8000-00000000000b"
USER_ALICE = "00000000-0000-4000-8000-0000000000a1"
USER_BOB = "00000000-0000-4000-8000-0000000000b2"
USER_CAROL = "00000000-0000-4000-8000-0000000000c3"
RESOURCE_A = "00000000-0000-4000-8000-0000000000aa"
RESOURCE_B = "00000000-0000-4000-8000-0000000000bb"
RULE_A = "00000000-0000-4000-8000-0000000000a5"
RULE_B = "00000000-0000-4000-8000-0000000000b5"
APP_ID = "isolated-dev-feishu-app"
PRESENCE_RULE_BODY = {"kind": "presence"}

SUBJECTS = {
    "alice": {"user_id": USER_ALICE, "tenant_id": TENANT_A, "tenant_key": "isolated-dev-a", "subject": "ou_alice"},
    "bob": {"user_id": USER_BOB, "tenant_id": TENANT_A, "tenant_key": "isolated-dev-a", "subject": "ou_bob"},
    "carol": {"user_id": USER_CAROL, "tenant_id": TENANT_B, "tenant_key": "isolated-dev-b", "subject": "ou_carol"},
}
