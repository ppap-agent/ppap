"""What people did: one row per write request a known person made, through any channel (#362).

A row is started before the request takes effect and finished with its result. Who, when, through what and with which
parameters are recorded here and nowhere else; business tables keep ownership, content and state.
"""

from __future__ import annotations

import json
import uuid

from psycopg.types.json import Jsonb

MAX_PARAMS = 64 * 1024
# The person's no to a client's proposal (#348): an action like any other, with the reason as its parameter.
DECLINE_ROUTE = "/intents/{intent_id}/decline"


def parameters(body: bytes, content_type: str) -> dict:
    """The request as a person's action: its JSON, or what was uploaded (never the bytes)."""
    if not body:
        return {}
    if content_type.startswith("application/json"):
        if len(body) > MAX_PARAMS:
            return {"too_large": len(body)}
        try:
            value = json.loads(body)
        except ValueError:
            return {"unreadable": len(body)}
        return value if isinstance(value, dict) else {"value": value}
    return {"content_type": content_type.split(";")[0], "bytes": len(body)}


def start(store, principal, *, channel: str, grant_id: str | None, intent_id: str | None, method: str, route: str, path: str, params: dict) -> str:
    ident = str(uuid.uuid4())
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO product.actions (id, tenant_id, user_id, channel, grant_id, intent_id, method, route, path, params)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (ident, principal.tenant_id, principal.user_id, channel, grant_id, intent_id, method, route[:300], path[:2000], Jsonb(params)),
        )
    return ident


def finish(store, ident: str, status_code: int, error: str | None = None) -> None:
    outcome = "succeeded" if status_code < 400 else "failed" if status_code < 500 else "unknown"
    with store._conn() as conn:
        conn.execute(
            "UPDATE product.actions SET outcome=%s, status_code=%s, error=%s, finished_at=now() WHERE id=%s AND outcome='pending'",
            (outcome, status_code, (error or None) and error[:200], ident),
        )


# ---- receipts (#361) -----------------------------------------------------------------------------------
# A receipt is a signing action as its person sees it: no table of its own, only the action row shown. Every signing
# route (`api.signing`) has a label here; the contract test checks that none is missing.
LABELS = {
    ("POST", "/audit-tasks/{task_id}/confirm"): "开始审核",
    ("POST", "/audit-tasks/{task_id}/reports/{report_id}/regenerations"): "重新生成报告",
    ("POST", "/integrations/feishu/rounds/{round_id}/report"): "在飞书生成报告文档",
    ("POST", "/integrations/feishu/rounds/{round_id}/writeback"): "写回飞书原文件",
    ("POST", "/audit-report-shares"): "分享报告",
    ("POST", "/rulebooks/{customer_id}/revisions"): "确认规则修订",
    ("POST", "/rulebooks/{customer_id}/{version}/{revision}/clarified"): "标记规则已澄清",
    ("PUT", "/rulebooks/{customer_id}/{version}/{revision}/readings"): "发布条款解读",
    ("POST", "/rule-questionnaires/cards"): "发问题卡片给客户",
    ("POST", "/rulebooks/{customer_id}/plan"): "确认规则澄清的这一轮",
}
CHANNELS = {"web": "网页", "docs": "飞书云文档插件", "meego": "飞书项目插件", "feishu_card": "飞书卡片", "mcp": "MCP 客户端"}
STEPS = {"revision": "冻结修订", "cards": "发问题卡片给客户", "clarified": "标记已澄清", "readings": "发布解读"}
OUTCOMES = {"succeeded": "已完成", "failed": "没有完成", "unknown": "结果未知", "pending": "结果未知"}


