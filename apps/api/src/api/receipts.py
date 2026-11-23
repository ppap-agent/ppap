"""Receipts: a person's own signing actions, as recorded (#361, #362). There is no receipt table; each receipt is one
row of `product.actions` on a signing route, numbered within the enterprise's day and downloadable as a PDF."""

from __future__ import annotations

import asyncio
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from fastapi import Query, Request
from fastapi.responses import JSONResponse, Response
from product.store import actions
from pydantic import BaseModel

SHANGHAI = ZoneInfo("Asia/Shanghai")
HEADERS = {"Cache-Control": "private, no-store"}


class ReceiptOutcome(BaseModel):
    state: str
    text: str
    error: str | None = None


class ReceiptPoints(BaseModel):
    # What starting the audit charged; none yet (or a preview that charges nothing) is said so, never guessed.
    points: int | None
    state: str


class Receipt(BaseModel):
    id: str
    number: str
    at: str
    action: str
    subject: str
    task_id: str | None
    via: str
    outcome: ReceiptOutcome
    details: list[str]
    points: ReceiptPoints | None


class ReceiptList(BaseModel):
    receipts: list[Receipt]
    next_offset: int | None


def local(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def render(receipt: dict, person: str) -> bytes:
    import pymupdf
    from documents.report_layout import text

    rows = [
        ("回执编号", receipt["number"]),
        ("时间", local(receipt["at"]) + "（北京时间）"),
        ("操作人", person),
        ("操作", receipt["action"]),
        ("对象", receipt["subject"]),
        ("经由", receipt["via"]),
        ("结果", receipt["outcome"]["text"]),
    ]
    rows += [("说明", line) for line in receipt["details"]]
    if receipt["points"]:
        points = receipt["points"]
        rows.append(("本轮扣点", f"{points['points']} 点" if points["points"] is not None else "未扣点"))
    body = "".join(f"<tr><td>{text(label)}</td><td>{text(value)}</td></tr>" for label, value in rows)
    html = (
        '<html lang="zh"><body><h1>PPAP 操作回执</h1>'
        f"<table>{body}</table>"
        "<p>这份回执记录的是本人在 PPAP 里做的这一次操作：谁、何时、经由什么、做了什么、结果。业务内容以 PPAP 里的记录为准。</p></body></html>"
    )
    story = pymupdf.Story(html=html, user_css="body {font-family:sans-serif; font-size:10.5pt; line-height:1.7} h1 {font-size:18pt} td {padding:4px 12px 4px 0; vertical-align:top}")
    output = BytesIO()
    with pymupdf.DocumentWriter(output) as writer:
        more = True
        while more:
            device = writer.begin_page(pymupdf.paper_rect("a4"))
            more, _ = story.place(pymupdf.paper_rect("a4") + (56, 56, -56, -56))
            story.draw(device)
            writer.end_page()
    return output.getvalue()


def register_receipt_routes(app, store, session_of) -> None:
    async def principal(request: Request):
        session = await asyncio.to_thread(session_of, request)
        return session

    @app.get("/receipts", response_model=ReceiptList)
    async def list_receipts(request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
        session = await principal(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        rows = await asyncio.to_thread(actions.receipts, store, session.principal, limit=limit + 1, offset=offset)
        return JSONResponse({"receipts": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}, headers=HEADERS)

    @app.get("/receipts/{receipt_id}", response_model=Receipt)
    async def read_receipt(receipt_id: str, request: Request):
        session = await principal(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        found = await asyncio.to_thread(lambda: actions.receipts(store, session.principal, ident=_uuid(receipt_id), limit=1))
        return JSONResponse(found[0], headers=HEADERS) if found else JSONResponse({"error": "receipt_not_found"}, status_code=404, headers=HEADERS)

    @app.get("/receipts/{receipt_id}/pdf", response_class=Response, responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
    async def receipt_pdf(receipt_id: str, request: Request):
        session = await principal(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        found = await asyncio.to_thread(lambda: actions.receipts(store, session.principal, ident=_uuid(receipt_id), limit=1))
        if not found:
            return JSONResponse({"error": "receipt_not_found"}, status_code=404, headers=HEADERS)
        body = await asyncio.to_thread(render, found[0], session.principal.display_name or "本人")
        return Response(body, media_type="application/pdf", headers={**HEADERS, "Content-Disposition": f'attachment; filename="{found[0]["number"]}.pdf"'})


def _uuid(value: str) -> str:
    import uuid

    try:
        return str(uuid.UUID(value))
    except ValueError:
        return "00000000-0000-0000-0000-000000000000"
