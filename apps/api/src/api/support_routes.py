import hashlib
import json
from io import BytesIO
from pathlib import Path

from fastapi import Query, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image
from product.settings import Settings

from api import web_contract as wc

READ_HEADERS = {"Cache-Control": "private, no-store"}
MAX_QR_BYTES = 2 * 1024 * 1024


def _support_snapshot(settings: Settings) -> tuple[wc.AuditSupportInfo, bytes | None, str | None]:
    phone = settings.support_phone.strip() or None
    email = settings.support_email.strip() or None
    hours = settings.support_service_hours.strip() or None
    qr, content_type = None, None
    if settings.support_qr_file.strip():
        try:
            with Path(settings.support_qr_file).open("rb") as source:
                body = source.read(MAX_QR_BYTES + 1)
            if not body or len(body) > MAX_QR_BYTES:
                raise ValueError("support image size")
            formats = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
            with Image.open(BytesIO(body), formats=list(formats)) as image:
                if image.width * image.height > 4 * 1024 * 1024 or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("support image dimensions")
                image_type = formats[image.format or ""]
                image.verify()
            qr, content_type = body, image_type
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            pass
    configured = bool(phone or email or qr)
    version = hashlib.sha256(json.dumps([phone, email, hours, content_type, hashlib.sha256(qr).hexdigest() if qr else None]).encode()).hexdigest() if configured else None
    info: wc.AuditSupportInfo = {
        "state": "configured" if configured else "unavailable",
        "config_version": version,
        "phone": phone if configured else None,
        "email": email if configured else None,
        "service_hours": hours if configured else None,
        "qr_available": qr is not None,
    }
    return info, qr, content_type


def register_support_routes(app, principal_of, settings):
    @app.get("/support", response_model=wc.AuditSupportInfo)
    def support_info(request: Request):
        if not principal_of(request):
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=READ_HEADERS)
        info, _, _ = _support_snapshot(settings)
        return JSONResponse(info, headers=READ_HEADERS)

    @app.get(
        "/support/qr",
        response_class=Response,
        responses={200: {"content": {kind: {"schema": {"type": "string", "format": "binary"}} for kind in ("image/png", "image/jpeg", "image/webp")}}},
    )
    def support_qr(request: Request, config_version: str = Query(min_length=1, max_length=64)):
        if not principal_of(request):
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=READ_HEADERS)
        info, body, content_type = _support_snapshot(settings)
        if not body:
            return JSONResponse({"error": "support_qr_unavailable"}, status_code=404, headers=READ_HEADERS)
        if config_version != info["config_version"]:
            return JSONResponse({"error": "support_configuration_changed"}, status_code=409, headers=READ_HEADERS)
        return Response(body, media_type=content_type, headers={**READ_HEADERS, "X-Content-Type-Options": "nosniff"})
