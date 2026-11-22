"""Runs a rulebook clarification task. Every decision is `product.clarification`'s.

Prepared work (a review, proposed wording, a further question, a reading) is saved on the task and changes nothing
else. What takes effect (clauses becoming a revision, cards reaching the customer, a revision marked clarified,
readings shown beside clauses) is one approved call to the Product API the Web uses, by a platform operator.
"""

from __future__ import annotations

import asyncio
from typing import Any

from product import clarification, workflow
from product.customers import by_key
from product.mcp import Delegation, McpError

from api import mcp_views as views
from api.mcp_gateway import Reply

READ, WRITE = "rulebooks:read", "rulebooks:write"


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
        """Apply one action of an open issue. None: the task took prepared work; a Reply: an approved call's receipt."""
        actor.require(WRITE)
        task_id, state = row["id"], row["state"]
        if request_id in state["decisions"]:
            if state["decisions"][request_id] != [issue_id, action_id]:
                raise workflow.Rejected("request_conflict")
            return None
        prior = await self.tasks.recorded(actor, request_id)
        if prior is not None and prior["state"] not in {"confirmation_required", "ready"}:
            return await self.gateway.operation_reply(actor, prior)
        replayed = await self.tasks.replay(actor, request_id, "resolve_issue")
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
        customer, version = state["customer"], state["version"]
        listing = f"/rulebooks/{customer}"
        if action_id == "confirm_clauses":
            body = {"version": version, "revision": 0, "base": None, "documents": state["draft"]["documents"], "changes": {}, "origin": "clarification"}
            summary, details = views.rulebook_clauses(customer, version, state["draft"])
            return await self._approved(actor, request_id, "POST", listing + "/revisions", body, listing, summary, details)
        revision = facts["revision"]
        number = revision["revision"]
        path = f"/rulebooks/{customer}/{version}/{number}"
        if action_id == "send_cards":
            cards = clarification._standing(state, facts, revision)["unsent"]
            body = {"customer_id": customer, "rulebook": {"version": version, "revision": revision["revision"]}, "cards": cards}
            summary, details = views.rule_cards(revision, cards)
            return await self._approved(actor, request_id, "POST", "/rule-questionnaires/cards", body, path, summary, details)
        if action_id == "confirm_revision":
            body = clarification.next_revision(state, revision)
            summary, details = views.rulebook_revision(revision, clarification.proposals(state, revision))
            return await self._approved(actor, request_id, "POST", listing + "/revisions", body, path, summary, details)
        if action_id == "confirm_clarified":
            asked = [row for row in facts["cards"] if row["revision"] <= number]
            summary, details = views.rulebook_clarified(revision, clarification.project(task_id, state, facts)["result"]["progress"], asked)
            return await self._approved(actor, request_id, "POST", path + "/clarified", None, path, summary, details)
        if action_id == "confirm_readings":
            readings = {clause: entry["reading"] for clause, entry in clarification.pending_readings(state, revision).items()}
            summary, details = views.rule_readings(revision, readings)
            return await self._approved(actor, request_id, "PUT", path + "/readings", {"readings": readings}, path, summary, details)
        raise McpError("unknown_action", 500)

    async def _approved(self, actor, request_id: str, method: str, path: str, body, probe: str, summary: list[str], details: list[dict]) -> Reply:
        """One call that takes effect only after the person approves, in the browser, exactly what it will do."""
        source = await self.gateway.get(actor, READ, probe)
        snapshot = {"operation": "resolve_issue", "summary": summary, "details": details, "request": body, "sources": [source]}
        return await self.gateway.write(actor, WRITE, "resolve_issue", request_id, method, path, body, probes=[probe], confirmation=snapshot)
