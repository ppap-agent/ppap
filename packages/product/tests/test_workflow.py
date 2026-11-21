"""The audit task's state machine: events, effects, issues and the next action, with no I/O."""

import uuid

import pytest
from product import workflow as w

TASK = str(uuid.uuid4())
FOLDER = {"kind": "folder", "token": "fld"}


def listed(*names, extra=()):
    return [*({"token": "t-" + name, "name": name, "type": "file"} for name in names), *extra]


def version(name, sha=None):
    return {"file_id": "f-" + name, "id": "v-" + name, "sha256": sha or "sha-" + name, "original_filename": name, "byte_size": 1}


def prepared(*names, shas=None):
    """A task whose files are all imported and scheduled for their check."""
    state = w.apply(w.new_state(FOLDER, {"customer": None, "deep_mode": False}), {"type": "discovered", "files": listed(*names)})
    for name in names:
        state = w.apply(state, {"type": "imported", "token": "t-" + name, "version": version(name, (shas or {}).get(name))})
        state = w.apply(state, {"type": "check_scheduled", "token": "t-" + name, "validation_id": "c-" + name})
    return state


def checks(**states):
    return {"validations": {"c-" + name: {"state": state, "reasons": []} for name, state in states.items()}}


def audit_row(name, match="matched", selection="selected", **more):
    checked = {"validation_id": "c-" + name, "validation": {"state": "valid", "reasons": []}}
    return {"file": version(name), "selection": selection, "removal_reason": None, **checked, "match": {"state": match, "rule": {"id": "r1", "title": "规则"}}, **more}


def audit(*rows, state="pending_confirm", **more):
    return {"id": TASK, "state": state, "files": list(rows), "input_revision": 0, "customer_preference": "UAES", "confirmation_basis_digest": "q1", "estimated_points": 2, "deep_mode": False, **more}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://x.feishu.cn/drive/folder/AbC_123?from=share", {"kind": "folder", "token": "AbC_123"}),
        ("AbC_123", {"kind": "folder", "token": "AbC_123"}),
        ("https://x.feishu.cn/sheets/sht9", {"kind": "file", "token": "sht9", "doc_type": "sheet"}),
        ("https://x.larkoffice.com/file/box1", {"kind": "file", "token": "box1", "doc_type": "file"}),
    ],
)
def test_a_source_is_a_feishu_folder_or_file_link(text, expected):
    assert w.parse_source(text) == expected


@pytest.mark.parametrize("text,code", [("https://example.com/a.xlsx", "not_a_feishu_link"), ("/Users/me/a.xlsx", "not_a_feishu_link"), ("https://x.feishu.cn/wiki/wk1", "wiki_link_not_supported")])
def test_anything_else_is_not_a_source(text, code):
    with pytest.raises(w.Rejected, match=code):
        w.parse_source(text)


def test_a_new_task_first_reads_the_folder_then_imports_every_file_at_once():
    state = w.new_state(FOLDER, {})
    assert w.effects(state, {}) == [{"type": "discover"}]
    state = w.apply(state, {"type": "discovered", "files": listed("a.xlsx", "b.pdf", extra=[{"token": "sub", "name": "旧版", "type": "folder"}, {"token": "mm", "name": "脑图", "type": "mindnote"}])})
    assert [e["token"] for e in w.effects(state, {})] == ["t-a.xlsx", "t-b.pdf"]
    codes = {issue["code"]: issue for issue in w.issues(TASK, state, {})}
    assert set(codes) == {"SUBFOLDER_NOT_READ", "UNSUPPORTED_FILE"} and not any(issue["blocking"] for issue in codes.values())


def test_one_failed_file_keeps_the_others_and_every_failure_is_reported():
    state = w.apply(w.new_state(FOLDER, {}), {"type": "discovered", "files": listed("a", "b", "c", "d")})
    for name in ("a", "b"):
        state = w.apply(state, {"type": "imported", "token": "t-" + name, "version": version(name)})
        state = w.apply(state, {"type": "check_scheduled", "token": "t-" + name, "validation_id": "c-" + name})
    for name in ("c", "d"):
        state = w.apply(state, {"type": "import_failed", "token": "t-" + name, "code": "feishu_download_failed"})
    facts = checks(a="valid", b="valid")
    (create,) = w.effects(state, facts)
    assert create["type"] == "create_audit" and [f["file_version_id"] for f in create["files"]] == ["v-a", "v-b"]
    failed = [issue for issue in w.issues(TASK, state, facts) if issue["code"] == "IMPORT_FAILED"]
    assert [issue["subject"]["filename"] for issue in failed] == ["c", "d"]
    assert [a["id"] for a in failed[0]["allowed_actions"]] == ["retry", "exclude"]
    view = w.project(TASK, state, facts)
    assert view["phase"] == "preparing" and view["result"]["stages"][1]["state"] == "partial"


