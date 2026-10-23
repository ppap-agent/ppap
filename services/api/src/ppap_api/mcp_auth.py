"""MCP SDK OAuth handlers backed by PPAP identity, consent and PostgreSQL."""

from __future__ import annotations

import asyncio
from html import escape
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastmcp.server.auth import AccessToken, TokenVerifier
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import AuthorizationCode, AuthorizeError, RefreshToken, RegistrationError, TokenError
from mcp.server.auth.routes import build_metadata, create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from ppap_core.feishu import refresh_user_token
from ppap_core.identity import SESSION_COOKIE, new_token
from ppap_core.mcp import READ_SCOPES, SCOPES, McpError, pkce_challenge
from ppap_product_store.mcp import McpStore
from pydantic import AnyHttpUrl, AnyUrl
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount, Route
from starlette.types import ASGIApp


class OAuthProvider(TokenVerifier):
    """Let the SDK parse OAuth and check PKCE; PPAP owns atomic token rotation."""

    def __init__(self, store: McpStore, http):
        self.store = store
        self.settings = store.settings
        self.http = http
        self.base = self.settings.public_api_url.rstrip("/")
        self.resource = self.base + "/mcp"
        self.issuer = self.base + "/oauth"
        super().__init__(base_url=self.base)

    @property
    def scopes_supported(self):
        return list(SCOPES)

    async def get_client(self, client_id):
        data = await asyncio.to_thread(self.store.client, client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info):
        try:
            await asyncio.to_thread(self.store.register, client_info.client_id, client_info.model_dump(mode="json"))
        except McpError:
            raise RegistrationError("invalid_client_metadata", "Unsupported client metadata") from None

    async def authorize(self, client, params):
        try:
            ident = await asyncio.to_thread(
                self.store.authorization,
                {
                    "client_id": client.client_id,
                    "response_type": "code",
                    "redirect_uri": str(params.redirect_uri),
                    "resource": params.resource,
                    "scope": " ".join(params.scopes or READ_SCOPES),
                    "state": params.state or "",
                    "code_challenge": params.code_challenge,
                    "code_challenge_method": "S256",
                },
                self.resource,
            )
        except McpError as exc:
            raise AuthorizeError("invalid_request", exc.code) from None
        return self.base + "/oauth/consent/" + ident

    async def load_authorization_code(self, client, authorization_code):
        row = await asyncio.to_thread(self.store.code, authorization_code, client.client_id)
        if not row:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=row["granted_scopes"],
            expires_at=row["expires_at"].timestamp(),
            client_id=row["client_id"],
            code_challenge=row["challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=True,
            resource=row["resource"],
            subject=str(row["grant_id"]),
        )

    async def exchange_authorization_code(self, client, authorization_code):
        try:
            result = await asyncio.to_thread(
                self.store.exchange,
                {
                    "grant_type": "authorization_code",
                    "client_id": client.client_id,
                    "code": authorization_code.code,
                    "redirect_uri": str(authorization_code.redirect_uri),
                    "resource": authorization_code.resource,
                },
                self.resource,
                verified_challenge=authorization_code.code_challenge,
            )
        except McpError as exc:
            raise TokenError("invalid_grant", exc.code) from None
        return OAuthToken.model_validate(result)

    async def load_refresh_token(self, client, refresh_token):
        row = await asyncio.to_thread(self.store.token, refresh_token, "refresh")
        if not row or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token, client_id=row["client_id"], scopes=row["scopes"], expires_at=int(row["expires_at"].timestamp()), resource=row["resource"], subject=str(row["grant_id"])
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        try:
            result = await asyncio.to_thread(
                self.store.exchange,
                {
                    "grant_type": "refresh_token",
                    "client_id": client.client_id,
                    "refresh_token": refresh_token.token,
                    "scope": " ".join(scopes),
                    "resource": refresh_token.resource,
                },
                self.resource,
            )
        except McpError as exc:
            raise TokenError("invalid_grant", exc.code) from None
        return OAuthToken.model_validate(result)

    def delegation(self, token: str):
        return self.store.authenticate(token, self.resource, lambda refresh: refresh_user_token(self.http, self.settings, refresh))

    async def load_access_token(self, token):
        try:
            # Authentication verifies the PPAP grant. Provider refresh happens
            # at tool/transfer execution, where a busy result is retryable and
            # does not incorrectly tell the client its OAuth token is invalid.
            delegation = await asyncio.to_thread(self.store.authenticate, token, self.resource)
            row = await asyncio.to_thread(self.store.token, token, "access")
            if not row:
                return None
        except McpError:
            return None
        return AccessToken(
            token=token,
            client_id=delegation.client_id,
            scopes=sorted(delegation.scopes),
            expires_at=int(row["expires_at"].timestamp()),
            resource=self.resource,
            subject=delegation.session.principal.tenant_id + ":" + delegation.session.principal.user_id,
            claims={"grant_id": delegation.id},
        )

    async def verify_token(self, token):
        return await self.load_access_token(token)

    async def revoke_token(self, token):
        await asyncio.to_thread(self.store.revoke, token.token, token.client_id)


