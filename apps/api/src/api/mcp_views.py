"""What an approval page shows: the facts the Web shows at the same decision, built from the same Product API reads.

A client of MCP sees PPAP only through what an agent relays; the approval page is the one place the person sees it
first-hand, so it carries what the Web page carries when the same decision is made there. Each builder returns
`(summary, details)`: one or two sentences, then blocks for `mcp_pages.details`. Every value is text from the reads.
"""

from __future__ import annotations

from typing import Any

FINDING = {"PASS": "符合", "NG": "不符合", "UNABLE": "待审核", "NA": "不适用", "ERR": "没有检查完"}
FILE = {"PASS": "符合", "NG": "不符合", "UNKNOWN": "待审核", "ERROR": "没有检查完"}
TASK = {"PASS": "符合", "NG": "不符合", "UNKNOWN": "待审核", "ERROR": "未完成（有检查项没有检查完）"}
MAX_ROWS = 60


def _rows(rows: list[list[Any]]) -> dict:
    return {"rows": rows[:MAX_ROWS], "more": max(0, len(rows) - MAX_ROWS)}


def _precheck_rating(precheck: dict | None) -> str:
    if not precheck:
        return "未执行"
    blocks, warnings = precheck.get("blocks") or [], precheck.get("warnings") or []
    if blocks:
        return f"不通过：存在阻断项 {len(blocks)} 项"
    if warnings:
        return f"有条件通过 · 含警告 {len(warnings)} 项"
    return "通过"


def audit_start(task: dict, precheck: dict | None, folders: dict[str, str]) -> tuple[list[str], list[dict]]:
    """Before starting an audit: the precheck-and-rules page of the Web."""
    scope = task.get("confirmation_scope") or {}
    targets = set(scope.get("target_file_version_ids") or [])
    references = set(scope.get("reference_file_version_ids") or [])
    excluded = {row["file_version_id"]: row.get("reason") for row in scope.get("excluded_files") or []}
    flagged: dict[str, list[str]] = {}
    for item in (precheck or {}).get("blocks") or []:
        flagged.setdefault(item.get("file_version_id"), []).append("阻断")
    for item in (precheck or {}).get("warnings") or []:
        flagged.setdefault(item.get("file_version_id"), []).append("警告")

    sent, skipped = [], []
    for row in task.get("files", []):
        version = row["file"]["id"]
        name = row["file"]["original_filename"]
        match = row.get("match") or {}
        metadata = match.get("metadata") or {}
        if version in targets or version in references:
            levels = flagged.get(version) or []
            note = "需要处理" if "阻断" in levels else (f"警告 {levels.count('警告')} 项" if levels else "通过")
            sent.append(
                [
                    name,
                    folders.get(version),
                    match.get("document_type"),
                    metadata.get("part_number"),
                    metadata.get("part"),
                    ((match.get("rule") or {}).get("title") or "") + ("（参考，不审核）" if version in references else ""),
                    note,
                ]
            )
        else:
            reason = excluded.get(version) or row.get("removal_reason") or ("没有对应的审核要求" if match.get("state") == "unmatched" else "不送审")
            skipped.append([name, folders.get(version), reason])

    findings = [
        [item.get("filename"), "阻断" if item in ((precheck or {}).get("blocks") or []) else "提醒", item.get("problem") or item.get("title"), item.get("action")]
        for item in ((precheck or {}).get("blocks") or []) + ((precheck or {}).get("warnings") or [])
    ]
    mode = "深度模式（逐条复核每条结论，审核时长约增加 2 倍）" if task.get("deep_mode") else "标准模式"
    points = task.get("estimated_points")
    summary = [f"开始审核：送审 {len(targets)} 份文件" + (f"，预计扣 {points} 点。" if points is not None else "。")]
    facts = [
        ("任务", task.get("number")),
        ("客户", task.get("customer_preference") or "按文件自动识别"),
        ("送审", f"{len(targets)} 份" + (f" · 参考 {len(references)} 份" if references else "") + (f" · 不送审 {len(skipped)} 份" if skipped else "")),
        ("审核方式", mode),
        ("预计点数", f"{points} 点（飞书通用 AI 点数，按 500 点/份 × 送审份数计算）" if points is not None else None),
        ("预检报告", _precheck_rating(precheck)),
    ]
    details: list[dict] = [{"kind": "facts", "rows": facts}]
    details.append({"kind": "table", "title": f"送审的文件 · {len(sent)} 份", "head": ["文件", "所在文件夹", "文件类型", "零件号", "零件名称", "审核规则", "预检"], **_rows(sent)})
    details.append({"kind": "table", "title": f"预检发现 · {len(findings)} 项", "head": ["文件", "级别", "问题", "整改"], **_rows(findings)})
    details.append({"kind": "table", "title": f"不送审的文件 · {len(skipped)} 份", "head": ["文件", "所在文件夹", "原因"], **_rows(skipped)})
    details.append({"kind": "note", "text": "预检提醒不阻止开始审核，正式审核会再核对。确认后审核在后台进行，进度和结果回到客户端查看；不送审的文件不计费。"})
    return summary, details


