"""Explicit rule inputs and their idempotent application to existing rounds."""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone

from ppap_core.identity import can_read, can_review
from ppap_core.rules import normalize_rule_appendix, normalize_rule_body
from psycopg.types.json import Jsonb


def _uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        raise ValueError("invalid_feedback_request") from None


def _authorize(store, conn, principal, round_id, *, write=False):
    resource = store._resource(conn, round_id)
    grants = store._grants(conn, round_id, principal.user_id) if resource else set()
    if not resource or resource.kind != "audit_round" or not can_read(principal, resource, grants) or write and not can_review(principal, resource, grants):
        raise KeyError("missing")
    row = conn.execute("SELECT analysis_mode FROM product.audit_rounds WHERE id=%s", (round_id,)).fetchone()
    if not row or row[0] != "deep":
        raise ValueError("deep_feedback_requires_deep")


def _workspace(conn, round_id):
    row = conn.execute("SELECT workspace_round_id FROM product.deep_rule_changes WHERE analysis_round_id=%s", (round_id,)).fetchone()
    return str(row[0]) if row else round_id


def _state(conn, workspace_id):
    rows = conn.execute("SELECT receipt FROM product.deep_rule_changes WHERE workspace_round_id=%s ORDER BY revision", (workspace_id,)).fetchall()
    operations = [row[0] for row in rows]
    latest = operations[-1] if operations else None
    current = next((item["analysis_round_id"] for item in reversed(operations) if item["analysis_round_id"]), workspace_id)
    initial = {}
    if not latest:
        for source in _inputs(conn, workspace_id):
            for entry in (source["rule_body"] or {}).get("appendix", []):
                if entry["id"] in initial and initial[entry["id"]] != entry:
                    raise ValueError("feedback_scope_conflict")
                initial[entry["id"]] = entry
    return current, latest["revision"] if latest else 0, latest["appendix"] if latest else list(initial.values()), operations


def _inputs(conn, round_id):
    rows = conn.execute("SELECT file_version_id, role, rule_version_id, rule_body FROM product.round_inputs WHERE round_id=%s ORDER BY file_version_id", (round_id,)).fetchall()
    return [{"file_version_id": str(row[0]), "role": row[1], "rule_version_id": str(row[2]) if row[2] else None, "rule_body": row[3]} for row in rows]


def _rule_targets(inputs, rule_ids):
    return [
        {"document_id": item["file_version_id"], "rule_id": str(rule["id"])}
        for item in inputs
        if item["role"] == "target"
        for rule in (item["rule_body"] or {}).get("rules", [])
        if str(rule["id"]) in rule_ids
    ]


def _question_targets(question, inputs):
    question = deepcopy(question)
    question["scope"].setdefault("rule_targets", _rule_targets(inputs, question["scope"]["rule_ids"]))
    return question


