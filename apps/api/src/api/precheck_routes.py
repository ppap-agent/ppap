"""Free data-standardization precheck of an audit task: read-only, outside the checker flow, never billed."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from product.files import attachment_content_disposition
from product.precheck_snapshot import current_precheck
from product.store import AuthStore

PrecheckLevel = Literal["ok", "warn", "block", "unmatched", "reference", "pending"]


class PrecheckFinding(TypedDict):
    number: str
    code: str
    title: str
    level: Literal["block", "warn"]
    file_version_id: str
    filename: str
    location: str
    evidence: str
    problem: str
    action: str
    responsibility: str
    scope: Literal["file", "content"]


class PrecheckSourceMismatch(TypedDict):
    """S07: the linked peer arrived but names another part; `value` is the audited file's, `found_value` the peer's."""

    document: str
    document_type: str
    identity: str
    value: str
    found_value: str
    found_file_version_id: str
    found_filename: str


class PrecheckMissingPeer(TypedDict):
    """S04: the peer this file is compared against is not in the batch."""

    document: str
    document_type: str
    part_number: NotRequired[str]


class PrecheckCoverage(TypedDict):
    """S06: `judged` of the `total` criteria the precheck should decide for this file."""

    judged: int
    total: int


class PrecheckStatus(TypedDict):
    code: str
    scope: NotRequired[str]
    file_version_id: str
    filename: str
    note: str
    source_mismatch: NotRequired[PrecheckSourceMismatch]
    missing: NotRequired[PrecheckMissingPeer]
    coverage: NotRequired[PrecheckCoverage]


class PrecheckCriterion(TypedDict):
    criterion_id: str
    criterion: str
    result: str
    code: str | None
    location: str
    evidence: str
    reason: str
    source: str


class PrecheckFile(TypedDict):
    file_version_id: str
    filename: str
    level: PrecheckLevel
    document_type: str | None
    customer: str | None
    entry_key: NotRequired[str | None]
    criteria: NotRequired[list[PrecheckCriterion]]


class PrecheckCounts(TypedDict):
    ok: int
    warn: int
    block: int
    unmatched: int
    reference: int
    pending: int


class AuditTaskPrecheck(TypedDict):
    outline_version: str
    conclusion: Literal["pass", "fail", "not_run"]
    files: list[PrecheckFile]
    blocks: list[PrecheckFinding]
    warnings: list[PrecheckFinding]
    statuses: list[PrecheckStatus]
    # 未审条数: S03/S04/S05/S07 one each plus the open criteria of each S06; S01/S02 are not counted.
    unreviewed: int
    counts: PrecheckCounts


HEADERS = {"Cache-Control": "private, no-store"}


def register_precheck_routes(app: FastAPI, store: AuthStore, principal_of) -> None:
    def read(request: Request, task_id: str):
        principal = principal_of(request)
        if not principal:
            return None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            # The task read applies tenant and object authorization.
            task = store.get_audit_task(principal, task_id)
        except (KeyError, ValueError):
            return None, JSONResponse({"error": "access_denied"}, status_code=403)
        # An accepted task reads the precheck frozen with its acceptance; before that, and for Rounds accepted
        # before snapshots existed, the task as it stands.
        frozen = store.round_precheck(principal, task["round_id"]) if task.get("round_id") else None
        return {"task": task, **(frozen or current_precheck(store, principal, task))}, None

    @app.get("/audit-tasks/{task_id}/precheck", response_model=AuditTaskPrecheck)
    def audit_task_precheck(task_id: str, request: Request) -> JSONResponse:
        source, denied = read(request, task_id)
        if denied:
            return denied
        return JSONResponse(source["precheck"], headers=HEADERS)

    @app.get("/audit-tasks/{task_id}/precheck/report", response_class=Response, responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
    def audit_task_precheck_report(task_id: str, request: Request) -> Response:
        source, denied = read(request, task_id)
        if denied:
            return denied
        from documents.precheck_pdf import render_precheck_pdf

        task = source["task"]
        content = render_precheck_pdf({"task_number": task.get("number"), "generated_at": source["generated_at"], "recognition": source["recognition"], "precheck": source["precheck"]})
        # The task number is what users see; the internal task id never names a delivered file.
        filename = f"{task['number']}-预检报告.pdf" if task.get("number") else "预检报告.pdf"
        return Response(content, media_type="application/pdf", headers={"Content-Disposition": attachment_content_disposition(filename), **HEADERS})
