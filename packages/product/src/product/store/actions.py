"""What people did: one row per write request a known person made, through any channel (#362).

A row is started before the request takes effect and finished with its result. Who, when, through what and with which
parameters are recorded here and nowhere else; business tables keep ownership, content and state.
"""

from __future__ import annotations

import json
import uuid

from psycopg.types.json import Jsonb

MAX_PARAMS = 64 * 1024
# The person's no to a client's proposal (#348): an action like any other, with the reason as its parameter.
DECLINE_ROUTE = "/intents/{intent_id}/decline"


def parameters(body: bytes, content_type: str) -> dict:
    """The request as a person's action: its JSON, or what was uploaded (never the bytes)."""
    if not body:
        return {}
    if content_type.startswith("application/json"):
        if len(body) > MAX_PARAMS:
            return {"too_large": len(body)}
        try:
            value = json.loads(body)
        except ValueError:
            return {"unreadable": len(body)}
        return value if isinstance(value, dict) else {"value": value}
    return {"content_type": content_type.split(";")[0], "bytes": len(body)}


def start(store, principal, *, channel: str, grant_id: str | None, intent_id: str | None, method: str, route: str, path: str, params: dict) -> str:
    ident = str(uuid.uuid4())
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO product.actions (id, tenant_id, user_id, channel, grant_id, intent_id, method, route, path, params)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (ident, principal.tenant_id, principal.user_id, channel, grant_id, intent_id, method, route[:300], path[:2000], Jsonb(params)),
        )
    return ident


def finish(store, ident: str, status_code: int, error: str | None = None) -> None:
    outcome = "succeeded" if status_code < 400 else "failed" if status_code < 500 else "unknown"
    with store._conn() as conn:
        conn.execute(
            "UPDATE product.actions SET outcome=%s, status_code=%s, error=%s, finished_at=now() WHERE id=%s AND outcome='pending'",
            (outcome, status_code, (error or None) and error[:200], ident),
        )
