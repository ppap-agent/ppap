"""An audit task as a state machine. No I/O happens here.

    apply(state, event)        -> state      what a fact changes
    effects(state, facts)      -> [effect]   what the runtime should do now
    project(task_id, state, facts) -> view   what a client sees: stages, issues, the next action

`state` is what the task has decided and collected (the folder's manifest, each file's import, the user's decisions);
it is persisted with a revision. `facts` are read from their owners when needed (readability checks, the audit task
and its precheck, the round's open questions) and are never copied into the state.

Files do not depend on each other, so their outcomes accumulate: every file ends as an artifact or an issue, and one
failure hides neither the others' results nor the others' failures. Stages depend on the stage before them, so a stage
acts only on what that stage produced, and an issue holds back only what depends on it.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from copy import deepcopy
from typing import Any

SCHEMA_VERSION = "1.0"
# Feishu object types PPAP can read as an audit input. A folder inside the folder is not walked into.
IMPORTABLE = frozenset({"file", "sheet", "doc", "docx", "bitable"})
SETTLED_CHECKS = frozenset({"valid", "invalid", "error"})


class Rejected(ValueError):
    """A command the current state does not allow. The state is unchanged."""


_LINK = re.compile(r"/(drive/folder|file|docx|docs|doc|sheets|base|wiki)/([A-Za-z0-9_-]{1,256})")
_LINK_TYPES = {"file": "file", "docx": "docx", "docs": "doc", "doc": "doc", "sheets": "sheet", "base": "bitable"}


def parse_source(text: str) -> dict:
    """A Feishu folder link (or its token), or the link of one Feishu file. Nothing else is a source."""
    text = text.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", text):
        return {"kind": "folder", "token": text}
    found = _LINK.search(text) if re.match(r"https://[^/\s]+/", text) else None
    if not found:
        raise Rejected("not_a_feishu_link")
    kind, token = found.groups()
    if kind == "wiki":
        raise Rejected("wiki_link_not_supported")
    if kind == "drive/folder":
        return {"kind": "folder", "token": token}
    return {"kind": "file", "token": token, "doc_type": _LINK_TYPES[kind]}


def new_state(source: dict, options: dict) -> dict:
    return {
        "source": source,
        "options": options,
        "manifest": None,
        "discover_error": None,
        "skipped": [],
        "files": {},
        "audit": False,
        "audit_error": None,
        "closed": False,
        "source_change": None,
        "decisions": {},
    }


def step_id(task_id: str, name: str) -> str:
    """The request ID of one effect of this task: the same for every replay, so no effect happens twice."""
    return str(uuid.uuid5(uuid.UUID(task_id), name))


def listing_digest(files: list[dict]) -> str:
    return hashlib.sha256("\n".join(sorted(f"{row['token']}\t{row['type']}\t{row['name']}" for row in files)).encode()).hexdigest()


# ---- events ------------------------------------------------------------------------------------------


def apply(state: dict, event: dict) -> dict:
    state = deepcopy(state)
    kind = event["type"]
    files = state["files"]
    if kind == "discovered":
        listed = {row["token"]: row for row in event["files"] if row["type"] in IMPORTABLE}
        state["skipped"] = [{**row, "reason": "subfolder" if row["type"] == "folder" else "unsupported_type"} for row in event["files"] if row["type"] not in IMPORTABLE]
        for token, row in listed.items():
            files.setdefault(token, {"name": row["name"], "type": row["type"], "attempts": 0, "version": None, "error": None, "validation_id": None, "excluded": None})
            if files[token]["excluded"] == "removed_from_source":
                files[token]["excluded"] = None
        for token, entry in files.items():
            # A file that left the folder leaves the task; what was already imported stays as a record.
            if token not in listed and not entry["excluded"]:
                entry["excluded"] = "removed_from_source"
        state.update(manifest=sorted(listed), discover_error=None, source_change=None, listing=listing_digest(event["files"]))
    elif kind == "discover_failed":
        state["discover_error"] = event["code"]
    elif kind == "imported":
        files[event["token"]].update(version=event["version"], error=None)
    elif kind == "import_failed":
        entry = files[event["token"]]
        entry.update(error=event["code"], attempts=entry["attempts"] + 1)
    elif kind == "check_scheduled":
        files[event["token"]]["validation_id"] = event["validation_id"]
    elif kind == "audit_created":
        state.update(audit=True, audit_error=None)
    elif kind == "audit_failed":
        state["audit_error"] = event["code"]
    elif kind == "source_changed":
        state["source_change"] = {"listing": event["listing"], "added": event["added"], "removed": event["removed"]}
    elif kind == "decided":
        _decide(state, event)
    else:
        raise Rejected("unknown_event")
    return state


def _decide(state: dict, event: dict) -> None:
    """A user's decision that changes the task itself. Decisions on the audit task's own facts go to their owner."""
    prior = state["decisions"].get(event["request_id"])
    if prior is not None:
        if prior != [event["issue_id"], event["action_id"]]:
            raise Rejected("request_conflict")
        return
    action, subject = event["action_id"], event.get("subject")
    files, options = state["files"], state["options"]
    if action == "retry" and subject in files:
        files[subject].update(error=None, attempts=0)
    elif action == "retry" and subject is None:
        state.update(discover_error=None, audit_error=None)
    elif action == "exclude" and subject in files:
        files[subject]["excluded"] = "by_user"
    elif action == "refresh":
        state.update(manifest=None, discover_error=None, source_change=None)
    elif action == "keep_snapshot" and state["source_change"]:
        state.update(listing=state["source_change"]["listing"], source_change=None)
    elif action == "close":
        state["closed"] = True
    elif action.startswith("set_customer:"):
        options["customer"] = action.split(":", 1)[1]
    elif action.startswith("choose_rule:") and subject in files:
        files[subject]["rule"] = action.split(":", 1)[1]
    elif action == "standard_mode":
        options["deep_mode"] = False
    else:
        raise Rejected("action_not_allowed")
    state["decisions"][event["request_id"]] = [event["issue_id"], event["action_id"]]


# ---- derived facts -----------------------------------------------------------------------------------


def duplicates(state: dict) -> dict[str, str]:
    """Files whose content another file of the task already has: the later one maps to the first."""
    first: dict[str, str] = {}
    same: dict[str, str] = {}
    for token in state["manifest"] or []:
        entry = state["files"][token]
        digest = (entry["version"] or {}).get("sha256")
        if entry["excluded"] or not digest:
            continue
        if digest in first:
            same[token] = first[digest]
        else:
            first[digest] = token
    return same


def _check(entry: dict, facts: dict) -> str:
    """One file's readability check: none yet, waiting, or how it ended."""
    if not entry["validation_id"]:
        return "none"
    return (facts.get("validations") or {}).get(entry["validation_id"], {}).get("state", "pending")


