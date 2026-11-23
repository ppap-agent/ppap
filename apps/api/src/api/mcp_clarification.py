"""Runs a rulebook clarification task. Every decision is `product.clarification`'s.

Prepared work (a review, proposed wording, a further question, a reading) is saved on the task and changes nothing
else. What takes effect (clauses becoming a revision, cards reaching the customer, a revision marked clarified,
readings shown beside clauses) is proposed here and completed by a platform operator on the rules page (#348), which
shows the step as `describe` gives it now and makes the one Product API call it names.
"""

from __future__ import annotations

import asyncio
from typing import Any

from product import clarification, workflow
from product.customers import by_key
from product.mcp import Delegation, McpError

from api.mcp_gateway import Reply

READ, WRITE = "rulebooks:read", "rulebooks:write"
# The steps that take effect, each one signing call (#348); everything else a task takes is prepared work.
STEPS = frozenset({"confirm_clauses", "send_cards", "confirm_revision", "confirm_clarified", "confirm_readings"})


class ClarificationRuntime:
    def __init__(self, tasks):
        self.tasks, self.gateway, self.store = tasks, tasks.gateway, tasks.store

    def roles(self, actor: Delegation) -> dict:
        settings, principal = self.gateway.store.settings, actor.session.principal
        confirm = settings.platform_operator(principal.tenant_key, principal.subject)
        return {"confirm": confirm, "prepare": confirm or settings.clause_reviewer(principal.tenant_key, principal.subject)}

    async def open(self, actor: Delegation, customer: str, version: str) -> str:
        actor.require(READ)
        registered = by_key(customer)
        if registered is None:
            raise workflow.Rejected("customer_required")
        if not clarification.rulebooks._VERSION.fullmatch(version):
            raise workflow.Rejected("rulebook_version_required")
        principal = actor.session.principal
        task_id = clarification.task_id_for(principal.tenant_id, registered.id, version)
        existing = await asyncio.to_thread(self.store.load, principal, task_id)
        if existing is None:
            listed = await self.gateway.get(actor, READ, f"/rulebooks/{registered.id}")
            if not any(row["version"] == version for row in listed["revisions"]) and not self.roles(actor)["prepare"]:
                raise workflow.Rejected("rulebook_version_not_found")
            await asyncio.to_thread(self.store.open_shared, principal, task_id, clarification.new_state(registered.id, version))
        return task_id

    async def facts(self, actor: Delegation, state: dict) -> dict:
        customer, version = state["customer"], state["version"]
        facts: dict[str, Any] = {"roles": self.roles(actor), "cards": []}
        listed = await self.gateway.get(actor, READ, f"/rulebooks/{customer}")
        numbers = [row["revision"] for row in listed["revisions"] if row["version"] == version]
        if numbers:
            facts["revision"] = await self.gateway.get(actor, READ, f"/rulebooks/{customer}/{version}/{max(numbers)}")
        asked = await self.gateway.get(actor, READ, "/rule-questionnaires")
        mine = [row for row in asked["questionnaires"] if row["customer_id"] == customer and row["rulebook"]["version"] == version]
        for head, found in zip(mine, await asyncio.gather(*(self.gateway.get(actor, READ, f"/rule-questionnaires/{row['id']}") for row in mine))):
            for question in found["questions"]:
                labels = {option["key"]: option["label"] for option in question.get("options", [])}
                answers = [
                    {"answerer": a["answerer"], "choice": a["choice"], "choice_label": labels.get(a["choice"]), "text": a["text"], "note": a["note"], "answered_at": a["answered_at"]}
                    for a in question["answers"]
                ]
                facts["cards"].append({"card": {k: v for k, v in question.items() if k != "answers"}, "revision": head["rulebook"]["revision"], "closed": head["closed"], "answers": answers})
        return facts

    async def view(self, actor: Delegation, row: dict) -> tuple[dict, dict]:
        actor.require(READ)
        facts = await self.facts(actor, row["state"])
        return facts, clarification.project(row["id"], row["state"], facts)

    async def resolve(self, actor: Delegation, row: dict, issue_id: str, action_id: str, expected_revision: int, request_id: str, value: Any) -> Reply | None:
        """Apply one action of an open issue. None: the task took prepared work; a Reply: a signing step proposed."""
        actor.require(WRITE)
        task_id, state = row["id"], row["state"]
        if request_id in state["decisions"]:
            if state["decisions"][request_id] != [issue_id, action_id]:
                raise workflow.Rejected("request_conflict")
            return None
        replayed = await self.tasks.replay(actor, request_id, "resolve_issue", {"task_id": task_id, "issue_id": issue_id, "action_id": action_id})
        if replayed is not None:
            return replayed
        if expected_revision != row["revision"]:
            raise workflow.Rejected("stale_revision")
        facts = await self.facts(actor, state)
        issue, _ = workflow.find_issue(clarification.issues(task_id, state, facts), issue_id, action_id)
        principal = actor.session.principal
        if action_id in clarification.PREPARE:
            event = clarification.decide(state, facts, issue, action_id, value, principal.display_name or "未署名成员")
            event.update(request_id=request_id, issue_id=issue_id, action_id=action_id)
            if not await asyncio.to_thread(self.store.save_shared, principal, task_id, row["revision"], clarification.apply(state, event)):
                raise workflow.Rejected("stale_revision")
            return None
        step = self.step(task_id, state, facts, action_id)
        request = {"task_id": task_id, "issue_id": issue_id, "action_id": action_id}
        page = f"/requirements?template={state['customer']}"
        return await self.gateway.propose(actor, WRITE, "resolve_issue", request_id, step["method"], step["path"], page=page, request=request, probes=[f"/rulebooks/{state['customer']}"])

    async def describe(self, actor: Delegation, request: dict) -> dict:
        """A proposed step as it stands now: what the page shows and the exact call its confirmation makes. A step the task
        has moved past is gone (`proposal_outdated`); the client proposes the next one."""
        actor.require(READ)
        row = await asyncio.to_thread(self.store.load, actor.session.principal, request["task_id"])
        if row is None or row["state"].get("kind") != "clarification":
            raise McpError("proposal_outdated", 409)
        facts = await self.facts(actor, row["state"])
        try:
            workflow.find_issue(clarification.issues(row["id"], row["state"], facts), request["issue_id"], request["action_id"])
        except workflow.Rejected:
            raise McpError("proposal_outdated", 409) from None
        return self.step(row["id"], row["state"], facts, request["action_id"])

    def step(self, task_id: str, state: dict, facts: dict, action_id: str) -> dict:
        """One signing step from the task's current state: the call, and what it does in the person's words."""
        customer, version = state["customer"], state["version"]
        listing = f"/rulebooks/{customer}"
        if action_id == "confirm_clauses":
            body = {"version": version, "revision": 0, "base": None, "documents": state["draft"]["documents"], "changes": {}, "origin": "clarification"}
            summary, details = rulebook_clauses(customer, version, state["draft"])
            return {"method": "POST", "path": listing + "/revisions", "body": body, "summary": summary, "details": details}
        revision = facts["revision"]
        number = revision["revision"]
        path = f"/rulebooks/{customer}/{version}/{number}"
        if action_id == "send_cards":
            cards = clarification._standing(state, facts, revision)["unsent"]
            body = {"customer_id": customer, "rulebook": {"version": version, "revision": number}, "cards": cards}
            answerers = sorted({answer["answerer"] for row in facts["cards"] for answer in row["answers"]})
            summary, details = rule_cards(revision, cards, answerers)
            return {"method": "POST", "path": "/rule-questionnaires/cards", "body": body, "summary": summary, "details": details}
        if action_id == "confirm_revision":
            summary, details = rulebook_revision(revision, clarification.proposals(state, revision))
            return {"method": "POST", "path": listing + "/revisions", "body": clarification.next_revision(state, revision), "summary": summary, "details": details}
        if action_id == "confirm_clarified":
            asked = [row for row in facts["cards"] if row["revision"] <= number]
            summary, details = rulebook_clarified(revision, clarification.project(task_id, state, facts)["result"]["progress"], asked)
            return {"method": "POST", "path": path + "/clarified", "body": None, "summary": summary, "details": details}
        if action_id == "confirm_readings":
            readings = {clause: entry["reading"] for clause, entry in clarification.pending_readings(state, revision).items()}
            summary, details = rule_readings(revision, readings)
            return {"method": "PUT", "path": path + "/readings", "body": {"readings": readings}, "summary": summary, "details": details}
        raise McpError("unknown_action", 500)


