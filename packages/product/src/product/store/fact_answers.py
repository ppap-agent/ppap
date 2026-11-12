"""Review-authorized original facts and in-round, model-free recomputation."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from audit.fact_questions import BooleanFactEvaluation, FactQuestion, fingerprint
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from product.identity import can_review


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        raise ValueError("invalid_fact_answer_request") from None


def _authorize(store, conn, principal, round_id, *, write=False):
    membership = "SELECT 1 FROM product.memberships WHERE tenant_id=%s AND user_id=%s AND role='member'"
    if not conn.execute(membership + (" FOR KEY SHARE" if write else ""), (principal.tenant_id, principal.user_id)).fetchone():
        raise KeyError("missing")
    versions = [str(row[0]) for row in conn.execute("SELECT file_version_id FROM product.round_inputs WHERE round_id=%s", (round_id,)).fetchall()]
    if write:
        # Keep current source and review grants through the answer transaction.
        conn.execute(
            """SELECT resource_id FROM product.resource_grants WHERE user_id=%s AND
               (resource_id=%s OR resource_id IN (SELECT file_id FROM product.file_versions WHERE id=ANY(%s::uuid[])))
               ORDER BY resource_id,capability FOR SHARE""",
            (principal.user_id, round_id, versions),
        ).fetchall()
    resource = store._resource(conn, round_id)
    grants = store._grants(conn, round_id, principal.user_id) if resource else set()
    if not resource or resource.kind != "audit_round" or not can_review(principal, resource, grants):
        raise KeyError("missing")
    store._prepared_items(conn, principal, [{"file_version_id": version, "role": "reference"} for version in versions])


def _machine(conn, round_id):
    rows = conn.execute("SELECT item_key,status,suggestions,locator FROM product.findings WHERE round_id=%s ORDER BY item_key", (round_id,)).fetchall()
    return [{"item_key": row[0], "status": row[1], "suggestions": row[2], "locator": row[3] or {}} for row in rows]


def _questions(findings, versions):
    questions = {}
    ambiguous = set()
    for finding in findings:
        if finding["status"] != "UNABLE":
            continue
        location = finding["locator"]
        version, clause = location.get("file_version_id"), location.get("rule_item_id")
        if version not in versions or not clause:
            continue
        for raw in (location.get("semantic") or {}).get("fact_questions", []):
            try:
                question = FactQuestion.model_validate(raw).model_dump(mode="json")
            except ValidationError:
                continue
            if question["file_version_id"] != version:
                continue
            key = version, question["field"]
            if key in questions and any(questions[key][name] != question[name] for name in question):
                ambiguous.add(key)
                continue
            entry = questions.setdefault(key, {**question, "clause_ids": [], "item_keys": []})
            if clause not in entry["clause_ids"]:
                entry["clause_ids"].append(clause)
            entry["item_keys"].append(finding["item_key"])
    return [{**entry, "fingerprint": fingerprint(entry)} for key, entry in questions.items() if key not in ambiguous]


def _records(conn, round_id):
    return [row[0] for row in conn.execute("SELECT receipt FROM product.original_fact_answers WHERE round_id=%s ORDER BY revision", (round_id,)).fetchall()]


def _results(records):
    latest = {}
    for record in records:
        latest.update((item["item_key"], item) for item in record["results"])
    return list(latest.values())


def _view(conn, round_id):
    versions = {str(row[0]) for row in conn.execute("SELECT file_version_id FROM product.round_inputs WHERE round_id=%s", (round_id,)).fetchall()}
    records = _records(conn, round_id)
    answers = [record["answer"] for record in records]
    answered = {(answer["file_version_id"], answer["field"]) for answer in answers}
    current_results = {result["item_key"]: result for result in _results(records)}
    questions = []
    findings = _machine(conn, round_id)
    clause_by_item = {finding["item_key"]: finding["locator"].get("rule_item_id") for finding in findings}
    for question in _questions(findings, versions):
        is_answered = (question["file_version_id"], question["field"]) in answered
        if not is_answered:
            # An already settled dependent clause cannot justify another question.
            question["item_keys"] = [key for key in question["item_keys"] if current_results.get(key, {}).get("status", "UNABLE") == "UNABLE"]
            if not question["item_keys"]:
                continue
            question["clause_ids"] = list(dict.fromkeys(clause_by_item[key] for key in question["item_keys"]))
            question["fingerprint"] = fingerprint(question)
        questions.append({**question, "answered": is_answered})
    questions.sort(key=lambda question: (question["file_version_id"], question["answered"], -len(question["clause_ids"]), question["field"]))
    return {"round_id": round_id, "revision": len(records), "questions": questions, "answers": answers, "results": _results(records), "model_calls": 0}


def read_answers(store, principal, round_id):
    round_id = _uuid(round_id)
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _authorize(store, conn, principal, round_id)
        return _view(conn, round_id)


def read_operation(store, principal, round_id, operation_id):
    round_id, operation_id = _uuid(round_id), _uuid(operation_id)
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _authorize(store, conn, principal, round_id)
        row = conn.execute("SELECT receipt FROM product.original_fact_answers WHERE operation_id=%s AND round_id=%s AND tenant_id=%s", (operation_id, round_id, principal.tenant_id)).fetchone()
        return row[0] if row else None


def _recompute(findings, question, answers):
    results = []
    for finding in findings:
        if finding["item_key"] not in question["item_keys"] or finding["status"] != "UNABLE":
            continue
        selected = [answer for answer in answers if answer["file_version_id"] == question["file_version_id"]]
        raw = (finding["locator"].get("semantic") or {}).get("fact_evaluation")
        status, message = "UNABLE", "原件回答已保存；本条款尚无完整的确定性复算依据，机器结果仍为 UNABLE。"
        used = [answer for answer in selected if answer["field"] == question["field"]]
        if raw is not None:
            try:
                evaluation = BooleanFactEvaluation.model_validate(raw)
            except ValidationError:
                evaluation = None
            if evaluation is not None and evaluation.file_version_id == question["file_version_id"] and question["field"] in evaluation.facts and evaluation.facts[question["field"]] is None:
                # A human answer can resolve only an explicitly unknown fact.
                eligible = {field for field, value in evaluation.facts.items() if value is None}
                used = [answer for answer in selected if answer["field"] in eligible]
                status, message = evaluation.evaluate({answer["field"]: answer["value"] for answer in used})
        results.append(
            {
                "item_key": finding["item_key"],
                "status": status,
                "suggestions": message,
                "answer_ids": [answer["operation_id"] for answer in used],
                "confirmed_by": list(dict.fromkeys(answer["answered_by"] for answer in used)),
                "confirmed_at": max(answer["answered_at"] for answer in used),
                "sources": [{key: answer[key] for key in ("file_version_id", "field", "locator", "answered_by", "answered_at", "value")} for answer in used],
            }
        )
    return results


def apply_answer(store, principal, round_id, payload):
    round_id = _uuid(round_id)
    required = {"operation_id", "expected_revision", "file_version_id", "field", "expected_question_fingerprint", "value"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("invalid_fact_answer_request")
    request = deepcopy(payload)
    request["operation_id"], request["file_version_id"] = _uuid(request["operation_id"]), _uuid(request["file_version_id"])
    if type(request["expected_revision"]) is not int or request["expected_revision"] < 0 or not isinstance(request["field"], str) or not 1 <= len(request["field"]) <= 256:
        raise ValueError("invalid_fact_answer_request")
    with store._conn() as conn, conn.transaction():
        _authorize(store, conn, principal, round_id, write=True)
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("fact-answer:" + request["operation_id"],))
        # One revision boundary for answers and all affected results. No Worker,
        # queue, usage-event, new Round, rule appendix or model call is involved.
        row = conn.execute("SELECT execution,result_attempt FROM product.audit_rounds WHERE id=%s FOR UPDATE", (round_id,)).fetchone()
        old = conn.execute("SELECT request,receipt,answered_by,round_id FROM product.original_fact_answers WHERE operation_id=%s", (request["operation_id"],)).fetchone()
        if old:
            if old[0] != request or str(old[2]) != principal.user_id or str(old[3]) != round_id:
                raise ValueError("idempotency_conflict")
            return old[1]
        if not row or row[0] != "completed":
            raise ValueError("fact_answers_requires_completed")
        view = _view(conn, round_id)
        if request["expected_revision"] != view["revision"]:
            raise ValueError("fact_revision_conflict")
        question = next((item for item in view["questions"] if item["file_version_id"] == request["file_version_id"] and item["field"] == request["field"]), None)
        if question is None:
            raise ValueError("fact_question_expired")
        if question["answered"]:
            raise ValueError("fact_answer_conflict")
        if question["fingerprint"] != request["expected_question_fingerprint"]:
            raise ValueError("fact_question_changed")
        value = request["value"]
        if value is not None and (
            (question["answer_type"] == "boolean" and type(value) is not bool) or (question["answer_type"] == "text" and (not isinstance(value, str) or not value.strip() or len(value) > 2000))
        ):
            raise ValueError("invalid_fact_answer_request")
        answer = {key: question[key] for key in ("file_version_id", "field", "prompt", "answer_type", "locator")}
        answer.update(operation_id=request["operation_id"], round_id=round_id, result_attempt=row[1], value=value, answered_by=principal.user_id, answered_at=datetime.now(timezone.utc).isoformat())
        results = _recompute(_machine(conn, round_id), question, [*view["answers"], answer])
        receipt = {"operation_id": request["operation_id"], "round_id": round_id, "revision": view["revision"] + 1, "answer": answer, "results": results, "model_calls": 0}
        conn.execute(
            """INSERT INTO product.original_fact_answers(operation_id,tenant_id,round_id,file_version_id,field,revision,request,answer,receipt,answered_by)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                request["operation_id"],
                principal.tenant_id,
                round_id,
                request["file_version_id"],
                request["field"],
                receipt["revision"],
                Jsonb(request),
                Jsonb(answer),
                Jsonb(receipt),
                principal.user_id,
            ),
        )
        return receipt


def export_answers(store, principal, round_id):
    round_id = _uuid(round_id)
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _authorize(store, conn, principal, round_id)
        return {"schema": "original-fact-confirmations-v1", "training_eligible": False, "machine_findings": _machine(conn, round_id), **_view(conn, round_id)}