def _wanted(state: dict, facts: dict) -> list[str]:
    """The files the audit should hold: imported, readable, kept by the user, not a copy of another."""
    same = duplicates(state)
    return [t for t in state["manifest"] or [] if not state["files"][t]["excluded"] and t not in same and _check(state["files"][t], facts) == "valid"]


def _audit_rows(facts: dict) -> dict[str, dict]:
    return {row["file"]["id"]: row for row in (facts.get("audit") or {}).get("files", [])}


def _revision(state: dict, facts: dict) -> dict | None:
    """The audit task's inputs as the state wants them, or None when they already are so."""
    audit = facts.get("audit")
    if not audit or audit.get("state") not in {"matching", "pending_confirm"}:
        return None
    rows, wanted = _audit_rows(facts), set(_wanted(state, facts))
    by_version = {entry["version"]["id"]: (token, entry) for token, entry in state["files"].items() if entry["version"]}
    inputs, changed = [], False
    for version_id, row in rows.items():
        token, entry = by_version.get(version_id, (None, None))
        line = {"file_version_id": version_id, "validation_id": row.get("validation_id"), "selection": row["selection"], "removal_reason": row.get("removal_reason")}
        for key in ("preferred_rule_version_id", "reference_only", "replacement_of_file_version_id"):
            if row.get(key):
                line[key] = row[key]
        if entry is not None and token not in wanted and row["selection"] == "selected":
            line.update(selection="removed", removal_reason={"by_user": "用户排除", "removed_from_source": "已从飞书文件夹移除"}.get(entry["excluded"] or "", "不再送审"))
            changed = True
        if entry is not None and token in wanted and entry.get("rule") and row.get("preferred_rule_version_id") != entry["rule"]:
            line["preferred_rule_version_id"] = entry["rule"]
            changed = True
        inputs.append(line)
    for token in _wanted(state, facts):
        entry = state["files"][token]
        if entry["version"]["id"] not in rows:
            inputs.append({"file_version_id": entry["version"]["id"], "validation_id": entry["validation_id"], "selection": "selected", "removal_reason": None})
            changed = True
    customer = state["options"].get("customer") or audit.get("customer_preference")
    if customer != audit.get("customer_preference"):
        changed = True
    if not changed or not any(line["selection"] == "selected" for line in inputs):
        return None
    return {"customer_preference": customer, "files": inputs, "expected_revision": audit.get("input_revision", 0)}


