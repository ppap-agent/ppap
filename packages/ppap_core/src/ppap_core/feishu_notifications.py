"""Task recall cards and one bounded Feishu send. No automatic send retry."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from ppap_core.feishu_drive import OPEN_API

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
        matched = sum((row.get("match") or {}).get("state") == "matched" for row in targets)
        blocks.append(f"已匹配 **{matched} 份**，共 {len(targets)} 份。<br>请核对匹配结果，再决定是否开始审核。")
        blocks.append("**审核模式** · " + {"standard": "标准审核", "deep": "深度审核"}.get(task.get("analysis_mode", "standard"), "待核对"))
        for row in files:
            match = row.get("match") or {}
            if row["selection"] == "selected" and match.get("state") == "reference":
                continue
            rule = match.get("rule") or {}
            included = row["selection"] == "selected" and match.get("state") == "matched" and rule
            excluded_reason = row.get("removal_reason") or ("未选择此文件" if row["selection"] == "unselected" else "未匹配到生效规则")
            detail = "规则：" + rule["title"] if included else "不纳入审核；" + excluded_reason
            if row["selection"] == "selected" and not included and match.get("state") != "unmatched":
                detail = "匹配尚未确认，请打开页面核对"
            notices = [gap for gap in match.get("gaps") or [] if gap != "metadata_uses_excerpt"]
            blocks.append("**" + _markdown(row["file"]["original_filename"]) + "**<br>" + "<br>".join(_markdown(value) for value in [detail, *notices]))
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
            blocks.append("**" + _markdown(outcome.get("filename") or names[reference["file_version_id"]]) + "**")
    else:
        ready, failed = reports.count("ready"), reports.count("failed")
        errors = sum((row.get("audit") or {}).get("execution") == "review_failed" or state == "not_eligible" for row, state in zip(targets, reports, strict=True))
        title = "审核报告已就绪" if ready else "审核已结束，未生成正式报告"
        blocks.append(f"**报告已就绪 {ready} 份**" + (f"<br>报告生成失败 {failed} 份 · 审核异常 {errors} 份" if failed or errors else ""))
        counts = [(row.get("audit") or {})["counts"] for row in targets if isinstance((row.get("audit") or {}).get("counts"), dict)]
        if counts and len(counts) == len(targets):
            blocks.append(_counts_text({key: sum(value[key] for value in counts) for key in counts[0]}))
        if task.get("completion_reason") == "no_matching_rules":
            blocks.append("没有匹配到生效规则，未开始审核，未生成正式报告。")
        for row in targets:
            audit = row.get("audit") or {}
            conclusion = {"PASS": "符合", "NG": "不符合"}.get(audit.get("conclusion") or "", "无正式结论")
            color = {"PASS": "green", "NG": "red"}.get(audit.get("conclusion") or "", "neutral")
            detail = f"<text_tag color='{color}'>{conclusion}</text_tag>"
            if audit.get("counts"):
                detail += " " + _counts_text(audit["counts"])
            blocks.append("**" + _markdown(row["file"]["original_filename"]) + "**<br>" + detail)
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
                    "text": {"tag": "plain_text", "content": f"按卡片所列规则审核 {matched} 份文件。未匹配文件不纳入审核。预计点数：{task['estimated_points']}。"},
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
