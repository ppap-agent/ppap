"""Rule clarification questionnaires: question cards found offline on a customer's rules, and the customer's answers.

A questionnaire belongs to one enterprise (the tenant it was imported into) and names one version of one registered
customer's rules. Platform operators import, close and export it; every member of the enterprise reads it and answers.
Each member's answer to a card is their own and keeps its history; several members may answer the same card and no
answer is final. Nothing here calls a model or touches rules, checkers or audit results.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from product import customers

RELAYED = "relayed"  # the choice that confirms the answer already relayed on the card


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Option(_Strict):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=2000)


class Related(_Strict):
    clause: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)
    example: str = Field(default="", max_length=4000)


class Relayed(_Strict):
    """An answer the customer already gave through someone else, shown with its source for the customer to confirm."""

    answer: str = Field(min_length=1, max_length=2000)
    applied: str = Field(default="", max_length=2000)
    source: str = Field(min_length=1, max_length=100)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class Card(_Strict):
    key: str = Field(min_length=1, max_length=200)
    group: Literal["common", "conclusion", "general", "clause"]
    clauses: list[str] = Field(default_factory=list, max_length=100)
    question: str = Field(min_length=1, max_length=2000)
    why: str = Field(default="", max_length=6000)
    options: list[Option] = Field(default_factory=list, max_length=12)
    expect: str = Field(default="", max_length=1000)
    related: list[Related] = Field(default_factory=list, max_length=100)
    items: list[str] = Field(default_factory=list, max_length=100)
    relayed: Relayed | None = None

    @field_validator("clauses", "items")
    @classmethod
    def _texts(cls, values: list[str]) -> list[str]:
        if any(not isinstance(value, str) or not value.strip() or len(value) > 200 for value in values):
            raise ValueError("invalid text")
        return [value.strip() for value in values]

    @field_validator("options")
    @classmethod
    def _option_keys(cls, values: list[Option]) -> list[Option]:
        keys = [option.key for option in values]
        if len(set(keys)) != len(keys) or RELAYED in keys:
            raise ValueError("duplicate option")
        return values


class QuestionnaireFile(_Strict):
    """What clause-check's questionnaire script writes and an operator imports."""

    schema_: Literal["rule-questionnaire-v1"] = Field(alias="schema")
    customer_id: str = Field(min_length=1, max_length=64)
    rule_version: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    questions: list[Card] = Field(min_length=1, max_length=2000)

    @field_validator("questions")
    @classmethod
    def _unique(cls, values: list[Card]) -> list[Card]:
        if len({card.key for card in values}) != len(values):
            raise ValueError("duplicate question")
        return values


def _uuid(value) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        raise ValueError("invalid_questionnaire_request") from None


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def is_operator(store, principal) -> bool:
    return store.settings.platform_operator(principal.tenant_key, principal.subject)


def _member(conn, principal, *, write: bool = False) -> None:
    sql = "SELECT 1 FROM product.memberships WHERE tenant_id=%s AND user_id=%s AND role='member'"
    if not conn.execute(sql + (" FOR KEY SHARE" if write else ""), (principal.tenant_id, principal.user_id)).fetchone():
        raise KeyError("missing")


def _operator(store, principal) -> None:
    if not is_operator(store, principal):
        raise PermissionError("platform_operator_required")