def effects(state: dict, facts: dict) -> list[dict]:
    """What to do now. Each effect is idempotent under its request ID; running one twice changes nothing."""
    if state["closed"]:
        return []
    if state.get("external"):
        return []
    todo: list[dict] = []
    if state["manifest"] is None:
        return [] if state["discover_error"] else [{"type": "discover"}]
    for token in state["manifest"]:
        entry = state["files"][token]
        if entry["excluded"]:
            continue
        if entry["version"] is None:
            if entry["error"] is None:
                todo.append({"type": "import", "token": token, "doc_type": entry["type"]})
        elif not entry["validation_id"]:
            todo.append({"type": "check", "token": token, "version": entry["version"]})
    if todo:
        return todo
    if state["audit_error"]:
        return []
    waiting = any(_check(e, facts) not in SETTLED_CHECKS for e in state["files"].values() if not e["excluded"] and e["version"])
    if not state["audit"]:
        wanted = _wanted(state, facts)
        if wanted and not waiting:
            replaces = state["options"].get("replaces") or {}
            files = [
                {
                    "file_version_id": state["files"][t]["version"]["id"],
                    "validation_id": state["files"][t]["validation_id"],
                    "selection": "selected",
                    "removal_reason": None,
                    # The same Feishu file audited before: its earlier version, so the rule it was audited under can be offered again.
                    **({"replacement_of_file_version_id": replaces[t]} if t in replaces else {}),
                }
                for t in wanted
            ]
            return [{"type": "create_audit", "files": files, "customer_preference": state["options"].get("customer"), "predecessor_id": state["options"].get("follows_round_id")}]
        return []
    audit = facts.get("audit") or {}
    revision = None if waiting else _revision(state, facts)
    if revision:
        return [{"type": "revise_audit", **revision}]
    option = audit.get("deep_mode_option") or {}
    wants_deep = bool(state["options"].get("deep_mode"))
    if audit.get("state") == "pending_confirm" and wants_deep != bool(audit.get("deep_mode")) and (option.get("available") or not wants_deep):
        return [{"type": "set_deep_mode", "deep_mode": wants_deep, "expected_revision": audit.get("input_revision", 0)}]
    return []


# ---- issues ------------------------------------------------------------------------------------------


def _issue(task_id: str, code: str, category: str, severity: str, stage: str, message: str, impact: str, actions: list[dict], *, subject: dict | None = None, key: str = "") -> dict:
    ident = "iss_" + hashlib.sha256(f"{task_id}:{code}:{key}".encode()).hexdigest()[:12]
    return {
        "id": ident,
        "code": code,
        "category": category,
        "severity": severity,
        "blocking": severity == "blocking",
        "stage": stage,
        "subject": subject or {"kind": "task"},
        "message": message,
        "impact": impact,
        "status": "open",
        "allowed_actions": actions,
    }


def _act(ident: str, label: str, **more: Any) -> dict:
    return {"id": ident, "label": label, **more}


