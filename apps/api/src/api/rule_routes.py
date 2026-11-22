from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse
from product.files import ObjectMissing
from product.logs import emit
from product.rules import latest_rule_versions, normalize_document_type
from worker.intake.rule_sources import rule_sources, validate_registration_sources

from api import web_contract as wc


def customer_catalog(store, tenant_id: str) -> wc.AuditCustomerCatalog:
    """Every registered customer with M and both coverage numerators; rule customer names that match no register entry are listed as recorded."""
    from product import customers as register
    from product import template_library

    rules = [rule for rule in latest_rule_versions(store.list_rules(tenant_id)) if rule.get("kind") == "engine" and isinstance(rule.get("customers"), list)]
    root = template_library.template_root(store.settings)
    rows: list[wc.AuditCustomer] = []
    for customer in register.CUSTOMERS:
        templates = template_library.find_customer(customer.id)
        template_codes = [template.type_code for template, _size in template_library.available(root, templates)] if templates else []
        coverage = cast(wc.CustomerCoverage | None, register.coverage(customer, template_codes, register.rule_type_codes(customer, rules)))
        if coverage is None:
            emit("customer_type_set_missing", level=logging.WARNING, customer_id=customer.id)
        rows.append({"customer_id": customer.id, "name": customer.short_name, "full_name": customer.full_name, "short_name": customer.short_name, "coverage": coverage})
    unregistered: set[str] = set()
    for rule in rules:
        try:
            normalize_document_type(rule.get("type"))
        except ValueError:
            continue
        unregistered.update(value.strip() for value in rule["customers"] if isinstance(value, str) and value.strip() and register.resolve(value) is None)
    return {"customers": rows, "unregistered": sorted(unregistered)}


def register_rule_routes(app: FastAPI, store, principal_of):
    def sources(principal, file_id, version_id, version=None):
        version = version or store.get_version(file_id, version_id)
        if version is None or version.tenant_id != principal.tenant_id:
            raise KeyError("missing")
        content = store.version_bytes(version)
        try:
            return version, rule_sources(version.original_filename, content)
        except Exception:
            raise ValueError("rule_source_unavailable") from None

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

    @app.get("/audit-customers/default", response_model=wc.AuditCustomerDefault)
    def read_customer_default(request: Request):
        headers = {"Cache-Control": "private, no-store"}
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=headers)
        try:
            return JSONResponse(store.customer_default(principal), headers=headers)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403, headers=headers)

    @app.put("/audit-customers/default", response_model=wc.AuditCustomerDefault, openapi_extra=wc.openapi_body(wc.AuditCustomerDefaultRequest))
    def update_customer_default(request: Request, body: Any = Body(...)):
        headers = {"Cache-Control": "private, no-store"}
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=headers)
        if not isinstance(body, dict) or set(body) != {"customer_preference", "expected_revision", "confirmed"}:
            return JSONResponse({"error": "invalid_customer_default"}, status_code=400, headers=headers)
        try:
            result = store.set_customer_default(principal, body["customer_preference"], body["expected_revision"], body["confirmed"])
            return JSONResponse(result, headers=headers)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403, headers=headers)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409 if str(exc) == "customer_default_conflict" else 400, headers=headers)

    @app.post("/rules/parse", response_model=wc.RuleParseJob, status_code=202, openapi_extra=wc.openapi_body(wc.RuleParseRequest))
    def rules_parse(request: Request, body: Any = Body(...)):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not store.settings.platform_operator(principal.tenant_key, principal.subject):
            return JSONResponse({"error": "platform_operator_required"}, status_code=403)
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
        if not store.settings.platform_operator(principal.tenant_key, principal.subject):
            return JSONResponse({"error": "platform_operator_required"}, status_code=403)
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
            return JSONResponse({"error": str(exc)}, status_code=409 if str(exc) in {"file_not_ready", "suite_head_conflict", "idempotency_conflict"} else 400)
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
