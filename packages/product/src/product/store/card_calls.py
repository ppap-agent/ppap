"""Who clicked a card button, and whether the card is one PPAP sent them (#355, CR-06). Feishu has verified the clicker;
this binds them to a member of the enterprise and the click to the confirmation card PPAP sent about that task."""

from __future__ import annotations

from product.identity import Principal


def person(store, tenant_key: str, subject: str) -> Principal | None:
    settings = store.settings
    if not settings.tenant_allowed(tenant_key):
        return None
    with store._conn() as conn:
        row = conn.execute(
            """SELECT i.user_id, m.tenant_id, m.role, COALESCE(i.display_name, '') FROM product.identities i
               JOIN product.tenants t ON t.provider = i.provider AND t.external_key = i.tenant_key
               JOIN product.memberships m ON m.user_id = i.user_id AND m.tenant_id = t.id
               WHERE i.provider = 'feishu' AND i.app_id = %s AND i.tenant_key = %s AND i.subject = %s
               ORDER BY i.created_at, i.id LIMIT 1""",
            (settings.feishu_app_id, tenant_key, subject),
        ).fetchone()
    if row is None:
        return None
    return Principal(str(row[0]), str(row[1]), "feishu", settings.feishu_app_id, tenant_key, subject, row[2], row[3])


def card_sent(store, principal: Principal, message_id: str, task_id: str) -> bool:
    """The confirmation card about this task that PPAP delivered as this message; a value copied elsewhere is no card."""
    with store._conn() as conn:
        return (
            conn.execute(
                """SELECT 1 FROM engine.jobs WHERE kind = 'feishu_notification' AND tenant_id = %s AND payload->>'task_id' = %s
                   AND payload->>'stage' = 'confirmation' AND result->>'state' = 'sent' AND result->>'message_id' = %s""",
                (principal.tenant_id, task_id, message_id),
            ).fetchone()
            is not None
        )
