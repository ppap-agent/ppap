"""Summarize a finished audit Task as one Meego work-item comment.

The comment states the same per-file conclusions the PPAP web page shows (the
Task's ``audit.conclusion``), so one concept has one definition. It carries the
Round id as its marker, so a reader can tell whether this Round was already
written back before writing again. An unfinished, failed or unknown result is
never presented as passed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote, urlencode

# One vocabulary with the PPAP web app (apps/web/src/audit/status-labels.ts).
CONCLUSION_LABEL = {"PASS": "符合", "NG": "不符合", "UNKNOWN": "尚有未确定项"}
REFERENCE_LABEL = "作为参考资料，未单独审核"


def marker(round_id: str) -> str:
    return f"PPAP 审核轮次 {round_id}"


def task_link(web_url: str, task_id: str) -> str:
    """Open the task in the current edition; the bare site root serves the legacy edition."""
    base = (web_url or "").rstrip("/")
    target = base + "/tasks/" + quote(task_id, safe="")
    return base + "/enter-current?" + urlencode({"return_to": target})


def _selected(task: dict) -> list[dict]:
    return [row for row in task.get("files") or [] if isinstance(row, dict) and row.get("selection") == "selected"]


def writeback_state(task: dict) -> str:
    """``ready`` only when every audited file has a terminal web conclusion."""
    if not isinstance(task, dict) or task.get("state") != "completed" or not task.get("round_id"):
        return "round_incomplete"
    if task.get("execution") == "failed" or task.get("has_execution_error"):
        return "round_has_errors"
    if task.get("execution") != "completed":
        return "round_incomplete"
    audited = 0
    for row in _selected(task):
        audit = row.get("audit")
        if audit is None:
            if not row.get("reference_only") and (row.get("match") or {}).get("state") != "reference":
                return "round_incomplete"
            continue
        if audit.get("execution") == "review_failed":
            return "round_has_errors"
        if audit.get("execution") != "completed" or audit.get("conclusion") not in CONCLUSION_LABEL:
            return "round_incomplete"
        audited += 1
    return "ready" if audited else "round_incomplete"


def _clock(raw) -> str:
    stamp = datetime.now(timezone.utc)
    if isinstance(raw, str) and raw:
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(timezone.utc)
    return stamp.strftime("%Y 年 %m 月 %d 日 %H:%M")


def summary(task: dict, web_url: str) -> list[str]:
    state = writeback_state(task)
    if state != "ready":
        raise ValueError(state)
    lines = [f"PPAP 审核结果（{marker(str(task['round_id']))}）", "审核时间：" + _clock(task.get("round_started_at"))]
    conclusions: set[str] = set()
    ng = unsure = 0
    for row in _selected(task):
        name = str((row.get("file") or {}).get("original_filename") or "未命名资料")
        audit = row.get("audit")
        if audit is None:
            lines.append(f"{name}：{REFERENCE_LABEL}")
            continue
        conclusion = audit["conclusion"]
        conclusions.add(conclusion)
        counts = audit.get("counts") or {}
        ng += int(counts.get("ng_count") or 0)
        unsure += sum(int(counts.get(key) or 0) for key in ("unknown_count", "not_covered_count", "error_count"))
        lines.append(f"{name}：{CONCLUSION_LABEL[conclusion]}")
    overall = "NG" if "NG" in conclusions else "UNKNOWN" if "UNKNOWN" in conclusions else "PASS"
    lines.append(f"总体：{CONCLUSION_LABEL[overall]}；不符合 {ng} 项，尚未确定 {unsure} 项")
    lines.append("查看详情：" + task_link(web_url, str(task["id"])))
    return lines
