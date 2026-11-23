"""Signing off a round (#357): the person who ran it stands by its conclusion, until they withdraw it with a reason.

There is no list of signers: whoever accepted the round (`audit_rounds.created_by`) signs it and answers for it; others
in the enterprise read where it stands and may run and sign a re-audit of their own. Who signed or withdrew, when and
why are their actions in `product.actions` (#362). The round keeps only when it was signed, the signing action and what
was signed: each file's conclusion, the review version of every reviewed item, the items accepted on the machine's
conclusion and the reports' fingerprint. Withdrawing clears them; reviews can then be corrected and the round signed
again. Signing changes no result, review or report, and nothing already delivered.
"""

from __future__ import annotations

import hashlib
import json

from psycopg.types.json import Jsonb

from product.identity import Principal
from product.store.task_view import pending_count

SIGN_ROUTE = "/rounds/{round_id}/signoff"
WITHDRAW_ROUTE = "/rounds/{round_id}/signoff/withdraw"
MAX_REASON = 500


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def content(store, conn, principal: Principal, round_id: str, task_id: str | None) -> dict | None:
    """What signing the round now would freeze, as its task page shows it. None until the task has ended: every file
    audited and its report made (or failed), so nothing signed is still changing."""
    if not task_id:
        return None
    task = store._recognition_public(conn, principal, task_id)
    if task.get("round_id") != round_id or task.get("state") != "completed":
        return None
    files, unreviewed, reported = [], [], {}
    for row in task["files"]:
        audit = row.get("audit")
        if not audit:
            continue
        version = row["file"]["id"]
        checks = audit["checks"]
        for check in checks:
            if not check.get("review"):
                unreviewed.append({"file_version_id": version, "clause_id": str(check["rule_item"]["id"]), "item_key": check.get("review_item_key"), "outcome": check["outcome"]})
        report = audit.get("report") or {}
        if report.get("state") == "ready":
            reported[version] = report["report_id"]
        files.append(
            {
                "file_version_id": version,
                "filename": row["file"]["original_filename"],
                "rule_title": (audit.get("rule") or {}).get("title"),
                # The file's conclusion as the page shows it (confirmed original facts included), and the machine's.
                "conclusion": audit["confirmed_conclusion"],
                "machine_conclusion": audit["conclusion"],
                "pending_count": pending_count(audit),
                "reviewed_count": sum(bool(check.get("review")) for check in checks),
                "accepted_count": sum(not check.get("review") for check in checks),
                "report": None,
            }
        )
    digests = dict(
        conn.execute(
            "SELECT id::text, result->>'sha256' FROM engine.jobs WHERE id = ANY(%s::uuid[]) AND kind = 'report_pdf' AND status = 'succeeded'",
            (list(reported.values()),),
        ).fetchall()
    )
    for file in files:
        report_id = reported.get(file["file_version_id"])
        file["report"] = {"report_id": report_id, "sha256": digests.get(report_id)} if report_id else None
    reviews = [
        {"item_key": key, "version": version, "judgment": judgment}
        for key, version, judgment in conn.execute("SELECT item_key, version, judgment FROM product.current_reviews WHERE round_id = %s ORDER BY item_key", (round_id,)).fetchall()
    ]
    body = {
        "conclusion": task["conclusion"],
        "machine_conclusion": task["machine_conclusion"],
        "files": files,
        "reviewed_count": sum(file["reviewed_count"] for file in files),
        "accepted_count": sum(file["accepted_count"] for file in files),
        "reviews": reviews,
        "unreviewed": unreviewed,
        "report_fingerprint": _digest([[file["file_version_id"], (file["report"] or {}).get("sha256")] for file in files]),
    }
    # What the person saw: signing binds to it, and a round that changed since is shown to them again.
    return {**body, "fingerprint": _digest(body)}


def _round(conn, principal: Principal, round_id: str, *, lock: bool = False):
    row = conn.execute(
        """SELECT r.created_by, r.signed_at, r.signoff, r.signoff_action_id, CASE WHEN d.task_fingerprint IS NOT NULL THEN d.id::text END
           FROM product.audit_rounds r LEFT JOIN product.audit_drafts d ON d.id = r.source_draft_id
           WHERE r.id = %s AND r.tenant_id = %s"""
        + (" FOR UPDATE OF r" if lock else ""),
        (round_id, principal.tenant_id),
    ).fetchone()
    if row is None:
        raise KeyError("missing")
    return row


