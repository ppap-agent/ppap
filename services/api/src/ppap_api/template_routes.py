"""Customer Drive templates: frozen content, fresh per-request provider authority."""

import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import Query, Request
from fastapi.responses import JSONResponse, Response
from ppap_core.feishu_drive import FeishuExportError, FeishuMetaError, PermanentFailure, UnsupportedDocType, import_drive_material
from ppap_core.feishu_usage import FeishuUsageError, tenant_access_token
from ppap_core.files import MAX_UPLOAD_BYTES, ObjectMissing, attachment_content_disposition
from ppap_core.report_sharing import ReportSharingError, report_share_profile, resolve_report_target, send_report_message, upload_report_message_file
from ppap_product_store.share_delivery import new_share_attempt, target_key
from ppap_product_store.template_permissions import TemplatePermissionChangeStore
from ppap_product_store.templates import TemplateShareStore, freeze_template, template_file_id, template_folder_id
from pydantic import TypeAdapter

from ppap_api import web_contract as wc
from ppap_api.rule_routes import template_catalog

HEADERS = {"Cache-Control": "private, no-store"}
OPEN = "https://open.feishu.cn/open-apis"


class TemplateError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status


def register_template_routes(app, store, session_of, http, settings):
    changes = TemplatePermissionChangeStore(store)
    shares = TemplateShareStore(store)

    @app.exception_handler(TemplateError)
    async def template_error(_request, error):
        return JSONResponse({"error": error.code}, status_code=error.status, headers=HEADERS)

    def source_of(request, customer_id):
        session = session_of(request)
        if not session:
            raise TemplateError("unauthenticated", 401)
        if not session.access_token or session.principal.app_id != settings.feishu_app_id:
            raise TemplateError("feishu_user_token_missing", 403)
        eligible = template_catalog(store, session.principal.tenant_id)["customers"]
        if customer_id not in {row["customer_id"] for row in eligible}:
            raise TemplateError("access_denied", 403)
        sources = [source for source in settings.feishu_template_sources if source.tenant_key == session.principal.tenant_key and source.customer_id == customer_id]
        if len(sources) != 1:
            raise TemplateError("template_source_unavailable", 503)
        return session, sources[0]

    def share_identity(request, customer_id):
        session, source = source_of(request, customer_id)
        if session.principal.provider != "feishu":
            raise TemplateError("access_denied", 403)
        try:
            report_share_profile(http, session.access_token, session.principal)
        except ReportSharingError:
            raise TemplateError("feishu_identity_unavailable", 403) from None
        return session, source

    def file_access(session, row, action):
        try:
            response = http.get(
                OPEN + f"/drive/v1/permissions/{row['token']}/members/auth", params={"type": row["type"], "action": action}, headers={"Authorization": "Bearer " + session.access_token}, timeout=20
            )
            body = response.json()
            value = body.get("data", {}).get("auth_result") if isinstance(body, dict) and isinstance(body.get("data"), dict) else None
            if response.status_code == 200 and isinstance(body, dict) and type(body.get("code")) is int and body["code"] == 0 and type(value) is bool:
                return {"state": "allowed" if value else "denied", "reason": None if value else "飞书未允许当前账号执行此操作。"}
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return {"state": "unknown", "reason": "暂时无法确认飞书文件权限。"}

    def combined_access(values):
        return next((value for value in values if value["state"] == "denied"), next((value for value in values if value["state"] != "allowed"), {"state": "allowed", "reason": None}))

    def share_scope(request, customer_id, folder_id, folder_revision, folder_template_count):
        session, source = share_identity(request, customer_id)
        actual_folder, actual_revision, items = snapshot(session, source)
        if (actual_folder, actual_revision, len(items)) != (folder_id, folder_revision, folder_template_count) or not items:
            raise TemplateError("template_folder_changed")
        rows = inventory(session, source)
        providers = {template_file_id(session.principal.tenant_id, source.customer_id, source.folder_token, row["token"]): row for row in rows}
        for item in items:
            row = providers.get(item["reference"]["template_id"])
            if row is None:
                raise TemplateError("template_folder_changed")
            view = file_access(session, row, "view")
            item["preview_access"] = view
            item["share_access"] = combined_access([view, file_access(session, row, "share")])
            item["download_access"] = combined_access([view, file_access(session, row, "export")])
        scope = {
            "customer_id": customer_id,
            "customer_name": customer_id,
            "folder_id": folder_id,
            "folder_revision": folder_revision,
            "templates": items,
            "link_access": combined_access([item["share_access"] for item in items]),
            "zip_access": combined_access([access for item in items for access in (item["share_access"], item["download_access"])]),
        }
        return session, source, scope, providers

    @app.get("/audit-template-share-scope", response_model=wc.AuditTemplateShareScope)
    def read_share_scope(request: Request, customer_id: str, folder_id: str, folder_revision: str, folder_template_count: int = Query(ge=1, le=1000)):
        _session, _source, scope, _providers = share_scope(request, customer_id, folder_id, folder_revision, folder_template_count)
        return JSONResponse(scope, headers=HEADERS)

    def frozen_template(item):
        file = item["file"]
        version = store.get_version(file["file_id"], file["id"])
        resource = store.resource(file["file_id"])
        if version is None or version.public() != file or resource is None or resource.kind != "template":
            raise TemplateError("template_version_unavailable")
        return version

    def share_authority(request, payload, *, copying=False, sending=False):
        scope = payload["scope"]
        session, source = share_identity(request, scope["customer_id"])
        if source.folder_token != payload["folder_token"] or template_folder_id(session.principal.tenant_id, source.customer_id, source.folder_token) != scope["folder_id"]:
            raise TemplateError("access_denied", 403)
        current = {row["token"]: row for row in inventory(session, source)}
        for row in payload["providers"]:
            if row["token"] not in current or current[row["token"]]["type"] != row["type"]:
                raise TemplateError("access_denied", 403)
            for action in ["view", *(["share"] if sending else []), *(["export"] if copying else [])]:
                access = file_access(session, row, action)
                if access["state"] != "allowed":
                    raise TemplateError("template_share_permission_unverified", 403)
        return session

    def frozen_zip(payload):
        output = io.BytesIO()
        by_reference = {item["reference"]["template_id"]: item for item in payload["scope"]["templates"]}
        info = payload["request"]["content"]["package"]
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for entry in info["templates"]:
                item = by_reference[entry["reference"]["template_id"]]
                if entry["reference"] != item["reference"]:
                    raise TemplateError("template_version_unavailable")
                body = store.version_bytes(frozen_template(item))
                if len(body) != entry["byte_size"]:
                    raise TemplateError("template_version_unavailable")
                archive.writestr(zipfile.ZipInfo(entry["filename"], date_time=(1980, 1, 1, 0, 0, 0)), body)
        data = output.getvalue()
        if len(data) != info["byte_size"]:
            raise TemplateError("template_version_unavailable")
        return data

    def share_token(session):
        return tenant_access_token(http, settings.feishu_app_id, settings.feishu_app_secret, store.app_ticket(settings.feishu_app_id) or "", session.principal.tenant_key)

    def dispatch_share(guard, row, request, session, keys):
        for key in keys:
            attempt = row["result"]["attempts"][key][-1]
            attempt.update(phase="preparing")
            shares.save(guard, row)
            try:
                copying = row["payload"]["request"]["content"]["kind"] == "zip"
                token = share_token(session)
                if copying:
                    share_authority(request, row["payload"], copying=True, sending=True)
                    if not row["result"].get("file_key"):
                        info = row["payload"]["request"]["content"]["package"]
                        row["result"]["file_key"] = upload_report_message_file(http, token, {**info, "content_type": "application/zip"}, frozen_zip(row["payload"]))
                        shares.save(guard, row)
                    message_type, message = "file", {"file_key": row["result"]["file_key"]}
                else:
                    target = settings.canonical_web_url() + "/audit?" + urlencode({"templateShare": row["payload"]["request"]["request_id"]})
                    address = settings.canonical_web_url() + "/enter-current?" + urlencode({"return_to": target})
                    names = "、".join(item["file"]["original_filename"] for item in row["payload"]["scope"]["templates"])
                    message_type, message = (
                        "post",
                        {"zh_cn": {"title": "客户 PPAP 模板", "content": [[{"tag": "text", "text": names[:2000]}], [{"tag": "a", "text": "查看固定模板版本", "href": address}]]}},
                    )
                share_authority(request, row["payload"], copying=copying, sending=True)
                reference = {field: attempt["target"][field] for field in ("id", "kind")}
                attempt["target"] = resolve_report_target(http, token, session.principal.tenant_key, reference)
                if attempt["target"]["eligibility"] != "eligible":
                    raise TemplateError("template_share_target_unverified")
            except (TemplateError, KeyError, ValueError, ObjectMissing, ReportSharingError, FeishuUsageError):
                attempt.update(phase="failed", reason="尚未发送消息。请核对模板权限、机器人和接收对象；私有上传可能仍保留。")
                shares.save(guard, row)
                continue
            attempt.update(phase="dispatching", reason="正在核对发送结果，请勿重新发送。")
            shares.save(guard, row)
            attempt.update(send_report_message(http, token, attempt["target"], attempt["provider_uuid"], message_type, message))
            shares.save(guard, row)

    @app.post("/audit-template-shares", response_model=wc.AuditTemplateShare, openapi_extra=wc.openapi_body(wc.AuditTemplateShareRequest))
    def send_templates(body: dict, request: Request):
        try:
            command = TypeAdapter(wc.AuditTemplateShareRequest).validate_python(body, strict=True)
            identity = str(uuid.UUID(command["request_id"]))
            if identity != command["request_id"] or not 1 <= len(command["scope"]["templates"]) <= 1000 or not 1 <= len(command["targets"]) <= 20:
                raise ValueError()
            if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", target["id"]) for target in command["targets"]) or len({target_key(target) for target in command["targets"]}) != len(command["targets"]):
                raise ValueError()
            session, source = share_identity(request, command["scope"]["customer_id"])
            if command["organization_id"] != session.principal.tenant_key:
                raise KeyError()
            with shares.operation(session.principal, identity) as (row, guard):
                if row:
                    if row["payload"]["request"] != command:
                        raise ValueError()
                    if guard is not None:
                        shares.recover(guard, row)
                    return JSONResponse(shares.public(row), headers=HEADERS)
                if guard is None:
                    raise TemplateError("share_request_pending")
                selected = command["scope"]
                _session, _source, scope, providers = share_scope(request, selected["customer_id"], selected["folder_id"], selected["folder_revision"], len(selected["templates"]))
                if selected["templates"] != [item["reference"] for item in scope["templates"]]:
                    raise ValueError()
                copying = command["content"]["kind"] == "zip"
                if scope["zip_access" if copying else "link_access"]["state"] != "allowed":
                    raise TemplateError("template_share_permission_unverified", 403)
                if copying:
                    info, data = package(request, selected["customer_id"], selected["folder_id"], selected["folder_revision"], len(selected["templates"]))
                    if command["content"]["package"] != info or len(data) > 30 * 1024 * 1024:
                        raise ValueError()
                elif command["content"]["package"] is not None:
                    raise ValueError()
                targets = [resolve_report_target(http, share_token(session), session.principal.tenant_key, target) for target in command["targets"]]
                if any(target["eligibility"] != "eligible" for target in targets):
                    raise TemplateError("template_share_target_unverified")
                bindings = [
                    {"template_id": item["reference"]["template_id"], "token": providers[item["reference"]["template_id"]]["token"], "type": providers[item["reference"]["template_id"]]["type"]}
                    for item in scope["templates"]
                ]
                with guard.transaction():
                    row = shares.record(guard, session.principal, command, targets, {"scope": scope, "folder_token": source.folder_token, "providers": bindings})
                dispatch_share(guard, row, request, session, list(row["result"]["attempts"]))
                return JSONResponse(shares.public(row), headers=HEADERS)
        except KeyError:
            raise TemplateError("access_denied", 403) from None
        except (ValueError, FeishuUsageError):
            raise TemplateError("template_share_conflict") from None

    def private_share_session(request):
        session = session_of(request)
        if not session:
            raise TemplateError("unauthenticated", 401)
        if session.principal.provider != "feishu" or session.principal.app_id != settings.feishu_app_id:
            raise TemplateError("access_denied", 403)
        return session

    def recover_share(guard, row, session):
        shares.recover(guard, row)
        failed = [attempts[-1] for attempts in row["result"]["attempts"].values() if attempts[-1]["phase"] == "failed" and attempts[-1]["target"]["eligibility"] != "eligible"]
        if not failed:
            return
        try:
            report_share_profile(http, session.access_token, session.principal)
            token = share_token(session)
            for attempt in failed:
                reference = {field: attempt["target"][field] for field in ("id", "kind")}
                attempt["target"] = resolve_report_target(http, token, session.principal.tenant_key, reference)
            shares.save(guard, row)
        except (ReportSharingError, FeishuUsageError):
            pass

    @app.get("/audit-template-share-restore", response_model=wc.AuditTemplateShareRecovery | None)
    def restore_template_share(request: Request, customer_id: str = Query(min_length=1, max_length=200), request_id: uuid.UUID | None = None):
        session = private_share_session(request)
        selected = str(request_id) if request_id else shares.latest_for_customer(session.principal, customer_id)
        if selected is None:
            return JSONResponse(None, headers=HEADERS)
        try:
            with shares.operation(session.principal, selected) as (row, guard):
                if row is None:
                    raise TemplateError("share_request_pending")
                if row["payload"]["scope"]["customer_id"] != customer_id:
                    raise KeyError()
                if guard is not None:
                    recover_share(guard, row, session)
                return JSONResponse({"scope": row["payload"]["scope"], "share": shares.public(row)}, headers=HEADERS)
        except KeyError:
            raise TemplateError("access_denied", 403) from None

    @app.get("/audit-template-shares/{request_id}", response_model=wc.AuditTemplateShare)
    def read_template_share(request: Request, request_id: uuid.UUID, attempts: str | None = Query(None, max_length=10000)):
        session = private_share_session(request)
        try:
            selected = TypeAdapter(list[wc.AuditShareAttemptReference]).validate_python(json.loads(attempts), strict=True) if attempts else None
            with shares.operation(session.principal, str(request_id)) as (row, guard):
                if row is None:
                    raise TemplateError("share_request_missing", 404)
                if guard is not None:
                    recover_share(guard, row, session)
                return JSONResponse(shares.public(row, selected), headers=HEADERS)
        except KeyError:
            raise TemplateError("access_denied", 403) from None
        except ValueError:
            raise TemplateError("invalid_share_attempts", 400) from None

    @app.post("/audit-template-shares/{request_id}/retries", response_model=wc.AuditTemplateShare, openapi_extra=wc.openapi_body(wc.AuditReportShareRetry))
    def retry_template_share(request: Request, request_id: uuid.UUID, body: dict):
        try:
            command = TypeAdapter(wc.AuditReportShareRetry).validate_python(body, strict=True)
            attempt_id = str(uuid.UUID(command["attempt_id"]))
            if attempt_id != command["attempt_id"] or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", command["target"]["id"]):
                raise ValueError()
            session = session_of(request)
            if not session:
                raise TemplateError("unauthenticated", 401)
            if session.principal.provider != "feishu" or session.principal.app_id != settings.feishu_app_id:
                raise KeyError()
            with shares.operation(session.principal, str(request_id)) as (row, guard):
                if row is None:
                    raise TemplateError("share_request_missing", 404)
                key = target_key(command["target"])
                history = row["result"]["attempts"].get(key)
                if not history:
                    raise KeyError()
                if any(item["attempt_id"] == attempt_id for item in history):
                    selected = [
                        {"target": {field: attempts[-1]["target"][field] for field in ("id", "kind")}, "attempt_id": attempt_id if selected_key == key else attempts[-1]["attempt_id"]}
                        for selected_key, attempts in row["result"]["attempts"].items()
                    ]
                    return JSONResponse(shares.public(row, selected), headers=HEADERS)
                if guard is None:
                    raise TemplateError("share_request_pending")
                shares.recover(guard, row)
                if history[-1]["phase"] != "failed" or attempt_id == str(request_id) or any(item["attempt_id"] == attempt_id for attempts in row["result"]["attempts"].values() for item in attempts):
                    raise ValueError()
                history.append(new_share_attempt(dict(history[-1]["target"]), str(request_id), attempt_id))
                shares.save(guard, row)
                dispatch_share(guard, row, request, session, [key])
                return JSONResponse(shares.public(row), headers=HEADERS)
        except KeyError:
            raise TemplateError("access_denied", 403) from None
        except ValueError:
            raise TemplateError("share_attempt_unconfirmed") from None

    def shared_templates(request, request_id):
        session = session_of(request)
        if not session:
            raise TemplateError("unauthenticated", 401)
        if session.principal.provider != "feishu" or session.principal.app_id != settings.feishu_app_id:
            raise TemplateError("access_denied", 403)
        try:
            payload = shares.shared(session.principal, str(request_id))
        except KeyError:
            raise TemplateError("access_denied", 403) from None
        return payload, share_authority(request, payload)

    @app.get("/audit-template-shares/{request_id}/templates", response_model=wc.AuditTemplateShareScope)
    def read_shared_templates(request: Request, request_id: uuid.UUID):
        payload, session = shared_templates(request, request_id)
        items = []
        for item, row in zip(payload["scope"]["templates"], payload["providers"], strict=True):
            items.append(
                {
                    **item,
                    "preview_access": {"state": "allowed", "reason": None},
                    "download_access": file_access(session, row, "export"),
                    "share_access": {"state": "denied", "reason": "请从客户模板库发起新的分享。"},
                }
            )
        return JSONResponse(
            {
                **payload["scope"],
                "templates": items,
                "link_access": {"state": "denied", "reason": "请从客户模板库发起新的分享。"},
                "zip_access": combined_access([item["download_access"] for item in items]),
            },
            headers=HEADERS,
        )

    @app.get("/audit-template-shares/{request_id}/files/{template_id}/versions/{version_id}", response_class=Response)
    def read_shared_template(request: Request, request_id: uuid.UUID, template_id: str, version_id: str, purpose: Literal["preview", "download"] = "download"):
        payload, session = shared_templates(request, request_id)
        item = next((item for item in payload["scope"]["templates"] if item["reference"]["template_id"] == template_id and item["file"]["id"] == version_id), None)
        if item is None:
            raise TemplateError("access_denied", 403)
        provider = next(row for row in payload["providers"] if row["template_id"] == template_id)
        if purpose == "download" and file_access(session, provider, "export")["state"] != "allowed":
            raise TemplateError("template_share_permission_unverified", 403)
        version = frozen_template(item)
        return Response(
            store.version_bytes(version),
            media_type=version.content_type,
            headers={**HEADERS, "X-Content-SHA256": version.sha256, "Content-Disposition": attachment_content_disposition(version.original_filename), "X-Content-Type-Options": "nosniff"},
        )

    def inventory(session, source):
        rows, seen_tokens, seen_pages = [], set(), set()
        page = ""
        for _ in range(20):
            params = {"folder_token": source.folder_token, "page_size": 50}
            if page:
                params["page_token"] = page
            try:
                response = http.get(OPEN + "/drive/v1/files", params=params, headers={"Authorization": "Bearer " + session.access_token}, timeout=20)
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("invalid_inventory_response")
                if response.status_code != 200 or body.get("code") != 0:
                    raise TemplateError("feishu_templates_unavailable", 403 if response.status_code in {401, 403} or body.get("code") == 99991672 else 503)
                data = body["data"]
                if not isinstance(data["files"], list) or len(data["files"]) > 50 or type(data.get("has_more")) is not bool:
                    raise ValueError("incomplete_inventory")
                for row in data["files"]:
                    if not isinstance(row, dict) or not all(isinstance(row.get(key), str) and row[key] for key in ("token", "name", "type", "modified_time")):
                        raise ValueError("invalid_template_metadata")
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", row["token"]) or row["token"] in seen_tokens:
                        raise ValueError("invalid_template_identity")
                    if row["type"] not in {"file", "doc", "docx", "sheet", "bitable"}:
                        raise TemplateError("template_source_contains_unsupported_items")
                    row = dict(row, updated_at=datetime.fromtimestamp(int(row["modified_time"]), timezone.utc).isoformat())
                    seen_tokens.add(row["token"])
                    rows.append(row)
                if not data["has_more"]:
                    return rows
                page = data.get("next_page_token")
                if not isinstance(page, str) or not page or page in seen_pages:
                    raise ValueError("incomplete_inventory")
                seen_pages.add(page)
            except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
                raise TemplateError("feishu_templates_unavailable", 503) from None
        raise TemplateError("template_folder_too_large")

    def material(session, row):
        try:
            imported = import_drive_material(http, session.access_token, row["token"], row["type"])
            if imported.file_token != row["token"] or imported.doc_type != row["type"]:
                raise ValueError("template_provider_identity_changed")
            return imported
        except (FeishuExportError, FeishuMetaError, PermanentFailure, UnsupportedDocType, ValueError, httpx.HTTPError):
            raise TemplateError("feishu_template_unavailable", 403) from None

    def snapshot(session, source):
        rows = inventory(session, source)
        items, size = [], 0
        allowed = {"state": "allowed", "reason": None}
        unknown = {"state": "unknown", "reason": "暂时无法确认分享权限。"}
        for row in rows:
            imported = material(session, row)
            size += len(imported.data)
            if size > MAX_UPLOAD_BYTES:
                raise TemplateError("template_folder_too_large")
            fid = template_file_id(session.principal.tenant_id, source.customer_id, source.folder_token, row["token"])
            try:
                version = freeze_template(store, session.principal, fid, row["modified_time"], imported.filename, imported.content_type, imported.data)
            except (ValueError, ObjectMissing, OSError):
                raise TemplateError("template_version_unavailable") from None
            extension = Path(version.original_filename).suffix.lower()
            kind = "Excel" if extension in {".xls", ".xlsx", ".xlsm"} else "Word" if extension in {".doc", ".docx"} else "PDF" if extension == ".pdf" else extension.lstrip(".").upper() or "文件"
            items.append(
                {
                    "reference": {"customer_id": source.customer_id, "template_id": fid, "file_version_id": version.id},
                    "file": version.public(),
                    "document_type": kind,
                    "updated_at": row["updated_at"],
                    "preview_access": allowed,
                    "download_access": allowed,
                    "share_access": unknown,
                }
            )
        current = inventory(session, source)

        def signature(values):
            return sorted((row["token"], row["name"], row["type"], row["modified_time"]) for row in values)

        if signature(rows) != signature(current):
            raise TemplateError("template_folder_changed")
        folder_id = template_folder_id(session.principal.tenant_id, source.customer_id, source.folder_token)
        frozen = sorted((item["reference"]["file_version_id"], item["updated_at"]) for item in items)
        revision = hashlib.sha256(json.dumps([folder_id, frozen]).encode()).hexdigest()
        return folder_id, revision, sorted(items, key=lambda item: (item["updated_at"], item["reference"]["template_id"]), reverse=True)

    def creator_name(session, source):
        try:
            metadata = []
            for path in ("/drive/explorer/v2/root_folder/meta", f"/drive/explorer/v2/folder/{source.folder_token}/meta", "/authen/v1/user_info"):
                response = http.get(OPEN + path, headers={"Authorization": "Bearer " + session.access_token}, timeout=20)
                body = response.json()
                if response.status_code != 200 or not isinstance(body, dict) or body.get("code") != 0 or not isinstance(body.get("data"), dict):
                    return None
                metadata.append(body["data"])
            root, folder, profile = metadata
            # Drive's internal UID is not an open_id. Bind it through the same
            # user's root metadata and current, tenant-matched profile.
            user_uid, creator_uid = root.get("user_id"), folder.get("createUid")
            if not all(type(uid) in {str, int} and re.fullmatch(r"[1-9][0-9]*", str(uid)) for uid in (user_uid, creator_uid)):
                return None
            if folder.get("token") != source.folder_token or str(user_uid) != str(creator_uid):
                return None
            if profile.get("tenant_key") != session.principal.tenant_key or profile.get("open_id") != session.principal.subject:
                return None
            name = profile.get("name")
            if not isinstance(name, str):
                return None
            return name.strip() or None
        except (httpx.HTTPError, ValueError, TypeError):
            return None

    def permission_snapshot(request, customer_id, folder_id, query=""):
        session, source = source_of(request, customer_id)
        if folder_id != template_folder_id(session.principal.tenant_id, source.customer_id, source.folder_token):
            raise TemplateError("access_denied", 403)
        try:
            report_share_profile(http, session.access_token, session.principal)
        except ReportSharingError as error:
            raise TemplateError("access_denied" if str(error) == "share_identity_mismatch" else "feishu_identity_unavailable", 403 if str(error) == "share_identity_mismatch" else 503) from None
        try:
            response = http.get(
                OPEN + f"/drive/v1/permissions/{source.folder_token}/members",
                params={"type": "folder", "fields": "*"},
                headers={"Authorization": "Bearer " + session.access_token},
                timeout=20,
            )
            body = response.json()
            if response.status_code != 200 or not isinstance(body, dict) or type(body.get("code")) is not int or body["code"] != 0:
                raise TemplateError("feishu_permissions_unavailable", 403 if response.status_code in {401, 403} or isinstance(body, dict) and body.get("code") == 99991672 else 503)
            items = body["data"]["items"]
            if not isinstance(items, list) or len(items) > 1000:
                raise ValueError("invalid_permission_members")
            members: list[wc.AuditTemplatePermissionMember] = []
            seen = set()
            roles = {"view": "可阅读", "edit": "可编辑", "full_access": "可管理"}
            kinds: dict[str, Literal["user", "group"]] = {"openid": "user", "openchat": "group"}
            for item in items:
                if (
                    not isinstance(item, dict)
                    or item.get("member_type") not in kinds
                    or item.get("perm") not in roles
                    or not isinstance(item.get("member_id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", item["member_id"])
                ):
                    raise ValueError("unsupported_permission_member")
                key = (kinds[item["member_type"]], item["member_id"])
                if key in seen:
                    raise ValueError("duplicate_permission_member")
                seen.add(key)
                name = item.get("name")
                members.append(
                    {
                        "target": {"kind": key[0], "id": key[1]},
                        "display_name": name.strip() if isinstance(name, str) and name.strip() else "名称未取得",
                        "role": roles[item["perm"]],
                        # The member-list API covers direct grants, not the
                        # inheritance chain. full_access may also be an owner;
                        # never offer destructive changes for that role.
                        "source": "direct" if item["perm"] in {"view", "edit"} else "unknown",
                    }
                )
            members.sort(key=lambda row: (row["target"]["kind"], row["target"]["id"]))
            revision = hashlib.sha256(json.dumps([folder_id, sorted(items, key=lambda item: (item["member_type"], item["member_id"]))], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise TemplateError("feishu_permissions_unavailable", 503) from None
        search = query.strip()
        managed = any(item["member_type"] == "openid" and item["member_id"] == session.principal.subject and item["perm"] == "full_access" for item in items)
        visible = [member for member in members if search.casefold() in member["display_name"].casefold()]
        actions = []
        for member in visible if managed else []:
            if member["source"] != "direct" or member["role"] not in {"可阅读", "可编辑"}:
                continue
            after = "可编辑" if member["role"] == "可阅读" else "可阅读"
            actions.append(
                {
                    "id": member["target"]["kind"] + ":" + member["target"]["id"] + ":" + after,
                    "target": member["target"],
                    "target_name": member["display_name"],
                    "before": member["role"],
                    "after": after,
                    "label": "设为" + after,
                }
            )
        return {
            "folder": {"customer_id": customer_id, "folder_id": folder_id},
            "permission_revision": revision,
            "query": search,
            "creator_name": creator_name(session, source),
            "manage_access": {"state": "allowed", "reason": None} if managed else {"state": "unknown", "reason": "暂时无法确认当前用户的文件夹管理权限。"},
            "members": visible,
            "actions": actions,
        }

    @app.get("/audit-template-permissions", response_model=wc.AuditTemplatePermissions)
    def read_permissions(request: Request, customer_id: str, folder_id: str, query: str = Query(default="", max_length=200)):
        snapshot = permission_snapshot(request, customer_id, folder_id, query)
        session, _source = source_of(request, customer_id)
        change, unresolved = changes.unresolved(session.principal, folder_id)
        snapshot["change"] = change
        if unresolved:
            snapshot["manage_access"] = {"state": "unknown", "reason": "此文件夹已有待确认的权限修改，请先读取原修改回执。"}
            snapshot["actions"] = []
        return JSONResponse(snapshot, headers=HEADERS)

    @app.post("/audit-template-permission-changes", response_model=wc.AuditTemplatePermissionReceipt, openapi_extra=wc.openapi_body(wc.AuditTemplatePermissionRequest))
    def apply_permission(request: Request, body: dict):
        try:
            command = TypeAdapter(wc.AuditTemplatePermissionRequest).validate_python(body, strict=True)
            request_id = str(uuid.UUID(command["request_id"]))
        except ValueError:
            raise TemplateError("invalid_permission_request", 422) from None
        if request_id != command["request_id"]:
            raise TemplateError("invalid_permission_request", 422)
        session, source = source_of(request, command["folder"]["customer_id"])
        folder_id = template_folder_id(session.principal.tenant_id, source.customer_id, source.folder_token)
        if folder_id != command["folder"]["folder_id"]:
            raise TemplateError("access_denied", 403)
        try:
            with changes.operation(session.principal, request_id) as (row, guard):
                if row:
                    if row["payload"]["request"] != command:
                        raise TemplateError("permission_request_conflict")
                    if guard is not None:
                        changes.recover(guard, row)
                    return JSONResponse(changes.public(row), headers=HEADERS)
                if guard is None:
                    raise TemplateError("permission_request_pending", 503)
                row = changes.accept(guard, session.principal, command)

                def finish(state, reason=None, revision=None):
                    row["result"].update(phase=state, state=state, reason=reason, result_revision=revision)
                    changes.save(guard, row)
                    return JSONResponse(changes.public(row), headers=HEADERS)

                with changes.folder(guard, folder_id) as locked:
                    if not locked or changes.unresolved(session.principal, folder_id, request_id, guard=guard)[1]:
                        return finish("conflict", "此文件夹已有待确认的修改，请先读取原修改回执。")
                    try:
                        current = permission_snapshot(request, source.customer_id, folder_id)
                    except TemplateError:
                        return finish("rejected", "无法核实当前身份及文件夹权限，本次未提交修改。")
                    if current["manage_access"]["state"] != "allowed":
                        return finish("rejected", "当前文件夹管理权限未确认，本次未提交修改。")
                    if current["permission_revision"] != command["permission_revision"]:
                        return finish("conflict", "权限已变化，请重新读取后再修改。")
                    if command["action"] not in current["actions"]:
                        return finish("rejected", "本次修改不在当前可用操作中，请重新读取。")
                    target = command["action"]["target"]
                    member = {"member_type": "openid" if target["kind"] == "user" else "openchat", "perm": "edit" if command["action"]["after"] == "可编辑" else "view"}
                    # Commit the dispatch fence before I/O. A lost connection
                    # cannot acquire a new guard or repeat the provider call.
                    row["result"].update(phase="dispatching", state="unknown", reason="修改已提交，正在等待原回执。")
                    changes.save(guard, row)
                    try:
                        response = http.put(
                            OPEN + f"/drive/v1/permissions/{source.folder_token}/members/{target['id']}",
                            params={"type": "folder", "need_notification": "false"},
                            headers={"Authorization": "Bearer " + session.access_token},
                            json=member,
                            timeout=20,
                        )
                        body = response.json()
                        if isinstance(body, dict) and type(body.get("code")) is int:
                            if response.status_code in {400, 403, 404} and body["code"] in {1063001, 1063002, 1063004, 1063005, 99991672}:
                                return finish("rejected", "飞书未允许本次权限修改。")
                            result = body.get("data", {}).get("member") if isinstance(body.get("data"), dict) else None
                            if (
                                response.status_code == 200
                                and body["code"] == 0
                                and isinstance(result, dict)
                                and all(result.get(key) == value for key, value in {"member_id": target["id"], **member}.items())
                            ):
                                # This revision binds the acknowledged change,
                                # not an unrelated later ACL observation.
                                revision = hashlib.sha256(json.dumps([command, {"member_id": target["id"], **member}], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                                return finish("applied", revision=revision)
                    except (httpx.HTTPError, ValueError, TypeError):
                        pass
                    return finish("unknown", "未取得可靠修改回执。请读取原请求，不要重复修改。")
        except KeyError:
            raise TemplateError("access_denied", 403) from None

    @app.get("/audit-template-permission-changes/{request_id}", response_model=wc.AuditTemplatePermissionReceipt)
    def read_permission_change(request: Request, request_id: uuid.UUID):
        session = session_of(request)
        if not session:
            raise TemplateError("unauthenticated", 401)
        if session.principal.app_id != settings.feishu_app_id:
            raise TemplateError("access_denied", 403)
        try:
            with changes.operation(session.principal, str(request_id)) as (row, guard):
                if row is None:
                    raise TemplateError("permission_request_missing", 404)
                if guard is not None:
                    changes.recover(guard, row)
                return JSONResponse(changes.public(row), headers=HEADERS)
        except KeyError:
            raise TemplateError("access_denied", 403) from None

    @app.get("/audit-templates", response_model=wc.AuditTemplatePage)
    def list_templates(request: Request, customer_id: str, search: str = Query(default="", max_length=200), page: int = Query(default=1, ge=1), page_size: int = Query(default=10, ge=10, le=10)):
        session, source = source_of(request, customer_id)
        folder_id, revision, items = snapshot(session, source)
        manage_access = {"state": "unknown", "reason": "暂时无法确认文件夹管理权限。"}
        try:
            manage_access = permission_snapshot(request, customer_id, folder_id)["manage_access"]
        except TemplateError:
            pass
        filtered = [item for item in items if search.strip().casefold() in (item["file"]["original_filename"] + " " + item["document_type"]).casefold()]
        return JSONResponse(
            {
                "query": {"customer_id": customer_id, "search": search.strip(), "page": page, "page_size": page_size},
                "folder_id": folder_id,
                "folder_revision": revision,
                "folder_template_count": len(items),
                "creator_name": creator_name(session, source),
                "manage_access": manage_access,
                "zip_access": {"state": "allowed", "reason": None},
                "items": filtered[(page - 1) * 10 : page * 10],
                "total": len(filtered),
            },
            headers=HEADERS,
        )

    @app.get(
        "/audit-templates/{template_id}/versions/{version_id}",
        response_class=Response,
        responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    def read_template(request: Request, template_id: str, version_id: str, customer_id: str, purpose: Literal["preview", "download"] = "download"):
        session, source = source_of(request, customer_id)
        rows = inventory(session, source)
        selected = next((row for row in rows if template_file_id(session.principal.tenant_id, source.customer_id, source.folder_token, row["token"]) == template_id), None)
        if selected is None:
            raise TemplateError("access_denied", 403)
        # A fresh provider read is authority for this request only, never a grant.
        material(session, selected)
        resource = store.resource(template_id)
        if resource is None or resource.kind != "template" or resource.tenant_id != session.principal.tenant_id:
            raise TemplateError("access_denied", 403)
        version = store.get_version(template_id, version_id)
        if version is None or version.tenant_id != session.principal.tenant_id or not version.sha256:
            raise TemplateError("access_denied", 403)
        try:
            data = store.version_bytes(version)
        except (ObjectMissing, ValueError):
            raise TemplateError("template_version_unavailable") from None
        return Response(
            data,
            media_type=version.content_type,
            headers={**HEADERS, "X-Content-SHA256": version.sha256, "Content-Disposition": attachment_content_disposition(version.original_filename), "X-Content-Type-Options": "nosniff"},
        )

    def package(request, customer_id, folder_id, folder_revision, folder_template_count):
        session, source = source_of(request, customer_id)
        actual_folder, actual_revision, items = snapshot(session, source)
        if (actual_folder, actual_revision, len(items)) != (folder_id, folder_revision, folder_template_count) or not items:
            raise TemplateError("template_folder_changed")
        output, entries, names = io.BytesIO(), [], set()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for item in sorted(items, key=lambda item: item["reference"]["template_id"]):
                file, reference = item["file"], item["reference"]
                version = store.get_version(file["file_id"], file["id"])
                if version is None:
                    raise TemplateError("template_version_unavailable")
                name = file["original_filename"]
                if name in names:
                    suffix = Path(name).suffix
                    name = f"{Path(name).stem} ({reference['template_id']}){suffix}"
                if name in names:
                    raise TemplateError("template_zip_names_conflict")
                names.add(name)
                try:
                    body = store.version_bytes(version)
                except (ObjectMissing, ValueError):
                    raise TemplateError("template_version_unavailable") from None
                archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), body)
                entries.append({"reference": reference, "filename": name, "byte_size": len(body)})
        data = output.getvalue()
        info = {
            "customer_id": customer_id,
            "folder_id": folder_id,
            "folder_revision": folder_revision,
            "templates": entries,
            "manifest_filename": None,
            "filename": "客户模板.zip",
            "byte_size": len(data),
            "complete": True,
            "access": {"state": "allowed", "reason": None},
        }
        return info, data

    @app.get("/audit-template-packages", response_model=wc.AuditTemplateZip)
    def template_package(request: Request, customer_id: str, folder_id: str, folder_revision: str, folder_template_count: int = Query(ge=1, le=1000)):
        info, _data = package(request, customer_id, folder_id, folder_revision, folder_template_count)
        return JSONResponse(info, headers=HEADERS)

    @app.get(
        "/audit-template-packages/download",
        response_class=Response,
        responses={200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    def template_package_download(request: Request, customer_id: str, folder_id: str, folder_revision: str, folder_template_count: int = Query(ge=1, le=1000)):
        info, data = package(request, customer_id, folder_id, folder_revision, folder_template_count)
        return Response(
            data,
            media_type="application/zip",
            headers={**HEADERS, "X-Content-SHA256": hashlib.sha256(data).hexdigest(), "Content-Disposition": attachment_content_disposition(info["filename"]), "X-Content-Type-Options": "nosniff"},
        )
