"""Task recall cards and one bounded Feishu send. No automatic send retry."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from ppap_core.feishu_drive import OPEN_API


def task_notification_card(task: dict, stage: str, web_url: str, report_package: dict | None = None, *, confirmation_id: str | None = None) -> dict:
    files = task["files"]
    selected = [row for row in files if row["selection"] == "selected"]
    reports = [(row.get("audit") or {}).get("report", {}).get("state") for row in selected]
    title = {"confirmation": "规则匹配结果待确认", "started": "审核已开始", "completed": "审核已结束", "zip_ready": "报告 ZIP 已就绪"}[stage]
    lines = [task["number"]]
    if stage == "confirmation":
        matched = sum((row.get("match") or {}).get("state") == "matched" for row in selected)
        lines.append(f"已匹配 {matched} 份，共 {len(selected)} 份。请核对匹配结果，再决定是否开始审核。")
        lines.append("审核模式：" + {"standard": "标准审核", "deep": "深度审核"}.get(task.get("analysis_mode", "standard"), "待核对"))
        for row in files:
            match = row.get("match") or {}
            rule = match.get("rule") or {}
            included = row["selection"] == "selected" and match.get("state") == "matched" and rule
            excluded_reason = row.get("removal_reason") or ("未选择此文件" if row["selection"] == "unselected" else "未匹配到生效规则")
            detail = "\n规则：" + rule["title"] if included else "：不纳入审核；" + excluded_reason
            if row["selection"] == "selected" and not included and match.get("state") != "unmatched":
                detail = "：匹配尚未确认，请打开页面核对"
            lines.append(row["file"]["original_filename"] + detail)
            lines.extend(match.get("gaps") or [])
        lines.append(f"预计点数：{task.get('estimated_points') if task.get('estimated_points') is not None else '待核对'}")
    elif stage == "started":
        lines.append(f"本次审核 {len(selected)} 份资料。")
        for row in selected:
            lines.append("审核：" + row["file"]["original_filename"])
        for row in files:
            if row["selection"] == "removed":
                lines.append("已移除：" + row["file"]["original_filename"] + "；" + (row["removal_reason"] or "未纳入审核"))
    elif stage == "zip_ready":
        if not report_package or report_package["state"] != "ready":
            raise ValueError("notification_zip_not_ready")
        lines.append(f"已打包 {len(report_package['reports'])} 份报告。请打开本次 ZIP 核对并下载。")
        names = {row["file"]["id"]: row["file"]["original_filename"] for row in files}
        lines.extend(names[reference["file_version_id"]] for reference in report_package["reports"])
    else:
        ready, failed = reports.count("ready"), reports.count("failed")
        errors = sum((row.get("audit") or {}).get("execution") == "review_failed" or state == "not_eligible" for row, state in zip(selected, reports, strict=True))
        title = "审核报告已就绪" if ready else "审核已结束，未生成正式报告"
        lines.append(f"报告已就绪 {ready} 份，报告生成失败 {failed} 份，审核异常 {errors} 份。")
        if task.get("completion_reason") == "no_matching_rules":
            lines.append("没有匹配到生效规则，未开始审核，未生成正式报告。")
        for row in selected:
            audit = row.get("audit") or {}
            conclusion = {"PASS": "符合", "NG": "不符合"}.get(audit.get("conclusion") or "", "无正式结论")
            lines.append(row["file"]["original_filename"] + "：" + conclusion)
    params = {"task": task["id"]}
    if stage == "completed" and "ready" in reports or stage == "zip_ready":
        params["report"] = "1"
    if stage == "zip_ready" and report_package:
        params["packageRequest"] = report_package["receipt"]["request_id"]
    target = web_url.rstrip("/") + "/audit?" + urlencode(params)
    link = web_url.rstrip("/") + "/enter-current?" + urlencode({"return_to": target})
    body = "\n".join(lines)
    card: dict = {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}},
        "elements": [
            {"tag": "div", "text": {"tag": "plain_text", "content": body}},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看并核对规则" if stage == "confirmation" else "打开本次 ZIP" if stage == "zip_ready" else "打开审核"},
                        "type": "primary",
                        "url": link,
                    }
                ],
            },
        ],
    }
    actions = card["elements"][1]["actions"]
    if (
        stage == "confirmation"
        and confirmation_id
        and matched
        and type(task.get("estimated_points")) is int
        and all((row.get("match") or {}).get("state") == "unmatched" or (row.get("match") or {}).get("state") == "matched" and (row.get("match") or {}).get("rule") for row in selected)
    ):
        actions.insert(
            0,
            {
                "tag": "button",
                "type": "primary",
                "text": {"tag": "plain_text", "content": "确认规则并开始审核"},
                "value": {"action": "confirm_audit_task", "notification_id": confirmation_id},
                "confirm": {
                    "title": {"tag": "plain_text", "content": "开始本次审核？"},
                    "text": {"tag": "plain_text", "content": f"按卡片所列规则审核 {matched} 份文件。未匹配文件不纳入审核。预计点数：{task['estimated_points']}。"},
                },
            },
        )
        actions[-1]["type"] = "default"
    while body and len(json.dumps(card, ensure_ascii=False).encode()) > 26000:
        actions[:] = [action for action in actions if "value" not in action]
        body = body[: len(body) * 3 // 4]
        card["elements"][0]["text"]["content"] = body + "\n完整文件与原因请打开审核查看。"
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
        if isinstance(body, dict) and 200 <= response.status_code < 300:
            message_id = (body.get("data") or {}).get("message_id")
            if type(body.get("code")) is int and body["code"] == 0 and isinstance(message_id, str) and message_id.strip():
                return {"state": "sent", "message_id": message_id.strip(), "reason": None}
            if type(body.get("code")) is int and body["code"] != 0:
                return {"state": "failed", "message_id": None, "reason": "feishu_send_rejected"}
    except Exception:
        pass
    return {"state": "unknown", "message_id": None, "reason": "send_receipt_unknown"}