def test_a_stage_waits_for_the_one_before_it():
    state = prepared("a", "b")
    assert w.effects(state, checks(a="valid", b="checking")) == []
    assert w.project(TASK, state, checks(a="valid", b="checking"))["next"]["kind"] == "wait"
    assert w.effects(state, checks(a="valid", b="valid"))[0]["type"] == "create_audit"


def test_an_unreadable_file_is_an_issue_and_the_readable_ones_go_on():
    state = prepared("a", "b")
    facts = checks(a="valid", b="invalid")
    assert [f["file_version_id"] for f in w.effects(state, facts)[0]["files"]] == ["v-a"]
    (issue,) = w.issues(TASK, state, facts)
    assert issue["code"] == "FILE_UNREADABLE" and issue["blocking"]


def test_the_same_content_twice_is_audited_once_and_said_so():
    state = prepared("a", "copy", shas={"a": "same", "copy": "same"})
    facts = checks(a="valid", copy="valid")
    assert [f["file_version_id"] for f in w.effects(state, facts)[0]["files"]] == ["v-a"]
    (issue,) = w.issues(TASK, state, facts)
    assert issue["code"] == "DUPLICATE_CONTENT" and not issue["blocking"]


def test_a_repeated_event_or_decision_changes_nothing_and_a_reused_request_is_a_conflict():
    state = prepared("a", "b")
    decision = {"type": "decided", "request_id": "r1", "issue_id": "i1", "action_id": "exclude", "subject": "t-b"}
    once = w.apply(state, decision)
    assert w.apply(once, decision) == once
    assert w.apply(once, {"type": "imported", "token": "t-a", "version": version("a")}) == once
    with pytest.raises(w.Rejected, match="request_conflict"):
        w.apply(once, {**decision, "action_id": "retry"})


def test_only_an_action_the_open_issue_lists_is_accepted():
    state = w.apply(prepared("a"), {"type": "import_failed", "token": "t-a", "code": "x"})
    found = w.issues(TASK, state, {})
    issue, action = w.find_issue(found, found[0]["id"], "exclude")
    assert action["id"] == "exclude"
    with pytest.raises(w.Rejected, match="action_not_allowed"):
        w.find_issue(found, issue["id"], "mark_pass")
    with pytest.raises(w.Rejected, match="issue_not_open"):
        w.find_issue(found, "iss_gone", "exclude")
    with pytest.raises(w.Rejected, match="action_not_allowed"):
        w.apply(state, {"type": "decided", "request_id": "r", "issue_id": issue["id"], "action_id": "mark_pass", "subject": "t-a"})


def test_a_decision_redoes_only_what_depends_on_it():
    state = w.apply(prepared("a", "b"), {"type": "audit_created"})
    facts = {**checks(a="valid", b="valid"), "audit": audit(audit_row("a"), audit_row("b"))}
    assert w.effects(state, facts) == []
    state = w.apply(state, {"type": "decided", "request_id": "r1", "issue_id": "i", "action_id": "exclude", "subject": "t-b"})
    (revise,) = w.effects(state, facts)
    assert revise["type"] == "revise_audit" and revise["expected_revision"] == 0
    assert {f["file_version_id"]: f["selection"] for f in revise["files"]} == {"v-a": "selected", "v-b": "removed"}
    # Once the audit task holds the revision, nothing is left to do.
    after = {**facts, "audit": audit(audit_row("a"), audit_row("b", selection="removed"), input_revision=1)}
    assert w.effects(state, after) == []


def test_a_customer_is_asked_for_only_when_files_stay_unmatched_without_one():
    state = w.apply(prepared("a"), {"type": "audit_created"})
    facts = {**checks(a="valid"), "audit": audit(audit_row("a", match="unmatched"), customer_preference=None), "customers": {"customers": [{"name": "UAES"}, {"name": "蔚来"}]}}
    view = w.project(TASK, state, facts)
    assert view["phase"] == "awaiting_decision"
    (issue,) = view["issues"]
    assert issue["code"] == "CUSTOMER_REQUIRED" and [a["id"] for a in issue["allowed_actions"]] == ["set_customer:UAES", "set_customer:蔚来"]
    assert view["next"] == {"kind": "user_action", "issue_id": issue["id"], "prompt": issue["message"], "resolve_with": "resolve_issue"}
    state = w.apply(state, {"type": "decided", "request_id": "r1", "issue_id": issue["id"], "action_id": "set_customer:蔚来", "subject": None})
    assert w.effects(state, facts)[0]["customer_preference"] == "蔚来"


