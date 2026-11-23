"""A clarification round through the tools, against the real Product API and database: what the customer sees only
changes when the platform operator completes the step on the rules page."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

from api import create_app
from api.mcp_clarification import ClarificationRuntime
from api.mcp_gateway import Reply
from api.mcp_runtime import TaskRuntime
from api.mcp_tools import register_tools
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.mcp import SCOPES, McpError
from product.settings import Settings
from product.store.workflows import WorkflowStore


class Gateway:
    """The Product API as one signed-in person reaches it; a signing step is only proposed (#348)."""

    def __init__(self, client, settings):
        self.client, self.settings, self.operations = client, settings, {}

    async def request(self, actor, method, path, *, body=None, params=None, **_):
        return self.client.request(method, path, json=body, params=params)

    async def get(self, actor, scope, path, *, params=None):
        found = self.client.get(path, params=params)
        if not found.is_success:
            raise McpError(found.json().get("error", "business_request_failed"), found.status_code)
        return found.json()

    async def propose(self, actor, scope, tool, rid, method, path, *, page, request, probes):
        arguments = {"method": method, "path": path, "page": page, "request": request, "probes": probes}
        recorded = self.operations.setdefault(rid, {"tool": tool, "state": "proposed", "arguments": arguments, "required_scope": scope, "request_id": rid, "outcome": "proposed"})
        return await self.operation_reply(actor, recorded)

    async def operation_reply(self, actor, operation):
        if operation["outcome"] == "succeeded":
            return Reply(status="ok", request_id=operation["request_id"])
        return Reply(status="confirmation_required", request_id=operation["request_id"], confirmation_url="https://ppap.example" + operation["arguments"]["page"])

    @property
    def store(self):
        def operation(actor, rid):
            if rid not in self.operations:
                raise McpError("operation_not_found", 404)
            return self.operations[rid]

        return SimpleNamespace(settings=self.settings, operation=operation)


class Person:
    def __init__(self, store, api, tenant, subject, name):
        self.principal = store.upsert_feishu_identity(tenant, subject, name)
        self.web = TestClient(create_app(store.settings, auth_store=api))
        self.web.cookies.set(SESSION_COOKIE, store.create_session(self.principal))
        self.gateway = Gateway(self.web, store.settings)
        self.runtime = TaskRuntime(self.gateway, WorkflowStore(api))
        self.clarifications = ClarificationRuntime(self.runtime)

    def actor(self):
        return SimpleNamespace(session=SimpleNamespace(principal=self.principal), scopes=frozenset(SCOPES), require=lambda scope: None)

    def call(self, tool, **arguments):
        actor = self.actor()

        async def authority():
            return actor

        async def run():
            server = FastMCP("tools")
            register_tools(server, self.gateway, authority, self.runtime, self.clarifications)
            async with Client(server) as client:
                return (await client.call_tool(tool, arguments)).structured_content

        return asyncio.run(run())

    def resolve(self, task, issue, action, value=None, rid=None):
        return self.call("resolve_issue", task_id=task["task_id"], issue_id=issue["id"], action_id=action, expected_revision=task["task_revision"], request_id=rid or str(uuid4()), value=value)

    def approve(self, task, issue, action):
        """Propose, complete the step on the rules page as it reads now, and call again with the same request."""
        rid = str(uuid4())
        asked = self.resolve(task, issue, action, rid=rid)
        assert asked["next"]["action_id"] == "complete_in_web" and asked["next"]["url"].startswith("https://ppap.example/requirements?template=")
        proposal = self.gateway.operations[rid]
        step = asyncio.run(self.clarifications.describe(self.actor(), proposal["arguments"]["request"]))
        assert (step["method"], step["path"]) == (proposal["arguments"]["method"], proposal["arguments"]["path"]) and step["summary"]
        done = self.web.request(step["method"], step["path"], json=step["body"])
        assert done.is_success, done.text
        proposal["outcome"] = "succeeded"
        return self.resolve(task, issue, action, rid=rid)


def issue_of(task, code):
    return next(issue for issue in task["issues"] if issue["code"] == code)


def review(quote, findings=()):
    return {"schema": "clause-check-review-v1", "requirements": [{"quote": quote, "role": "necessary", "scope": "要求", "meaning": "必须满足。"}], "findings": list(findings)}


FINDING = {
    "id": "1",
    "kind": "ambiguity",
    "quotes": ["已签名"],
    "title": "签字方式",
    "question": "电子签名算已签名吗？",
    "example": "批准人为电子签名。",
    "challenge": "原文未限定签名介质，其余要求在两种理解下均满足。",
    "readings": [
        {"label": "电子签名也算", "meaning": "电子签名构成已签名。", "basis": "原文未限定载体。", "verdict": "通过"},
        {"label": "仅手写算", "meaning": "电子签名不能代替手写。", "basis": "签名通常指手写。", "verdict": "不通过"},
    ],
}


def test_a_round_reaches_the_customer_only_through_the_operators_approval(auth_store_factory, tmp_path):
    rules = tmp_path / "uaes" / "rulebook" / "v9.0" / "m0" / "rules"
    rules.mkdir(parents=True)
    (rules / "A.json").write_text(json.dumps({"title": "A 文件", "rules": [{"id": "A:1", "rule_to_check": "须已签名"}, {"id": "A:2", "rule_to_check": "须盖章"}]}), encoding="utf-8")
    settings = Settings(feishu_app_id="clarify", usage_charge_enabled=False, template_root=str(tmp_path), platform_operators="acme-t:ou_owner", clause_reviewers="acme-t:ou_reviewer")
    store = auth_store_factory(settings)
    api = build_product_store(store.settings, objects=store.objects, role="api")
    owner, reviewer, member = (Person(store, api, "acme-t", subject, name) for subject, name in (("ou_owner", "负责人"), ("ou_reviewer", "审查员"), ("ou_member", "客户工程师")))

    # The versions a client can open are listed under the same customer key the tool takes.
    assert reviewer.call("get_account")["result"]["rulebooks"]["UAES"] == [{"version": "v9.0", "revision": 0, "clause_count": 2, "clarified": False}]

    # The enterprise has one task for this version: the reviewer and the operator see the same one.
    task = reviewer.call("prepare_rule_clarification", customer="UAES", version="v9.0")
    assert task["result"]["phase"] == "reviewing" and task["result"]["progress"]["clauses"] == 2
    assert owner.call("prepare_rule_clarification", customer="UAES", version="v9.0")["task_id"] == task["task_id"]
    assert (
        member.call("resolve_issue", task_id=task["task_id"], issue_id=task["issues"][0]["id"], action_id="submit_review", expected_revision=0, request_id=str(uuid4()), value=review("须已签名"))[
            "next"
        ]["kind"]
        == "call_tool"
    )

    # The issue carries the clause as it reads; a rejected review says everything wrong with it and changes nothing.
    first = reviewer.call("get_issue", task_id=task["task_id"], issue_id=task["next"]["arguments"]["issue_id"])["result"]
    assert first["detail"]["clause"]["text"] == "须已签名"
    refused = reviewer.resolve(task, first, "submit_review", review("须已盖章"))
    assert refused["next"]["error_code"] == "review_rejected" and refused["next"]["problems"]
    task = reviewer.resolve(task, first, "submit_review", review("须已签名", [FINDING]))
    second = next(issue for issue in task["issues"] if issue["code"] == "CLAUSE_REVIEW")
    task = reviewer.resolve(task, second, "submit_review", review("须盖章"))
    assert task["result"]["phase"] == "ready_to_send" and task["next"]["kind"] == "blocked"

    # The reviewer cannot send; until the operator approves, the customer sees no card.
    assert member.web.get("/rule-questionnaires").json()["questionnaires"] == []
    task = owner.call("get_task", task_id=task["task_id"])
    task = owner.approve(task, issue_of(task, "CARDS_READY"), "send_cards")
    assert task["result"]["phase"] == "awaiting_answers" and task["next"]["kind"] == "wait"
    (questionnaire,) = member.web.get("/rule-questionnaires").json()["questionnaires"]
    assert questionnaire["total"] == 1

    answer = {"operation_id": str(uuid4()), "question_key": "A:1#1", "expected_revision": 0, "choice": "A", "text": None, "note": None}
    assert member.web.post(f"/rule-questionnaires/{questionnaire['id']}/answers", json=answer).status_code == 200

    # The answer reopens the clause; the proposed wording names the card and who answered it.
    task = reviewer.call("get_task", task_id=task["task_id"])
    answered = issue_of(task, "CLAUSE_ANSWERED")
    shown = reviewer.call("get_issue", task_id=task["task_id"], issue_id=answered["id"])["result"]["detail"]["cards"]
    assert [(card["key"], card["answers"][0]["answerer"], card["answers"][0]["choice_label"]) for card in shown] == [("A:1#1", "客户工程师", "理解一：电子签名也算")]
    task = reviewer.resolve(task, answered, "propose_text", {"text": "须已签名，手写或电子均可", "kind": "changed", "note": "电子签名也算已签名", "cards": ["A:1#1"]})
    assert task["result"]["phase"] == "ready_to_revise"
    task = owner.call("get_task", task_id=task["task_id"])
    task = owner.approve(task, issue_of(task, "REVISION_READY"), "confirm_revision")

    # Revision 1 exists with its grounds, revision 0 reads as before, and its questionnaire is closed with the answer kept.
    assert task["result"]["revision"] == {"version": "v9.0", "revision": 1, "clarified": False}
    changed = member.web.get("/rulebooks/uaes/v9.0/1").json()["documents"][0]["clauses"][0]
    assert changed["text"] == "须已签名，手写或电子均可" and changed["change"]["sources"] == [{"question_key": "A:1#1", "answerers": ["客户工程师"]}]
    assert member.web.get("/rulebooks/uaes/v9.0/0").json()["documents"][0]["clauses"][0]["text"] == "须已签名"
    closed = member.web.get(f"/rule-questionnaires/{questionnaire['id']}").json()
    assert closed["closed"] and closed["questions"][0]["answers"][0]["choice"] == "A"

    # Only the reworded clause is reviewed again; with nothing open, the operator confirms the revision clarified.
    assert [issue["subject"]["key"] for issue in task["issues"]] == ["A:1"]
    task = reviewer.resolve(reviewer.call("get_task", task_id=task["task_id"]), task["issues"][0], "submit_review", review("须已签名"))
    task = owner.call("get_task", task_id=task["task_id"])
    task = owner.approve(task, issue_of(task, "CLARIFIED_READY"), "confirm_clarified")
    assert task["result"]["revision"]["clarified"] and task["result"]["phase"] == "reading"

    for issue, reading in zip(
        [i for i in task["issues"] if i["code"] == "CLAUSE_READING"],
        [{"checks": ["签名栏"], "passes": ["有签名，手写或电子均可"], "fails": ["没有签名"], "manual": ["签名看不清"]}, {"checks": ["盖章处"], "passes": ["有章"], "fails": ["无章"], "manual": []}],
    ):
        task = reviewer.resolve(reviewer.call("get_task", task_id=task["task_id"]), issue, "submit_reading", reading)
    assert member.web.get("/rulebooks/uaes/v9.0/1").json()["documents"][0]["clauses"][0]["reading"] is None
    task = owner.call("get_task", task_id=task["task_id"])
    task = owner.approve(task, issue_of(task, "READINGS_READY"), "confirm_readings")
    assert task["result"]["phase"] == "completed" and task["next"]["kind"] == "done"
    assert member.web.get("/rulebooks/uaes/v9.0/1").json()["documents"][0]["clauses"][0]["reading"]["passes"] == ["有签名，手写或电子均可"]
