from __future__ import annotations

from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse
from ppap_application.rule_sources import rule_sources, validate_registration_sources
from ppap_core.files import ObjectMissing
from ppap_core.rules import document_types, latest_rule_versions
from ppap_core.types import PpapDocumentType

from ppap_api import web_contract as wc


def customer_catalog(store, tenant_id: str) -> wc.AuditCustomerCatalog:
    customers: dict[str, wc.AuditCustomer] = {}
    for rule in latest_rule_versions(store.list_rules(tenant_id)):
        document_type = PpapDocumentType.from_string(rule.get("type") or "").get_type_name()
        if rule.get("kind") != "engine" or document_type == PpapDocumentType.UNKNOWN.get_type_name() or not isinstance(rule.get("customers"), list):
            continue
        for value in rule.get("customers") or []:
            if not isinstance(value, str) or not value.strip():
                continue
            name, key = value.strip(), value.strip().casefold()
            customers.setdefault(key, {"customer_id": key, "name": name})
    return {"customers": sorted(customers.values(), key=lambda item: item["customer_id"])}


def register_rule_routes(app: FastAPI, store, principal_of, require_file_read):
    def sources(principal, file_id, version_id, version=None):
        version = version or store.get_version(file_id, version_id)
        if version is None or version.tenant_id != principal.tenant_id:
            raise KeyError("missing")
        content = store.version_bytes(version)
        try:
            return version, rule_sources(version.original_filename, content)
        except Exception:
            raise ValueError("rule_source_unavailable") from None

    @app.get("/rule-library", response_model=wc.RuleLibrary)
    def read_rule_library(request: Request):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers={"Cache-Control": "private, no-store"})
        return JSONResponse({"versions": store.list_rule_library(principal.tenant_id)}, headers={"Cache-Control": "private, no-store"})

    @app.get("/files/{file_id}/versions/{version_id}/rule-sources", response_model=wc.RuleSources)
    def read_rule_sources(file_id: str, version_id: str, request: Request):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            version = store.read_file_version(principal, file_id, version_id)
            _, units = sources(principal, file_id, version_id, version)
            return JSONResponse({"file_version_id": version_id, "units": units}, headers={"Cache-Control": "private, no-store"})
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except (ValueError, ObjectMissing):
            return JSONResponse({"error": "rule_source_unavailable"}, status_code=409)

    @app.get("/audit-customers", response_model=wc.AuditCustomerCatalog)
    def read_customer_catalog(request: Request):
        headers = {"Cache-Control": "private, no-store"}
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=headers)
        return JSONResponse(customer_catalog(store, principal.tenant_id), headers=headers)

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
            if isinstance(body.get("case_context"), dict) and "registration" in body["case_context"]:
                rule_body = body.get("body")
                if not isinstance(rule_body, dict):
                    raise ValueError("invalid_rule_body")
                previous = store.get_rule(principal.tenant_id, body.get("predecessor_id")) if body["case_context"].get("customer_scope") == "all" else None
                scope_only = (
                    previous
                    and body.get("title") == previous["title"]
                    and rule_body == previous["body"]
                    and body.get("source_file_version_id") == previous["source_file_version_id"]
                    and body["case_context"] == {**previous["case_context"], "customer_scope": "all"}
                )
                # Inherit existing citations for a scope-only successor, not a new claim that they were rechecked.
                if not scope_only:
                    version = store.readable_version(principal, body.get("source_file_version_id"))
                    version, units = sources(principal, version.file_id, version.id)
                    validate_registration_sources(body["case_context"], rule_body, version.id, version.original_filename, units)
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
        except ObjectMissing:
            return JSONResponse({"error": "rule_source_unavailable"}, status_code=409)
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
