"""Organization-authenticated report shares never grant private task access."""

import json
import re
import uuid
from typing import Literal
from urllib.parse import urlencode

from fastapi import Query, Request
from fastapi.responses import JSONResponse, Response
from ppap_core.feishu_usage import FeishuUsageError, tenant_access_token
from ppap_core.files import ObjectMissing, attachment_content_disposition
from ppap_core.report_sharing import (
    ReportDirectoryHttp,
    ReportSharingError,
    list_report_targets,
    own_report_target,
    report_share_profile,
    resolve_report_target,
    send_report_message,
    upload_report_message_file,
)
from ppap_product_store.report_sharing import ReportShareStore
from ppap_product_store.share_delivery import new_share_attempt, target_key
from pydantic import TypeAdapter, ValidationError

from ppap_api import web_contract as wc

HEADERS = {"Cache-Control": "private, no-store"}


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("invalid_share_request")
    return value


def _target(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["id"]):
        raise ValueError("invalid_share_target")
    return value


def register_sharing_routes(app, store, session_of, http, settings):
    shares = ReportShareStore(store)

    def authenticated(request, provider=http):
        session = session_of(request)
        if not session:
            return None, None, JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        principal = session.principal
        if principal.provider != "feishu" or principal.app_id != settings.feishu_app_id or not session.access_token:
            raise KeyError("missing")
        profile = report_share_profile(provider, session.access_token, principal)
        return session, profile, None

    def token_for(principal, provider=http):
        return tenant_access_token(provider, settings.feishu_app_id, settings.feishu_app_secret, store.app_ticket(settings.feishu_app_id) or "", principal.tenant_key)

    def target_for(principal, profile, token, reference):
        if reference["kind"] == "user" and reference["id"] == principal.subject:
            return own_report_target(principal, profile)
        return resolve_report_target(http, token, principal.tenant_key, reference)

    def fail(error):
        status = (
            403
            if isinstance(error, KeyError) or str(error) == "share_identity_mismatch"
            else 503
            if isinstance(error, (FeishuUsageError, ReportSharingError, ObjectMissing))
            else 400
            if isinstance(error, ValidationError)
            else 409
        )
        message = "invalid_share_request" if isinstance(error, ValidationError) else str(error)
        if status == 403:
            message = "access_denied"
        elif status == 503 and not (isinstance(error, ReportSharingError) and message in {"share_directory_permission_missing", "share_directory_timeout"}):
            message = "share_unavailable"
        return JSONResponse({"error": message}, status_code=status, headers=HEADERS)

    @app.get("/audit-share-targets", response_model=wc.AuditShareTargetsPage)
    def targets(request: Request, kind: Literal["user", "group"] = "user", query: str = Query("", max_length=100), page_token: str | None = Query(None, max_length=2048)):
        directory_http = ReportDirectoryHttp(http)
        try:
            session, profile, denied = authenticated(request, directory_http)
            if denied:
                return denied
            token = token_for(session.principal, directory_http) if query.strip() or kind == "group" else ""
            directory_http.check()
            return JSONResponse(list_report_targets(directory_http, session.access_token, token, session.principal, profile, kind, query.strip(), page_token), headers=HEADERS)
        except (KeyError, ValueError, ReportSharingError, FeishuUsageError) as error:
            return fail(ReportSharingError("share_directory_timeout") if directory_http.timed_out else error)

    def dispatch(guard, row, session, token, keys):
        for key in keys:
            attempt = row["result"]["attempts"][key][-1]
            attempt.update(phase="preparing")
            shares.save(guard, row)
            try:
                for source in row["payload"]["sources"]:
                    store._recognition_public(guard, session.principal, source["reference"]["task_id"])
                content = row["payload"]["request"]["content"]
                if content["kind"] == "link":
                    target = settings.canonical_web_url() + "/audit?" + urlencode({"share": row["payload"]["request"]["request_id"]})
                    address = settings.canonical_web_url() + "/enter-current?" + urlencode({"return_to": target})
                    paragraphs = [[{"tag": "text", "text": source["name"] + "：" + ("通过" if source["conclusion"] == "PASS" else "不符合")}] for source in row["payload"]["sources"]]
                    paragraphs.append([{"tag": "a", "text": "查看审核报告", "href": address}])
                    message_type, message = "post", {"zh_cn": {"title": "PPAP 审核报告", "content": paragraphs}}
                else:
                    if not row["result"].get("file_key"):
                        source = row["payload"]["archive"] if content["kind"] == "zip" else row["payload"]["sources"][0]
                        row["result"]["file_key"] = upload_report_message_file(http, token, source["artifact"], shares.read_bytes(source))
                        shares.save(guard, row)
                    message_type, message = "file", {"file_key": row["result"]["file_key"]}
                profile = report_share_profile(http, session.access_token, session.principal)
                reference = {field: attempt["target"][field] for field in ("id", "kind")}
                attempt["target"] = target_for(session.principal, profile, token, reference)
                if attempt["target"]["eligibility"] != "eligible":
                    raise ReportSharingError("share_target_unverified")
            except (KeyError, ValueError, ObjectMissing, ReportSharingError, FeishuUsageError) as error:
                reason = "尚未向接收对象发送消息。请检查授权及接收对象后重试；私有上传可能仍保留。"
                if str(error) == "share_upload_permission_missing":
                    reason = "尚未发送消息：当前组织未授权上传飞书消息附件。请联系管理员完成授权，授权后仅重试失败目标；也可另建链接分享。"
                attempt.update(phase="failed", reason=reason)
                shares.save(guard, row)
                continue
            attempt.update(phase="dispatching", reason="正在核对发送结果，请勿重新发送。")
            shares.save(guard, row)
            attempt.update(send_report_message(http, token, attempt["target"], attempt["provider_uuid"], message_type, message))
            shares.save(guard, row)

    @app.post("/audit-report-shares", response_model=wc.AuditReportShare, openapi_extra=wc.openapi_body(wc.AuditReportShareRequest))
    def send(body: dict, request: Request):
        try:
            command = TypeAdapter(wc.AuditReportShareRequest).validate_python(body, strict=True)
            _uuid(command["request_id"])
            if not 1 <= len(command["reports"]) <= 100 or not 1 <= len(command["targets"]) <= 20 or len({target_key(_target(target)) for target in command["targets"]}) != len(command["targets"]):
                raise ValueError("invalid_share_request")
            if len({reference["report_id"] for reference in command["reports"]}) != len(command["reports"]):
                raise ValueError("invalid_share_request")
            for reference in command["reports"]:
                for field in ("task_id", "round_id", "file_version_id", "report_id"):
                    _uuid(reference[field])
                if reference["result_attempt"] < 1:
                    raise ValueError("invalid_share_request")
            session, _profile, denied = authenticated(request)
            if denied:
                return denied
            if command["organization_id"] != session.principal.tenant_key:
                raise KeyError("missing")
            with shares.operation(session.principal, command["request_id"]) as (row, guard):
                if row:
                    if row["payload"]["request"] != command:
                        raise ValueError("share_request_conflict")
                    if guard:
                        shares.recover(guard, row)
                    return JSONResponse(shares.public(row), headers=HEADERS)
                if guard is None:
                    raise ValueError("share_request_pending")
                token = token_for(session.principal)
                selected = [target_for(session.principal, _profile, token, target) for target in command["targets"]]
                if any(target["eligibility"] != "eligible" for target in selected):
                    raise ValueError("share_target_unverified")
                row = shares.accept(guard, session.principal, command, selected)
                dispatch(guard, row, session, token, list(row["result"]["attempts"]))
                return JSONResponse(shares.public(row), headers=HEADERS)
        except (KeyError, ValueError, ReportSharingError, FeishuUsageError, ObjectMissing) as error:
            return fail(error)

    @app.get("/audit-report-shares/{request_id}", response_model=wc.AuditReportShare)
    def read(request_id: str, request: Request, attempts: str | None = Query(None, max_length=10000)):
        try:
            _uuid(request_id)
            selected = TypeAdapter(list[wc.AuditShareAttemptReference]).validate_python(json.loads(attempts), strict=True) if attempts else None
            session, _profile, denied = authenticated(request)
            if denied:
                return denied
            with shares.operation(session.principal, request_id) as (row, guard):
                if row is None:
                    raise KeyError("missing")
                if guard:
                    shares.recover(guard, row)
                    failed = [attempts[-1] for attempts in row["result"]["attempts"].values() if attempts[-1]["phase"] == "failed" and attempts[-1]["target"]["eligibility"] != "eligible"]
                    if failed:
                        try:
                            token = token_for(session.principal)
                            for attempt in failed:
                                reference = {field: attempt["target"][field] for field in ("id", "kind")}
                                attempt["target"] = target_for(session.principal, _profile, token, reference)
                            shares.save(guard, row)
                        except FeishuUsageError:
                            pass
                return JSONResponse(shares.public(row, selected), headers=HEADERS)
        except (KeyError, ValueError, ReportSharingError, FeishuUsageError) as error:
            return fail(error)

    @app.post("/audit-report-shares/{request_id}/retries", response_model=wc.AuditReportShare, openapi_extra=wc.openapi_body(wc.AuditReportShareRetry))
    def retry(request_id: str, body: dict, request: Request):
        try:
            _uuid(request_id)
            command = TypeAdapter(wc.AuditReportShareRetry).validate_python(body, strict=True)
            _uuid(command["attempt_id"])
            _target(command["target"])
            session, _profile, denied = authenticated(request)
            if denied:
                return denied
            with shares.operation(session.principal, request_id) as (row, guard):
                if row is None:
                    raise KeyError("missing")
                key = target_key(command["target"])
                history = row["result"]["attempts"].get(key)
                if not history:
                    raise KeyError("missing")
                if any(item["attempt_id"] == command["attempt_id"] for item in history):
                    selected = [
                        {"target": {field: attempts[-1]["target"][field] for field in ("id", "kind")}, "attempt_id": command["attempt_id"] if selected_key == key else attempts[-1]["attempt_id"]}
                        for selected_key, attempts in row["result"]["attempts"].items()
                    ]
                    return JSONResponse(shares.public(row, selected), headers=HEADERS)
                if guard is None:
                    raise ValueError("share_request_pending")
                shares.recover(guard, row)
                if (
                    history[-1]["phase"] != "failed"
                    or command["attempt_id"] == request_id
                    or any(item["attempt_id"] == command["attempt_id"] for attempts in row["result"]["attempts"].values() for item in attempts)
                ):
                    raise ValueError("share_attempt_unconfirmed")
                history.append(new_share_attempt(dict(history[-1]["target"]), request_id, command["attempt_id"]))
                shares.save(guard, row)
                try:
                    token = token_for(session.principal)
                except FeishuUsageError:
                    history[-1].update(phase="failed", reason="尚未发送消息，请恢复飞书应用凭证后重试。")
                    shares.save(guard, row)
                    return JSONResponse(shares.public(row), headers=HEADERS)
                dispatch(guard, row, session, token, [key])
                return JSONResponse(shares.public(row), headers=HEADERS)
        except (KeyError, ValueError, ReportSharingError, FeishuUsageError, ObjectMissing) as error:
            return fail(error)

    def shared(request, request_id):
        _uuid(request_id)
        session, _profile, denied = authenticated(request)
        return (None, denied) if denied else (shares.shared(session.principal, request_id), None)

    @app.get("/audit-report-shares/{request_id}/reports", response_model=wc.AuditSharedReports)
    def reports(request_id: str, request: Request):
        try:
            payload, denied = shared(request, request_id)
            if denied:
                return denied
            entries = [
                {
                    **{key: source[key] for key in ("name", "rule", "conclusion", "reference")},
                    "report": {"state": "ready", "report_id": source["reference"]["report_id"], "reason": None, "artifact": source["artifact"], "generated_at": source["generated_at"]},
                }
                for source in payload["sources"]
            ]
            return JSONResponse({"reports": entries, "archive": payload["archive"]["artifact"] if payload["archive"] else None}, headers=HEADERS)
        except (KeyError, ValueError, ReportSharingError) as error:
            return fail(error)

    @app.get("/audit-report-shares/{request_id}/pdf", response_class=Response)
    def pdf(request_id: str, request: Request, report_id: str, artifact_id: str):
        return binary(request_id, request, report_id, artifact_id)

    @app.get("/audit-report-shares/{request_id}/zip", response_class=Response)
    def zip_file(request_id: str, request: Request, artifact_id: str):
        return binary(request_id, request, None, artifact_id)

    def binary(request_id, request, report_id, artifact_id):
        try:
            payload, denied = shared(request, request_id)
            if denied:
                return denied
            source = next((source for source in payload["sources"] if source["reference"]["report_id"] == report_id), None) if report_id else payload["archive"]
            if not source or source["artifact"]["artifact_id"] != artifact_id:
                raise KeyError("missing")
            return Response(
                shares.read_bytes(source),
                media_type=source["artifact"]["content_type"],
                headers={**HEADERS, "Content-Disposition": attachment_content_disposition(source["artifact"]["filename"]), "X-Content-SHA256": source["sha256"]},
            )
        except (KeyError, ValueError, ObjectMissing, ReportSharingError) as error:
            return fail(error)