def test_a_folder_that_changed_is_the_users_decision_and_a_refresh_follows_it():
    state = w.apply(prepared("a", "b"), {"type": "audit_created"})
    facts = {**checks(a="valid", b="valid"), "audit": audit(audit_row("a"), audit_row("b"))}
    state = w.apply(state, {"type": "source_changed", "listing": "new", "added": ["t-c"], "removed": ["t-b"]})
    view = w.project(TASK, state, facts)
    assert view["phase"] == "awaiting_decision" and view["issues"][0]["code"] == "SOURCE_CHANGED"
    kept = w.apply(state, {"type": "decided", "request_id": "r1", "issue_id": "i", "action_id": "keep_snapshot", "subject": None})
    assert kept["listing"] == "new" and w.project(TASK, kept, facts)["phase"] == "ready_to_confirm"
    fresh = w.apply(state, {"type": "decided", "request_id": "r2", "issue_id": "i", "action_id": "refresh", "subject": None})
    assert w.effects(fresh, facts) == [{"type": "discover"}]
    fresh = w.apply(fresh, {"type": "discovered", "files": listed("a", "c")})
    assert fresh["files"]["t-b"]["excluded"] == "removed_from_source"
    assert [e["token"] for e in w.effects(fresh, facts)] == ["t-c"]


def test_the_quote_is_what_confirming_starts_and_next_never_confirms_by_itself():
    state = w.apply(prepared("a"), {"type": "audit_created"})
    facts = {**checks(a="valid"), "audit": audit(audit_row("a"), confirmation_scope={"target_file_version_ids": ["v-a"], "reference_file_version_ids": []})}
    view = w.project(TASK, state, facts)
    assert view["phase"] == "ready_to_confirm"
    assert view["result"]["quote"] == {"quote_id": "q1", "points": 2, "mode": "standard", "files_to_audit": 1, "reference_files": 0, "estimated_time": None}
    assert view["next"]["kind"] == "user_action" and view["next"]["then"] == {"tool": "confirm_audit", "arguments": {"task_id": TASK, "quote_id": "q1"}}


def test_a_precheck_finding_never_blocks_the_audit():
    state = w.apply(prepared("a"), {"type": "audit_created"})
    block = {"code": "B01", "title": "缺项", "problem": "缺少控制计划", "action": "补交", "filename": "a", "file_version_id": "v-a", "number": "B-01"}
    facts = {**checks(a="valid"), "audit": audit(audit_row("a")), "precheck": {"conclusion": "block", "blocks": [block], "warnings": []}}
    view = w.project(TASK, state, facts)
    assert view["phase"] == "ready_to_confirm" and view["issues"][0]["severity"] == "warning" and view["issues"][0]["subject"]["key"] == "t-a"


def test_a_finished_audit_asks_its_open_questions_and_then_points_at_the_results():
    state = w.apply(prepared("a"), {"type": "audit_created"})
    done = audit(audit_row("a"), state="completed", conclusion="NG", round_id="round")
    question = {"file_version_id": "v-a", "field": "sop", "prompt": "SOP 阶段已勾选？", "answer_type": "boolean", "fingerprint": "fp", "answered": False}
    asked = w.project(TASK, state, {"audit": done, "questions": {"questions": [question]}})
    assert asked["phase"] == "completed" and asked["next"]["kind"] == "user_action"
    assert asked["issues"][0]["allowed_actions"][0]["input_schema"]["properties"]["value"] == {"type": "boolean"}
    answered = w.project(TASK, state, {"audit": done, "questions": {"questions": [{**question, "answered": True}]}})
    assert answered["issues"] == [] and answered["next"] == {"kind": "call_tool", "tool": "get_results", "arguments": {"task_id": TASK}}
    assert answered["result"]["audit"]["conclusion"] == "NG"


def test_a_failed_report_leaves_the_conclusion_alone():
    state = w.apply(prepared("a"), {"type": "audit_created"})
    row = audit_row("a", audit={"conclusion": "NG", "execution": "completed", "report": {"state": "failed", "report_id": "rep", "regenerate_allowed": True}})
    view = w.project(TASK, state, {"audit": audit(row, state="completed", conclusion="NG", round_id="round")})
    (issue,) = view["issues"]
    assert issue["code"] == "REPORT_FAILED" and not issue["blocking"] and view["result"]["audit"]["conclusion"] == "NG"
    assert view["result"]["files"][0]["conclusion"] == "NG" and view["result"]["files"][0]["report"] == "failed"


def test_a_closed_task_does_nothing_more():
    state = w.apply(prepared("a"), {"type": "decided", "request_id": "r", "issue_id": "i", "action_id": "close", "subject": None})
    assert w.effects(state, checks(a="valid")) == [] and w.project(TASK, state, {})["phase"] == "closed"


def test_an_audit_started_elsewhere_is_read_as_a_task_without_a_folder():
    state = w.new_state({"kind": "task", "token": TASK}, {})
    state.update(manifest=[], audit=True, external=True)
    view = w.project(TASK, state, {"audit": audit(audit_row("a"), state="reviewing")})
    assert view["phase"] == "auditing" and view["result"]["stages"] == [] and view["result"]["files"][0]["filename"] == "a"
    assert w.effects(state, {"audit": audit(audit_row("a"))}) == []