def _questionnaire(conn, principal, questionnaire_id: str, *, lock: bool = False) -> dict:
    """The enterprise's own questionnaire; another enterprise's, or none, is the same "missing"."""
    row = conn.execute(
        "SELECT id,customer_id,rule_version,title,imported_at,closed_at FROM product.rule_questionnaires WHERE id=%s AND tenant_id=%s" + (" FOR SHARE" if lock else ""),
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


def _cards(conn, questionnaire_id: str) -> list[dict]:
    rows = conn.execute("SELECT card FROM product.rule_questions WHERE questionnaire_id=%s ORDER BY position", (questionnaire_id,)).fetchall()
    return [row[0] for row in rows]


def list_questionnaires(store, principal) -> dict:
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _member(conn, principal)
        ids = [str(row[0]) for row in conn.execute("SELECT id FROM product.rule_questionnaires WHERE tenant_id=%s ORDER BY imported_at DESC", (principal.tenant_id,)).fetchall()]
        return {"questionnaires": [_summary(conn, principal, _questionnaire(conn, principal, ident)) for ident in ids], "can_manage": is_operator(store, principal)}


def read_questionnaire(store, principal, questionnaire_id) -> dict:
    questionnaire_id = _uuid(questionnaire_id)
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _member(conn, principal)
        head = _questionnaire(conn, principal, questionnaire_id)
        answers = _answers(store, conn, principal, questionnaire_id)
        questions = [{**card, "answers": [{k: v for k, v in entry.items() if k != "_user"} for entry in answers.get(card["key"], [])]} for card in _cards(conn, questionnaire_id)]
        return {**_summary(conn, principal, head), "can_manage": is_operator(store, principal), "questions": questions}


def import_questionnaire(store, principal, payload) -> dict:
    _operator(store, principal)
    if not isinstance(payload, dict) or set(payload) != {"operation_id", "questionnaire"}:
        raise ValueError("invalid_questionnaire_request")
    questionnaire_id = _uuid(payload["operation_id"])
    try:
        parsed = QuestionnaireFile.model_validate(payload["questionnaire"])
    except ValidationError:
        raise ValueError("invalid_questionnaire_file") from None
    if customers.find(parsed.customer_id) is None:
        raise ValueError("unknown_customer")
    cards = [card.model_dump(mode="json") for card in parsed.questions]
    digest = _digest({"customer_id": parsed.customer_id, "rule_version": parsed.rule_version, "title": parsed.title, "questions": cards})
    with store._conn() as conn, conn.transaction():
        _member(conn, principal, write=True)
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("rule-questionnaire:" + questionnaire_id,))
        old = conn.execute("SELECT tenant_id,imported_by,import_digest FROM product.rule_questionnaires WHERE id=%s", (questionnaire_id,)).fetchone()
        if old:
            if (str(old[0]), str(old[1]), old[2]) != (principal.tenant_id, principal.user_id, digest):
                raise ValueError("idempotency_conflict")
            return _summary(conn, principal, _questionnaire(conn, principal, questionnaire_id))
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"rule-questionnaire:{principal.tenant_id}:{parsed.customer_id}:{parsed.rule_version}",))
        if conn.execute(
            "SELECT 1 FROM product.rule_questionnaires WHERE tenant_id=%s AND customer_id=%s AND rule_version=%s", (principal.tenant_id, parsed.customer_id, parsed.rule_version)
        ).fetchone():
            raise ValueError("questionnaire_exists")
        conn.execute(
            "INSERT INTO product.rule_questionnaires(id,tenant_id,customer_id,rule_version,title,import_digest,imported_by) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (questionnaire_id, principal.tenant_id, parsed.customer_id, parsed.rule_version, parsed.title, digest, principal.user_id),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO product.rule_questions(questionnaire_id,question_key,position,card) VALUES(%s,%s,%s,%s)",
                [(questionnaire_id, card["key"], position, Jsonb(card)) for position, card in enumerate(cards)],
            )
        return _summary(conn, principal, _questionnaire(conn, principal, questionnaire_id))


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
        head = _questionnaire(conn, principal, questionnaire_id, lock=True)
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
        conn.execute(
            """INSERT INTO product.rule_question_answers(operation_id,tenant_id,questionnaire_id,question_key,answered_by,revision,choice,answer_text,note,request,answered_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (operation_id, principal.tenant_id, questionnaire_id, key, principal.user_id, current + 1, choice, text, note, Jsonb(request), datetime.now(timezone.utc)),
        )
        return _saved(store, conn, principal, operation_id)


def read_answer_operation(store, principal, questionnaire_id, operation_id) -> dict | None:
    questionnaire_id, operation_id = _uuid(questionnaire_id), _uuid(operation_id)
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _member(conn, principal)
        _questionnaire(conn, principal, questionnaire_id)
        receipt = _receipt(store, conn, principal, operation_id)
        return receipt if receipt and receipt["questionnaire_id"] == questionnaire_id else None


def close_questionnaire(store, principal, questionnaire_id) -> dict:
    _operator(store, principal)
    questionnaire_id = _uuid(questionnaire_id)
    with store._conn() as conn, conn.transaction():
        _member(conn, principal, write=True)
        _questionnaire(conn, principal, questionnaire_id)
        # Closing waits for answers being saved; a closed questionnaire keeps its first closing.
        conn.execute(
            "UPDATE product.rule_questionnaires SET closed_by=%s, closed_at=now() WHERE id=%s AND tenant_id=%s AND closed_at IS NULL",
            (principal.user_id, questionnaire_id, principal.tenant_id),
        )
        return _summary(conn, principal, _questionnaire(conn, principal, questionnaire_id))


def _choice_label(card: dict, choice: str | None) -> str | None:
    if choice is None:
        return None
    if choice == RELAYED:
        return "确认按共性答复处理" if (card.get("relayed") or {}).get("applied") else "确认已转达的答复"
    return next((option["label"] for option in card.get("options", []) if option["key"] == choice), choice)


def export_answers(store, principal, questionnaire_id) -> dict:
    """Every card with every answerer's current answer and its history, for clause-check to backfill offline."""
    _operator(store, principal)
    questionnaire_id = _uuid(questionnaire_id)
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _member(conn, principal)
        head = _questionnaire(conn, principal, questionnaire_id)
        answers = _answers(store, conn, principal, questionnaire_id)
        questions = []
        for card in _cards(conn, questionnaire_id):

            def entry(answer: dict) -> dict:
                return {**{k: answer[k] for k in ("revision", "choice", "text", "note", "answered_at")}, "choice_label": _choice_label(card, answer["choice"])}

            questions.append(
                {
                    "key": card["key"],
                    "group": card["group"],
                    "clauses": card.get("clauses", []),
                    "items": card.get("items", []),
                    "question": card["question"],
                    "options": card.get("options", []),
                    "relayed": card.get("relayed"),
                    "answers": [{"answerer": answer["answerer"], **entry(answer), "history": [entry(old) for old in answer["history"]]} for answer in answers.get(card["key"], [])],
                }
            )
        return {
            "schema": "rule-questionnaire-answers-v1",
            "customer_id": head["customer_id"],
            "rule_version": head["rule_version"],
            "title": head["title"],
            "closed_at": head["closed_at"],
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "questions": questions,
        }
