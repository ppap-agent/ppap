"""Feishu Project (Meego) plugin host routes.

The plugin page runs on Meego's plugin origin. It exchanges a Meego auth code
for a short Bearer host session once the person has bound their Meego identity
to their PPAP Feishu login in a browser page. All task, file and round reads
then use the ordinary Product API with that Bearer session.
"""

from __future__ import annotations

import asyncio
import logging
from html import escape
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from product.feishu import authorize_url
from product.identity import SESSION_COOKIE, can_read, new_token
from product.logs import emit
from product.meego import MeegoClient, MeegoError, WorkItemRef, work_item
from product.meego_writeback import marker, summary, task_link
from product.store.meego import MeegoConnectError, MeegoStore
from product.store.store import _as_uuid

from api.mcp_auth import page

CONNECT_MESSAGES = {
    "connect_ticket_invalid": "连接链接无效。请回到飞书项目重新点击“连接 PPAP”。",
    "connect_ticket_used": "这个连接链接已经用过了。请回到飞书项目刷新后查看。",
    "connect_ticket_expired": "连接链接已过期。请回到飞书项目重新点击“连接 PPAP”。",
    "connect_tenant_mismatch": "当前登录的飞书账号不属于这个飞书项目对应的企业。请换用该企业的飞书账号登录后重试。",
    "connect_confirmation_invalid": "确认已失效。请重新打开连接链接。",
    "connect_login_required": "飞书登录已失效。请重新登录后再连接。",
}


