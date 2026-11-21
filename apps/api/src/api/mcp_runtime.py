"""Runs an audit task's effects and saves what they did. Every decision is `product.workflow`'s.

The runtime reads the folder, imports files, schedules checks and creates or revises the audit task, each through the
same authorized Product API the Web uses and each under a request ID derived from the task, so an effect interrupted
at any point is simply run again. Nothing about a task lives in this process: a restart, a second caller or a client
that went away all continue from the saved state.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from product import workflow
from product.mcp import Delegation, McpError

from api import mcp_views as views
from api.mcp_gateway import Reply

log = logging.getLogger(__name__)
MAX_FILES = 200
MAX_DEPTH = 5  # subfolder levels below the folder the user gave
MAX_FOLDERS = 100
_TRANSIENT = {429, 502, 503, 504}


def check_state(job: dict | None) -> dict:
    """A readability check as the workflow reads it."""
    if not job or job.get("status") not in {"succeeded", "failed", "cancelled"}:
        return {"state": "pending", "reasons": []}
    result = job.get("result") or {}
    if job["status"] == "succeeded" and result.get("state") in {"valid", "invalid"}:
        return {"state": result["state"], "reasons": result.get("reasons") or []}
    return {"state": "error", "reasons": []}


class TaskRuntime:
    foreground = 8.0  # how long one tool call waits for the task to settle before answering "wait"
    background = 300.0  # how long a task keeps advancing after the call that started it
    poll = 2.0

    def __init__(self, gateway, workflows):
        self.gateway, self.store = gateway, workflows
        self.drivers: dict[str, asyncio.Task] = {}

    # ---- reading ---------------------------------------------------------------------------------

    async def api(self, actor: Delegation, method: str, path: str, body=None, params=None) -> tuple[int, Any]:
        response = await self.gateway.request(actor, method, path, body=body, params=params)
        try:
            value = response.json()
        except ValueError:
            value = {}
        return response.status_code, value

    async def read(self, actor, path, params=None):
        """A fact that may not exist yet: absent, not an error. A read that failed is an error, never "not yet":
        a task that cannot be read must not look like one that is still working."""
        status, value = await self.api(actor, "GET", path, params=params)
        if status >= 500 or status in {401, 429}:
            raise McpError(value.get("error", "service_unavailable") if isinstance(value, dict) else "service_unavailable", 503 if status != 401 else 401)
        return value if status == 200 else None

    async def load(self, actor: Delegation, task_id: str) -> dict:
        principal = actor.session.principal
        row = await asyncio.to_thread(self.store.load, principal, task_id)
        if row is not None:
            return row
        # An audit started elsewhere (the Web, a Feishu add-on) is a task too: it has no folder to prepare from,
        # and everything after confirmation works the same.
        audit = await self.read(actor, f"/audit-tasks/{task_id}")
        if audit is None:
            raise McpError("task_not_found", 404)
        state = workflow.new_state({"kind": "task", "token": task_id}, {})
        state.update(manifest=[], audit=True, external=True)
        return {"id": task_id, "revision": 0, "state": state, "mine": bool((audit.get("creator") or {}).get("mine"))}

    async def facts(self, actor: Delegation, task_id: str, state: dict) -> dict:
        facts: dict[str, Any] = {"validations": {}}
        audit = await self.read(actor, f"/audit-tasks/{task_id}") if state["audit"] else None
        if audit:
            facts["audit"] = audit
            for row in audit.get("files", []):
                if row.get("validation_id"):
                    facts["validations"][row["validation_id"]] = row.get("validation") or {"state": "pending", "reasons": []}
        missing = [e["validation_id"] for e in state["files"].values() if e["validation_id"] and not e["excluded"] and e["validation_id"] not in facts["validations"]]
        for ident, job in zip(missing, await asyncio.gather(*(self.read(actor, f"/file-validations/{ident}") for ident in missing))):
            facts["validations"][ident] = check_state(job)
        if audit and audit.get("state") == "pending_confirm":
            facts["precheck"] = await self.read(actor, f"/audit-tasks/{task_id}/precheck")
            facts["estimate"] = await self.read(actor, "/audit-duration-estimate")
            if not audit.get("customer_preference"):
                facts["customers"] = await self.read(actor, "/audit-customers")
        if audit and workflow.audited(audit):
            facts["questions"] = await self.read(actor, f"/audit-rounds/{audit['round_id']}/fact-answers")
            found = await self.read(actor, f"/rounds/{audit['round_id']}") or {}
            facts["hints"] = [{"item_key": row["item_key"], "item": row.get("item"), "hint": row["review_hint"]} for row in found.get("findings", []) if row.get("review_hint")]
        return facts

    async def view(self, actor: Delegation, task_id: str) -> tuple[dict, dict, dict]:
        row = await self.load(actor, task_id)
        facts = await self.facts(actor, task_id, row["state"])
        return row, facts, workflow.project(task_id, row["state"], facts)

    # ---- advancing -------------------------------------------------------------------------------

    async def settle(self, actor: Delegation, task_id: str) -> None:
        """Let the task advance, and wait a moment so a small folder is ready in the same call."""
        driver = self.drivers.get(task_id)
        if driver is None or driver.done():
            driver = self.drivers[task_id] = asyncio.create_task(self._drive(actor, task_id))

            def forget(done, key=task_id) -> None:
                if self.drivers.get(key) is done:
                    del self.drivers[key]

            driver.add_done_callback(forget)
        await asyncio.wait({driver}, timeout=self.foreground)

    async def _drive(self, actor: Delegation, task_id: str) -> None:
        deadline = time.monotonic() + self.background
        try:
            while time.monotonic() < deadline:
                row = await self.load(actor, task_id)
                state = row["state"]
                if not row["mine"] or state.get("external"):
                    return
                facts = await self.facts(actor, task_id, state)
                todo = workflow.effects(state, facts)
                if not todo:
                    if workflow.project(task_id, state, facts)["phase"] != "preparing":
                        return
                    await asyncio.sleep(self.poll)  # the worker is checking or recognising files
                    continue
                gate = asyncio.Semaphore(4)

                async def one(effect):
                    async with gate:
                        return await self.effect(actor, task_id, state, effect)

                events = [event for event in await asyncio.gather(*(one(effect) for effect in todo)) if event]
                if events:
                    await self.commit(actor, task_id, events)
                else:
                    await asyncio.sleep(self.poll)
        except McpError as exc:
            log.warning("mcp_task_stopped task_id=%s code=%s", task_id, exc.code)
        except Exception:
            log.exception("mcp_task_stopped task_id=%s", task_id)

    async def commit(self, actor: Delegation, task_id: str, events: list[dict]) -> dict:
        principal = actor.session.principal
        for _ in range(8):
            row = await asyncio.to_thread(self.store.load, principal, task_id)
            if row is None:
                raise McpError("task_not_found", 404)
            state = row["state"]
            for event in events:
                state = workflow.apply(state, event)
            if await asyncio.to_thread(self.store.save, principal, task_id, row["revision"], state):
                return state
        raise McpError("task_busy", 503)

    async def effect(self, actor: Delegation, task_id: str, state: dict, effect: dict) -> dict | None:
        """Run one effect; the event it returns is the fact to remember. None: nothing new yet."""
        kind = effect["type"]
        if kind == "discover":
            try:
                return {"type": "discovered", "files": await self.listing(actor, state["source"])}
            except McpError as exc:
                return {"type": "discover_failed", "code": exc.code}
        if kind == "import":
            token = effect["token"]
            body = {"request_id": workflow.step_id(task_id, "import:" + token), "file_token": token, "doc_type": effect["doc_type"]}
            status, value = await self.api(actor, "POST", "/integrations/feishu/files/import", body)
            if status in _TRANSIENT:
                await asyncio.sleep(self.poll)
                status, value = await self.api(actor, "POST", "/integrations/feishu/files/import", body)
            if status >= 300:
                return {"type": "import_failed", "token": token, "code": value.get("error", "import_failed")}
            if value.get("status") != "ready":
                return None  # another caller is importing this same file
            return {"type": "imported", "token": token, "version": {key: value.get(key) for key in ("file_id", "id", "sha256", "original_filename", "byte_size")}}
        if kind == "check":
            version = effect["version"]
            body = {"request_id": workflow.step_id(task_id, "check:" + version["id"]), "source_file_id": version["file_id"], "source_file_version_id": version["id"]}
            status, value = await self.api(actor, "POST", "/file-validations", body)
            return {"type": "check_scheduled", "token": effect["token"], "validation_id": value["id"]} if status < 300 else None
        if kind == "create_audit":
            body = {"request_id": task_id, "customer_preference": effect["customer_preference"], "files": effect["files"]}
            if effect.get("predecessor_id"):
                body["predecessor_id"] = effect["predecessor_id"]
            status, value = await self.api(actor, "POST", "/audit-tasks", body)
            return {"type": "audit_created"} if status < 300 else None if status in _TRANSIENT else {"type": "audit_failed", "code": value.get("error", "audit_not_created")}
        if kind == "revise_audit":
            body = {
                "request_id": workflow.step_id(task_id, f"revise:{effect['expected_revision']}"),
                "task_id": task_id,
                "expected_revision": effect["expected_revision"],
                "customer_preference": effect["customer_preference"],
                "files": effect["files"],
            }
            status, value = await self.api(actor, "POST", "/audit-tasks", body)
            # A revision that lost a race is decided again from the task as it now is.
            if status < 300 or status in _TRANSIENT or value.get("error") in {"task_revision_conflict", "idempotency_conflict"}:
                return None
            return {"type": "audit_failed", "code": value.get("error", "audit_not_revised")}
        if kind == "set_deep_mode":
            await self.api(actor, "PUT", f"/audit-tasks/{task_id}/deep-mode", {"deep_mode": effect["deep_mode"], "expected_revision": effect["expected_revision"]})
            return None
        raise McpError("unknown_effect", 500)

    async def listing(self, actor: Delegation, source: dict) -> list[dict]:
        """Every file under the folder, through its subfolders, or the one file the link names. Each file carries the
        folder path it sits in. A folder too deep to enter is returned as a folder row, so it is reported, not dropped."""
        if source["kind"] == "file":
            return [{"token": source["token"], "type": source["doc_type"], "name": source.get("name") or "飞书文件"}]
        found: list[dict] = []
        pending, seen = [(source["token"], "", 0)], {source["token"]}
        while pending:
            token, path, depth = pending.pop(0)
            page = ""
            for _ in range(20):
                status, value = await self.api(actor, "GET", "/integrations/feishu/files", params={"folder_token": token, "page_token": page})
                if status != 200:
                    raise McpError(value.get("error", "feishu_folder_failed"), status)
                for row in value.get("files", []):
                    if row["type"] != "folder":
                        found.append({**row, "folder": path})
                    elif row["token"] not in seen and depth < MAX_DEPTH and len(seen) < MAX_FOLDERS:
                        seen.add(row["token"])
                        pending.append((row["token"], f"{path}/{row['name']}" if path else row["name"], depth + 1))
                    elif row["token"] not in seen:
                        found.append({**row, "folder": path})
                page = value.get("next_page_token") or ""
                if not page:
                    break
            if page or sum(1 for row in found if row["type"] in workflow.IMPORTABLE) > MAX_FILES:
                raise McpError("too_many_files", 413)
        return found

    # ---- commands --------------------------------------------------------------------------------

    async def prepare(self, actor: Delegation, task_id: str, source: dict, options: dict) -> None:
        request = {"source": source, "options": options}
        if options.get("follows_task_id"):
            previous = await self.read(actor, f"/audit-tasks/{options['follows_task_id']}")
            if previous is None or not previous.get("round_id") or previous.get("state") != "completed":
                raise workflow.Rejected("previous_audit_not_finished")
            earlier = await asyncio.to_thread(self.store.load, actor.session.principal, options["follows_task_id"])
            replaces = {token: entry["version"]["id"] for token, entry in ((earlier or {}).get("state", {}).get("files") or {}).items() if entry.get("version") and not entry.get("excluded")}
            in_round = {row["file"]["id"] for row in previous.get("files", []) if row.get("selection") == "selected"}
            options = {**options, "follows_round_id": previous["round_id"], "replaces": {token: version for token, version in replaces.items() if version in in_round}}
        await asyncio.to_thread(self.store.create, actor.session.principal, task_id, request, workflow.new_state(source, options))

    async def recorded(self, actor: Delegation, request_id: str) -> dict | None:
        try:
            return await asyncio.to_thread(self.gateway.store.operation, actor, request_id)
        except McpError as exc:
            if exc.code != "operation_not_found":
                raise
            return None

    async def replay(self, actor: Delegation, request_id: str, tool: str) -> Reply | None:
        """An approval-bound write called again carries what the person approved, as it was recorded. Values the
        tool reads (a task, a round, a revision) move on their own; re-reading them would void every approval.
        Whether the approved content still holds is checked where it takes effect: the quote by the audit task,
        reviews, answers and reports against their approved sources. None: nothing recorded to replay."""
        prior = await self.recorded(actor, request_id)
        if prior is None or prior["tool"] != tool or prior["confirmation"] is None or prior["state"] not in {"confirmation_required", "ready"}:
            return None
        a = prior["arguments"]
        return await self.gateway.write(actor, prior["required_scope"], tool, request_id, a["method"], a["path"], a["body"], probes=a["probes"], confirmation=prior["confirmation"])

    async def source_changed(self, actor: Delegation, task_id: str, state: dict) -> bool:
        """The folder as it is now against the folder as prepared. A difference is the user's decision, not ours."""
        if state.get("external") or state["source"]["kind"] != "folder" or state["manifest"] is None:
            return False
        files = await self.listing(actor, state["source"])
        digest = workflow.listing_digest(files)
        if digest == state.get("listing"):
            return False
        known = set(state["files"]) | {row["token"] for row in state["skipped"]}
        now = {row["token"] for row in files}
        await self.commit(actor, task_id, [{"type": "source_changed", "listing": digest, "added": sorted(now - known), "removed": sorted(known - now)}])
        return True

    async def confirm(self, actor: Delegation, task_id: str, quote_id: str, request_id: str, notify_when_done: bool | None) -> Reply:
        path = f"/audit-tasks/{task_id}"
        replayed = await self.replay(actor, request_id, "confirm_audit")
        if replayed is not None:
            return replayed
        else:
            row, facts, view = await self.view(actor, task_id)
            if view["phase"] != "ready_to_confirm":
                raise workflow.Rejected("not_ready_to_confirm")
            if await self.source_changed(actor, task_id, row["state"]):
                raise workflow.Rejected("source_changed")
            if view["result"]["quote"]["quote_id"] != quote_id:
                raise workflow.Rejected("quote_changed")
            audit = facts["audit"]
            if notify_when_done is not None and notify_when_done != audit.get("notification_enabled"):
                await self.api(actor, "PUT", path + "/notification", {"enabled": notify_when_done, "expected_revision": audit.get("notification_revision", 0)})
            body = {"request_id": request_id, "expected_revision": audit.get("input_revision", 0), "expected_confirmation_basis_digest": quote_id}
        task = await self.gateway.get(actor, "audits:read", path)
        folders = {entry["version"]["id"]: entry.get("folder") for entry in row["state"]["files"].values() if entry.get("version")}
        summary, details = views.audit_start(task, facts.get("precheck"), folders)
        snapshot = {"operation": "confirm_audit", "summary": summary, "details": details, "request": body, "sources": [task]}
        return await self.gateway.write(actor, "audits:execute", "confirm_audit", request_id, "POST", path + "/confirm", body, probes=[path], confirmation=snapshot)

    async def resolve(self, actor: Delegation, task_id: str, issue_id: str, action_id: str, expected_revision: int, request_id: str, value: Any) -> Reply | None:
        """Apply one of the actions an open issue offers. None: the task itself changed; a Reply: the audit's own
        record changed (an answer, a review hint, a report), which keeps its own receipt."""
        row = await self.load(actor, task_id)
        state = row["state"]
        if request_id in state["decisions"]:
            if state["decisions"][request_id] != [issue_id, action_id]:
                raise workflow.Rejected("request_conflict")
            return None
        prior = await self.recorded(actor, request_id)
        if prior is not None and prior["state"] not in {"confirmation_required", "ready"}:
            return await self.gateway.operation_reply(actor, prior)
        replayed = await self.replay(actor, request_id, "resolve_issue")
        if replayed is not None:
            return replayed
        if not row["mine"]:
            raise McpError("access_denied", 403)
        if expected_revision != row["revision"]:
            raise workflow.Rejected("stale_revision")
        facts = await self.facts(actor, task_id, state)
        issue, _ = workflow.find_issue(workflow.issues(task_id, state, facts), issue_id, action_id)
        audit = facts.get("audit") or {}
        round_id = audit.get("round_id")
        if action_id == "answer":
            return await self._answer(actor, round_id, issue, facts, request_id, value)
        if action_id in {"accept", "ignore"}:
            hint = next(h["hint"] for h in facts["hints"] if h["item_key"] == issue["subject"]["key"])
            body = {"hint": hint, "item_key": issue["subject"]["key"], "action": "accepted" if action_id == "accept" else "ignored"}
            return await self.gateway.write(actor, "audits:write", "resolve_issue", request_id, "PUT", f"/rounds/{round_id}/review-hints", body, probes=[f"/rounds/{round_id}"])
        if action_id == "regenerate":
            line = next(r for r in audit["files"] if r["file"]["id"] == issue["subject"]["key"] or r["file"]["original_filename"] == issue["subject"]["filename"])
            review = line["audit"]
            reference = {"task_id": task_id, "round_id": round_id, "file_version_id": line["file"]["id"], "result_attempt": review.get("result_attempt"), "report_id": review["report"]["report_id"]}
            path = f"/audit-tasks/{task_id}/reports/{reference['report_id']}/regenerations"
            task = await self.gateway.get(actor, "audits:read", f"/audit-tasks/{task_id}")
            snapshot = {"operation": "resolve_issue", "summary": [f"重新生成「{line['file']['original_filename']}」的审核报告。"], "request": reference, "sources": [task]}
            return await self.gateway.write(
                actor, "reports:write", "resolve_issue", request_id, "POST", path, {"request_id": request_id, "reference": reference}, probes=[f"/audit-tasks/{task_id}"], confirmation=snapshot
            )
        subject = issue["subject"].get("key") if issue["subject"]["kind"] == "file" else None
        await self.commit(actor, task_id, [{"type": "decided", "request_id": request_id, "issue_id": issue_id, "action_id": action_id, "subject": subject if subject in state["files"] else None}])
        if action_id == "close" and audit.get("state") == "pending_confirm":
            await self.api(actor, "POST", f"/audit-tasks/{task_id}/end", {"expected_revision": audit.get("input_revision", 0)})
        return None

    async def _answer(self, actor, round_id, issue, facts, request_id, value) -> Reply:
        from api.fact_answers import FactAnswersView

        subject = issue["subject"]
        question = next(q for q in facts["questions"]["questions"] if q["file_version_id"] == subject["file_version_id"] and q["field"] == subject["key"])
        answer = value.get("value") if isinstance(value, dict) else value
        if question.get("answer_type") == "boolean":
            valid = isinstance(answer, bool)
        else:
            valid = isinstance(answer, str) and 0 < len(answer) <= 2000
        if not valid:
            raise workflow.Rejected("invalid_answer")
        path = f"/audit-rounds/{round_id}/fact-answers"
        current = await self.gateway.get(actor, "audits:read", path)
        body = {
            "operation_id": request_id,
            "expected_revision": current["revision"],
            "expected_question_fingerprint": question["fingerprint"],
            "file_version_id": subject["file_version_id"],
            "field": subject["key"],
            "value": answer,
        }
        # The owner checks the approval against its own canonical form of what the person saw.
        source = FactAnswersView.model_validate(current).model_dump(mode="json")
        names = {row["file"]["id"]: row["file"]["original_filename"] for row in (facts.get("audit") or {}).get("files", [])}
        summary, details = views.answer(question, names.get(subject["file_version_id"]), answer)
        snapshot = {"operation": "resolve_issue", "summary": summary, "details": details, "request": body, "sources": [source]}
        return await self.gateway.write(actor, "reviews:write", "resolve_issue", request_id, "POST", path, body, probes=[path], confirmation=snapshot)