def _places(finding: dict) -> list[list[Any]]:
    """Where the evidence is, as the Web lists it: file, place, excerpt."""
    locator = finding.get("locator") or {}
    rows = []
    for source in ((locator.get("semantic") or {}).get("sources")) or []:
        place = source.get("locator") or {}
        where = f"第 {place['page']} 页" if place.get("page") else f"{place.get('sheet')}!{place.get('cell')}" if place.get("cell") else source.get("location_gap") or "未提供具体位置"
        rows.append([source.get("filename") or locator.get("filename"), where, source.get("excerpt") or "—"])
    return rows


def review(finding: dict, body: dict) -> tuple[list[str], list[dict]]:
    """Before saving a human review: the clause detail the Web shows around its review form."""
    name = (finding.get("locator") or {}).get("filename") or "这份文件"
    machine = FINDING.get(str(finding.get("status")), str(finding.get("status") or "—"))
    summary = [f"对「{name}」的一条检查项记录人工复核：{body['judgment']}。"]
    details: list[dict] = [
        {"kind": "facts", "rows": [("文件", name), ("分类", finding.get("classification")), ("优先级", finding.get("priority")), ("机器结论", machine)]},
        {"kind": "text", "title": "审核规则原文", "text": finding.get("item")},
        {"kind": "text", "title": "判定理由", "text": finding.get("suggestions")},
        {"kind": "table", "title": "证据", "head": ["文件", "位置", "摘录"], **_rows(_places(finding))},
    ]
    prior = finding.get("review")
    if prior:
        details.append(
            {
                "kind": "facts",
                "title": f"现有人工复核（第 {prior.get('version')} 次，将被这次替换）",
                "rows": [("人工判断", prior.get("judgment")), ("判断理由", prior.get("rationale")), ("后续建议", prior.get("next_step"))],
            }
        )
    cites = [[c.get("file_version_id"), c.get("location"), c.get("excerpt")] for c in body.get("citations") or []]
    details.append({"kind": "facts", "title": "这次的人工复核", "rows": [("人工判断", body["judgment"]), ("判断理由", body["rationale"]), ("后续建议", body.get("next_step") or None)]})
    details.append({"kind": "table", "title": "原件引用", "head": ["文件", "位置", "摘录"], **_rows(cites)})
    details.append({"kind": "note", "text": "记录人工判断，不改变机器结果或正式报告资格。复核与机器结论并列保存。"})
    return summary, details


def report(task: dict, round_: dict) -> tuple[list[str], list[dict]]:
    """Before creating the Feishu report document: what the document will say."""
    counts: dict[str, int] = {}
    for finding in round_.get("findings", []):
        counts[finding["status"]] = counts.get(finding["status"], 0) + 1
    tally = " · ".join(f"{FINDING[key]} {counts[key]}" for key in ("NG", "PASS", "UNABLE", "NA", "ERR") if counts.get(key))
    files = []
    for row in task.get("files", []):
        audit = row.get("audit") or {}
        if not audit:
            continue
        numbers = audit.get("counts") or {}
        files.append(
            [
                row["file"]["original_filename"],
                FILE.get(str(audit.get("confirmed_conclusion") or audit.get("conclusion")), "—"),
                numbers.get("ng_count"),
                numbers.get("pass_count"),
                (row.get("match") or {}).get("document_type"),
            ]
        )
    conclusion = TASK.get(str(task.get("conclusion")), "—")
    summary = [f"在你的飞书里新建一份审核报告文档：任务 {task.get('number')}，结论{conclusion}。"]
    details: list[dict] = [
        {"kind": "facts", "rows": [("任务", task.get("number")), ("客户", task.get("customer_preference")), ("结论", conclusion), ("检查项", tally or None)]},
        {"kind": "table", "title": f"文件 · {len(files)} 份", "head": ["文件", "结论", "不符合", "符合", "文件类型"], **_rows(files)},
        {"kind": "note", "text": "文档以你的身份新建在你的飞书云盘，内容是这一轮的审核结论与检查项，包括已记录的人工复核。"},
    ]
    return summary, details


def answer(question: dict, filename: str | None, value: Any) -> tuple[list[str], list[dict]]:
    shown = "是" if value is True else "否" if value is False else value
    summary = [f"确认原件事实：{question.get('prompt')}", f"回答：{shown}"]
    details: list[dict] = [
        {"kind": "facts", "rows": [("文件", filename), ("问题", question.get("prompt")), ("你的回答", shown)]},
        {"kind": "note", "text": "回答只用于本轮所选文件版本：依赖它的检查项按规则重新计算，机器结果与你的确认分别保存，不修改规则。"},
    ]
    return summary, details
