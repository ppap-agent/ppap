"""Summarize a finished Round as one Meego work-item comment.

The comment carries the Round id as its marker, so a reader can tell whether
this Round was already written back before writing again. Only rounds that the
Feishu doc writeback would also accept are written: an unfinished, failed or
unknown result is never presented as passed.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

from audit.audits import NA, NG, PASS, UNABLE

from product.feishu_writeback import _audit_clock, _group_findings, writeback_state


def marker(round_id: str) -> str:
    return f"PPAP 审核轮次 {round_id}"


def task_link(web_url: str, round_body: dict) -> str:
    """Open the task in the current edition; the bare site root serves the legacy edition."""
    base = (web_url or "").rstrip("/")
    task_id = str(round_body.get("source_draft_id") or "")
    target = base + ("/tasks/" + quote(task_id, safe="") if task_id else "/?round=" + quote(str(round_body["id"]), safe=""))
    return base + "/enter-current?" + urlencode({"return_to": target})


def file_conclusion(rows: list[dict]) -> str:
    statuses = {row.get("status") for row in rows}
    if NG in statuses:
        return "不符合"
    if UNABLE in statuses or not statuses:
        return "尚有未确定项"
    if statuses == {NA}:
        return "不适用"
    if statuses <= {PASS, NA}:
        return "符合"
    return "尚有未确定项"


def summary(round_body: dict, web_url: str) -> list[str]:
    state = writeback_state(round_body)
    if state != "ready":
        raise ValueError(state)
    groups = _group_findings(round_body)
    lines = [f"PPAP 审核结果（{marker(str(round_body['id']))}）", "审核时间：" + _audit_clock(round_body)]
    ng = unable = 0
    for name, rows in groups:
        lines.append(f"{name}：{file_conclusion(rows)}")
        ng += sum(1 for row in rows if row.get("status") == NG)
        unable += sum(1 for row in rows if row.get("status") == UNABLE)
    passed = all(file_conclusion(rows) in {"符合", "不适用"} for _, rows in groups)
    lines.append(f"总体：{'全部符合' if passed else '存在问题'}；不符合 {ng} 项，无法判断 {unable} 项")
    lines.append("查看详情：" + task_link(web_url, round_body))
    return lines