def page(title: str, body: str, status: int = 200):
    # No remote fonts, images, scripts or client-provided HTML on the consent origin.
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)} · PPAP</title><style>body{{font:16px/1.6 system-ui;margin:40px auto;padding:0 24px;max-width:680px;color:#17212b}}"
        "label{display:block;padding:8px 0}input{margin-right:10px}button{font:inherit;padding:10px 20px;margin:16px 12px 0 0;cursor:pointer}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#174ea6}small{color:#52606d}h1{font-size:26px}</style>"
        f"<main><p>PPAP</p><h1>{escape(title)}</h1>{body}</main></html>",
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        },
    )


class OAuthGuard:
    """Bound request size/rate and enforce RFC 8707, which the SDK token handler omits."""

    def __init__(self, app: ASGIApp, provider: OAuthProvider):
        self.app, self.provider = app, provider

    async def __call__(self, scope, receive, send):
        request = Request(scope, receive)
        endpoint = scope["path"].rsplit("/", 1)[-1]
        try:
            if request.method not in {"GET", "OPTIONS", "POST"}:
                raise McpError("method_not_allowed", 405)
            if request.method == "OPTIONS":
                return await self.app(scope, receive, send)
            await asyncio.to_thread(self.provider.store.rate_limit, "oauth:" + endpoint + ":" + str(scope.get("client", ("unknown",))[0]), 60)
            body = bytearray()
            if request.method == "POST":
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise McpError("request_too_large", 413)
                if endpoint in {"token", "authorize"}:
                    pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=30)
                else:
                    pairs = []
            else:
                pairs = list(request.query_params.multi_items())
            if endpoint in {"token", "authorize"}:
                if len({p[0] for p in pairs}) != len(pairs):
                    raise McpError("invalid_request")
                args = dict(pairs)
                if args.get("resource") != self.provider.resource:
                    raise McpError("invalid_target")
                if endpoint == "authorize" and (not args.get("redirect_uri") or args.get("code_challenge_method") != "S256"):
                    raise McpError("invalid_request")
                if endpoint == "token" and args.get("grant_type") == "authorization_code":
                    pkce_challenge(args.get("code_verifier", ""))
            sent = False

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, replay if request.method == "POST" else receive, send)
        except (UnicodeError, ValueError) as exc:
            error = exc if isinstance(exc, McpError) else McpError("invalid_request")
            await JSONResponse({"error": error.code}, status_code=error.status, headers={"Cache-Control": "no-store", "Retry-After": "60"})(scope, receive, send)


