"""What an approval page shows: the facts the Web shows at the same decision, built from the same Product API reads.

A client of MCP sees PPAP only through what an agent relays; the approval page is the one place the person sees it
first-hand, so it carries what the Web page carries when the same decision is made there. Each builder returns
`(summary, details)`: one or two sentences, then blocks for `mcp_pages.details`. Every value is text from the reads.
"""

from __future__ import annotations

from typing import Any

from audit.models import public_locator
from documents.location import location_label
from product.customers import customer_name, type_names

FINDING = {"PASS": "符合", "NG": "不符合", "UNABLE": "待审核", "NA": "不适用", "ERR": "没有检查完"}
FILE = {"PASS": "符合", "NG": "不符合", "UNKNOWN": "待审核", "ERROR": "没有检查完"}
TASK = {"PASS": "符合", "NG": "不符合", "UNKNOWN": "待审核", "ERROR": "未完成（有检查项没有检查完）"}
MAX_ROWS = 60


def _type(value: str | None) -> str | None:
    """A document type as people read it; an unlisted type is shown as recorded."""
    return type_names().get(value, value) if value else value


def _rows(rows: list[list[Any]]) -> dict:
    return {"rows": rows[:MAX_ROWS], "more": max(0, len(rows) - MAX_ROWS)}


def effect(item: dict) -> str:
    """What a precheck finding does to starting, as the Web says it. Only a file-level block holds the start."""
    if item.get("level") != "block":
        return "警告"
    return "阻断" if item.get("scope") == "file" else "待正式审核核对"


def _precheck_rating(precheck: dict | None) -> str:
    if not precheck:
        return "未执行"
    items = (precheck.get("blocks") or []) + (precheck.get("warnings") or [])
    kinds = [effect(item) for item in items]
    if "阻断" in kinds:
        return f"不通过：{kinds.count('阻断')} 项阻断，移除对应文件后才能开始审核"
    parts = [f"{kinds.count(k)} 项{k}" for k in ("待正式审核核对", "警告") if k in kinds]
    return ("有条件通过 · " + "、".join(parts) + "：可以开始审核") if parts else "通过：可以开始审核"


