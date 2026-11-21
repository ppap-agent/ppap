"""The tools against an in-memory Product API: what a client is told at each point of an audit."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from api.mcp_gateway import Reply
from api.mcp_runtime import TaskRuntime
from api.mcp_tools import register_tools
from fastmcp import Client, FastMCP
from product.mcp import SCOPES, McpError, fingerprint
from product.settings import Settings

TOOLS = {
    "find_submissions",
    "prepare_audit",
    "get_task",
    "get_issue",
    "resolve_issue",
    "confirm_audit",
    "get_results",
    "get_finding",
    "record_review",
    "publish_report",
    "find_audits",
    "get_operation",
    "get_account",
}


def response(status, value):
    return SimpleNamespace(status_code=status, is_success=status < 300, json=lambda: value)


class Product:
    """A Feishu folder, its imports and checks, and the one audit task they become."""

    def __init__(self, *names, failing=()):
        self.folder = [{"token": "tok-" + name, "name": name, "type": "file"} for name in names]
        self.failing = set(failing)
        self.inside = {}  # a subfolder's token -> what it holds
        self.audit = None
        self.imports = 0
        self.approved = False
        self.operations = {}
        self.settings = Settings()

    # -- the gateway's surface --

    async def request(self, actor, method, path, *, body=None, params=None, **_):
        if (method, path) == ("GET", "/integrations/feishu/files"):
            return response(200, {"files": self.inside.get(params["folder_token"], self.folder)})
        if path == "/integrations/feishu/files/import":
            name = body["file_token"].removeprefix("tok-")
            if name in self.failing:
                return response(502, {"error": "feishu_download_failed"})
            self.imports += 1
            return response(201, {"file_id": "file-" + name, "id": "ver-" + name, "status": "ready", "sha256": "sha-" + name, "original_filename": name, "byte_size": 1})
        if (method, path) == ("POST", "/file-validations"):
            return response(201, {"id": "check-" + body["source_file_version_id"]})
        if method == "GET" and path.startswith("/file-validations/"):
            return response(200, {"status": "succeeded", "result": {"state": "valid", "reasons": []}})
        if (method, path) == ("POST", "/audit-tasks"):
            rows = [
                {
                    "file": {"id": f["file_version_id"], "file_id": "file", "original_filename": f["file_version_id"].removeprefix("ver-"), "sha256": "s"},
                    "selection": f["selection"],
                    "removal_reason": f["removal_reason"],
                    "validation_id": f["validation_id"],
                    "validation": {"state": "valid", "reasons": []},
                    "match": {"state": "matched", "rule": {"id": "rule", "title": "控制计划要求"}, "document_type": "控制计划"},
                }
                for f in body["files"]
            ]
            revision = body["expected_revision"] + 1 if "task_id" in body else 0
            selected = [row["file"]["id"] for row in rows if row["selection"] == "selected"]
            self.audit = {
                "id": body.get("task_id") or body["request_id"],
                "number": "A-1",
                "state": "pending_confirm",
                "files": rows,
                "input_revision": revision,
                "customer_preference": body["customer_preference"] or "UAES",
                "deep_mode": False,
                "estimated_points": len(selected),
                "confirmation_basis_digest": "quote-" + fingerprint(selected)[:8],
                "confirmation_scope": {"target_file_version_ids": selected, "reference_file_version_ids": []},
                "creator": {"mine": True},
            }
            return response(202, self.audit)
        if method == "GET" and self.audit and path == "/audit-tasks/" + self.audit["id"]:
            return response(200, self.audit)
        return response(404, {"error": "missing"})

    async def get(self, actor, scope, path, *, params=None):
        found = await self.request(actor, "GET", path, params=params)
        if not found.is_success:
            raise McpError(found.json().get("error", "business_request_failed"), found.status_code)
        return found.json()

    async def write(self, actor, scope, tool, rid, method, path, body, *, probes, confirmation=None, result_path=None):
        self.operations[rid] = {"tool": tool, "state": "ready", "arguments": {"body": body}}
        if confirmation is not None and not self.approved:
            self.operations[rid]["state"] = "confirmation_required"
            return Reply(status="confirmation_required", request_id=rid, confirmation_url="https://ppap.example/mcp/confirm/" + rid)
        if tool == "confirm_audit":
            self.audit["state"] = "reviewing"
        self.operations[rid]["state"] = "succeeded"
        return Reply(status="ok", data={}, request_id=rid)

    @property
    def store(self):
        def operation(actor, rid):
            if rid not in self.operations:
                raise McpError("operation_not_found", 404)
            return self.operations[rid]

        return SimpleNamespace(settings=self.settings, operation=operation)


class Workflows:
    """The durable task rows: created once per request, changed only on the revision that was read."""

    def __init__(self):
        self.rows = {}

    def create(self, principal, task_id, request, state):
        row = self.rows.setdefault(task_id, {"id": task_id, "revision": 0, "state": state, "request": fingerprint(request), "mine": True})
        if row["request"] != fingerprint(request):
            raise McpError("request_conflict", 409)
        return row

    def load(self, principal, task_id):
        return self.rows.get(task_id)

    def save(self, principal, task_id, revision, state):
        row = self.rows[task_id]
        if row["revision"] != revision:
            return False
        row.update(revision=revision + 1, state=state)
        return True

    def by_source(self, principal, source_ref, limit=5):
        return [{"task_id": key} for key, row in self.rows.items() if row["state"]["source"]["token"] == source_ref]


class World:
    def __init__(self, *names, failing=(), scopes=frozenset(SCOPES)):
        self.product = Product(*names, failing=failing)
        self.runtime = TaskRuntime(self.product, Workflows())
        self.runtime.foreground, self.runtime.poll = 2.0, 0.01
        self.scopes = scopes

    def call(self, tool, **arguments):
        def require(scope):
            if scope not in self.scopes:
                raise McpError("insufficient_scope", 403, missing_scope=scope)

        actor = SimpleNamespace(session=SimpleNamespace(principal=SimpleNamespace(user_id="u", tenant_id="t")), scopes=self.scopes, require=require)

        async def authority():
            return actor

        async def run():
            server = FastMCP("tools")
            register_tools(server, self.product, authority, self.runtime)
            async with Client(server) as client:
                if tool == "tools":
                    return {t.name for t in await client.list_tools()}
                return (await client.call_tool(tool, arguments)).structured_content

        return asyncio.run(run())


def test_the_tools_are_commands_and_queries_on_a_task_and_none_moves_bytes():
    assert World().call("tools") == TOOLS


def test_a_folder_link_becomes_a_priced_audit_without_the_client_sequencing_anything():
    world = World("控制计划.xlsx", "PFMEA.xlsx")
    task = str(uuid4())
    out = world.call("prepare_audit", request_id=task, source="https://x.feishu.cn/drive/folder/fld1")
    assert out["task_id"] == task and out["result"]["phase"] == "ready_to_confirm"
    assert [stage["state"] for stage in out["result"]["stages"]] == ["completed"] * 4 + ["pending", "completed"]
    assert out["result"]["files"]["total"] == 2 and out["result"]["quote"]["points"] == 2
    assert out["next"]["kind"] == "user_action" and out["next"]["then"]["tool"] == "confirm_audit"
    # The same request again is the same task, with nothing imported twice; another request under its ID is refused.
    assert world.call("prepare_audit", request_id=task, source="https://x.feishu.cn/drive/folder/fld1")["result"]["phase"] == "ready_to_confirm"
    assert world.product.imports == 2
    assert world.call("prepare_audit", request_id=task, source="https://x.feishu.cn/drive/folder/other")["next"] == {"kind": "blocked", "error_code": "request_conflict"}
    assert world.call("find_submissions", folder="fld1")["result"]["audits_from_this_folder"] == [{"task_id": task}]


def test_a_failed_file_is_an_issue_with_its_own_choices_and_the_rest_is_kept():
    world = World("a.xlsx", "b.xlsx", "c.xlsx", failing={"c.xlsx"})
    task = str(uuid4())
    out = world.call("prepare_audit", request_id=task, source="fld1")
    assert out["result"]["phase"] == "awaiting_decision" and out["result"]["stages"][1] == {"stage": "ingest", "state": "partial", "total": 3, "imported": 2, "failed": 1, "excluded": 0}
    (issue,) = out["issues"]
    assert issue["code"] == "IMPORT_FAILED" and out["next"]["issue_id"] == issue["id"]
    assert world.call("get_issue", task_id=task, issue_id=issue["id"])["result"]["allowed_actions"] == issue["allowed_actions"]

    stale = world.call("resolve_issue", task_id=task, issue_id=issue["id"], action_id="exclude", expected_revision=0, request_id=str(uuid4()))
    assert stale["next"] == {"kind": "call_tool", "tool": "get_task", "arguments": {"task_id": task}, "reason": "stale_revision"}
    refused = world.call("resolve_issue", task_id=task, issue_id=issue["id"], action_id="mark_pass", expected_revision=out["task_revision"], request_id=str(uuid4()))
    assert refused["next"]["reason"] == "action_not_allowed"

    decision = {"task_id": task, "issue_id": issue["id"], "action_id": "exclude", "expected_revision": out["task_revision"], "request_id": str(uuid4())}
    done = world.call("resolve_issue", **decision)
    assert done["result"]["phase"] == "ready_to_confirm" and done["issues"] == [] and done["result"]["quote"]["files_to_audit"] == 2
    assert world.call("resolve_issue", **decision)["result"]["phase"] == "ready_to_confirm"


def test_confirming_takes_the_agreed_quote_and_the_users_own_approval():
    world = World("a.xlsx")
    task = str(uuid4())
    quote = world.call("prepare_audit", request_id=task, source="fld1")["result"]["quote"]["quote_id"]
    assert world.call("confirm_audit", task_id=task, quote_id="an-older-quote", request_id=str(uuid4()))["next"]["reason"] == "quote_changed"

    request = str(uuid4())
    asked = world.call("confirm_audit", task_id=task, quote_id=quote, request_id=request)
    assert asked["next"]["kind"] == "user_action" and asked["next"]["url"].endswith(request)
    assert asked["next"]["then"] == {"tool": "confirm_audit", "arguments": {"task_id": task, "quote_id": quote, "request_id": request, "notify_when_done": None}}
    world.product.approved = True
    started = world.call("confirm_audit", task_id=task, quote_id=quote, request_id=request)
    assert started["result"]["phase"] == "auditing" and started["next"]["kind"] == "wait"
    assert world.call("get_results", task_id=task)["next"]["reason"] == "results_not_ready"


def test_a_folder_changed_since_it_was_prepared_is_put_to_the_user_before_anything_starts():
    world = World("a.xlsx")
    task = str(uuid4())
    quote = world.call("prepare_audit", request_id=task, source="fld1")["result"]["quote"]["quote_id"]
    world.product.folder.append({"token": "tok-late.xlsx", "name": "late.xlsx", "type": "file"})
    assert world.call("confirm_audit", task_id=task, quote_id=quote, request_id=str(uuid4()))["next"]["reason"] == "source_changed"
    seen = world.call("get_task", task_id=task)
    assert seen["result"]["phase"] == "awaiting_decision" and seen["issues"][0]["code"] == "SOURCE_CHANGED"
    choice = {"task_id": task, "issue_id": seen["issues"][0]["id"], "action_id": "refresh", "expected_revision": seen["task_revision"], "request_id": str(uuid4())}
    fresh = world.call("resolve_issue", **choice)
    assert fresh["result"]["phase"] == "ready_to_confirm" and fresh["result"]["quote"]["files_to_audit"] == 2 and fresh["result"]["quote"]["quote_id"] != quote


def test_what_is_not_a_feishu_link_or_not_permitted_is_said_in_words_the_user_can_act_on():
    world = World("a.xlsx")
    refused = world.call("prepare_audit", request_id=str(uuid4()), source="/Users/me/ppap.xlsx")
    assert refused["next"]["error_code"] == "not_a_feishu_link" and "飞书" in refused["next"]["message"]
    limited = World("a.xlsx", scopes=frozenset({"audits:read", "files:read"})).call("prepare_audit", request_id=str(uuid4()), source="fld1")
    assert limited["next"]["error_code"] == "insufficient_scope" and "「从飞书导入文件」" in limited["next"]["message"] and "重新连接" in limited["next"]["message"]
    assert world.call("get_task", task_id=str(uuid4()))["next"]["error_code"] == "task_not_found"


def test_a_submission_is_read_through_its_subfolders_as_one_audit():
    world = World()
    world.product.folder = [{"token": "fld-all", "name": "测试文件", "type": "folder"}]
    world.product.inside = {
        "fld-all": [{"token": "fld-cp", "name": "10 控制计划", "type": "folder"}, {"token": "fld-msa", "name": "06 测量系统分析评价", "type": "folder"}],
        "fld-cp": [{"token": "tok-cp.pdf", "name": "cp.pdf", "type": "file"}],
        "fld-msa": [{"token": "tok-msa.xls", "name": "msa.xls", "type": "file"}, {"token": "tok-kappa.xlsx", "name": "kappa.xlsx", "type": "file"}],
    }
    out = world.call("prepare_audit", request_id=str(uuid4()), source="fld-root")
    assert out["result"]["phase"] == "ready_to_confirm" and out["issues"] == [] and out["result"]["quote"]["files_to_audit"] == 3
    assert {row["filename"]: row["folder"] for row in out["result"]["files"]["items"]} == {
        "cp.pdf": "测试文件/10 控制计划",
        "msa.xls": "测试文件/06 测量系统分析评价",
        "kappa.xlsx": "测试文件/06 测量系统分析评价",
    }


def test_a_task_that_cannot_be_read_is_an_error_not_a_wait():
    world = World("a.xlsx")
    task = str(uuid4())
    assert world.call("prepare_audit", request_id=task, source="fld1")["result"]["phase"] == "ready_to_confirm"
    original = world.product.request

    async def broken(actor, method, path, **more):
        if method == "GET" and path == "/audit-tasks/" + task:
            return response(500, {"error": "internal_error"})
        return await original(actor, method, path, **more)

    world.product.request = broken
    seen = world.call("get_task", task_id=task)
    assert seen["result"] is None and seen["next"]["kind"] == "recover" and seen["next"]["reason"] == "internal_error"
