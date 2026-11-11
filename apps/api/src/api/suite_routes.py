"""Enterprise Suite candidates and releases(). Tests never accept formal Rounds."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse
from product.files import ObjectMissing
from product.store.suite_releases import SuiteReleaseStore

from api import web_contract as wc


class SuiteValidation(TypedDict):
    status: Literal["verified", "unverified", "stale"]
    revision: int
    write_revision: int
    proof_scope: Literal["official", "tenant", "none"]
    total_samples: int
    passed_samples: int
    new_errors: int
    verified: bool
    samples_not_rerun: bool
    reasons: list[str]
    library_revision: NotRequired[str]
    proof_digest: NotRequired[str]


class SuitePublication(TypedDict):
    operation_id: str
    suite_key: str
    revision_id: str
    previous_revision_id: str | None
    expected_revision: int
    revision: int
    verification_status: str
    force_unverified: bool
    published_by: str | None
    published_at: str
    published_by_name: str


class ClauseChange(TypedDict):
    clause_id: str
    kind: Literal["added", "removed", "changed", "unchanged"]
    before: str | None
    after: str | None
    checker: str | None


class RuleImpact(TypedDict):
    source_revision_id: str
    target_revision_id: str
    changed: bool
    appendix_changed: bool
    clauses: list[ClauseChange]


class SuiteActive(TypedDict):
    candidate_revision_id: NotRequired[str]
    rule_update: NotRequired[RuleImpact | None]
    official_rule_impact: NotRequired[RuleImpact | None]
    title: str
    version: int
    origin: Literal["official", "enterprise"]
    selection_mode: Literal["automatic_official", "pinned", "candidate_only"]
    suite_key: str
    revision: int
    current_revision_id: str | None
    latest_official_revision_id: str | None
    latest_official_version: int | None
    official_base_revision_id: str | None
    official_update_available: bool
    official_update_notice: bool
    offered_revision_id: NotRequired[str | None]
    verification_status: str
    force_unverified: bool
    validation: SuiteValidation
    history: list[SuitePublication]


class SuiteSummary(TypedDict):
    """One catalog row. Change details, history and validation proof are read per Suite."""

    suite_key: str
    title: str
    origin: Literal["official", "enterprise"]
    selection_mode: Literal["automatic_official", "pinned", "candidate_only"]
    current_revision_id: str | None
    candidate_revision_id: NotRequired[str]
    verification_status: str
    rule_update_pending: bool
    official_update_available: bool
    program_missing: bool


class SuiteUnavailable(TypedDict):
    suite_key: str
    error: Literal["suite_unavailable"]


class SuitePredefined(TypedDict):
    path: str
    title: str
    availability: Literal["installed", "unseeded", "unavailable", "historical"]
    suite_key: NotRequired[str]
    revision_id: NotRequired[str]
    clause_count: NotRequired[int]
    bound_clause_count: NotRequired[int]
    evidence_reader_clause_ids: NotRequired[list[str]]


class SuitePredefinedUnavailable(TypedDict):
    path: str
    error: Literal["unseeded", "unavailable", "invalid"]


class SuiteCatalog(TypedDict):
    suites: list[SuiteSummary]
    unavailable: NotRequired[list[SuiteUnavailable]]
    predefined: NotRequired[list[SuitePredefined]]
    predefined_unavailable: NotRequired[list[SuitePredefinedUnavailable]]
    predefined_error: NotRequired[Literal["predefined_catalog_unavailable"]]


class SuiteRound(TypedDict):
    id: str
    task_id: str | None
    created_at: str


class SuiteDiff(TypedDict):
    path: str
    before: str
    after: str


class SuiteUpgradeReceipt(TypedDict):
    operation_id: str
    source_revision_id: str
    target_revision_id: str
    expected_revision: int


class SuiteUpgradeRequest(SuiteUpgradeReceipt):
    pass


class SuiteOfficialUpdateSeen(TypedDict):
    official_revision_id: str


class SuiteRevisionDetail(TypedDict):
    rule_impact: NotRequired[RuleImpact | None]
    official_base_revision_id: NotRequired[str | None]
    upgrade: NotRequired[SuiteUpgradeReceipt]
    title: str
    version: int
    created_by_name: str
    diff: list[SuiteDiff]
    id: str
    tenant_id: str | None
    suite_key: str
    base_revision_id: str | None
    created_by: str | None
    created_at: str
    resolved_digest: str
    related_suite_keys: list[str]
    unadopted_related_suite_keys: NotRequired[list[str]]
    files: dict[str, str]
    changed_paths: list[str]
    validation: SuiteValidation
    round_count: int
    rounds: list[SuiteRound]


class SuiteVariantRequest(TypedDict):
    base_revision_id: str
    files: dict[str, str]
    operation_id: NotRequired[str]


class SuiteCandidateRequest(TypedDict):
    files: dict[str, str]
    operation_id: NotRequired[str]


class SuitePublishRequest(TypedDict):
    revision_id: str
    expected_revision: int
    operation_id: str
    force_unverified: bool


def register_suite_routes(app: FastAPI, store, principal_of):
    def releases():
        return SuiteReleaseStore(store.settings, store.objects, role=store.role)

    headers = {"Cache-Control": "private, no-store"}

    def respond(request: Request, action, *, status_code: int = 200):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=headers)
        try:
            return JSONResponse(action(principal), status_code=status_code, headers=headers)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403, headers=headers)
        except ObjectMissing:
            return JSONResponse({"error": "suite_artifact_missing"}, status_code=409, headers=headers)
        except ValueError as exc:
            code = str(exc)
            status = (
                404
                if code == "suite_candidate_not_found"
                else 409
                if code
                in {
                    "suite_head_conflict",
                    "suite_artifact_missing",
                    "suite_official_head_conflict",
                    "suite_operation_conflict",
                    "suite_confirmation_required",
                    "suite_version_confirmation_required",
                    "suite_dependencies_not_adopted",
                    "partial_directory_suite_not_adoptable",
                }
                else 400
            )
            return JSONResponse({"error": code}, status_code=status, headers=headers)

    def body_fields(body, fields: set[str], optional: frozenset[str] = frozenset()):
        if not isinstance(body, dict) or not fields <= set(body) or set(body) - fields - optional:
            raise ValueError("invalid_suite_request")
        return body

    @app.get("/suites", response_model=SuiteCatalog)
    def suites_list(request: Request):
        return respond(request, releases().list_suites)

    @app.get("/suites/{suite_key}/active", response_model=SuiteActive)
    def suites_active(suite_key: str, request: Request):
        return respond(request, lambda principal: releases().active(principal, suite_key))

    @app.put("/suites/{suite_key}/active", response_model=SuitePublication, openapi_extra=wc.openapi_body(SuitePublishRequest))
    def suites_publish(suite_key: str, request: Request, body: Any = Body(...)):
        return respond(request, lambda principal: releases().publish(principal, suite_key, **body_fields(body, {"revision_id", "expected_revision", "operation_id", "force_unverified"})))

    @app.post("/suites/{suite_key}/official-update/seen", response_model=SuiteActive, openapi_extra=wc.openapi_body(SuiteOfficialUpdateSeen))
    def suite_official_update_seen(suite_key: str, request: Request, body: Any = Body(...)):
        return respond(request, lambda principal: releases().acknowledge_official_update(principal, suite_key, **body_fields(body, {"official_revision_id"})))

    @app.post("/suites/{suite_key}/upgrades", response_model=SuiteRevisionDetail, status_code=201, openapi_extra=wc.openapi_body(SuiteUpgradeRequest))
    def suite_official_upgrade(suite_key: str, request: Request, body: Any = Body(...)):
        return respond(
            request,
            lambda principal: releases().upgrade_official(
                principal,
                suite_key,
                **body_fields(body, {"source_revision_id", "target_revision_id", "expected_revision", "operation_id"}),
            ),
            status_code=201,
        )

    @app.get("/suites/{suite_key}/upgrades/{operation_id}", response_model=SuiteRevisionDetail)
    def suite_official_upgrade_receipt(suite_key: str, operation_id: str, request: Request):
        return respond(request, lambda principal: releases().read_upgrade(principal, suite_key, operation_id))

    @app.get("/suite-revisions/{revision_id}", response_model=SuiteRevisionDetail)
    def suite_revision(revision_id: str, request: Request):
        return respond(request, lambda principal: releases().detail(principal, revision_id))

    @app.post("/suites/{suite_key}/variants", response_model=SuiteRevisionDetail, status_code=201, openapi_extra=wc.openapi_body(SuiteVariantRequest))
    def suite_variant(suite_key: str, request: Request, body: Any = Body(...)):
        return respond(
            request, lambda principal: releases().save_candidate(principal, suite_key=suite_key, **body_fields(body, {"base_revision_id", "files"}, frozenset({"operation_id"}))), status_code=201
        )

    @app.post("/suite-revisions/{revision_id}/candidates", response_model=SuiteRevisionDetail, status_code=201, openapi_extra=wc.openapi_body(SuiteCandidateRequest))
    def suite_candidate(revision_id: str, request: Request, body: Any = Body(...)):
        return respond(request, lambda principal: releases().save_candidate(principal, revision_id, **body_fields(body, {"files"}, frozenset({"operation_id"}))), status_code=201)

    @app.get("/suite-revisions/{revision_id}/candidates/{operation_id}", response_model=SuiteRevisionDetail)
    def suite_candidate_receipt(revision_id: str, operation_id: str, request: Request):
        return respond(request, lambda principal: releases().read_candidate(principal, revision_id, operation_id))
