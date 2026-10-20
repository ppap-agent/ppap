"""Template feedback uses operator-table authority, never customer-table authority."""

import hashlib
import uuid

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from ppap_core.feishu import FeishuError, user_info
from ppap_core.feishu_usage import FeishuUsageError, tenant_access_token
from ppap_core.files import ObjectMissing
from ppap_core.identity import can_read
from ppap_core.template_feedback import FeedbackError, FeedbackRejected, create_feedback_record, find_feedback_record, upload_feedback_attachment
from ppap_product_store.feedback import TemplateFeedbackStore, feedback_receipt

from ppap_api import web_contract as wc

HEADERS = {"Cache-Control": "private, no-store"}


def register_feedback_routes(app, store, session_of, http, settings):
    feedbacks = TemplateFeedbackStore(store)

    @app.get("/audit-template-feedback", response_model=wc.AuditTemplateFeedbackHistory)
    def feedback_history(request: Request, page: int = Query(default=1, ge=1)):
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        return JSONResponse(feedbacks.history(session.principal, page), headers=HEADERS)

    def token_for(destination):
        if destination["app_id"] != settings.feishu_app_id or not all(destination.values()):
            raise FeedbackError("feedback_destination_unavailable")
        return tenant_access_token(http, settings.feishu_app_id, settings.feishu_app_secret, store.app_ticket(settings.feishu_app_id) or "", destination["tenant_key"])

    def handle(request, request_id, attempt_id, command=None):
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        try:
            if str(uuid.UUID(request_id)) != request_id or str(uuid.UUID(attempt_id)) != attempt_id:
                raise ValueError("invalid_feedback_request")
            if command is not None and (command["request_id"] != request_id or not command["file_version_ids"] or len(set(command["file_version_ids"])) != len(command["file_version_ids"])):
                raise ValueError("invalid_feedback_request")
            if command:
                for version_id in command["file_version_ids"]:
                    if str(uuid.UUID(version_id)) != version_id:
                        raise ValueError("invalid_feedback_request")
            destination = {
                "app_id": settings.feishu_app_id,
                "base_token": settings.feishu_feedback_base_token,
                "table_id": settings.feishu_feedback_table_id,
                "tenant_key": settings.feishu_feedback_tenant_key,
            }
            with feedbacks.operation(session.principal, request_id, attempt_id, command, destination) as (row, guard):
                if not row:
                    if command is None:
                        raise KeyError("missing")
                    return JSONResponse(
                        {
                            "request": command,
                            "attempt_id": attempt_id,
                            "state": "unknown",
                            "submission_id": None,
                            "received_file_version_ids": [],
                            "received_at": None,
                            "retry_allowed": False,
                            "reason": "尚未读到原提交请求，请读取原结果。",
                        },
                        headers=HEADERS,
                    )
                if not guard or row["phase"] in {"received", "failed"}:
                    return JSONResponse(feedback_receipt(row), headers=HEADERS)
                payload = row["payload"]
                if row["phase"] in {"unknown", "dispatching"}:
                    try:
                        token = token_for(payload["destination"])
                        record_id = find_feedback_record(
                            http, token, payload["destination"]["base_token"], payload["destination"]["table_id"], payload["operation_id"], list(payload["tokens"].values())
                        )
                        row = feedbacks.save(guard, row, "received", submission_id=record_id)
                    except (FeedbackError, FeishuUsageError):
                        row = feedbacks.save(guard, row, "unknown", reason="尚未核实飞书运营记录，请继续读取原提交结果，不要重新提交。")
                    return JSONResponse(feedback_receipt(row), headers=HEADERS)
                if command is None:
                    # Acquiring the guard proves no handler still owns preparation.
                    row = feedbacks.save(guard, row, "failed", reason="提交在落表前中断，附件仍保留，可以安全重试。")
                    return JSONResponse(feedback_receipt(row), headers=HEADERS)
                try:
                    for file in payload["files"]:
                        resource = store.resource(file["file_id"])
                        if not resource or not can_read(session.principal, resource, store.grants(file["file_id"], session.principal.user_id)):
                            raise KeyError("missing")
                    token = token_for(payload["destination"])
                    if not session.access_token:
                        raise FeedbackError("feedback_profile_unavailable")
                    info = user_info(http, session.access_token)
                    if info["tenant_key"] != session.principal.tenant_key or info["subject"] != session.principal.subject:
                        raise FeedbackError("feedback_identity_mismatch")
                    for file in payload["files"]:
                        if file["id"] in payload["tokens"]:
                            continue
                        body = store.objects.get(file["object_key"])
                        if len(body) != file["byte_size"] or hashlib.sha256(body).hexdigest() != file["sha256"]:
                            raise FeedbackError("feedback_attachment_mismatch")
                        payload["tokens"][file["id"]] = upload_feedback_attachment(http, token, payload["destination"]["base_token"], file["original_filename"], body)
                        row = feedbacks.save(guard, row, "preparing")
                        payload = row["payload"]
                    fields = {
                        "请求编号": payload["operation_id"],
                        "提交时间": int(row["created_at"].timestamp() * 1000),
                        "提交人": info["display_name"] or session.principal.display_name,
                        "联系方式": " · ".join(dict.fromkeys(info[key] for key in ("mobile", "email", "enterprise_email") if info.get(key))),
                        "客户名称": "",
                        "处理状态": "待处理",
                        "文件附件": [{"file_token": payload["tokens"][file["id"]]} for file in payload["files"]],
                    }
                    row = feedbacks.save(guard, row, "dispatching")
                except (FeedbackError, FeishuUsageError, FeishuError, ObjectMissing, OSError) as error:
                    reason = (
                        "模板提报服务配置暂不可用，材料未提交给运营。已上传附件仍保留，请联系支持。"
                        if isinstance(error, FeedbackError) and str(error) == "feedback_destination_unavailable"
                        else "材料尚未落表，已上传附件仍保留。请检查授权或联系支持后重试。"
                    )
                    row = feedbacks.save(guard, row, "failed", reason=reason)
                    return JSONResponse(feedback_receipt(row), headers=HEADERS)
                try:
                    record_id = create_feedback_record(http, token, payload["destination"]["base_token"], payload["destination"]["table_id"], payload["operation_id"], fields)
                    row = feedbacks.save(guard, row, "received", submission_id=record_id)
                except FeedbackRejected:
                    row = feedbacks.save(guard, row, "failed", reason="飞书未允许本次落表。已上传附件仍保留；请检查权限后安全重试。")
                except FeedbackError:
                    row = feedbacks.save(guard, row, "unknown", reason="材料可能已收到，请读取原提交结果，不要再次提交。")
                return JSONResponse(feedback_receipt(row), headers=HEADERS)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403, headers=HEADERS)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_feedback_request"}, status_code=409, headers=HEADERS)

    @app.post("/audit-template-feedback/{request_id}/attempts/{attempt_id}", response_model=wc.AuditTemplateFeedbackReceipt)
    def submit_feedback(request_id: str, attempt_id: str, body: wc.AuditTemplateFeedbackRequest, request: Request):
        return handle(request, request_id, attempt_id, body)

    @app.get("/audit-template-feedback/{request_id}/attempts/{attempt_id}", response_model=wc.AuditTemplateFeedbackReceipt)
    def read_feedback(request_id: str, attempt_id: str, request: Request):
        return handle(request, request_id, attempt_id)