def question_fingerprint(question):
    """Bind an answer to the exact explanation the reader was shown."""
    shown = {key: question[key] for key in ("id", "input_id", "kind", "question", "document_ids", "rule_ids", "candidates", "sources", "scope")}
    return hashlib.sha256(json.dumps(shown, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_feedback(store, principal, round_id):
    round_id = _uuid(round_id)
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _authorize(store, conn, principal, round_id)
        workspace_id = _workspace(conn, round_id)
        _authorize(store, conn, principal, workspace_id)
        current_id, revision, appendix, operations = _state(conn, workspace_id)
        _authorize(store, conn, principal, current_id)
        current = store._round_public(conn, current_id)
        confirmed = {item.get("question_id") for item in appendix}
        current_inputs = _inputs(conn, current_id)
        questions = [_question_targets(item, current_inputs) for item in current["coverage"].get("clarifications", []) if item["id"] not in confirmed]
        clarifications = [{**item, "fingerprint": question_fingerprint(item)} for item in questions]
        rounds = list(dict.fromkeys([workspace_id, *[item["analysis_round_id"] for item in operations if item["analysis_round_id"]]]))
        results = []
        for ident in rounds:
            _authorize(store, conn, principal, ident)
            result = store._round_public(conn, ident)
            results.append(
                {"round_id": ident, "execution": result["execution"], "current": ident == current_id, "findings": result["findings"], "coverage": result["coverage"], "inputs": result["inputs"]}
            )
        input_changed = bool(operations and operations[-1]["analysis_round_id"] is None)
        return {
            "round_id": round_id,
            "workspace_id": workspace_id,
            "current_round_id": current_id,
            "revision": revision,
            "input_changed": input_changed,
            "clarifications": clarifications,
            "appendix": appendix,
            "operations": operations,
            "results": results,
        }


def read_change(store, principal, round_id, operation_id):
    round_id, operation_id = _uuid(round_id), _uuid(operation_id)
    with store._conn() as conn:
        _authorize(store, conn, principal, round_id)
        workspace = _workspace(conn, round_id)
        _authorize(store, conn, principal, workspace)
        row = conn.execute("SELECT receipt FROM product.deep_rule_changes WHERE operation_id=%s AND workspace_round_id=%s AND tenant_id=%s", (operation_id, workspace, principal.tenant_id)).fetchone()
        if row:
            for ident in (row[0]["source_round_id"], row[0].get("analysis_round_id")):
                if ident:
                    _authorize(store, conn, principal, ident)
        return row[0] if row else None


def export_feedback(store, principal, round_id):
    """Select observable records; never export arbitrary runtime dictionaries."""
    view = read_feedback(store, principal, round_id)
    with store._conn() as conn:
        ids = [result["round_id"] for result in view["results"]]
        for ident in ids:
            _authorize(store, conn, principal, ident)
        rows = conn.execute(
            """SELECT j.audit_round_id,a.attempt,a.outcome,a.diagnostic FROM engine.jobs j
               JOIN engine.attempts a ON a.job_id=j.id
               WHERE j.audit_round_id=ANY(%s::uuid[]) AND j.kind='audit' ORDER BY j.created_at,a.attempt""",
            (ids,),
        ).fetchall()
    attempts = []
    for rid, attempt, outcome, diagnostic in rows:
        raw = (diagnostic or {}).get("usage") or {}
        usage = {key: raw[key] for key in ("input_tokens", "output_tokens", "tool_calls", "completed_tool_calls", "compactions") if type(raw.get(key)) is int and raw[key] >= 0}
        attempts.append(
            {
                "round_id": str(rid),
                "attempt": attempt,
                "outcome": outcome,
                "usage": usage,
                "usage_known": {"input_tokens", "output_tokens"} <= usage.keys(),
                "usage_source": diagnostic.get("usage_source", "unknown") if (diagnostic or {}).get("usage_source") in {"provider", "controlled"} else "unknown",
            }
        )
    return {"schema": "deep-feedback-v1", "human_gold": False, "training_eligible": False, "record": view, "attempts": attempts}


def apply_change(store, principal, round_id, payload, *, usage_policy=None):
    round_id = _uuid(round_id)
    allowed = {"operation_id", "expected_revision", "question_id", "expected_question_fingerprint", "candidate_id", "kind", "content", "scope", "intent", "confirmation_source", "remove_appendix_ids"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("invalid_feedback_request")
    request = deepcopy({key: value for key, value in payload.items() if value is not None})
    request.setdefault("confirmation_source", "user")
    request.setdefault("remove_appendix_ids", [])
    request["operation_id"] = _uuid(request.get("operation_id"))
    revision = request.get("expected_revision")
    removals = request["remove_appendix_ids"]
    if type(revision) is not int or revision < 0 or request.get("intent") not in {"save_only", "save_and_analyze"}:
        raise ValueError("invalid_feedback_request")
    if not isinstance(removals, list) or len(removals) > 256 or any(not isinstance(ident, str) for ident in removals) or len(set(removals)) != len(removals):
        raise ValueError("invalid_feedback_request")
    if "content" not in request and (any(key in request for key in ("kind", "scope", "question_id", "candidate_id")) or not removals and request["intent"] != "save_and_analyze"):
        raise ValueError("invalid_feedback_request")
    with store._conn() as conn:
        with conn.transaction():
            _authorize(store, conn, principal, round_id, write=True)
            workspace_id = _workspace(conn, round_id)
            _authorize(store, conn, principal, workspace_id, write=True)
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("deep-feedback:" + workspace_id,))
            old = conn.execute("SELECT request, receipt, created_by FROM product.deep_rule_changes WHERE operation_id=%s AND tenant_id=%s", (request["operation_id"], principal.tenant_id)).fetchone()
            if old:
                if old[0] != request or str(old[2]) != principal.user_id or old[1]["workspace_id"] != workspace_id:
                    raise ValueError("idempotency_conflict")
                for ident in (old[1]["source_round_id"], old[1].get("analysis_round_id")):
                    if ident:
                        _authorize(store, conn, principal, ident)
                return old[1]
            current_id, current_revision, appendix, _operations = _state(conn, workspace_id)
            if revision != current_revision:
                raise ValueError("feedback_revision_conflict")
            _authorize(store, conn, principal, current_id, write=True)
            inputs = _inputs(conn, current_id)
            document_ids = {item["file_version_id"] for item in inputs}
            rule_ids = {str(rule["id"]) for item in inputs for rule in (item["rule_body"] or {}).get("rules", [])}
            if not set(removals) <= {part["id"] for part in appendix}:
                raise ValueError("feedback_revision_conflict")
            appendix = [part for part in appendix if part["id"] not in removals]
            entry = {key: request[key] for key in ("kind", "content", "scope", "question_id", "candidate_id", "confirmation_source") if key in request}
            entry.update(id=request["operation_id"], confirmed_by=principal.user_id, confirmed_at=datetime.now(timezone.utc).isoformat())
            entry = normalize_rule_appendix([entry], rule_ids)[0] if "content" in request else None
            if entry and not set(entry["scope"]["document_ids"]) <= document_ids:
                raise ValueError("feedback_scope_conflict")
            if entry:
                valid_targets = {(target["document_id"], target["rule_id"]) for target in _rule_targets(inputs, rule_ids)}
            if entry and entry.get("question_id"):
                row = conn.execute("SELECT coverage FROM product.audit_rounds WHERE id=%s", (current_id,)).fetchone()
                question = next((q for q in (row[0] or {}).get("clarifications", []) if q["id"] == entry["question_id"]), None)
                if question is None or any(old_entry.get("question_id") == entry["question_id"] for old_entry in appendix):
                    raise ValueError("feedback_question_expired")
                question = _question_targets(question, inputs)
                if request.get("expected_question_fingerprint") != question_fingerprint(question):
                    raise ValueError("feedback_question_changed")
                if not set(entry["scope"]["document_ids"]) <= set(question["scope"]["document_ids"]) or not set(entry["scope"]["rule_ids"]) <= set(question["scope"]["rule_ids"]):
                    raise ValueError("feedback_scope_conflict")
                entry["scope"].setdefault("rule_targets", [target for target in question["scope"]["rule_targets"] if target["rule_id"] in entry["scope"]["rule_ids"]])
                question_targets = {(target["document_id"], target["rule_id"]) for target in question["scope"]["rule_targets"]}
                if not {(target["document_id"], target["rule_id"]) for target in entry["scope"]["rule_targets"]} <= question_targets:
                    raise ValueError("feedback_scope_conflict")
                if entry.get("candidate_id"):
                    candidate = next((c for c in question["candidates"] if c["id"] == entry["candidate_id"]), None)
                    if not candidate or candidate["explanation"] != entry["content"]:
                        raise ValueError("feedback_candidate_changed")
            elif entry and entry.get("candidate_id"):
                raise ValueError("feedback_question_expired")
            if entry:
                entry["scope"].setdefault("rule_targets", _rule_targets(inputs, entry["scope"]["rule_ids"]))
                targets = entry["scope"]["rule_targets"]
                if (
                    not targets
                    or not {(target["document_id"], target["rule_id"]) for target in targets} <= valid_targets
                    or {target["rule_id"] for target in targets} != set(entry["scope"]["rule_ids"])
                ):
                    raise ValueError("feedback_scope_conflict")
                appendix = [*appendix, entry]
            analysis_id = None
            if request["intent"] == "save_and_analyze":
                for item in inputs:
                    if item["role"] == "target":
                        body = deepcopy(item["rule_body"])
                        own_ids = {str(rule["id"]) for rule in body["rules"]}
                        body["appendix"] = []
                        for part in appendix:
                            targets = [
                                target
                                for target in part["scope"].get("rule_targets", _rule_targets(inputs, part["scope"]["rule_ids"]))
                                if target["document_id"] == item["file_version_id"] and target["rule_id"] in own_ids
                            ]
                            if targets:
                                body["appendix"].append({**part, "scope": {**part["scope"], "rule_ids": list(dict.fromkeys(target["rule_id"] for target in targets)), "rule_targets": targets}})
                        item["rule_body"] = normalize_rule_body(body)
                accepted = store._accept_locked(
                    conn, principal, "deep-feedback:" + request["operation_id"], source_draft_id=None, predecessor_id=current_id, items=inputs, usage_policy=usage_policy, feedback_inputs=inputs
                )
                analysis_id = accepted["id"]
                # Workspace readers keep their explicit object grants on its new calculation.
                conn.execute(
                    "INSERT INTO product.resource_grants (resource_id,user_id,capability) SELECT %s,user_id,capability FROM product.resource_grants WHERE resource_id=%s ON CONFLICT DO NOTHING",
                    (analysis_id, workspace_id),
                )
            receipt = {
                "operation_id": request["operation_id"],
                "workspace_id": workspace_id,
                "state": "saved",
                "revision": revision + 1,
                "source_round_id": current_id,
                "analysis_round_id": analysis_id,
                "analysis_state": "queued" if analysis_id else "not_started",
                "appendix": appendix,
            }
            conn.execute(
                """INSERT INTO product.deep_rule_changes
                (operation_id,tenant_id,workspace_round_id,source_round_id,analysis_round_id,created_by,revision,request,receipt)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (request["operation_id"], principal.tenant_id, workspace_id, current_id, analysis_id, principal.user_id, revision + 1, Jsonb(request), Jsonb(receipt)),
            )
            return receipt
