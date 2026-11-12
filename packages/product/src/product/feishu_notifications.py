"""Task recall cards and one bounded Feishu send. No automatic send retry."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from product.feishu_drive import OPEN_API

_MARKDOWN_ESCAPES = {ord(char): f"&#{ord(char)};" for char in "&<>*_~[]()\\#!`+-|{}:'\""}


def _markdown(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").translate(_MARKDOWN_ESCAPES)


def _counts_text(counts: dict) -> str:
    labels = {"pass_count": "符合", "ng_count": "不符合", "unknown_count": "无法判断", "not_covered_count": "未覆盖", "error_count": "执行异常"}
    return " · ".join(f"{label} {counts[key]} 项" for key, label in labels.items() if key in counts and (key in {"pass_count", "ng_count"} or counts[key]))


def task_notification_card(task: dict, stage: str, web_url: str, report_package: dict | None = None, *, confirmation_id: str | None = None) -> dict:
    files = task["files"]
    references = [row for row in files if row["selection"] == "selected" and (row.get("match") or {}).get("state") == "reference"]
    targets = [row for row in files if row["selection"] == "selected" and (row.get("match") or {}).get("state") != "reference"]
    reports = [(row.get("audit") or {}).get("report", {}).get("state") for row in targets]
    title = {"confirmation": "规则匹配结果待确认", "started": "审核已开始", "completed": "审核已结束", "zip_ready": "报告 ZIP 已就绪"}[stage]
    archive_package = bool(stage == "zip_ready" and report_package and any(reference.get("task_id", task["id"]) != task["id"] for reference in report_package["reports"]))
    blocks: list[str] = []
    if stage == "confirmation":
        scope = task.get("confirmation_scope")
        excluded = {row["file_version_id"]: row["reason"] for row in scope["excluded_files"]} if scope else {}
        matched = len(scope["target_file_version_ids"]) if scope else sum((row.get("match") or {}).get("state") == "matched" for row in targets)
        blocks.append(f"已匹配 **{matched} 份**，共 {len(targets)} 份。<br>请核对匹配结果，再决定是否开始审核。")
        for row in files:
            match = row.get("match") or {}
            if row["selection"] == "selected" and match.get("state") == "reference":
                continue
            rule = match.get("rule") or {}
            included = row["selection"] == "selected" and match.get("state") == "matched" and rule and row["file"]["id"] not in excluded
            excluded_reason = excluded.get(row["file"]["id"]) or row.get("removal_reason") or ("未选择此文件" if row["selection"] == "unselected" else "未匹配到生效规则")
            detail = "规则：" + rule["title"] if included else "不纳入审核；" + excluded_reason
            if row["selection"] == "selected" and not included and match.get("state") != "unmatched" and row["file"]["id"] not in excluded:
                detail = "匹配尚未确认，请打开页面核对"
            notices = [gap for gap in match.get("gaps") or [] if gap != "metadata_uses_excerpt"]
            blocks.append("**" + _markdown(row["file"]["original_filename"]) + "**<br>" + "<br>".join(_markdown(value) for value in [detail, *notices]))
        if task.get("deep_mode"):
            blocks.append("**深度模式**（点数按 1.5 倍计；未完成深度复核的文件按 500 点计）")
        blocks.append(f"**预计点数：{task.get('estimated_points') if task.get('estimated_points') is not None else '待核对'}**")
    elif stage == "started":
        blocks.append(f"本次审核 **{len(targets)} 份文件**。<br>可离开页面，稍后打开审核查看进度。")
        for row in targets:
            blocks.append("**" + _markdown(row["file"]["original_filename"]) + "**")
        for row in files:
            if row["selection"] == "removed":
                blocks.append("**已移除** · " + _markdown(row["file"]["original_filename"]) + "<br>" + _markdown(row["removal_reason"] or "未纳入审核"))
    elif stage == "zip_ready":
        if not report_package or report_package["state"] != "ready":
            raise ValueError("notification_zip_not_ready")
        blocks.append(f"已打包 **{len(report_package['reports'])} 份报告**。<br>请打开本次 ZIP 核对并下载。")
        names = {row["file"]["id"]: row["file"]["original_filename"] for row in files}
        for reference in report_package["reports"]:
            outcome: dict = next((item for item in report_package.get("outcomes", []) if item["reference"] == reference), {})
            detail = "**" + _markdown(outcome.get("filename") or names[reference["file_version_id"]]) + "**"
            if outcome.get("checker_verification"):
                detail += "<br>" + _markdown(outcome["checker_verification"]["notice"])
            blocks.append(detail)
    else:
        ready, failed = reports.count("ready"), reports.count("failed")
        errors = sum((row.get("audit") or {}).get("execution") == "review_failed" or state == "not_eligible" for row, state in zip(targets, reports, strict=True))
        title = "审核报告已就绪" if ready else "审核已结束，未生成正式报告"
        blocks.append(f"**报告已就绪 {ready} 份**" + (f"<br>报告生成失败 {failed} 份 · 审核异常 {errors} 份" if failed or errors else ""))
        counts = [(row.get("audit") or {})["counts"] for row in targets if isinstance((row.get("audit") or {}).get("counts"), dict)]
        if counts and len(counts) == len(targets):
            blocks.append(_counts_text({key: sum(value[key] for value in counts) for key in counts[0]}))
        if task.get("completion_reason") == "no_matching_rules":
            selected = [row for row in files if row.get("selection") == "selected"]
            reason = "未选择待审文件" if not selected else "仅保留比对资料" if all((row.get("match") or {}).get("state") == "reference" for row in selected) else "没有匹配到生效规则"
            blocks.append(reason + "，未开始审核，未生成正式报告。")
        for row in targets:
            audit = row.get("audit") or {}
            conclusion = {"PASS": "符合", "NG": "不符合"}.get(audit.get("conclusion") or "", "无正式结论")
            color = {"PASS": "green", "NG": "red"}.get(audit.get("conclusion") or "", "neutral")
            detail = f"<text_tag color='{color}'>{conclusion}</text_tag>"
            if audit.get("counts"):
                detail += " " + _counts_text(audit["counts"])
            verification = audit.get("checker_verification")
            if verification:
                detail += "<br>" + _markdown(verification["notice"])
            blocks.append("**" + _markdown(row["file"]["original_filename"]) + "**<br>" + detail)
    if stage in {"completed", "zip_ready"}:
        verifications = (
            [outcome.get("checker_verification") for outcome in (report_package or {}).get("outcomes", [])]
            if stage == "zip_ready"
            else [(row.get("audit") or {}).get("checker_verification") for row in targets]
        )
        unverified = sum(bool(value and value["status"] == "unverified") for value in verifications)
        not_rerun = sum(bool(value and value["samples_not_rerun"]) for value in verifications)
        if unverified:
            blocks[0] += f"<br>**{unverified} 份采用未经验证的检查版本；全部 PASS 不表示版本已通过样本验证。**"
        if not_rerun:
            blocks[0] += f"<br>**{not_rerun} 份在受理时平台已更新，样本未重跑。**"
    if stage != "zip_ready" and references:
        blocks.append("**比对资料**<br>" + "<br>".join(_markdown(row["file"]["original_filename"]) for row in references) + "<br><font color='grey'>已固定版本，不单独审核。</font>")
    params = {"view": "archive"} if archive_package else {"task": task["id"]}
    if not archive_package and (stage == "completed" and "ready" in reports or stage == "zip_ready"):
        params["report"] = "1"
    if stage == "zip_ready" and report_package:
        params["packageRequest"] = report_package["receipt"]["request_id"]
    target = web_url.rstrip("/") + "/audit?" + urlencode(params)
    link = web_url.rstrip("/") + "/enter-current?" + urlencode({"return_to": target})
    body = {"tag": "markdown", "content": "\n\n".join(blocks)}
    opened = {
        "tag": "button",
        "text": {
            "tag": "plain_text",
            "content": {"confirmation": "查看并核对规则", "started": "查看审核进度", "completed": "查看审核报告" if "ready" in reports else "查看审核结果", "zip_ready": "打开本次 ZIP"}[stage],
        },
        "type": "primary_filled",
        "size": "large",
        "width": "fill",
        "behaviors": [{"type": "open_url", "default_url": link}],
    }
    subtitle = "文件档案" if archive_package else task["number"]
    card: dict = {
        "schema": "2.0",
        "config": {"update_multi": True, "summary": {"content": f"{subtitle} · {title}"}},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "padding": "12px 16px 12px 16px",
        },
        "body": {"direction": "vertical", "padding": "16px", "vertical_spacing": "12px", "elements": [body, opened]},
    }
    elements = card["body"]["elements"]
    if (
        stage == "confirmation"
        and confirmation_id
        and matched
        and type(task.get("estimated_points")) is int
        and all((row.get("match") or {}).get("state") == "unmatched" or (row.get("match") or {}).get("state") == "matched" and (row.get("match") or {}).get("rule") for row in targets)
    ):
        elements.insert(
            1,
            {
                "tag": "button",
                "type": "primary_filled",
                "size": "large",
                "width": "fill",
                "text": {"tag": "plain_text", "content": "确认规则并开始审核"},
                "behaviors": [{"type": "callback", "value": {"action": "confirm_audit_task", "notification_id": confirmation_id}}],
                "confirm": {
                    "title": {"tag": "plain_text", "content": "开始本次审核？"},
                    "text": {
                        "tag": "plain_text",
                        "content": f"按卡片所列规则{'以深度模式' if task.get('deep_mode') else ''}审核 {matched} 份文件。未匹配文件不纳入审核。预计点数：{task['estimated_points']}。",
                    },
                },
            },
        )
        opened["type"] = "default"
    while blocks and len(json.dumps(card, ensure_ascii=False).encode()) > 26000:
        elements[1:] = [opened]
        opened["type"] = "primary_filled"
        blocks.pop()
        body["content"] = "\n\n".join([*blocks, "完整文件与原因请打开审核查看。"])
    if len(json.dumps(card, ensure_ascii=False).encode()) > 26000:
        raise ValueError("notification_card_too_large")
    return card


# What each clause waits for, in the order a person should take them up.
_ATTENTION_LABELS = (
    ("question", "要选择"),
    ("review", "要补充标准"),
    ("ready", "可以直接确认"),
    ("failed", "没整理出来"),
    ("waiting", "还没整理"),
    ("running", "还在整理"),
    ("decided", "已处理"),
)


def formalization_notification_card(context: dict, web_url: str) -> dict:
    """One card per saved rule version: how many clauses wait for which decision, and where to make it."""
    counts = context["counts"]
    title = "规则条款整理好了，等你处理"
    lines = [f"**{_markdown(context['title'] or '规则')}** 共 {context['total']} 条条款。"]
    lines.append(" · ".join(f"{label} **{counts[key]}** 条" for key, label in _ATTENTION_LABELS if counts.get(key)))
    lines.append("没处理的条款，审核时交给人判断；拒绝的条款固定为无法判断。都不会判为通过。")
    target = web_url.rstrip("/") + "/requirements?" + urlencode({"requirement": context["root_rule_version_id"], "reqVersion": context["rule_version_id"]})
    link = web_url.rstrip("/") + "/enter-current?" + urlencode({"return_to": target})
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "summary": {"content": title}},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}},
        "body": {
            "direction": "vertical",
            "padding": "16px",
            "vertical_spacing": "12px",
            "elements": [
                {"tag": "markdown", "content": "<br>".join(lines)},
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "去处理"},
                    "type": "primary_filled",
                    "size": "large",
                    "width": "fill",
                    "behaviors": [{"type": "open_url", "default_url": link}],
                },
            ],
        },
    }


def send_task_notification(http, tenant_token: str, notification_id: str, receive_id: str, card: dict) -> dict:
    try:
        response = http.post(
            OPEN_API + "/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={"Authorization": "Bearer " + tenant_token},
            json={"receive_id": receive_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False), "uuid": notification_id},
            timeout=20,
        )
        body = response.json()
        if isinstance(body, dict):
            message_id = (body.get("data") or {}).get("message_id")
            if 200 <= response.status_code < 300 and type(body.get("code")) is int and body["code"] == 0 and isinstance(message_id, str) and message_id.strip():
                return {"state": "sent", "message_id": message_id.strip(), "reason": None}
            if (200 <= response.status_code < 300 or 400 <= response.status_code < 500) and type(body.get("code")) is int and body["code"] != 0:
                return {"state": "failed", "message_id": None, "reason": "feishu_send_rejected"}
    except Exception:
        pass
    return {"state": "unknown", "message_id": None, "reason": "send_receipt_unknown"}
