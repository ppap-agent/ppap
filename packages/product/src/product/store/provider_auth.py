"""One durable owner of provider token rotation across Web and MCP consumers."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from product.feishu import RefreshNotSent
from product.identity import ProviderAuthError, Session, new_token, token_hash


def create_authorization(conn, principal, tokens) -> str:
    # Only identical provider credentials are shared. A later login never
    # overwrites another authorization, even for the same person and app.
    row = conn.execute(
        """INSERT INTO product.provider_authorizations(user_id,tenant_id,provider,app_id,subject,
           credential_hash,access_token,refresh_token,access_expires_at,refresh_expires_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (user_id,tenant_id,provider,app_id,subject,credential_hash)
           DO UPDATE SET credential_hash=EXCLUDED.credential_hash RETURNING id""",
        (
            principal.user_id,
            principal.tenant_id,
            principal.provider,
            principal.app_id,
            principal.subject,
            token_hash((tokens.refresh_token or tokens.access_token) if tokens else new_token()),
            tokens.access_token if tokens else "",
            tokens.refresh_token if tokens else "",
            tokens.access_expires_at if tokens else None,
            tokens.refresh_expires_at if tokens else None,
        ),
    ).fetchone()
    return str(row[0])


class ProviderAuthStore:
    def __init__(self, product):
        self.product = product

    def fresh(self, session: Session, refresh) -> Session:
        p = session.principal
        claim = str(uuid4())
        error = None
        with self.product._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """SELECT * FROM product.provider_authorizations WHERE id=%s AND user_id=%s AND tenant_id=%s
                   AND provider=%s AND app_id=%s AND subject=%s FOR UPDATE""",
                (session.provider_authorization_id, p.user_id, p.tenant_id, p.provider, p.app_id, p.subject),
            ).fetchone()
            if not row:
                raise ProviderAuthError("provider_reauthorization_required")
            at = datetime.now(timezone.utc)
            current = replace(session, access_token=row["access_token"], refresh_token=row["refresh_token"], access_expires_at=row["access_expires_at"], refresh_expires_at=row["refresh_expires_at"])
            if row["state"] == "refreshing":
                if row["refresh_started_at"] and row["refresh_started_at"] > at - timedelta(seconds=60):
                    raise ProviderAuthError("provider_refresh_in_progress", 503)
                # Expiry is evidence of an unknown outcome, not a new lease.
                error = ProviderAuthError("provider_reauthorization_required")
            elif row["state"] != "ready" or current.provider_auth_dead(at):
                error = ProviderAuthError("provider_reauthorization_required")
            elif not current.needs_refresh(at):
                return current
            if error:
                cur.execute("UPDATE product.provider_authorizations SET state='reauthorize' WHERE id=%s", (row["id"],))
            else:
                cur.execute(
                    """UPDATE product.provider_authorizations SET state='refreshing',refresh_claim=%s,
                            refresh_started_at=now() WHERE id=%s""",
                    (claim, row["id"]),
                )
        if error:
            raise error
        # The transaction is committed before crossing the external boundary.
        # Process death or a lost DB connection cannot authorize a second call.
        try:
            tokens = refresh(current.refresh_token)
            if not tokens.access_token or tokens.access_expires_at <= datetime.now(timezone.utc):
                raise ValueError("invalid_provider_refresh_result")
        except Exception as exc:
            not_sent = isinstance(exc, RefreshNotSent)
            with self.product._conn() as conn:
                conn.execute(
                    """UPDATE product.provider_authorizations SET state=%s
                             WHERE id=%s AND refresh_claim=%s AND state='refreshing'""",
                    ("ready" if not_sent else "reauthorize", row["id"], claim),
                )
            raise ProviderAuthError("provider_temporarily_unavailable" if not_sent else "provider_reauthorization_required", 503 if not_sent else 401) from None
        try:
            with self.product._conn() as conn:
                saved = conn.execute(
                    """UPDATE product.provider_authorizations SET state='ready',access_token=%s,refresh_token=%s,
                       access_expires_at=%s,refresh_expires_at=%s,credential_hash=%s
                       WHERE id=%s AND refresh_claim=%s AND state='refreshing' RETURNING id""",
                    (tokens.access_token, tokens.refresh_token, tokens.access_expires_at, tokens.refresh_expires_at, token_hash(tokens.refresh_token or tokens.access_token), row["id"], claim),
                ).fetchone()
        except UniqueViolation:
            # A concurrent login already owns the returned credential. Do not
            # create a second refresh owner or silently rebind old MCP grants.
            with self.product._conn() as conn:
                conn.execute(
                    """UPDATE product.provider_authorizations SET state='reauthorize'
                             WHERE id=%s AND refresh_claim=%s AND state='refreshing'""",
                    (row["id"], claim),
                )
            raise ProviderAuthError("provider_reauthorization_required") from None
        if not saved:
            raise ProviderAuthError("provider_reauthorization_required")
        return replace(current, access_token=tokens.access_token, refresh_token=tokens.refresh_token, access_expires_at=tokens.access_expires_at, refresh_expires_at=tokens.refresh_expires_at)
