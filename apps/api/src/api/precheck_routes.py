"""Free data-standardization precheck of an audit task: read-only, outside the checker flow, never billed."""

from __future__ import annotations

import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Literal, NotRequired, TypedDict
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from product.customers import full_name
from product.files import attachment_content_disposition
from product.precheck import precheck_task
from product.precheck_content import entry_key, precheck_content
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


class PrecheckStatus(TypedDict):
    code: str
    scope: NotRequired[str]
    file_version_id: str
    filename: str
    note: str
    source_mismatch: NotRequired[PrecheckSourceMismatch]
    missing: NotRequired[PrecheckMissingPeer]


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
    counts: PrecheckCounts


HEADERS = {"Cache-Control": "private, no-store"}
# Content results depend only on the bytes and the rule entry, so one read serves every later view.
_CACHE: OrderedDict[tuple, list[dict]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="precheck")


def _cached_content(key: tuple, read) -> list[dict]:
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    result = read()
    with _CACHE_LOCK:
        _CACHE[key] = result
        while len(_CACHE) > 512:
            _CACHE.popitem(last=False)
    return result


COVER_FIELDS = ("customer", "project", "model", "part_number", "part")


def _cover_recognition(task: dict) -> dict[str, str | None]:
    """Cover values from the frozen recognition (match metadata) of the submitted files, never display corrections.

    Differing values are all listed; a field no file recognized stays empty. The customer reads as its registered full name.
    """
    rows = [row for row in task.get("files", []) if row.get("selection") == "selected" and not row.get("reference_only") and (row.get("match") or {}).get("state") == "matched"]
    cover: dict[str, str | None] = {}
    for field in COVER_FIELDS:
        # The customer falls back to the task's customer, as the precheck routes it.
        found = [((row.get("match") or {}).get("metadata") or {}).get(field) or (task.get("customer_preference") if field == "customer" else None) for row in rows]
        values = [full_name(value) if field == "customer" else value.strip() for value in found if isinstance(value, str) and value.strip()]
        cover[field] = "、".join(dict.fromkeys(values)) or None
    return cover


def register_precheck_routes(app: FastAPI, store: AuthStore, principal_of) -> None:
    def read(request: Request, task_id: str):
        principal = principal_of(request)
        if not principal:
            return None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            # The task read applies tenant and object authorization.
            return store.get_audit_task(principal, task_id), None
        except (KeyError, ValueError):
            return None, JSONResponse({"error": "access_denied"}, status_code=403)

    def content_of(principal, task: dict) -> dict[str, list[dict]]:
        """Read each recognized target once and run the rule entry routed to it; an unreadable file is left to the file level."""
        jobs = {}
        for row in task.get("files", []):
            match = row.get("match") or {}
            if row.get("selection") != "selected" or match.get("state") != "matched" or row.get("reference_only"):
                continue
            customer = (match.get("metadata") or {}).get("customer") or task.get("customer_preference")
            key = entry_key(customer, match.get("document_type"))
            if key is None:
                continue

            def read(row=row, key=key, customer=customer):
                version = store.read_file_version(principal, row["file"]["file_id"], row["file"]["id"])
                return _cached_content((version.sha256, key, customer), lambda: precheck_content(store.version_bytes(version), version.original_filename, key, customer=customer))

            jobs[row["file"]["id"]] = _POOL.submit(read)
        content = {}
        for file_version_id, job in jobs.items():
            try:
                content[file_version_id] = job.result(timeout=60)
            except Exception:  # noqa: BLE001 - a file that cannot be read stays at the file-level result
                continue
        return content

    def result_of(principal, task: dict) -> dict:
        return precheck_task(task, content_of(principal, task))

    @app.get("/audit-tasks/{task_id}/precheck", response_model=AuditTaskPrecheck)
    def audit_task_precheck(task_id: str, request: Request) -> JSONResponse:
        task, denied = read(request, task_id)
        if denied:
            return denied
        return JSONResponse(result_of(principal_of(request), task), headers=HEADERS)

    @app.get("/audit-tasks/{task_id}/precheck/report", response_class=Response, responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
    def audit_task_precheck_report(task_id: str, request: Request) -> Response:
        task, denied = read(request, task_id)
        if denied:
            return denied
        from documents.precheck_pdf import render_precheck_pdf

        generated_at = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
        source = {"task_number": task.get("number"), "generated_at": generated_at, "recognition": _cover_recognition(task), "precheck": result_of(principal_of(request), task)}
        content = render_precheck_pdf(source)
        # The task number is what users see; the internal task id never names a delivered file.
        filename = f"{task['number']}-预检报告.pdf" if task.get("number") else "预检报告.pdf"
        return Response(content, media_type="application/pdf", headers={"Content-Disposition": attachment_content_disposition(filename), **HEADERS})
