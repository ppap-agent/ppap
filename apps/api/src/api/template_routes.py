"""Built-in template library: list, download, save to the user's Feishu, and customer-offered templates."""

from __future__ import annotations

import re
import threading

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from product import template_library as library
from product.feishu_drive import FeishuMetaError, list_drive_folder
from product.feishu_personal_upload import PersonalDriveClient
from product.files import attachment_content_disposition
from product.logs import emit
from product.store.template_submissions import submit_templates

from api import web_contract as wc

READ_HEADERS = {"Cache-Control": "private, no-store"}
_FOLDER = re.compile(r"[A-Za-z0-9_-]{1,256}")


def register_template_routes(app, store, session_of, http, settings):
    root = library.template_root(settings)
    saving: set[tuple[str, str]] = set()
    saving_lock = threading.Lock()

    def principal_of(request: Request):
        session = session_of(request)
        return session.principal if session else None

    def unauthenticated() -> JSONResponse:
        return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=READ_HEADERS)

    @app.get("/template-library", response_model=wc.TemplateLibrary)
    def template_library(request: Request):
        if not principal_of(request):
            return unauthenticated()
        return JSONResponse(library.catalog(root), headers=READ_HEADERS)

    @app.get(
        "/template-library/{customer_id}/files/{template_id}",
        response_class=Response,
        responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    def template_download(customer_id: str, template_id: str, request: Request):
        if not principal_of(request):
            return unauthenticated()
        customer = library.find_customer(customer_id)
        template = library.find_template(customer, template_id) if customer else None
        if not customer or not template:
            return JSONResponse({"error": "template_not_found"}, status_code=404, headers=READ_HEADERS)
        try:
            body = library.source_path(root, customer, template).read_bytes()
        except OSError:
            return JSONResponse({"error": "template_not_found"}, status_code=404, headers=READ_HEADERS)
        return Response(
            body,
            media_type=library.content_type(template),
            headers={**READ_HEADERS, "Content-Disposition": attachment_content_disposition(template.filename), "X-Content-Type-Options": "nosniff"},
        )

    @app.get(
        "/template-library/{customer_id}/archive",
        response_class=StreamingResponse,
        responses={200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    def template_archive(customer_id: str, request: Request):
        if not principal_of(request):
            return unauthenticated()
        customer = library.find_customer(customer_id)
        if not customer or not library.available(root, customer):
            return JSONResponse({"error": "template_not_found"}, status_code=404, headers=READ_HEADERS)
        return StreamingResponse(
            library.zip_chunks(root, customer),
            media_type="application/zip",
            headers={**READ_HEADERS, "Content-Disposition": attachment_content_disposition(customer.name + "模板.zip")},
        )

    @app.post("/template-library/{customer_id}/feishu", response_model=wc.TemplateSaveResponse, openapi_extra=wc.openapi_body(wc.TemplateSaveRequest))
    def template_save_to_feishu(customer_id: str, body: dict, request: Request):
        session = session_of(request)
        if not session:
            return unauthenticated()
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            return JSONResponse({"error": "feishu_disabled"}, status_code=409)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        customer = library.find_customer(customer_id)
        if not customer:
            return JSONResponse({"error": "template_not_found"}, status_code=404)
        folder = body.get("folder_token")
        if not isinstance(folder, str) or not _FOLDER.fullmatch(folder):
            return JSONResponse({"error": "invalid_folder_token"}, status_code=400)
        present = [template for template, _size in library.available(root, customer)]
        wanted = body.get("template_ids")
        if wanted is None:
            templates = present
        else:
            by_id = {template.id: template for template in present}
            if not isinstance(wanted, list) or not wanted or len(set(map(str, wanted))) != len(wanted) or any(item not in by_id for item in wanted):
                return JSONResponse({"error": "template_not_found"}, status_code=404)
            templates = [by_id[item] for item in wanted]
        if not templates:
            return JSONResponse({"error": "template_not_found"}, status_code=404)
        key = (session.principal.user_id, folder)
        with saving_lock:
            if key in saving:
                return JSONResponse({"error": "template_save_in_progress"}, status_code=409)
            saving.add(key)
        try:
            token = session.access_token
            results = library.save_to_drive(
                root,
                customer,
                templates,
                folder,
                PersonalDriveClient(http, token),
                lambda folder_token, page: list_drive_folder(http, token, folder_token, page),
            )
        except FeishuMetaError:
            return JSONResponse({"error": "feishu_folder_failed"}, status_code=502)
        except ValueError as exc:
            code = "template_folder_too_large" if str(exc) == "folder_too_large" else "invalid_folder_token"
            return JSONResponse({"error": code}, status_code=409 if code == "template_folder_too_large" else 400)
        finally:
            with saving_lock:
                saving.discard(key)
        emit("template_library_saved", customer=customer.id, states=",".join(sorted({row["state"] for row in results})))
        return JSONResponse({"customer_id": customer.id, "folder_token": folder, "results": results})

    @app.post("/template-submissions", response_model=wc.TemplateSubmissionReceipt, openapi_extra=wc.openapi_body(wc.TemplateSubmissionRequest))
    def template_submission(body: dict, request: Request):
        principal = principal_of(request)
        if not principal:
            return unauthenticated()
        try:
            receipt = submit_templates(store, principal, body.get("request_id"), body.get("customer"), body.get("files"))
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ValueError as exc:
            code = str(exc)
            if code in {"file_not_ready", "template_submission_conflict"}:
                return JSONResponse({"error": code}, status_code=409)
            return JSONResponse({"error": "invalid_template_submission"}, status_code=400)
        # Operators read product.template_submissions; the log line only announces one.
        emit("template_submission_received", submission_id=receipt["request_id"], file_count=receipt["file_count"], customer=str(body.get("customer") or "")[:100])
        return JSONResponse(receipt)
