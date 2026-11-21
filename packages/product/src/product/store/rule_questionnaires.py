"""Rule clarification questionnaires: question cards found offline on a customer's rules, and the customer's answers.

A questionnaire belongs to one enterprise (the tenant it was loaded for) and names one version of one registered
customer's rules. It is loaded, closed and exported offline through the database (clause-check's questionnaire script);
here every member of the enterprise reads it and answers. Each member's answer to a card is their own and keeps its
history; several members may answer the same card and no answer is final. Nothing here calls a model or touches rules,
checkers or audit results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from psycopg import errors
from psycopg.types.json import Jsonb

from product import customers

RELAYED = "relayed"  # the choice that confirms the answer already relayed on the card


def _uuid(value) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        raise ValueError("invalid_questionnaire_request") from None


def _member(conn, principal, *, write: bool = False) -> None:
    sql = "SELECT 1 FROM product.memberships WHERE tenant_id=%s AND user_id=%s AND role='member'"
    if not conn.execute(sql + (" FOR KEY SHARE" if write else ""), (principal.tenant_id, principal.user_id)).fetchone():
        raise KeyError("missing")


def _questionnaire(conn, principal, questionnaire_id: str) -> dict:
    """The enterprise's own questionnaire; another enterprise's, or none, is the same "missing"."""
    row = conn.execute(
        "SELECT id,customer_id,rule_version,title,imported_at,closed_at FROM product.rule_questionnaires WHERE id=%s AND tenant_id=%s",
        (questionnaire_id, principal.tenant_id),
    ).fetchone()
    if row is None:
        raise KeyError("missing")
    return {"id": str(row[0]), "customer_id": row[1], "rule_version": row[2], "title": row[3], "imported_at": row[4].isoformat(), "closed_at": row[5].isoformat() if row[5] else None}


def _summary(conn, principal, head: dict) -> dict:
    counts = conn.execute(
        """SELECT (SELECT count(*) FROM product.rule_questions WHERE questionnaire_id=%(q)s),
                  (SELECT count(DISTINCT question_key) FROM product.rule_question_answers WHERE questionnaire_id=%(q)s),
                  (SELECT count(DISTINCT question_key) FROM product.rule_question_answers WHERE questionnaire_id=%(q)s AND answered_by=%(u)s)""",
        {"q": head["id"], "u": principal.user_id},
    ).fetchone()
    customer = customers.find(head["customer_id"])
    return {
        **head,
        "customer_name": customer.short_name if customer else head["customer_id"],
        "closed": head["closed_at"] is not None,
        "total": counts[0],
        "answered": counts[1],
        "mine": counts[2],
    }


def _answer_row(row) -> dict:
    return {"revision": row[3], "choice": row[4], "text": row[5], "note": row[6], "answered_at": row[7].isoformat()}


def _answers(store, conn, principal, questionnaire_id: str) -> dict[str, list[dict]]:
    """Per card, each answerer's current answer with its earlier revisions, oldest answerer first."""
    rows = conn.execute(
        """SELECT question_key,answered_by,operation_id,revision,choice,answer_text,note,answered_at
           FROM product.rule_question_answers WHERE questionnaire_id=%s ORDER BY question_key,answered_by,revision""",
        (questionnaire_id,),
    ).fetchall()
    names: dict[str, str] = {}
    by_card: dict[str, dict[str, list]] = {}
    for row in rows:
        by_card.setdefault(row[0], {}).setdefault(str(row[1]), []).append(row)
    result: dict[str, list[dict]] = {}
    for key, people in by_card.items():
        entries = []
        for user, revisions in people.items():
            if user not in names:
                names[user] = store._display_name(conn, principal.tenant_id, user) or "未署名成员"
            current = revisions[-1]
            entries.append(
                {
                    "answerer": names[user],
                    "mine": user == principal.user_id,
                    **_answer_row(current),
                    "first_answered_at": revisions[0][7].isoformat(),
                    "history": [_answer_row(revision) for revision in revisions[:-1]],
                    "_user": user,
                }
            )
        entries.sort(key=lambda entry: entry["first_answered_at"])
        result[key] = entries
    return result


# A loaded card may leave out what it does not have; the page always gets every field.
CARD_DEFAULTS: dict = {"clauses": [], "why": "", "options": [], "expect": "", "related": [], "items": [], "relayed": None}


def _cards(conn, questionnaire_id: str) -> list[dict]:
    rows = conn.execute("SELECT card FROM product.rule_questions WHERE questionnaire_id=%s ORDER BY position", (questionnaire_id,)).fetchall()
    return [{**CARD_DEFAULTS, **row[0], "options": [{"detail": "", **option} for option in row[0].get("options", [])]} for row in rows]


def list_questionnaires(store, principal) -> dict:
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _member(conn, principal)
        ids = [str(row[0]) for row in conn.execute("SELECT id FROM product.rule_questionnaires WHERE tenant_id=%s ORDER BY imported_at DESC", (principal.tenant_id,)).fetchall()]
        return {"questionnaires": [_summary(conn, principal, _questionnaire(conn, principal, ident)) for ident in ids]}