_EXCLUDE = _act("exclude", "不审这份文件")
_ACCESS_CODES = {"feishu_user_token_missing", "provider_reauthorization_required", "unauthenticated"}


def issues(task_id: str, state: dict, facts: dict) -> list[dict]:
    """Everything open on the task, in the order a person would deal with it. Derived, never stored: an issue is
    gone when what caused it is gone."""
    found: list[dict] = []
    files = state["files"]

    def file_subject(token: str) -> dict:
        return {"kind": "file", "key": token, "filename": files[token]["name"]}

    if state["closed"]:
        return []
    if state["discover_error"]:
        access = state["discover_error"] in _ACCESS_CODES
        found.append(
            _issue(
                task_id,
                "SOURCE_UNREADABLE",
                "access",
                "blocking",
                "discover",
                "文件夹里的文件超过 200 份，请分成几个文件夹分别审核。"
                if state["discover_error"] == "too_many_files"
                else "读不到这个飞书文件夹。" + ("请重新登录飞书并授权后再试。" if access else "请确认链接正确，并且你有权限查看它。"),
                "资料还没有开始导入。",
                [_act("retry", "再读一次")],
            )
        )
    if state["manifest"] == [] and not state["discover_error"]:
        found.append(
            _issue(task_id, "NO_FILES", "import", "blocking", "discover", "文件夹里没有可以审核的文件。", "没有可审的资料。", [_act("refresh", "重新读取文件夹"), _act("close", "结束这次审核")])
        )
    for row in state["skipped"]:
        if row["reason"] == "subfolder":
            text, code = f"子文件夹「{row['name']}」没有读取；需要审核它时，请单独给出它的链接。", "SUBFOLDER_NOT_READ"
        else:
            text, code = f"「{row['name']}」不是可以审核的文件类型，没有导入。", "UNSUPPORTED_FILE"
        found.append(
            _issue(task_id, code, "import", "info", "discover", text, "不在这次审核范围内。", [], subject={"kind": "source_item", "key": row["token"], "filename": row["name"]}, key=row["token"])
        )
    same = duplicates(state)
    for token in state["manifest"] or []:
        entry = files[token]
        if entry["excluded"]:
            continue
        if entry["error"]:
            found.append(
                _issue(
                    task_id,
                    "IMPORT_FAILED",
                    "import",
                    "blocking",
                    "ingest",
                    f"「{entry['name']}」没有导入成功。",
                    "这份文件还不在审核范围内；其它文件不受影响。",
                    [_act("retry", "再导入一次"), _EXCLUDE],
                    subject=file_subject(token),
                    key=token,
                )
            )
            continue
        if token in same:
            found.append(
                _issue(
                    task_id,
                    "DUPLICATE_CONTENT",
                    "import",
                    "info",
                    "ingest",
                    f"「{entry['name']}」与「{files[same[token]]['name']}」内容相同，只审一次。",
                    "不重复审核，也不重复计费。",
                    [],
                    subject=file_subject(token),
                    key=token,
                )
            )
            continue
        check = _check(entry, facts)
        if check in {"invalid", "error"}:
            reasons = "；".join((facts.get("validations") or {}).get(entry["validation_id"], {}).get("reasons") or [])
            found.append(
                _issue(
                    task_id,
                    "FILE_UNREADABLE",
                    "import",
                    "blocking",
                    "ingest",
                    f"「{entry['name']}」读不出内容。" + reasons,
                    "这份文件不能审核。",
                    [_EXCLUDE, _act("refresh", "已在飞书里更换文件，重新读取文件夹")],
                    subject=file_subject(token),
                    key=token,
                )
            )
    if state["audit_error"]:
        quota = "usage" in state["audit_error"] or "quota" in state["audit_error"]
        found.append(
            _issue(
                task_id,
                "PREPARATION_FAILED",
                "import",
                "blocking",
                "recognize",
                "资料已导入，但识别没有开始。" + ("额度不足，请联系管理员。" if quota else ""),
                "还没有识别结果和费用。",
                [_act("retry", "再试一次")],
                key=state["audit_error"],
            )
        )
    if state["source_change"]:
        change = state["source_change"]
        found.append(
            _issue(
                task_id,
                "SOURCE_CHANGED",
                "source_changed",
                "blocking",
                "snapshot",
                f"准备之后，飞书文件夹有变化：新增 {len(change['added'])} 份，移除 {len(change['removed'])} 份。",
                "按原来的资料审核，还是按现在的文件夹重新准备，需要你决定。",
                [_act("refresh", "按现在的文件夹重新准备"), _act("keep_snapshot", "按原来的资料审核")],
                key=change["listing"],
            )
        )
    audit = facts.get("audit")
    if audit:
        found.extend(_audit_issues(task_id, state, facts))
    return found