def receipts(store, principal, *, ident: str | None = None, intent: str | None = None, limit: int = 20, offset: int = 0) -> list[dict]:
    """The person's own signing actions, newest first, each numbered R-<day>-<n> within the enterprise's day."""
    keys = [f"{method} {route}" for method, route in LABELS]
    with store._conn() as conn:
        rows = conn.execute(
            """SELECT * FROM (
                   SELECT a.id, a.user_id, a.at, a.channel, a.grant_id, a.intent_id, a.method, a.route, a.path, a.params, a.outcome, a.error,
                          (a.at AT TIME ZONE 'Asia/Shanghai')::date AS day,
                          row_number() OVER (PARTITION BY (a.at AT TIME ZONE 'Asia/Shanghai')::date ORDER BY a.at, a.id) AS seq
                   FROM product.actions a WHERE a.tenant_id=%s AND a.method || ' ' || a.route = ANY(%s)) r
               WHERE r.user_id=%s AND (%s::uuid IS NULL OR r.id=%s::uuid) AND (%s::uuid IS NULL OR r.intent_id=%s::uuid)
               ORDER BY r.at DESC, r.id DESC LIMIT %s OFFSET %s""",
            (principal.tenant_id, keys, principal.user_id, ident, ident, intent, intent, limit, offset),
        ).fetchall()
        tasks = [row[8].split("/")[2] for row in rows if row[8].startswith("/audit-tasks/")]
        rounds = [row[8].split("/")[4] for row in rows if row[8].startswith("/integrations/feishu/rounds/")]
        numbers = {
            str(task): f"PPAP-{day:%Y%m%d}-{sequence:03d}"
            for task, day, sequence in conn.execute(
                "SELECT id, task_day, task_sequence FROM product.audit_drafts WHERE tenant_id=%s AND id::text = ANY(%s) AND task_day IS NOT NULL",
                (principal.tenant_id, tasks),
            ).fetchall()
        }
        by_round = dict(
            conn.execute(
                "SELECT r.id::text, d.id::text FROM product.audit_rounds r JOIN product.audit_drafts d ON d.id=r.source_draft_id WHERE r.tenant_id=%s AND r.id::text = ANY(%s)",
                (principal.tenant_id, rounds),
            ).fetchall()
        )
        numbers |= {
            str(task): f"PPAP-{day:%Y%m%d}-{sequence:03d}"
            for task, day, sequence in conn.execute(
                "SELECT id, task_day, task_sequence FROM product.audit_drafts WHERE tenant_id=%s AND id::text = ANY(%s) AND task_day IS NOT NULL",
                (principal.tenant_id, list(by_round.values())),
            ).fetchall()
        }
        clients = dict(
            conn.execute(
                """SELECT o.id::text, c.metadata->>'client_name' FROM product.mcp_operations o JOIN product.mcp_grants g ON g.id=o.grant_id
                   JOIN product.mcp_clients c ON c.id=g.client_id WHERE o.id::text = ANY(%s)""",
                ([str(row[5]) for row in rows if row[5]],),
            ).fetchall()
        )
        # What starting an audit charged: the usage of the round that request created (#361).
        charged = {
            str(key): (points, states)
            for key, points, states in conn.execute(
                """SELECT r.idempotency_key, sum(u.consumed_points), array_agg(DISTINCT u.external_status) FROM product.audit_rounds r
                   JOIN product.usage_events u ON u.round_id=r.id AND u.tenant_id=r.tenant_id
                   WHERE r.tenant_id=%s AND r.idempotency_key = ANY(%s) GROUP BY r.idempotency_key""",
                (principal.tenant_id, [str((row[9] or {}).get("request_id")) for row in rows if row[7] == "/audit-tasks/{task_id}/confirm"]),
            ).fetchall()
        }
    return [_receipt(row, numbers, by_round, clients, charged) for row in rows]


def _receipt(row, numbers: dict, by_round: dict, clients: dict, charged: dict) -> dict:
    ident, _user, at, channel, _grant, intent, method, route, path, params, outcome, error, day, seq = row
    params = params or {}
    parts = path.strip("/").split("/")
    task = parts[1] if route.startswith("/audit-tasks/") else by_round.get(parts[3]) if route.startswith("/integrations/feishu/rounds/") else None
    if task:
        subject = numbers.get(task, "审核任务")
    elif route.startswith("/rulebooks/"):
        subject = f"{parts[1]} 规则库" + (f" {parts[2]} 修订 {parts[3]}" if len(parts) >= 4 and parts[2].startswith("v") else "")
    elif route == "/rule-questionnaires/cards":
        rulebook = params.get("rulebook") or {}
        subject = f"{params.get('customer_id', '')} 规则库 {rulebook.get('version', '')} 修订 {rulebook.get('revision', '')}"
    else:
        subject = "报告"
    via = CHANNELS.get(channel, channel)
    if intent:
        via = f"由「{clients.get(str(intent)) or 'MCP 客户端'}」提议，本人在{via}完成"
    details = []
    if route == "/rulebooks/{customer_id}/plan":
        details.append("执行：" + "、".join(STEPS.get(step.get("step"), str(step.get("step"))) for step in params.get("steps") or []))
        if params.get("unselected"):
            details.append("没有勾选：" + "、".join(STEPS.get(step, step) for step in params["unselected"]) + (f"（原因：{params['reason']}）" if params.get("reason") else ""))
    points = None
    if route == "/audit-tasks/{task_id}/confirm" and outcome == "succeeded":
        total, states = charged.get(str(params.get("request_id")), (None, None))
        points = {"points": total, "state": "confirmed" if states and set(states) <= {"confirmed", "none"} else "pending" if states else "none"}
    return {
        "id": str(ident),
        "number": f"R-{day:%Y%m%d}-{seq:04d}",
        "at": at.isoformat(),
        "action": LABELS[(method, route)],
        "subject": subject,
        "task_id": task,
        "via": via,
        "outcome": {"state": outcome, "text": OUTCOMES.get(outcome, outcome) + (f"（{error}）" if outcome == "failed" and error else ""), "error": error},
        "details": details,
        "points": points,
    }