def read_questionnaire(store, principal, questionnaire_id) -> dict:
    questionnaire_id = _uuid(questionnaire_id)
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _member(conn, principal)
        head = _questionnaire(conn, principal, questionnaire_id)
        answers = _answers(store, conn, principal, questionnaire_id)
        questions = [{**card, "answers": [{k: v for k, v in entry.items() if k != "_user"} for entry in answers.get(card["key"], [])]} for card in _cards(conn, questionnaire_id)]
        return {**_summary(conn, principal, head), "questions": questions}


def _text(value, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("invalid_answer_request")
    return value.strip() or None


def _receipt(store, conn, principal, operation_id: str) -> dict | None:
    row = conn.execute(
        """SELECT questionnaire_id,question_key,answered_by,revision,choice,answer_text,note,answered_at
           FROM product.rule_question_answers WHERE operation_id=%s AND tenant_id=%s""",
        (operation_id, principal.tenant_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "operation_id": operation_id,
        "questionnaire_id": str(row[0]),
        "question_key": row[1],
        "answerer": store._display_name(conn, principal.tenant_id, row[2]) or "未署名成员",
        "mine": str(row[2]) == principal.user_id,
        **_answer_row((None, None, None, *row[3:])),
    }


def _saved(store, conn, principal, operation_id: str) -> dict:
    receipt = _receipt(store, conn, principal, operation_id)
    if receipt is None:
        raise KeyError("missing")
    return receipt


def answer(store, principal, questionnaire_id, payload) -> dict:
    """Save one member's answer to one card: a new answer, or a change that keeps the earlier one as history."""
    questionnaire_id = _uuid(questionnaire_id)
    required = {"operation_id", "question_key", "expected_revision", "choice", "text", "note"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("invalid_answer_request")
    operation_id = _uuid(payload["operation_id"])
    key, expected = payload["question_key"], payload["expected_revision"]
    if not isinstance(key, str) or not 1 <= len(key) <= 200 or type(expected) is not int or expected < 0:
        raise ValueError("invalid_answer_request")
    choice = payload["choice"]
    if choice is not None and (not isinstance(choice, str) or not 1 <= len(choice) <= 64):
        raise ValueError("invalid_answer_request")
    text, note = _text(payload["text"], 4000), _text(payload["note"], 2000)
    if choice is None and text is None:
        raise ValueError("answer_required")
    request = {"question_key": key, "expected_revision": expected, "choice": choice, "text": text, "note": note}
    with store._conn() as conn, conn.transaction():
        _member(conn, principal, write=True)
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("rule-answer:" + operation_id,))
        old = conn.execute("SELECT request,answered_by,questionnaire_id,tenant_id FROM product.rule_question_answers WHERE operation_id=%s", (operation_id,)).fetchone()
        if old:
            if (old[0], str(old[1]), str(old[2]), str(old[3])) != (request, principal.user_id, questionnaire_id, principal.tenant_id):
                raise ValueError("idempotency_conflict")
            return _saved(store, conn, principal, operation_id)
        head = _questionnaire(conn, principal, questionnaire_id)
        if head["closed_at"]:
            raise ValueError("questionnaire_closed")
        row = conn.execute("SELECT card FROM product.rule_questions WHERE questionnaire_id=%s AND question_key=%s", (questionnaire_id, key)).fetchone()
        if row is None:
            raise ValueError("invalid_answer_request")
        card = row[0]
        allowed = {option["key"] for option in card.get("options", [])} | ({RELAYED} if card.get("relayed") else set())
        if choice is not None and choice not in allowed:
            raise ValueError("invalid_answer_request")
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"rule-answer:{questionnaire_id}:{key}:{principal.user_id}",))
        current = conn.execute(
            "SELECT coalesce(max(revision),0) FROM product.rule_question_answers WHERE questionnaire_id=%s AND question_key=%s AND answered_by=%s",
            (questionnaire_id, key, principal.user_id),
        ).fetchone()[0]
        if current != expected:
            raise ValueError("answer_revision_conflict")
        try:
            # The database refuses an answer to a questionnaire closed meanwhile; closing waits for this one.
            conn.execute(
                """INSERT INTO product.rule_question_answers(operation_id,tenant_id,questionnaire_id,question_key,answered_by,revision,choice,answer_text,note,request,answered_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (operation_id, principal.tenant_id, questionnaire_id, key, principal.user_id, current + 1, choice, text, note, Jsonb(request), datetime.now(timezone.utc)),
            )
        except errors.CheckViolation as exc:
            if exc.diag.message_primary == "questionnaire_closed":
                raise ValueError("questionnaire_closed") from None
            raise
        return _saved(store, conn, principal, operation_id)


def read_answer_operation(store, principal, questionnaire_id, operation_id) -> dict | None:
    questionnaire_id, operation_id = _uuid(questionnaire_id), _uuid(operation_id)
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _member(conn, principal)
        _questionnaire(conn, principal, questionnaire_id)
        receipt = _receipt(store, conn, principal, operation_id)
        return receipt if receipt and receipt["questionnaire_id"] == questionnaire_id else None