def audit_start(task: dict, precheck: dict | None, folders: dict[str, str]) -> tuple[list[str], list[dict]]:
    """Before starting an audit: the precheck-and-rules page of the Web."""
    # Folders as the person sees them inside what they shared: a prefix every file shares says nothing.
    parts = [path.split("/") for path in folders.values() if path]
    common = 0
    while parts and all(len(p) > common + 1 for p in parts) and len({p[common] for p in parts}) == 1:
        common += 1
    folders = {key: "/".join(path.split("/")[common:]) if path else path for key, path in folders.items()}
    scope = task.get("confirmation_scope") or {}
    targets = set(scope.get("target_file_version_ids") or [])
    references = set(scope.get("reference_file_version_ids") or [])
    excluded = {row["file_version_id"]: row.get("reason") for row in scope.get("excluded_files") or []}
    flagged: dict[str, list[str]] = {}
    for item in ((precheck or {}).get("blocks") or []) + ((precheck or {}).get("warnings") or []):
        flagged.setdefault(item.get("file_version_id"), []).append(effect(item))

    sent, skipped = [], []
    for row in task.get("files", []):
        version = row["file"]["id"]
        name = row["file"]["original_filename"]
        match = row.get("match") or {}
        metadata = match.get("metadata") or {}
        if version in targets or version in references:
            levels = flagged.get(version) or []
            note = "阻断" if "阻断" in levels else "，".join(f"{k} {levels.count(k)} 项" for k in ("待正式审核核对", "警告") if k in levels) or "通过"
            sent.append(
                [
                    name,
                    folders.get(version),
                    _type(match.get("document_type")),
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
        [item.get("filename"), effect(item), item.get("problem") or item.get("title"), item.get("action")] for item in ((precheck or {}).get("blocks") or []) + ((precheck or {}).get("warnings") or [])
    ]
    mode = "深度模式（逐条复核每条结论，审核时长约增加 2 倍）" if task.get("deep_mode") else "标准模式"
    points = task.get("estimated_points")
    summary = [f"开始审核：送审 {len(targets)} 份文件" + (f"，预计扣 {points} 点。" if points is not None else "。")]
    facts = [
        ("任务", task.get("number")),
        ("审核客户", customer_name(task["customer_preference"]) if task.get("customer_preference") else None),
        ("送审", f"{len(targets)} 份" + (f" · 参考 {len(references)} 份" if references else "") + (f" · 不送审 {len(skipped)} 份" if skipped else "")),
        ("审核方式", mode),
        ("预计点数", f"{points} 点（飞书通用 AI 点数，按 500 点/份 × 送审份数计算）" if points is not None else None),
        ("预检报告", _precheck_rating(precheck)),
    ]
    details: list[dict] = [{"kind": "facts", "rows": facts}]
    details.append({"kind": "table", "title": f"送审的文件 · {len(sent)} 份", "head": ["文件", "所在文件夹", "文件类型", "零件号", "零件名称", "审核规则", "预检"], **_rows(sent)})
    details.append({"kind": "table", "title": f"预检发现 · {len(findings)} 项", "head": ["文件", "级别", "问题", "整改"], **_rows(findings)})
    details.append({"kind": "table", "title": f"不送审的文件 · {len(skipped)} 份", "head": ["文件", "所在文件夹", "原因"], **_rows(skipped)})
    details.append({"kind": "note", "text": "警告不影响开始审核；“待正式审核核对”的问题由正式审核按规则判定。确认后审核在后台进行，进度和结果回到客户端查看；不送审的文件不计费。"})
    return summary, details


def _places(finding: dict) -> list[list[Any]]:
    """Where the evidence is, as the Web lists it: file, place, excerpt."""
    locator = finding.get("locator") or {}
    rows = []
    for source in ((locator.get("semantic") or {}).get("sources")) or []:
        place = public_locator(source.get("locator"))
        rows.append([source.get("filename") or locator.get("filename"), location_label(place) if place else "未提供具体位置", source.get("excerpt") or "—"])
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
                _type((row.get("match") or {}).get("document_type")),
            ]
        )
    conclusion = TASK.get(str(task.get("conclusion")), "—")
    rule_set = customer_name(task["customer_preference"]) if task.get("customer_preference") else None
    summary = [f"在你的飞书里新建一份审核报告文档：任务 {task.get('number')}，结论{conclusion}。"]
    details: list[dict] = [
        {"kind": "facts", "rows": [("任务", task.get("number")), ("审核客户", rule_set), ("结论", conclusion), ("检查项", tally or None)]},
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


# ---- rulebook clarification: what the platform operator approves ----------------------------------------------

CHANGE = {"same": "意思不变", "changed": "意思变了", "new": "新增"}


def _revision_name(revision: dict, number: int | None = None) -> str:
    return f"{revision['version']} 修订 {revision['revision'] if number is None else number}"


def rulebook_clauses(customer: str, version: str, draft: dict) -> tuple[list[str], list[dict]]:
    """Before clauses become revision 0: every clause as it will be frozen."""
    documents = draft["documents"]
    total = sum(len(document["clauses"]) for document in documents)
    summary = [f"把 {len(documents)} 类文件、{total} 条条款确认为 {version} 修订 0。"]
    details: list[dict] = [{"kind": "facts", "rows": [("提交人", draft["by"]), ("版本", f"{version} 修订 0"), ("条款", f"{total} 条")]}]
    for document in documents:
        details.append({"kind": "table", "title": f"{document['title']} · {len(document['clauses'])} 条", "head": ["编号", "原文"], **_rows([[c["id"], c["text"]] for c in document["clauses"]])})
    details.append({"kind": "note", "text": "确认后这一修订冻结，原文不能再改；之后的改动成为新的修订。审核仍用原来的规则，不会因此切换。"})
    return summary, details


def rule_cards(revision: dict, cards: list[dict]) -> tuple[list[str], list[dict]]:
    """Before cards reach the customer: each question, why it is asked and the answers offered."""
    summary = [f"把 {len(cards)} 张问题卡片发给客户成员，在规则页 {_revision_name(revision)} 下作答。"]
    rows = [[card["clauses"][0] if card["clauses"] else None, card["question"], card.get("why"), "；".join(option["label"] for option in card.get("options", [])) or "自己写答案"] for card in cards]
    details: list[dict] = [
        {"kind": "table", "title": f"卡片 · {len(cards)} 张", "head": ["条款", "问题", "为什么问", "可选答案"], **_rows(rows)},
        {"kind": "note", "text": "发送后本企业成员都能看到并作答，卡片内容不再修改。"},
    ]
    return summary, details


def rulebook_revision(revision: dict, proposals: dict[str, dict]) -> tuple[list[str], list[dict]]:
    """Before proposed wording becomes the next revision: each clause before and after, and what it rests on."""
    before = {clause["id"]: clause["text"] for document in revision["documents"] for clause in document["clauses"]}
    summary = [f"把 {len(proposals)} 条条款的新写法确认为 {_revision_name(revision, revision['revision'] + 1)}。"]
    details: list[dict] = []
    for clause, row in proposals.items():
        grounds = "；".join(f"卡片 {source['question_key']}（{'、'.join(source['answerers'])}）" for source in row["sources"])
        details.append({"kind": "facts", "title": clause, "rows": [("改动", CHANGE[row["kind"]]), ("说明", row["note"]), ("依据", grounds), ("提出人", row["by"])]})
        details.append({"kind": "text", "title": "原来", "text": before[clause]})
        details.append({"kind": "text", "title": "改为", "text": row["text"]})
    details.append({"kind": "note", "text": f"确认后新修订冻结，{_revision_name(revision)} 的问卷关闭，答复保留可查。审核仍用原来的规则，不会因此切换。"})
    return summary, details


def rulebook_clarified(revision: dict, progress: dict) -> tuple[list[str], list[dict]]:
    summary = [f"确认 {_revision_name(revision)} 已澄清：每条都已审查，没有未决问题。"]
    cards = progress["cards"]
    details: list[dict] = [
        {
            "kind": "facts",
            "rows": [
                ("条款", f"{progress['clauses']} 条，已审 {progress['reviewed']} 条"),
                ("审查没有发现问题", f"{progress['reviewed_without_findings']} 条"),
                ("已处理的卡片", f"{cards['settled']} 张"),
            ],
        },
        {"kind": "note", "text": "确认后可以为每条条款配解读。已澄清不可撤销；以后发现新问题，改动成为新的修订。"},
    ]
    return summary, details


def rule_readings(revision: dict, readings: dict[str, dict]) -> tuple[list[str], list[dict]]:
    """Before readings are shown beside clauses: each reading in full, next to the clause it reads."""
    texts = {clause["id"]: clause["text"] for document in revision["documents"] for clause in document["clauses"]}
    summary = [f"把 {len(readings)} 条解读发布到规则页 {_revision_name(revision)} 的原文旁。"]
    details: list[dict] = []
    for clause, reading in readings.items():
        details.append({"kind": "text", "title": f"{clause} 原文", "text": texts[clause]})
        rows = [("查什么", "；".join(reading["checks"])), ("通过", "；".join(reading["passes"])), ("不通过", "；".join(reading["fails"])), ("转人工", "；".join(reading["manual"]))]
        details.append({"kind": "facts", "rows": [(label, value or None) for label, value in rows]})
    details.append({"kind": "note", "text": "原文仍是唯一的判定依据；解读发布后不再修改。"})
    return summary, details