def _audit_issues(task_id: str, state: dict, facts: dict) -> list[dict]:
    audit, found = facts["audit"], []
    by_version = {entry["version"]["id"]: token for token, entry in state["files"].items() if entry["version"]}
    selected = [row for row in audit.get("files", []) if row["selection"] == "selected"]
    editable = audit.get("state") == "pending_confirm" and not state.get("external")

    def subject(row: dict) -> dict:
        return {"kind": "file", "key": by_version.get(row["file"]["id"], row["file"]["id"]), "filename": row["file"]["original_filename"]}

    if audit.get("state") == "pending_confirm":
        matched = [row for row in selected if (row.get("match") or {}).get("state") in {"matched", "reference"}]
        unmatched = [row for row in selected if (row.get("match") or {}).get("state") == "unmatched"]
        customers = [c["name"] for c in (facts.get("customers") or {}).get("customers", [])]
        if unmatched and not audit.get("customer_preference") and customers and editable:
            found.append(
                _issue(
                    task_id,
                    "CUSTOMER_REQUIRED",
                    "rule_conflict",
                    "blocking",
                    "recognize",
                    f"有 {len(unmatched)} 份文件没有找到审核要求，因为还不知道按哪个客户审。",
                    "选定客户后重新识别，费用随之重新计算。",
                    [_act("set_customer:" + name, "按「" + name + "」审") for name in customers],
                )
            )
        elif not matched and selected:
            found.append(
                _issue(
                    task_id,
                    "NOTHING_TO_AUDIT",
                    "missing_evidence",
                    "blocking",
                    "recognize",
                    "没有一份文件找到对应的审核要求。",
                    "这次没有可以审核的内容。",
                    [_act("refresh", "补交文件后重新读取文件夹"), _act("close", "结束这次审核")] if editable else [],
                )
            )
        for row in selected:
            match = row.get("match") or {}
            name = row["file"]["original_filename"]
            if match.get("state") == "failed":
                found.append(
                    _issue(
                        task_id,
                        "RECOGNITION_FAILED",
                        "recognition",
                        "blocking",
                        "recognize",
                        f"「{name}」没有识别成功。",
                        "这份文件还不能审核。",
                        [_EXCLUDE] if editable else [],
                        subject=subject(row),
                        key=row["file"]["id"],
                    )
                )
            elif match.get("state") == "unmatched" and matched:
                found.append(
                    _issue(
                        task_id,
                        "NO_MATCHING_RULE",
                        "missing_evidence",
                        "warning",
                        "recognize",
                        f"「{name}」没有对应的审核要求，这次不会审核它。",
                        "不审核，不计费。",
                        [_EXCLUDE] if editable else [],
                        subject=subject(row),
                        key=row["file"]["id"],
                    )
                )
            options = row.get("rule_options") or []
            if len(options) > 1 and editable:
                current = row.get("preferred_rule_version_id") or ((match.get("rule") or {}).get("id"))
                found.append(
                    _issue(
                        task_id,
                        "RULE_VERSION_CHOICE",
                        "rule_conflict",
                        "warning",
                        "recognize",
                        f"「{name}」的审核要求有更新的版本。",
                        "默认按较新版本审核；也可以沿用上次审核的版本。",
                        [_act("choose_rule:" + o["id"], o["title"], current=o["id"] == current) for o in options],
                        subject=subject(row),
                        key=row["file"]["id"] + ":" + ",".join(sorted(o["id"] for o in options)),
                    )
                )
        option = audit.get("deep_mode_option") or {}
        if state["options"].get("deep_mode") and not audit.get("deep_mode") and option.get("available") is False:
            found.append(
                _issue(
                    task_id,
                    "DEEP_MODE_UNAVAILABLE",
                    "quote_changed",
                    "blocking",
                    "quote",
                    "这次不能用深度模式。" + (option.get("reason") or ""),
                    "可以改用标准模式继续。",
                    [_act("standard_mode", "改用标准模式")] if editable else [],
                )
            )
        precheck = facts.get("precheck") or {}
        for item in precheck.get("blocks") or []:
            found.append(_precheck_issue(task_id, item, "warning", editable, by_version))
        for item in precheck.get("warnings") or []:
            found.append(_precheck_issue(task_id, item, "info", editable, by_version))
    if audit.get("state") == "completed":
        for question in (facts.get("questions") or {}).get("questions", []):
            if question.get("answered"):
                continue
            kind = {"type": "boolean"} if question.get("answer_type") == "boolean" else {"type": "string", "maxLength": 2000}
            found.append(
                _issue(
                    task_id,
                    "FACT_TO_CONFIRM",
                    "fact_confirmation",
                    "warning",
                    "audit",
                    question["prompt"],
                    "回答后，依赖它的检查项按规则重新计算；机器结论与你的回答分开保存。",
                    [_act("answer", "回答", input_schema={"type": "object", "properties": {"value": kind}, "required": ["value"]})],
                    subject={"kind": "question", "key": question["field"], "file_version_id": question["file_version_id"]},
                    key=question["file_version_id"] + ":" + question["field"] + ":" + str(question.get("fingerprint", "")),
                )
            )
        for row in facts.get("hints") or []:
            found.append(
                _issue(
                    task_id,
                    "RECHECK_SUGGESTED",
                    "review",
                    "info",
                    "audit",
                    f"「{row.get('item') or row['item_key']}」的结论与之前的结果不同，建议人工复核。",
                    "接受后这一项标为待复核；忽略则保持现在的结论。",
                    [_act("accept", "接受建议"), _act("ignore", "忽略")],
                    subject={"kind": "finding", "key": row["item_key"]},
                    key=row["item_key"],
                )
            )
        for row in audit.get("files", []):
            report = (row.get("audit") or {}).get("report") or {}
            if report.get("state") == "failed":
                found.append(
                    _issue(
                        task_id,
                        "REPORT_FAILED",
                        "delivery",
                        "warning",
                        "report",
                        f"「{row['file']['original_filename']}」的报告没有生成成功。",
                        "审核结论不受影响，也不会重新审核或重新计费。",
                        [_act("regenerate", "重新生成报告")] if report.get("regenerate_allowed") else [],
                        subject=subject(row),
                        key=row["file"]["id"] + ":" + str(report.get("report_id")),
                    )
                )
    return found


