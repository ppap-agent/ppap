"""Durable OAuth delegations and MCP receipts. No audit or worker state lives here."""

from __future__ import annotations

import hmac
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from ppap_core.identity import Principal, Session, new_token, token_hash
from ppap_core.mcp import Delegation, McpError, fingerprint, parse_scopes, pkce_challenge, valid_redirect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def now():
    return datetime.now(timezone.utc)


class McpStore:
    def __init__(self, product):
        self.product = product
        self.settings = product.settings

    @contextmanager
    def connection(self):
        with self.product._conn() as conn:
            conn.row_factory = dict_row
            # Product connections use autocommit. A connection context alone
            # does not retain FOR UPDATE locks across the following statements.
            with conn.transaction():
                yield conn

    def rate_limit(self, bucket: str, limit: int) -> None:
        with self.connection() as conn:
            row = conn.execute(
                """INSERT INTO product.mcp_rate_limits VALUES (%s, date_trunc('minute', now()), 1)
                   ON CONFLICT(bucket) DO UPDATE SET
                   requests = CASE WHEN mcp_rate_limits.window_start = EXCLUDED.window_start THEN mcp_rate_limits.requests + 1 ELSE 1 END,
                   window_start = EXCLUDED.window_start RETURNING requests""",
                (token_hash(bucket),),
            ).fetchone()
        if row["requests"] > limit:
            raise McpError("rate_limited", 429)

    def register(self, ident: str, metadata: dict) -> None:
        redirects = metadata.get("redirect_uris")
        name = metadata.get("client_name", "MCP client")
        if (
            not isinstance(redirects, list)
            or not 1 <= len(redirects) <= 10
            or any(not isinstance(u, str) or not valid_redirect(u) for u in redirects)
            or not isinstance(name, str)
            or not 1 <= len(name) <= 100
            or metadata.get("token_endpoint_auth_method", "none") not in {"none", "client_secret_post", "client_secret_basic"}
            or set(metadata.get("grant_types", ["authorization_code", "refresh_token"])) - {"authorization_code", "refresh_token"}
            or metadata.get("response_types", ["code"]) != ["code"]
        ):
            raise McpError("invalid_client_metadata")
        with self.connection() as conn:
            conn.execute("INSERT INTO product.mcp_clients(id, metadata) VALUES (%s,%s)", (ident, Jsonb(metadata)))

    def client(self, ident: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT metadata FROM product.mcp_clients WHERE id=%s", (ident,)).fetchone()
        return row["metadata"] if row else None

    def code(self, code: str, client_id: str) -> dict | None:
        with self.connection() as conn:
            return conn.execute(
                """SELECT a.*,g.scopes AS granted_scopes FROM product.mcp_authorizations a
                                   JOIN product.mcp_grants g ON g.id=a.grant_id
                                   WHERE a.code_hash=%s AND a.client_id=%s AND a.consumed_at IS NULL
                                   AND g.revoked_at IS NULL AND g.expires_at>now()""",
                (token_hash(code), client_id),
            ).fetchone()

    def token(self, raw: str, kind: str) -> dict | None:
        with self.connection() as conn:
            return conn.execute(
                """SELECT t.*,g.client_id,g.resource,g.scopes,g.user_id,g.tenant_id FROM product.mcp_tokens t
                                   JOIN product.mcp_grants g ON g.id=t.grant_id WHERE t.token_hash=%s AND t.kind=%s
                                   AND g.revoked_at IS NULL AND g.expires_at>now()""",
                (token_hash(raw), kind),
            ).fetchone()

    def authorization(self, args: dict, resource: str) -> str:
        scopes = parse_scopes(args.get("scope") or "identity:read")
        if (
            args.get("response_type") != "code"
            or args.get("code_challenge_method") != "S256"
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", args.get("code_challenge", ""))
            or args.get("resource") != resource
            or len(args.get("state", "")) > 2048
        ):
            raise McpError("invalid_request")
        with self.connection() as conn:
            client = conn.execute("SELECT metadata FROM product.mcp_clients WHERE id=%s", (args.get("client_id"),)).fetchone()
            if not client or args.get("redirect_uri") not in client["metadata"]["redirect_uris"]:
                raise McpError("invalid_client")
            ident = new_token()
            conn.execute(
                """INSERT INTO product.mcp_authorizations(id,client_id,redirect_uri,resource,scopes,state,challenge,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,now()+interval '10 minutes')""",
                (ident, args["client_id"], args["redirect_uri"], resource, scopes, args.get("state", ""), args["code_challenge"]),
            )
        return ident

    def consent_view(self, ident: str, browser: str) -> dict:
        csrf = new_token()
        with self.connection() as conn:
            row = conn.execute(
                """UPDATE product.mcp_authorizations SET csrf_hash=%s,browser_hash=%s
                   WHERE id=%s AND decided_at IS NULL AND expires_at>now() RETURNING *""",
                (token_hash(csrf), token_hash(browser), ident),
            ).fetchone()
            if not row:
                raise McpError("authorization_expired", 404)
            client = conn.execute("SELECT metadata FROM product.mcp_clients WHERE id=%s", (row["client_id"],)).fetchone()
        return {"client": client["metadata"], "scopes": row["scopes"], "csrf": csrf, "redirect_uri": row["redirect_uri"]}

    def consent(self, ident: str, browser: str, csrf: str, session: Session, scopes: list[str], approve: bool) -> dict:
        code = new_token()
        p = session.principal
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM product.mcp_authorizations WHERE id=%s FOR UPDATE", (ident,)).fetchone()
            if (
                not row
                or row["decided_at"]
                or row["expires_at"] <= now()
                or not hmac.compare_digest(row["csrf_hash"] or "", token_hash(csrf))
                or not hmac.compare_digest(row["browser_hash"] or "", token_hash(browser))
            ):
                raise McpError("invalid_consent", 403)
            if approve:
                selected = parse_scopes(" ".join(scopes))
                if not set(selected).issubset(row["scopes"]):
                    raise McpError("invalid_scope")
                expires = min(now() + timedelta(days=30), session.refresh_expires_at or session.access_expires_at or session.expires_at)
                grant = conn.execute(
                    """INSERT INTO product.mcp_grants(client_id,user_id,tenant_id,provider,app_id,subject,scopes,resource,
                       access_token,refresh_token,access_expires_at,refresh_expires_at,expires_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (
                        row["client_id"],
                        p.user_id,
                        p.tenant_id,
                        p.provider,
                        p.app_id,
                        p.subject,
                        selected,
                        row["resource"],
                        session.access_token,
                        session.refresh_token,
                        session.access_expires_at,
                        session.refresh_expires_at,
                        expires,
                    ),
                ).fetchone()
                conn.execute(
                    "UPDATE product.mcp_authorizations SET decided_at=now(),code_hash=%s,grant_id=%s,expires_at=now()+interval '5 minutes' WHERE id=%s", (token_hash(code), grant["id"], ident)
                )
            else:
                conn.execute("UPDATE product.mcp_authorizations SET decided_at=now() WHERE id=%s", (ident,))
        return {"redirect_uri": row["redirect_uri"], "state": row["state"], **({"code": code} if approve else {"error": "access_denied"})}

    def _tokens(self, conn, grant) -> dict:
        access, refresh = new_token(), new_token()
        for value, kind, expiry in ((access, "access", min(now() + timedelta(minutes=15), grant["expires_at"])), (refresh, "refresh", grant["expires_at"])):
            conn.execute("INSERT INTO product.mcp_tokens(token_hash,grant_id,kind,expires_at) VALUES (%s,%s,%s,%s)", (token_hash(value), grant["id"], kind, expiry))
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": max(0, min(900, int((grant["expires_at"] - now()).total_seconds()))),
            "scope": " ".join(grant["scopes"]),
        }

    def exchange(self, args: dict, resource: str, *, verified_challenge: str | None = None) -> dict:
        if args.get("resource") != resource:
            raise McpError("invalid_target")
        kind = args.get("grant_type")
        replay = False
        with self.connection() as conn:
            if kind == "authorization_code":
                row = conn.execute("SELECT * FROM product.mcp_authorizations WHERE code_hash=%s FOR UPDATE", (token_hash(args.get("code", "")),)).fetchone()
                if (
                    not row
                    or row["consumed_at"]
                    or row["expires_at"] <= now()
                    or row["client_id"] != args.get("client_id")
                    or row["redirect_uri"] != args.get("redirect_uri")
                    or row["resource"] != resource
                    or not hmac.compare_digest(row["challenge"], verified_challenge or pkce_challenge(args.get("code_verifier", "")))
                ):
                    raise McpError("invalid_grant")
                grant_id = row["grant_id"]
                conn.execute("UPDATE product.mcp_authorizations SET consumed_at=now() WHERE id=%s", (row["id"],))
            elif kind == "refresh_token":
                row = conn.execute("SELECT * FROM product.mcp_tokens WHERE token_hash=%s AND kind='refresh' FOR UPDATE", (token_hash(args.get("refresh_token", "")),)).fetchone()
                if not row or row["expires_at"] <= now():
                    raise McpError("invalid_grant")
                grant_id = row["grant_id"]
                replay = row["used_at"] is not None
                conn.execute("UPDATE product.mcp_tokens SET used_at=now() WHERE token_hash=%s", (row["token_hash"],))
            else:
                raise McpError("unsupported_grant_type")
            grant = conn.execute("SELECT * FROM product.mcp_grants WHERE id=%s FOR UPDATE", (grant_id,)).fetchone()
            if not grant or grant["client_id"] != args.get("client_id") or grant["resource"] != resource or grant["revoked_at"] or grant["expires_at"] <= now():
                raise McpError("invalid_grant")
            if args.get("scope"):
                requested = parse_scopes(args["scope"])
                if not set(requested).issubset(grant["scopes"]):
                    raise McpError("invalid_scope")
                # A refresh can reduce a grant, never silently widen it.
                grant["scopes"] = requested
                conn.execute("UPDATE product.mcp_grants SET scopes=%s WHERE id=%s", (requested, grant_id))
            if replay:
                conn.execute("UPDATE product.mcp_grants SET revoked_at=now() WHERE id=%s", (grant_id,))
            else:
                response = self._tokens(conn, grant)
        if replay:
            raise McpError("invalid_grant")
        return response

    def delegation(self, grant_id: str) -> Delegation:
        with self.connection() as conn:
            return self._delegation(conn, grant_id)

    def revoke(self, token: str, client_id: str) -> None:
        with self.connection() as conn:
            conn.execute(
                """UPDATE product.mcp_grants SET revoked_at=now() WHERE client_id=%s
                            AND id IN (SELECT grant_id FROM product.mcp_tokens WHERE token_hash=%s)""",
                (client_id, token_hash(token)),
            )

    def _delegation(self, conn, grant_id: str) -> Delegation:
        row = conn.execute(
            """SELECT g.*, m.role, i.display_name, i.tenant_key FROM product.mcp_grants g
               JOIN product.memberships m ON m.user_id=g.user_id AND m.tenant_id=g.tenant_id
               JOIN product.tenants t ON t.id=g.tenant_id AND t.provider=g.provider
               JOIN product.identities i ON i.user_id=g.user_id AND i.app_id=g.app_id AND i.provider=g.provider
                 AND i.subject=g.subject AND i.tenant_key=t.external_key
               WHERE g.id=%s AND g.revoked_at IS NULL AND g.expires_at>now()""",
            (grant_id,),
        ).fetchone()
        if not row or not self.settings.tenant_allowed(row["tenant_key"]) or row["app_id"] != self.settings.feishu_app_id or row["provider_refresh_state"] != "ready":
            raise McpError("invalid_token", 401)
        p = Principal(str(row["user_id"]), str(row["tenant_id"]), row["provider"], row["app_id"], row["tenant_key"], row["subject"], row["role"], row["display_name"])
        session = Session(p, row["expires_at"], row["access_token"] or "", row["refresh_token"] or "", row["access_expires_at"], row["refresh_expires_at"])
        return Delegation(str(row["id"]), row["client_id"], frozenset(row["scopes"]), session)

    def authenticate(self, token: str, resource: str, refresh_provider=None) -> Delegation:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT t.grant_id FROM product.mcp_tokens t JOIN product.mcp_grants g ON g.id=t.grant_id
                                  WHERE t.token_hash=%s AND t.kind='access' AND t.expires_at>now() AND g.resource=%s""",
                (token_hash(token), resource),
            ).fetchone()
            if not row:
                raise McpError("invalid_token", 401)
            result = self._delegation(conn, row["grant_id"])
        if result.session.needs_refresh(now()):
            if refresh_provider is None:
                raise McpError("reauthorization_required", 401)
            self.refresh_provider(result.id, refresh_provider)
            with self.connection() as conn:
                result = self._delegation(conn, result.id)
        if result.session.provider_auth_dead(now()):
            raise McpError("reauthorization_required", 401)
        return result

    def refresh_provider(self, grant_id: str, refresh) -> None:
        # Persist the external effect boundary before calling Feishu. A crash or
        # ambiguous response requires sign-in, never a blind second refresh.
        with self.connection() as conn:
            row = conn.execute(
                """UPDATE product.mcp_grants SET provider_refresh_state='refreshing'
                                  WHERE id=%s AND revoked_at IS NULL AND provider_refresh_state='ready'
                                  AND access_expires_at<=now() RETURNING refresh_token""",
                (grant_id,),
            ).fetchone()
        if not row:
            raise McpError("reauthorization_required", 401)
        try:
            tokens = refresh(row["refresh_token"])
        except Exception:
            with self.connection() as conn:
                conn.execute("UPDATE product.mcp_grants SET provider_refresh_state='reauthorize' WHERE id=%s", (grant_id,))
            raise McpError("reauthorization_required", 401) from None
        with self.connection() as conn:
            conn.execute(
                """UPDATE product.mcp_grants SET provider_refresh_state='ready',access_token=%s,refresh_token=%s,
                            access_expires_at=%s,refresh_expires_at=%s WHERE id=%s AND revoked_at IS NULL""",
                (tokens.access_token, tokens.refresh_token, tokens.access_expires_at, tokens.refresh_expires_at, grant_id),
            )

    def connections(self, principal: Principal) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT g.id,c.metadata->>'client_name' AS client_name,g.scopes,g.created_at,g.expires_at,g.revoked_at
                                   FROM product.mcp_grants g JOIN product.mcp_clients c ON c.id=g.client_id
                                   WHERE g.user_id=%s AND g.tenant_id=%s ORDER BY g.created_at DESC LIMIT 100""",
                (principal.user_id, principal.tenant_id),
            ).fetchall()
        return [{k: v.isoformat() if isinstance(v, datetime) else str(v) if isinstance(v, uuid.UUID) else v for k, v in r.items()} for r in rows]

    def revoke_connection(self, principal: Principal, grant_id: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE product.mcp_grants SET revoked_at=now() WHERE id=%s AND user_id=%s AND tenant_id=%s", (grant_id, principal.user_id, principal.tenant_id))

    def prepare_operation(self, delegation: Delegation, request_id: str, tool: str, arguments: dict, scope: str, confirmation: dict | None) -> dict:
        ident = str(uuid.UUID(request_id))
        digest = fingerprint({"tool": tool, "arguments": arguments})
        with self.connection() as conn:
            current = self._delegation(conn, delegation.id)
            current.require(scope)
            conn.execute(
                """INSERT INTO product.mcp_operations(grant_id,request_id,tool,arguments,fingerprint,required_scope,state,confirmation)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(grant_id,request_id) DO NOTHING""",
                (
                    delegation.id,
                    ident,
                    tool,
                    Jsonb(arguments),
                    digest,
                    scope,
                    "confirmation_required" if confirmation is not None else "ready",
                    Jsonb(confirmation) if confirmation is not None else None,
                ),
            )
            row = conn.execute("SELECT * FROM product.mcp_operations WHERE grant_id=%s AND request_id=%s", (delegation.id, ident)).fetchone()
            if row["fingerprint"] != digest:
                raise McpError("request_conflict", 409)
        return self.operation_public(row)

    @staticmethod
    def operation_public(row: dict) -> dict:
        state = row["state"]
        if state == "running" and (now() - row["updated_at"]).total_seconds() > 300:
            state = "unknown"
        return {
            "id": str(row["id"]),
            "request_id": str(row["request_id"]),
            "tool": row["tool"],
            "state": state,
            "arguments": row["arguments"],
            "confirmation": row["confirmation"],
            "result": row["result"],
            "confirmed_by": str(row["confirmed_by"]) if row["confirmed_by"] else None,
            "confirmed_at": row["confirmed_at"].isoformat() if row["confirmed_at"] else None,
        }

    def operation(self, delegation: Delegation, request_id: str) -> dict:
        with self.connection() as conn:
            current = self._delegation(conn, delegation.id)
            row = conn.execute("SELECT * FROM product.mcp_operations WHERE grant_id=%s AND request_id=%s", (delegation.id, str(uuid.UUID(request_id)))).fetchone()
            if not row:
                raise McpError("operation_not_found", 404)
            current.require(row["required_scope"])
        return self.operation_public(row)

    def claim_operation(self, delegation: Delegation, operation_id: str) -> bool:
        with self.connection() as conn:
            # Revocation and the start claim serialize on the same grant row.
            conn.execute("SELECT id FROM product.mcp_grants WHERE id=%s FOR UPDATE", (delegation.id,))
            current = self._delegation(conn, delegation.id)
            row = conn.execute("SELECT * FROM product.mcp_operations WHERE id=%s AND grant_id=%s FOR UPDATE", (operation_id, delegation.id)).fetchone()
            if not row:
                raise McpError("operation_not_found", 404)
            current.require(row["required_scope"])
            if row["state"] != "ready":
                return False
            if row["confirmation"] is not None and (not row["confirmed_at"] or now() - row["confirmed_at"] > timedelta(minutes=15)):
                raise McpError("confirmation_expired", 409)
            conn.execute("UPDATE product.mcp_operations SET state='running',updated_at=now() WHERE id=%s", (operation_id,))
        return True

    def settle_operation(self, operation_id: str, state: str, result: dict) -> None:
        if state not in {"succeeded", "failed", "unknown"}:
            raise ValueError("invalid_operation_state")
        with self.connection() as conn:
            conn.execute("UPDATE product.mcp_operations SET state=%s,result=%s,updated_at=now() WHERE id=%s AND state='running'", (state, Jsonb(result), operation_id))

    def confirmation_view(self, principal: Principal, operation_id: str, browser: str) -> dict:
        csrf = new_token()
        with self.connection() as conn:
            row = conn.execute(
                """UPDATE product.mcp_operations o SET csrf_hash=%s,browser_hash=%s FROM product.mcp_grants g
                                  WHERE o.id=%s AND o.grant_id=g.id AND g.user_id=%s AND g.tenant_id=%s
                                  AND g.revoked_at IS NULL AND g.expires_at>now() AND o.state='confirmation_required'
                                  RETURNING o.*""",
                (token_hash(csrf), token_hash(browser), operation_id, principal.user_id, principal.tenant_id),
            ).fetchone()
            if not row:
                raise McpError("confirmation_unavailable", 404)
            self._delegation(conn, row["grant_id"]).require(row["required_scope"])
        return {"tool": row["tool"], "grant_id": str(row["grant_id"]), "arguments": row["arguments"], "confirmation": row["confirmation"], "csrf": csrf}

    def confirm_operation(self, principal: Principal, operation_id: str, browser: str, csrf: str, approve: bool):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM product.mcp_operations WHERE id=%s FOR UPDATE", (operation_id,)).fetchone()
            if not row:
                raise McpError("confirmation_unavailable", 404)
            current = self._delegation(conn, row["grant_id"])
            current.require(row["required_scope"])
            if (
                current.session.principal.user_id != principal.user_id
                or current.session.principal.tenant_id != principal.tenant_id
                or row["state"] != "confirmation_required"
                or now() - row["created_at"] > timedelta(hours=1)
                or not hmac.compare_digest(row["csrf_hash"] or "", token_hash(csrf))
                or not hmac.compare_digest(row["browser_hash"] or "", token_hash(browser))
            ):
                raise McpError("invalid_confirmation", 403)
            conn.execute(
                "UPDATE product.mcp_operations SET state=%s,confirmed_by=%s,confirmed_at=now(),updated_at=now() WHERE id=%s", ("ready" if approve else "denied", principal.user_id, operation_id)
            )

    def create_transfer(self, delegation: Delegation, method: str, path: str, scope: str, parameters: dict | None = None) -> str:
        delegation.require(scope)
        with self.connection() as conn:
            self._delegation(conn, delegation.id).require(scope)
            row = conn.execute(
                "INSERT INTO product.mcp_transfers(grant_id,method,path,required_scope,parameters,expires_at) VALUES (%s,%s,%s,%s,%s,now()+interval '15 minutes') RETURNING id",
                (delegation.id, method, path, scope, Jsonb(parameters or {})),
            ).fetchone()
        return str(row["id"])

    def transfer(self, delegation: Delegation, ident: str, method: str) -> dict:
        with self.connection() as conn:
            current = self._delegation(conn, delegation.id)
            row = conn.execute("SELECT * FROM product.mcp_transfers WHERE id=%s AND grant_id=%s AND method=%s AND expires_at>now()", (ident, delegation.id, method)).fetchone()
            if not row:
                raise McpError("transfer_not_found", 404)
            current.require(row["required_scope"])
        return row