def register_oauth(app, provider: OAuthProvider, session_of):
    registration = ClientRegistrationOptions(enabled=True, valid_scopes=list(SCOPES), default_scopes=list(READ_SCOPES))
    revocation = RevocationOptions(enabled=True)
    issuer = AnyHttpUrl(provider.issuer)
    routes = create_auth_routes(provider, issuer, client_registration_options=registration, revocation_options=revocation)

    async def revoke(request: Request):
        # SDK 1.30's revocation model requires a client_secret even for public
        # clients. Reuse its authenticator; revocation must not refresh Feishu.
        try:
            client = await ClientAuthenticator(provider).authenticate_request(request)
        except AuthenticationError:
            return JSONResponse({"error": "unauthorized_client"}, status_code=401)
        form = await request.form()
        token = form.get("token")
        if not isinstance(token, str) or not token or len(token) > 1024:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if not client.client_id:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        await asyncio.to_thread(provider.store.revoke, token, client.client_id)
        return JSONResponse({}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    routes = [Route("/revoke", revoke, methods=["POST"]) if r.path == "/revoke" else r for r in routes]
    metadata = build_metadata(issuer, None, registration, revocation).model_dump(mode="json", exclude_none=True)
    metadata["token_endpoint_auth_methods_supported"] = ["none", "client_secret_post", "client_secret_basic"]
    metadata["revocation_endpoint_auth_methods_supported"] = ["none", "client_secret_post", "client_secret_basic"]

    async def auth_metadata(request: Request):
        return JSONResponse(metadata, headers={"Access-Control-Allow-Origin": "*"})

    issuer_path = urlsplit(provider.issuer).path
    for path in {"/.well-known/oauth-authorization-server" + issuer_path, "/oauth/.well-known/oauth-authorization-server"}:
        app.add_api_route(path, auth_metadata, methods=["GET"], include_in_schema=False)
    app.router.routes.extend(create_protected_resource_routes(AnyHttpUrl(provider.resource), [issuer], list(SCOPES), "PPAP"))

    def sign_in(path: str):
        # This target is server-built, unlike the ordinary user-provided return_to.
        state = new_token()
        provider.store.product.save_state(state, provider.base + path)
        from ppap_core.feishu import authorize_url

        return RedirectResponse(authorize_url(provider.settings, state), status_code=302)

    @app.get("/oauth/consent/{ident}", include_in_schema=False)
    def consent_get(ident: str, request: Request):
        session = session_of(request)
        if not session:
            if request.query_params.get("auth_error"):
                return page("登录未完成", '<p>请重新登录后授权。</p><a href="' + escape(provider.base + "/oauth/consent/" + quote(ident), quote=True) + '">重新登录</a>', 401)
            return sign_in("/oauth/consent/" + quote(ident))
        try:
            view = provider.store.consent_view(ident, request.cookies.get(SESSION_COOKIE, ""))
        except McpError as exc:
            return page("授权请求已失效", "<p>请回到客户端重新连接。</p>", exc.status)
        boxes = "".join(f'<label><input type="checkbox" name="scope" value="{escape(s)}" checked>{escape(SCOPES[s])}</label>' for s in view["scopes"])
        return page(
            "连接外部客户端",
            f"<p><strong>{escape(view['client'].get('client_name') or 'MCP client')}</strong> 请求访问你的 PPAP 数据。</p>"
            f"<p>当前用户：{escape(session.principal.display_name)}。权限仅限你当前可访问的对象，可随时撤销。</p>"
            f'<p><small>客户端回调：{escape(view["redirect_uri"])}</small></p><form method="post">'
            f'<input type="hidden" name="csrf" value="{escape(view["csrf"])}">{boxes}'
            '<button name="decision" value="approve">允许所选权限</button><button name="decision" value="deny">拒绝</button></form>',
        )

    @app.post("/oauth/consent/{ident}", include_in_schema=False)
    async def consent_post(ident: str, request: Request):
        session = await asyncio.to_thread(session_of, request)
        if not session:
            return page("请重新登录", "<p>会话已过期，未保存授权。</p>", 401)
        if len(await request.body()) > 8192:
            return page("请求过大", "<p>未保存授权。</p>", 413)
        form = await request.form()
        try:
            result = await asyncio.to_thread(
                provider.store.consent, ident, request.cookies.get(SESSION_COOKIE, ""), str(form.get("csrf", "")), session, [str(s) for s in form.getlist("scope")], form.get("decision") == "approve"
            )
        except McpError as exc:
            return page("授权未保存", "<p>授权请求失效或校验未通过。请从客户端重新连接。</p>", exc.status)
        parts = urlsplit(result.pop("redirect_uri"))
        return RedirectResponse(urlunsplit(parts._replace(query=urlencode([*parse_qsl(parts.query), *result.items()]))), 303)

    # Mount after browser routes so /oauth/consent remains owned by the real session.
    oauth = OAuthGuard(Starlette(routes=[r for r in routes if not r.path.startswith("/.well-known")]), provider)
    app.router.routes.append(
        Mount("/oauth", app=CORSMiddleware(oauth, allow_origins=["*"], allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "MCP-Protocol-Version"]))
    )