def _precheck_issue(task_id: str, item: dict, severity: str, editable: bool, by_version: dict[str, str]) -> dict:
    token = by_version.get(item.get("file_version_id") or "")
    return _issue(
        task_id,
        "PRECHECK_" + str(item.get("code", "")),
        "missing_evidence",
        severity,
        "precheck",
        f"「{item.get('filename', '')}」{item.get('problem') or item.get('title', '')}",
        (item.get("action") or "") + " 预检不影响能否开始审核。",
        [_EXCLUDE] if editable and token else [],
        subject={"kind": "file", "key": token or item.get("file_version_id"), "filename": item.get("filename")},
        key=f"{item.get('file_version_id')}:{item.get('code')}:{item.get('number')}",
    )


def find_issue(found: list[dict], issue_id: str, action_id: str | None = None) -> tuple[dict, dict | None]:
    issue = next((row for row in found if row["id"] == issue_id), None)
    if issue is None:
        raise Rejected("issue_not_open")
    if action_id is None:
        return issue, None
    action = next((row for row in issue["allowed_actions"] if row["id"] == action_id), None)
    if action is None:
        raise Rejected("action_not_allowed")
    return issue, action


# ---- projection --------------------------------------------------------------------------------------


def quote(facts: dict) -> dict | None:
    """What confirming would start and cost. Its ID changes whenever the files, rules, mode or price basis change."""
    audit = facts.get("audit") or {}
    if audit.get("state") != "pending_confirm" or not audit.get("confirmation_basis_digest"):
        return None
    scope = audit.get("confirmation_scope") or {}
    return {
        "quote_id": audit["confirmation_basis_digest"],
        "points": audit.get("estimated_points"),
        "mode": "deep" if audit.get("deep_mode") else "standard",
        "files_to_audit": len(scope.get("target_file_version_ids") or []),
        "reference_files": len(scope.get("reference_file_version_ids") or []),
        "estimated_time": facts.get("estimate"),
    }


