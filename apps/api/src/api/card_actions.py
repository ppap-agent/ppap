"""A card button's signing call (#355, CR-06): the same route the Web calls, made in-process as the person who clicked.

Feishu has verified who clicked; the click is bound to a member of the enterprise and to the confirmation card PPAP
sent about that task. The route itself still decides everything else (only the creator may start the audit; the quote and
revision must be the ones the card showed). Feishu wants an answer within three seconds: a call still running then goes
on, and the person is told to look at the page; the request ID makes a second click the same request, never a second one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.responses import JSONResponse
from product.feishu import CardCall
from product.feishu_notifications import task_notification_card
from product.identity import Session
from product.logs import emit
from product.store import card_calls

ANSWER_WITHIN = 2.5
SAY = {
    "task_revision_conflict": "文件或报价已变化，本次没有开始。请打开页面核对。",
    "task_confirmation_changed": "审核依据已更新，本次没有开始。请打开页面核对后再开始。",
    "suite_validation_required": "当前检查版本需要重新验证，本次没有开始。请打开页面查看。",
    "usage_quota_unavailable": "当前无法确认用量，没有开始审核。请打开页面查看。",
    "usage_ai_disabled": "飞书AI包开关已关闭，没有开始审核。请联系管理员。",
    "usage_balance_empty": "当前没有可用额度，没有开始审核。请联系管理员。",
    "access_denied": "只有这次审核的创建人能开始它。",
    # Started already (in the Web, or from another card): nothing was started twice.
    "draft_already_accepted": "这次审核已经开始过，不用再点。打开审核可以查看进度。",
}
_running: set[asyncio.Task] = set()


def toast(kind: str, content: str) -> JSONResponse:
    return JSONResponse({"toast": {"type": kind, "content": content}})


async def handle_card_call(app, store, call: CardCall) -> JSONResponse:
    principal = await asyncio.to_thread(card_calls.person, store, call.tenant_key, call.subject)
    task_id = call.path.split("/")[2]
    if principal is None or not await asyncio.to_thread(card_calls.card_sent, store, principal, call.message_id, task_id):
        return toast("warning", "无法用这张卡片操作。请打开页面核对。")
    session = Session(principal, datetime.now(timezone.utc) + timedelta(minutes=5))

    async def as_person(scope, receive, send):
        # Only this in-process call carries these keys; a network request cannot set them.
        await app({**scope, "ppap.card_session": session, "ppap.channel": "feishu_card"}, receive, send)

    async def make() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=as_person), base_url="http://ppap.internal") as client:
            return await client.request(call.method, call.path, json=call.body)

    work = asyncio.create_task(make())
    _running.add(work)
    work.add_done_callback(_running.discard)
    try:
        response = await asyncio.wait_for(asyncio.shield(work), ANSWER_WITHIN)
    except asyncio.TimeoutError:
        return toast("info", "已收到，正在受理。请打开审核查看结果，不用再点一次。")
    except Exception:
        emit("card_call_unavailable", level=logging.WARNING)
        return toast("warning", "结果还不确定。请打开页面查看，不用再点一次。")
    if response.is_success:
        # The result is written back to the card: it now says the audit started, with no button left to press.
        try:
            card = task_notification_card(response.json(), "started", store.settings.canonical_web_url())
        except (KeyError, TypeError, ValueError):
            return toast("success", "已开始审核，打开审核可查看进度。")
        return JSONResponse({"toast": {"type": "success", "content": "已开始审核，打开审核可查看进度。"}, "card": {"type": "raw", "data": card}})
    try:
        code = response.json().get("error", "")
    except ValueError:
        code = ""
    return toast("warning", SAY.get(code, "本次没有开始，请打开页面核对当前任务。"))
