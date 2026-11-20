"""Signs Feishu JSAPI authentication for our own pages, for the signed-in member's organization."""

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from product.feishu_jsapi import FeishuJsapiError, JsapiTickets, jsapi_config, signed_page
from product.feishu_usage import FeishuUsageError, tenant_access_token

from api import web_contract as wc

HEADERS = {"Cache-Control": "private, no-store"}


def register_feishu_jsapi_routes(app, store, session_of, http, settings, tickets: JsapiTickets | None = None):
    tickets = tickets or JsapiTickets()

    @app.get("/integrations/feishu/jsapi-config", response_model=wc.FeishuJsapiConfig)
    def feishu_jsapi_config(request: Request, url: str = Query(default="")) -> JSONResponse:
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        principal = session.principal
        if principal.provider != "feishu" or principal.app_id != settings.feishu_app_id:
            return JSONResponse({"error": "feishu_session_required"}, status_code=403, headers=HEADERS)
        # Only our own pages are signed, so a signature cannot authorize another site's page.
        page = signed_page(url, settings.canonical_web_url())
        if page is None:
            return JSONResponse({"error": "invalid_jsapi_url"}, status_code=400, headers=HEADERS)

        def tenant_token() -> str:
            return tenant_access_token(http, settings.feishu_app_id, settings.feishu_app_secret, store.app_ticket(settings.feishu_app_id) or "", principal.tenant_key)

        try:
            ticket = tickets.ticket(http, principal.tenant_key, tenant_token)
        except (FeishuJsapiError, FeishuUsageError):
            return JSONResponse({"error": "feishu_jsapi_unavailable"}, status_code=503, headers=HEADERS)
        return JSONResponse(jsapi_config(settings.feishu_app_id, ticket, page), headers=HEADERS)