def _stage(name: str, state_: str, **more: Any) -> dict:
    return {"stage": name, "state": state_, **more}


def project(task_id: str, state: dict, facts: dict) -> dict:
    """The task as a client sees it. The same state and facts always give the same view, whoever asks and whenever."""
    found = issues(task_id, state, facts)
    todo = effects(state, facts)
    audit = facts.get("audit") or {}
    files = state["files"]
    kept = [t for t in state["manifest"] or [] if not files[t]["excluded"]]
    imported = [t for t in kept if files[t]["version"]]
    failed = [t for t in kept if files[t]["error"]]
    checking = [t for t in imported if _check(files[t], facts) not in SETTLED_CHECKS]
    running = bool(todo) or bool(checking) or audit.get("state") == "matching"
    blocking = [row for row in found if row["blocking"]]
    price = quote(facts)

    if state["closed"]:
        phase = "closed"
    elif audit.get("state") == "completed":
        phase = "completed"
    elif audit.get("state") == "reviewing":
        phase = "auditing"
    elif running:
        phase = "preparing"
    elif blocking:
        phase = "awaiting_decision"
    elif price:
        phase = "ready_to_confirm"
    else:
        phase = "preparing"

    def stage_issue(name: str) -> bool:
        return any(row["stage"] == name for row in blocking)

    if state.get("external"):
        stages = []
    else:
        ingest = "pending" if state["manifest"] is None else "running" if len(imported) + len(failed) < len(kept) or checking else "partial" if failed else "completed"
        stages = [
            _stage("discover", "failed" if state["discover_error"] else "running" if state["manifest"] is None else "completed", found=len(state["manifest"] or []), not_read=len(state["skipped"])),
            _stage("ingest", ingest, total=len(kept), imported=len(imported), failed=len(failed), excluded=sum(1 for e in files.values() if e["excluded"])),
            _stage("snapshot", "completed" if state["audit"] else "waiting_input" if stage_issue("snapshot") else "pending", files=len(audit.get("files", [])) if audit else 0),
            _stage("recognize", "pending" if not audit else "running" if audit.get("state") == "matching" else "waiting_input" if stage_issue("recognize") else "completed"),
            _stage("precheck", "completed" if facts.get("precheck") else "pending", conclusion=(facts.get("precheck") or {}).get("conclusion")),
            _stage("quote", "completed" if price and not blocking else "waiting_input" if price else "pending"),
        ]
    result: dict[str, Any] = {"phase": phase, "source": state["source"], "stages": stages, "files": _file_lines(state, facts)}
    if audit:
        result["audit"] = {
            "number": audit.get("number"),
            "customer": audit.get("customer_preference"),
            "execution": audit.get("execution"),
            "conclusion": audit.get("conclusion"),
            "progress": audit.get("progress"),
            "completion_estimate": audit.get("completion_estimate"),
        }
    if price:
        result["quote"] = price
    return {"phase": phase, "result": result, "issues": found, "next": next_action(task_id, phase, found, price, facts), "available_actions": available_actions(task_id, phase, state, facts)}