def _history(store, conn, principal: Principal, round_id: str, creator: str, current: list[str]) -> list[dict]:
    """Every signing and withdrawal of the round, oldest first, from the creator's actions."""
    rows = conn.execute(
        """SELECT a.route, a.at, a.params FROM product.actions a
           WHERE a.tenant_id = %s AND a.user_id = %s AND a.method = 'POST' AND a.route = ANY(%s) AND lower(a.path) LIKE ANY(%s)
             AND (a.outcome = 'succeeded' OR a.id = ANY(%s::uuid[]))
           ORDER BY a.at, a.id""",
        (principal.tenant_id, creator, [SIGN_ROUTE, WITHDRAW_ROUTE], [f"%/rounds/{round_id}/signoff", f"%/rounds/{round_id}/signoff/withdraw"], current),
    ).fetchall()
    if not rows:
        return []
    name = store._display_name(conn, principal.tenant_id, creator)
    return [
        {
            "action": "signed" if route == SIGN_ROUTE else "withdrawn",
            "at": at.isoformat(),
            "by": name,
            "reason": (" ".join(str((params or {}).get("reason") or "").split()) or None) if route == WITHDRAW_ROUTE else None,
        }
        for route, at, params in rows
    ]


def _status(store, conn, principal: Principal, round_id: str, action_id: str | None = None) -> dict:
    creator, signed_at, frozen, signing_action, task_id = _round(conn, principal, round_id)
    creator = str(creator)
    mine = creator == principal.user_id
    # The signer is the person whose action signed it, as recorded.
    signer = conn.execute("SELECT user_id::text FROM product.actions WHERE id = %s", (signing_action,)).fetchone() if signing_action else None
    current = None if signed_at else content(store, conn, principal, round_id, task_id)
    if signed_at:
        code = None if mine else "not_creator"
        reason = None if mine else "只有签发人可以撤回签发。"
    elif not mine:
        code, reason = "not_creator", "只有发起这一轮审核的人可以签发。"
    elif current is None:
        code, reason = "not_finished", "审核结束、报告生成完后才能签发。"
    else:
        code, reason = None, None
    return {
        "round_id": round_id,
        "task_id": task_id,
        "state": "signed" if signed_at else "unsigned",
        "signed_at": signed_at.isoformat() if signed_at else None,
        "signer": {"name": store._display_name(conn, principal.tenant_id, signer[0]), "mine": signer[0] == principal.user_id} if signer else None,
        "signed": frozen,
        "current": current,
        "can_sign": not signed_at and mine and current is not None,
        "can_withdraw": bool(signed_at) and mine,
        "unavailable": {"code": code, "message": reason} if code else None,
        "history": _history(store, conn, principal, round_id, creator, [str(item) for item in (signing_action, action_id) if item]),
    }


def status(store, principal: Principal, round_id: str) -> dict:
    """Where the round stands: signed (when, by whom, what) or not, and what signing it now would freeze."""
    with store._conn() as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return _status(store, conn, principal, round_id)


def sign(store, principal: Principal, round_id: str, *, action_id: str | None, expected: str | None = None) -> dict:
    """The creator signs the round as it stands now; `expected` is the fingerprint of what they were shown."""
    if not action_id:
        # A signing moment is always someone's recorded action (#362); without it nobody could be named as signer.
        raise ValueError("signoff_unrecorded")
    with store._conn() as conn, conn.transaction():
        # Reviews of the round wait on this lock and are refused once it is signed (`save_review`).
        creator, signed_at, _frozen, _action, task_id = _round(conn, principal, round_id, lock=True)
        if str(creator) != principal.user_id:
            raise KeyError("missing")
        if signed_at is not None:
            raise ValueError("round_already_signed")
        current = content(store, conn, principal, round_id, task_id)
        if current is None:
            raise ValueError("round_not_finished")
        if expected is not None and expected != current["fingerprint"]:
            raise ValueError("content_changed")
        conn.execute(
            "UPDATE product.audit_rounds SET signed_at = now(), signoff = %s, signoff_action_id = %s WHERE id = %s",
            (Jsonb(current), action_id, round_id),
        )
        return _status(store, conn, principal, round_id, action_id)


def withdraw(store, principal: Principal, round_id: str, reason: object, *, action_id: str | None = None) -> dict:
    """The signer takes the signoff back, saying why; the reason is their action's parameter, not stored here."""
    text = " ".join(reason.split()) if isinstance(reason, str) else ""
    if not text:
        raise ValueError("withdraw_reason_required")
    if len(text) > MAX_REASON:
        raise ValueError("withdraw_reason_too_long")
    with store._conn() as conn, conn.transaction():
        creator, signed_at, _frozen, _action, _task = _round(conn, principal, round_id, lock=True)
        if str(creator) != principal.user_id:
            raise KeyError("missing")
        if signed_at is None:
            raise ValueError("round_not_signed")
        conn.execute("UPDATE product.audit_rounds SET signed_at = NULL, signoff = NULL, signoff_action_id = NULL WHERE id = %s", (round_id,))
        return _status(store, conn, principal, round_id, action_id)