def register_meego_routes(app: FastAPI, store, meego: MeegoStore, session_of, http, settings) -> None:
    client = MeegoClient(http, settings)
    api_base = settings.public_api_url.rstrip("/")
    web_base = settings.canonical_web_url()

    def disabled() -> JSONResponse | None:
        if not settings.meego_enabled():
            return JSONResponse({"error": "meego_disabled"}, status_code=409)
        return None

    def host(request: Request):
        session = session_of(request)
        binding = getattr(request.state, "meego_binding", None)
        if session is None or binding is None:
            return None, None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        return session, binding, None

    def meego_failure(exc: MeegoError) -> JSONResponse:
        if exc.code in {"meego_access_denied", "meego_user_inactive"}:
            return JSONResponse({"error": exc.code}, status_code=403)
        if exc.code in {"meego_work_item_not_found", "meego_file_not_found"}:
            return JSONResponse({"error": exc.code}, status_code=404)
        if exc.code == "file_too_large":
            return JSONResponse({"error": exc.code}, status_code=413)
        return JSONResponse({"error": exc.code}, status_code=502)

    def item_of(body_or_path: dict) -> WorkItemRef:
        return work_item(str(body_or_path.get("project_key") or ""), str(body_or_path.get("work_item_type_key") or ""), str(body_or_path.get("work_item_id") or ""))

    @app.post("/integrations/meego/session", include_in_schema=False)
    def meego_session(body: dict) -> JSONResponse:
        if (off := disabled()) is not None:
            return off
        code = str(body.get("code") or "").strip()
        if not code or len(code) > 200:
            return JSONResponse({"error": "invalid_code"}, status_code=400)
        try:
            user = client.exchange_code(code)
        except MeegoError as exc:
            emit("meego_auth_failed", level=logging.WARNING, code=exc.code)
            return JSONResponse({"error": "meego_auth_failed"}, status_code=502 if exc.code == "meego_unavailable" else 401)
        feishu_tenant = settings.meego_feishu_tenant(user.tenant_key)
        if not feishu_tenant:
            return JSONResponse({"error": "meego_tenant_not_mapped"}, status_code=403)
        if not settings.tenant_allowed(feishu_tenant):
            return JSONResponse({"error": "tenant_not_allowed"}, status_code=403)
        binding = meego.active_binding(settings.meego_plugin_id, user.tenant_key, user.user_key)
        if binding is not None:
            try:
                raw, expires_at = meego.create_host_session(binding)
                loaded = meego.load_host_session(raw)
                if loaded is not None:
                    session, _ = loaded
                    return JSONResponse(
                        {
                            "state": "connected",
                            "token": raw,
                            "expires_at": expires_at.isoformat(),
                            "display_name": session.principal.display_name,
                            "meego_name": user.name,
                            "web_url": web_base,
                        }
                    )
            except MeegoConnectError:
                pass
        ticket, expires_at = meego.create_ticket(settings.meego_plugin_id, user.tenant_key, user.user_key, user.name, feishu_tenant)
        return JSONResponse(
            {
                "state": "connect_required",
                "connect_url": api_base + "/integrations/meego/connect?ticket=" + quote(ticket, safe=""),
                "expires_at": expires_at.isoformat(),
                "meego_name": user.name,
            }
        )

    def connect_page_url(ticket: str) -> str:
        return api_base + "/integrations/meego/connect?ticket=" + quote(ticket, safe="")

    @app.get("/integrations/meego/connect", include_in_schema=False)
    def meego_connect_get(request: Request, ticket: str = ""):
        if not settings.meego_enabled():
            return page("暂未开放", "<p>飞书项目连接尚未开启。</p>", 409)
        session = session_of(request)
        if session is None or getattr(request.state, "meego_binding", None) is not None:
            if request.query_params.get("auth_error"):
                return page("登录未完成", f'<p>请重新登录后连接。</p><a href="{escape(connect_page_url(ticket), quote=True)}">重新登录</a>', 401)
            state = new_token()
            # Server-built target, like the MCP consent sign-in.
            store.save_state(state, connect_page_url(ticket))
            return RedirectResponse(authorize_url(settings, state), status_code=302)
        try:
            view = meego.connect_view(ticket, request.cookies.get(SESSION_COOKIE, ""), session.principal)
        except MeegoConnectError as exc:
            return page("无法连接", f"<p>{escape(CONNECT_MESSAGES.get(str(exc), '连接失败，请重试。'))}</p>", 400)
        return page(
            "连接飞书项目",
            f"<p>飞书项目账号 <strong>{escape(view['meego_name'] or '（未显示姓名）')}</strong> 将使用你的 PPAP 身份发起审核、查看结果。</p>"
            f"<p>当前 PPAP 用户：<strong>{escape(session.principal.display_name)}</strong>。审核会按你的权限和额度执行，可随时在插件中断开连接。</p>"
            "<p><small>如果这不是你本人在飞书项目里发起的连接，请直接关闭本页。</small></p>"
            f'<form method="post"><input type="hidden" name="ticket" value="{escape(ticket, quote=True)}">'
            f'<input type="hidden" name="csrf" value="{escape(view["csrf"], quote=True)}">'
            '<button name="decision" value="approve">确认连接</button></form>',
        )

    @app.post("/integrations/meego/connect", include_in_schema=False)
    async def meego_connect_post(request: Request):
        if not settings.meego_enabled():
            return page("暂未开放", "<p>飞书项目连接尚未开启。</p>", 409)
        session = await asyncio.to_thread(session_of, request)
        if session is None or getattr(request.state, "meego_binding", None) is not None:
            return page("请重新登录", "<p>登录已过期，未建立连接。请重新打开连接链接。</p>", 401)
        if len(await request.body()) > 8192:
            return page("请求过大", "<p>未建立连接。</p>", 413)
        form = await request.form()
        if form.get("decision") != "approve":
            return page("已取消", "<p>未建立连接。</p>")
        try:
            await asyncio.to_thread(meego.confirm, str(form.get("ticket", "")), request.cookies.get(SESSION_COOKIE, ""), str(form.get("csrf", "")), session)
        except MeegoConnectError as exc:
            return page("无法连接", f"<p>{escape(CONNECT_MESSAGES.get(str(exc), '连接失败，请重试。'))}</p>", 400)
        return page("连接成功", "<p>已连接。请回到飞书项目，点击“我已完成连接”。</p>")

    @app.get("/integrations/meego/me", include_in_schema=False)
    def meego_me(request: Request) -> JSONResponse:
        session, binding, denied = host(request)
        if denied:
            return denied
        return JSONResponse({"display_name": session.principal.display_name, "meego_name": binding["meego_name"], "web_url": web_base})

    @app.delete("/integrations/meego/connection", include_in_schema=False)
    def meego_disconnect(request: Request) -> JSONResponse:
        session, binding, denied = host(request)
        if denied:
            return denied
        meego.revoke_binding(binding["id"], session.principal.user_id)
        return JSONResponse({"disconnected": True})

    @app.get("/integrations/meego/work-items/{project_key}/{work_item_type_key}/{work_item_id}/files", include_in_schema=False)
    def meego_files(project_key: str, work_item_type_key: str, work_item_id: str, request: Request) -> JSONResponse:
        _, binding, denied = host(request)
        if denied:
            return denied
        try:
            item = work_item(project_key, work_item_type_key, work_item_id)
            return JSONResponse(client.work_item_files(binding["user_key"], item))
        except ValueError:
            return JSONResponse({"error": "invalid_work_item"}, status_code=400)
        except MeegoError as exc:
            return meego_failure(exc)

    @app.get("/integrations/meego/work-items/{project_key}/{work_item_type_key}/{work_item_id}/audits", include_in_schema=False)
    def meego_audits(project_key: str, work_item_type_key: str, work_item_id: str, request: Request) -> JSONResponse:
        """This person's audits of one work item, newest first, and which of them the work item already shows."""
        session, binding, denied = host(request)
        if denied:
            return denied
        try:
            item = work_item(project_key, work_item_type_key, work_item_id)
        except ValueError:
            return JSONResponse({"error": "invalid_work_item"}, status_code=400)
        # Reading the comments is also the Meego access check for this person.
        texts: list[str] | None = None
        try:
            texts = client.comment_texts(binding["user_key"], item)
        except MeegoError as exc:
            if exc.code in {"meego_access_denied", "meego_user_inactive"}:
                return meego_failure(exc)
        try:
            tasks = store.list_audit_tasks(session.principal, 20, 0, target={"provider": "meego", "object_kind": "work_item", "object_ref": item.object_ref()})
        except (KeyError, ValueError):
            return JSONResponse({"error": "access_denied"}, status_code=403)
        tasks.sort(key=lambda task: task["created_at"], reverse=True)
        # None means the comments could not be read; the plugin must not show that as "not written".
        written = None if texts is None else [task["round_id"] for task in tasks if task.get("round_id") and any(marker(task["round_id"]) in text for text in texts)]
        return JSONResponse({"audits": tasks, "written_round_ids": written})

    @app.post("/integrations/meego/files/import", status_code=201, include_in_schema=False)
    def meego_import(body: dict, request: Request) -> JSONResponse:
        session, binding, denied = host(request)
        if denied:
            return denied
        request_id = _as_uuid(str(body.get("request_id") or ""))
        uid = str(body.get("uid") or "").strip()
        if not request_id or not uid or len(uid) > 1024:
            return JSONResponse({"error": "invalid_import_request"}, status_code=400)
        try:
            item = item_of(body)
            listed = client.work_item_files(binding["user_key"], item)
            match = next((f for f in listed["files"] if f["uid"] == uid), None)
            if match is None:
                return JSONResponse({"error": "meego_file_not_found"}, status_code=404)
            version = store.receive_meego_upload(
                session.principal,
                request_id,
                item.object_ref(),
                uid,
                match["name"],
                lambda: (client.download(binding["user_key"], item, uid), match["content_type"] or "application/octet-stream"),
            )
        except MeegoError as exc:
            return meego_failure(exc)
        except ValueError as exc:
            code = str(exc)
            return JSONResponse({"error": code or "invalid_import_request"}, status_code=409 if "conflict" in code else 400)
        return JSONResponse(version.public(), status_code=201)

    @app.put("/integrations/meego/tasks/{task_id}/work-item", include_in_schema=False)
    def meego_task_target(task_id: str, body: dict, request: Request) -> JSONResponse:
        session, binding, denied = host(request)
        if denied:
            return denied
        try:
            item = item_of(body)
            client.work_item_files(binding["user_key"], item)
            meego.set_task_target(session.principal, task_id, item)
        except MeegoError as exc:
            return meego_failure(exc)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc) or "invalid_work_item"}, status_code=409 if str(exc) in {"task_input_locked", "writeback_target_conflict"} else 400)
        return JSONResponse({"work_item": item.object_ref()})

    @app.post("/integrations/meego/rounds/{round_id}/writeback", include_in_schema=False)
    def meego_writeback(round_id: str, request: Request) -> JSONResponse:
        session, binding, denied = host(request)
        if denied:
            return denied
        resource = store.resource(round_id)
        grants = store.grants(round_id, session.principal.user_id) if resource else set()
        if resource is None or not can_read(session.principal, resource, grants):
            return JSONResponse({"error": "access_denied"}, status_code=403)
        round_body = store.get_round(round_id)
        if round_body is None:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        target = store.round_writeback(round_id) or {}
        if target.get("provider") != "meego" or not round_body.get("source_draft_id"):
            return JSONResponse({"error": "writeback_target_missing"}, status_code=409)
        try:
            # The comment states the conclusions the web task page shows.
            task = store.get_audit_task(session.principal, str(round_body["source_draft_id"]))
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        if str(task.get("round_id") or "") != str(round_body["id"]):
            return JSONResponse({"error": "writeback_target_missing"}, status_code=409)
        try:
            item = WorkItemRef.parse(str(target.get("object_ref") or ""))
            lines = summary(task, web_base)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        link = task_link(web_base, str(task["id"]))
        mark = marker(str(round_body["id"]))
        with store._conn() as guard:
            row = guard.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", ("meego-writeback:" + str(round_body["id"]),)).fetchone()
            if not (row and row[0]):
                return JSONResponse({"error": "writeback_in_progress"}, status_code=409)
            try:
                # Read before writing: an earlier attempt may already have landed.
                if any(mark in text for text in client.comment_texts(binding["user_key"], item)):
                    return JSONResponse({"state": "already_written", "round_id": round_body["id"], "link": link})
                try:
                    client.add_comment(binding["user_key"], item, lines)
                except MeegoError as exc:
                    if not exc.unknown:
                        return meego_failure(exc)
                    # Unknown outcome: read back once, never write blindly again.
                    if any(mark in text for text in client.comment_texts(binding["user_key"], item)):
                        return JSONResponse({"state": "written", "round_id": round_body["id"], "link": link})
                    return JSONResponse({"error": "meego_writeback_unknown"}, status_code=502)
            except MeegoError as exc:
                return meego_failure(exc)
            finally:
                guard.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", ("meego-writeback:" + str(round_body["id"]),))
        return JSONResponse({"state": "written", "round_id": round_body["id"], "link": link})
