from __future__ import annotations

from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse
from ppap_core.rules import document_types

from ppap_api import web_contract as wc


def register_rule_routes(app: FastAPI, store, principal_of):
    @app.get("/rule-types", response_model=wc.RuleTypes)
    def rules_types(request: Request):
        if not principal_of(request):
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return {"types": document_types()}

    @app.post("/rules/parse", response_model=wc.RuleParseJob, status_code=202, openapi_extra=wc.openapi_body(wc.RuleParseRequest))
    def rules_parse(request: Request, body: Any = Body(...)):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid_rule_request"}, status_code=400)
        try:
            accepted = store.accept_rule_parse(principal, body.get("request_id"), body.get("source_file_id"), body.get("source_file_version_id"), body.get("document_type", ""))
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409 if str(exc) in {"file_not_ready", "rule_parse_conflict"} else 400)
        return JSONResponse(accepted, status_code=202)

    @app.get("/rules/parse/{parse_id}", response_model=wc.RuleParseJob)
    def rules_parse_get(parse_id: str, request: Request):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            return store.get_rule_parse(principal, parse_id)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ValueError:
            return JSONResponse({"error": "file_not_ready"}, status_code=409)

    @app.post("/rules", response_model=wc.RuleDetail, status_code=201, openapi_extra=wc.openapi_body(wc.RulePublishRequest))
    def rules_publish(request: Request, body: Any = Body(...)):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid_rule_request"}, status_code=400)
        try:
            published = store.publish_rule(
                principal,
                body.get("title"),
                body.get("body"),
                body.get("source_file_version_id"),
                body.get("predecessor_id"),
                body.get("request_id"),
                body.get("case_context"),
            )
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409 if str(exc) == "file_not_ready" else 400)
        return JSONResponse(published, status_code=201)

    @app.get("/rules/{rule_version_id}", response_model=wc.RuleDetail)
    def rules_get(rule_version_id: str, request: Request):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        result = store.get_rule(principal.tenant_id, rule_version_id)
        if result is None:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        return JSONResponse(result)
