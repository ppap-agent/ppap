from fastapi import Request
from fastapi.responses import JSONResponse, Response
from ppap_core.files import ObjectMissing, attachment_content_disposition, safe_content_type
from ppap_core.office_preview import preview_config, read_ticket

from ppap_api import web_contract as wc


def register_office_routes(app, store, principal_of, settings):
    @app.get("/files/{file_id}/versions/{version_id}/office", response_model=wc.OfficePreviewResponse)
    def office_config(file_id: str, version_id: str, request: Request, round_id: str | None = None):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            version = store.read_file_version(principal, file_id, version_id, round_id=round_id)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        if version.status != "ready":
            return JSONResponse({"error": "file_not_ready"}, status_code=409)
        try:
            return JSONResponse(preview_config(settings, version), headers={"Cache-Control": "no-store"})
        except ValueError:
            return JSONResponse({"error": "invalid_onlyoffice_configuration"}, status_code=503)

    @app.get("/office/content")
    def office_content(ticket: str):
        try:
            claims = read_ticket(ticket, settings.onlyoffice_secret)
        except ValueError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        version = store.get_version(claims["file_id"], claims["version_id"])
        if version is None or version.tenant_id != claims["tenant_id"] or version.status != "ready":
            return JSONResponse({"error": "access_denied"}, status_code=403)
        try:
            data = store.version_bytes(version)
        except (ValueError, ObjectMissing):
            return JSONResponse({"error": "not_ready"}, status_code=409)
        return Response(
            data,
            media_type=safe_content_type(version.content_type),
            headers={
                "Content-Disposition": attachment_content_disposition(version.original_filename),
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )
