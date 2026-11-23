"""Rule clarification questionnaires: question cards found offline on a customer's rules, and the customer's answers.

A questionnaire belongs to one enterprise (the tenant it was loaded for) and names one revision of one registered
customer's rulebook. Cards reach it from the clarification loop (`send_cards`, a platform operator's approved decision) or
offline through the database (clause-check's questionnaire script); every member of the enterprise reads it and answers. Each member's answer to a card is their own and keeps its
history; several members may answer the same card and no answer is final. Nothing here calls a model or touches rules,
checkers or audit results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5

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
        "SELECT id,customer_id,rule_version,title,imported_at,closed_at,rulebook_version,rulebook_revision FROM product.rule_questionnaires WHERE id=%s AND tenant_id=%s",
        (questionnaire_id, principal.tenant_id),
    ).fetchone()
    if row is None:
        raise KeyError("missing")
    return {
        "id": str(row[0]),
        "customer_id": row[1],
        "rule_version": row[2],
        "rulebook": {"version": row[6], "revision": row[7]},
        "title": row[3],
        "imported_at": row[4].isoformat(),
        "closed_at": row[5].isoformat() if row[5] else None,
    }


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
        "customer_name": customer.name if customer else head["customer_id"],
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
CARD_DEFAULTS: dict = {"clauses": [], "why": "", "options": [], "expect": "", "related": [], "sources": [], "items": [], "relayed": None, "follows": None}


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


def send_cards(store, principal, payload) -> dict:
    """Put cards before the enterprise on one rulebook revision: its questionnaire is created with the first cards and
    later cards are added to it. A card is written once; the same card again changes nothing, and a card that asks
    something else under a key already used is refused. A closed questionnaire takes no more cards."""
    with store._conn() as conn, conn.transaction():
        _member(conn, principal, write=True)
        return put_cards(conn, principal, payload)


def put_cards(conn, principal, payload) -> dict:
    """`send_cards` inside the caller's transaction (the caller has checked membership): a rulebook plan (#351) sends
    cards together with the revision they are asked on, or not at all."""
    required = {"customer_id", "rulebook", "cards"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("invalid_questionnaire_request")
    customer, rulebook, cards = customers.find(payload["customer_id"]), payload["rulebook"], payload["cards"]
    if customer is None or not isinstance(rulebook, dict) or set(rulebook) != {"version", "revision"} or not isinstance(rulebook["version"], str) or type(rulebook["revision"]) is not int:
        raise ValueError("invalid_questionnaire_request")
    keys = [card.get("key") if isinstance(card, dict) else None for card in cards] if isinstance(cards, list) else []
    if not keys or len(keys) > 500 or len(set(keys)) != len(keys) or not all(isinstance(key, str) and 1 <= len(key) <= 200 for key in keys):
        raise ValueError("invalid_questionnaire_request")
    for card in cards:
        options = [option.get("key") for option in card.get("options", [])]
        if card.get("group") not in {"common", "conclusion", "general", "clause"} or not str(card.get("question", "")).strip() or len(set(options)) != len(options) or RELAYED in options:
            raise ValueError("invalid_questionnaire_request")
    version, revision = rulebook["version"], rulebook["revision"]
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"rule-questionnaire:{principal.tenant_id}:{customer.id}:{version}:{revision}",))
    if not conn.execute(
        "SELECT 1 FROM product.rulebook_revisions WHERE tenant_id=%s AND customer_id=%s AND version=%s AND revision=%s", (principal.tenant_id, customer.id, version, revision)
    ).fetchone():
        raise ValueError("rulebook_revision_not_found")
    row = conn.execute(
        "SELECT id, closed_at FROM product.rule_questionnaires WHERE tenant_id=%s AND customer_id=%s AND rulebook_version=%s AND rulebook_revision=%s FOR UPDATE",
        (principal.tenant_id, customer.id, version, revision),
    ).fetchone()
    if row is None:
        ident = str(uuid4())
        label = f"{customer.name} 规则库 {version}" + (f" 修订 {revision}" if revision else "")
        conn.execute(
            "INSERT INTO product.rule_questionnaires(id, tenant_id, customer_id, rule_version, rulebook_version, rulebook_revision, title) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (ident, principal.tenant_id, customer.id, f"规则库 {version}", version, revision, label + " 待确认问题"),
        )
    elif row[1] is not None:
        raise ValueError("questionnaire_closed")
    else:
        ident = str(row[0])
    existing = dict(conn.execute("SELECT question_key, card FROM product.rule_questions WHERE questionnaire_id=%s", (ident,)).fetchall())
    position = conn.execute("SELECT coalesce(max(position), -1) FROM product.rule_questions WHERE questionnaire_id=%s", (ident,)).fetchone()[0]
    added = 0
    for card in cards:
        if card["key"] in existing:
            if existing[card["key"]] != card:
                raise ValueError("question_exists")
            continue
        position += 1
        added += 1
        conn.execute("INSERT INTO product.rule_questions(questionnaire_id, question_key, position, card) VALUES (%s,%s,%s,%s)", (ident, card["key"], position, Jsonb(card)))
    # Everyone who has answered this version's questionnaires hears about the new cards, once per send.
    recipients = [
        str(row[0])
        for row in conn.execute(
            """SELECT DISTINCT a.answered_by FROM product.rule_question_answers a JOIN product.rule_questionnaires q ON q.id = a.questionnaire_id
               WHERE q.tenant_id=%s AND q.customer_id=%s AND q.rulebook_version=%s""",
            (principal.tenant_id, customer.id, version),
        ).fetchall()
    ]
    if added:
        total = position + 1
        for user in recipients:
            payload = {"questionnaire_id": ident, "user_id": user, "added": added, "customer_id": customer.id, "rulebook": {"version": version, "revision": revision}}
            conn.execute(
                """INSERT INTO engine.jobs (id, tenant_id, kind, payload, status, result)
                   VALUES (%s, %s, 'rule_cards_notification', %s, 'queued', '{"state":"queued","reason":null}'::jsonb) ON CONFLICT DO NOTHING""",
                (str(uuid5(UUID(ident), f"cards:{total}:{user}")), principal.tenant_id, Jsonb(payload)),
            )
    return {**_summary(conn, principal, _questionnaire(conn, principal, ident)), "added": added, "notified": len(recipients) if added else 0}


def claim_card_notification(store, worker_ident: str) -> dict | None:
    """One queued card notification and its recipient's Feishu identity; None when nothing is queued."""
    with store._conn() as conn, conn.transaction():
        row = conn.execute(
            """SELECT id, tenant_id, payload FROM engine.jobs WHERE kind='rule_cards_notification' AND status='queued'
               AND (lease_until IS NULL OR lease_until < now()) ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1""",
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """UPDATE engine.jobs SET status='leased', attempt=attempt+1, lease_owner=%s, lease_until=now()+interval '90 seconds',
               result='{"state":"dispatching","reason":null}'::jsonb, updated_at=now() WHERE id=%s""",
            (worker_ident, row[0]),
        )
        identity = conn.execute(
            """SELECT i.tenant_key, i.subject FROM product.identities i JOIN product.tenants t ON t.provider=i.provider AND t.external_key=i.tenant_key
               JOIN product.memberships m ON m.user_id=i.user_id AND m.tenant_id=t.id WHERE i.user_id=%s AND t.id=%s AND i.app_id=%s""",
            (row[2]["user_id"], row[1], store.settings.feishu_app_id),
        ).fetchone()
        if identity is None:
            conn.execute(
                """UPDATE engine.jobs SET status='cancelled', result='{"state":"suppressed","reason":"recipient_unavailable"}'::jsonb, lease_until=NULL, updated_at=now() WHERE id=%s""",
                (row[0],),
            )
            return None
        return {"id": str(row[0]), "tenant_key": identity[0], "receive_id": identity[1], "worker": worker_ident, **row[2]}


def finish_card_notification(store, claimed: dict, receipt: dict) -> None:
    """A send whose outcome is unknown is never repeated: it stays leased and is recorded as unknown."""
    status = {"sent": "succeeded", "failed": "failed", "unknown": "leased"}[receipt["state"]]
    with store._conn() as conn, conn.transaction():
        conn.execute(
            "UPDATE engine.jobs SET status=%s, result=%s, lease_until=NULL, updated_at=now() WHERE id=%s AND lease_owner=%s AND kind='rule_cards_notification'",
            (status, Jsonb(receipt), claimed["id"], claimed["worker"]),
        )