# ---- what a step does, in the person's words ----------------------------------------------------------------------
# `(summary, details)`: one or two sentences, then blocks (facts, table, text, note) the rules page shows as they are.

MAX_ROWS = 60


def _rows(rows: list[list[Any]]) -> dict:
    return {"rows": rows[:MAX_ROWS], "more": max(0, len(rows) - MAX_ROWS)}


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


def rule_cards(revision: dict, cards: list[dict], answerers: list[str]) -> tuple[list[str], list[dict]]:
    """Before cards reach the customer: each question, why it is asked, the answers offered, and who is told."""
    summary = [f"把 {len(cards)} 张问题卡片发给客户成员，在规则页 {_revision_name(revision)} 下作答。"]
    rows = [[card["clauses"][0] if card["clauses"] else None, card["question"], card.get("why"), "；".join(option["label"] for option in card.get("options", [])) or "自己写答案"] for card in cards]
    told = "、".join(answerers) + "（答复过这个版本的问卷）会收到飞书通知" if answerers else "这次没有人会收到通知：还没有人答复过这个版本的问卷。请把规则页链接发给需要作答的人"
    details: list[dict] = [
        {"kind": "facts", "rows": [("通知", told)]},
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


def rulebook_clarified(revision: dict, progress: dict, asked: list[dict]) -> tuple[list[str], list[dict]]:
    """`asked`: every card put to the customer on this version up to this revision, with its answers."""
    summary = [f"确认 {_revision_name(revision)} 已澄清：每条都已审查，没有未决问题。"]
    answered = sum(1 for row in asked if row["answers"])
    details: list[dict] = [
        {
            "kind": "facts",
            "rows": [
                ("条款", f"{progress['clauses']} 条，已审 {progress['reviewed']} 条"),
                ("审查没有发现问题", f"{progress['reviewed_without_findings']} 条"),
                (f"{revision['version']} 问过的卡片", f"{len(asked)} 张，已答复 {answered} 张" if asked else "没有"),
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