def _file_lines(state: dict, facts: dict) -> list[dict]:
    rows = _audit_rows(facts)
    same = duplicates(state)
    lines = []

    def audit_part(row: dict) -> dict:
        match, review = row.get("match") or {}, row.get("audit") or {}
        part = {"selection": row["selection"], "document_type": match.get("document_type"), "rule": (match.get("rule") or {}).get("title"), "match": match.get("state")}
        if review:
            report = review.get("report") or {}
            part.update(conclusion=review.get("confirmed_conclusion") or review.get("conclusion"), execution=review.get("execution"), counts=review.get("counts"), report=report.get("state"))
        return part

    seen = set()
    for token in state["manifest"] or []:
        entry = state["files"][token]
        version = entry["version"] or {}
        line = {"key": token, "filename": version.get("original_filename") or entry["name"], "file_version_id": version.get("id")}
        if entry["excluded"]:
            line["state"] = "excluded"
        elif token in same:
            line["state"] = "duplicate"
        elif entry["error"]:
            line["state"] = "import_failed"
        elif not version:
            line["state"] = "importing"
        else:
            line["state"] = {"valid": "ready", "invalid": "unreadable", "error": "unreadable"}.get(_check(entry, facts), "checking")
        if version.get("id") in rows:
            line.update(audit_part(rows[version["id"]]))
            seen.add(version["id"])
        lines.append(line)
    for version_id, row in rows.items():
        if version_id not in seen:
            lines.append({"key": version_id, "filename": row["file"]["original_filename"], "file_version_id": version_id, "state": "ready", **audit_part(row)})
    return lines


def next_action(task_id: str, phase: str, found: list[dict], price: dict | None, facts: dict) -> dict:
    """The one thing to do now. It is advice from the task's state, not permission: charging and sending still need
    the user's own approval, and nothing in it comes from a document under audit."""
    if phase == "closed":
        return {"kind": "done", "goal_status": "closed"}
    if phase == "preparing":
        return {"kind": "wait", "observe_with": "get_task", "check_after_ms": 5000}
    if phase == "awaiting_decision":
        issue = next(row for row in found if row["blocking"])
        if not issue["allowed_actions"]:
            return {"kind": "blocked", "error_code": issue["code"], "message": issue["message"]}
        return {"kind": "user_action", "issue_id": issue["id"], "prompt": issue["message"], "resolve_with": "resolve_issue"}
    if phase == "ready_to_confirm" and price:
        return {
            "kind": "user_action",
            "action_id": "approve_quote",
            "prompt": "请用户核对审核范围和费用。用户同意后调用 confirm_audit；用户没有明确同意前不要调用。",
            "then": {"tool": "confirm_audit", "arguments": {"task_id": task_id, "quote_id": price["quote_id"]}},
        }
    if phase == "auditing":
        return {"kind": "wait", "observe_with": "get_task", "check_after_ms": 15000}
    question = next((row for row in found if row["code"] == "FACT_TO_CONFIRM"), None)
    if question:
        return {"kind": "user_action", "issue_id": question["id"], "prompt": question["message"], "resolve_with": "resolve_issue"}
    return {"kind": "call_tool", "tool": "get_results", "arguments": {"task_id": task_id}}


def available_actions(task_id: str, phase: str, state: dict, facts: dict) -> list[dict]:
    """Other things the state allows now, beside the next one."""
    actions: list[dict[str, Any]] = []
    if phase in {"preparing", "awaiting_decision", "ready_to_confirm"} and not state.get("external") and state["manifest"] is not None:
        actions.append({"tool": "resolve_issue", "summary": "处理列出的问题；每个问题只接受它列出的选项。"})
    if phase == "completed":
        actions.append({"tool": "get_results", "arguments": {"task_id": task_id}, "summary": "读取审核结论和检查项。"})
        actions.append({"tool": "record_review", "summary": "记录人工复核，机器结论不被覆盖。"})
        actions.append({"tool": "publish_report", "arguments": {"task_id": task_id}, "summary": "把审核结果交付到飞书；需要用户同意。"})
        actions.append({"tool": "prepare_audit", "arguments": {"follows_task_id": task_id}, "summary": "供应商补件后，对新的资料复审并关联这一次。"})
    return actions
