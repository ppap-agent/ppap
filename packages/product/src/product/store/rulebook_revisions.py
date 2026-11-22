"""Rulebook revisions as product data.

A revision belongs to one enterprise and names one registered customer, a version and a revision number. Its clauses
are rows of `product.rule_versions`, one per document type, written once and never offered to an audit. The revisions
shipped with the product (`<template_root>/<customer>/rulebook`) are copied in the first time an enterprise reads them;
later revisions come from the clarification loop or from the offline import, through the same checks. Nothing here
decides which revision audits use.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from product import customers, rulebooks
from product.logs import emit

ORIGINS = {"shipped", "local", "clarification"}


def _member(conn, principal) -> None:
    if not conn.execute("SELECT 1 FROM product.memberships WHERE tenant_id=%s AND user_id=%s AND role='member'", (principal.tenant_id, principal.user_id)).fetchone():
        raise KeyError("missing")


def _head(conn, tenant_id: str, customer_id: str, version: str, revision: int):
    return conn.execute(
        """SELECT id, base_version, base_revision, changes, origin, digest, created_at, clarified_at
           FROM product.rulebook_revisions WHERE tenant_id=%s AND customer_id=%s AND version=%s AND revision=%s""",
        (tenant_id, customer_id, version, revision),
    ).fetchone()


def _documents(conn, revision_id) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT body FROM product.rule_versions WHERE rulebook_revision_id=%s ORDER BY (body->>'position')::int", (revision_id,)).fetchall()
    return [{"key": body["type"], "title": body["title"], "clauses": [{"id": rule["id"], "category": rule["category"], "text": rule["rule_to_check"]} for rule in body["rules"]]} for (body,) in rows]


def _insert(conn, tenant_id: str, user_id: str | None, customer_id: str, version: str, revision: int, base: dict | None, documents: list[dict], changes: dict, origin: str) -> str:
    ident = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO product.rulebook_revisions(id, tenant_id, customer_id, version, revision, base_version, base_revision, changes, origin, digest, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ident, tenant_id, customer_id, version, revision, base and base["version"], base and base["revision"], Jsonb(changes), origin, rulebooks.digest(documents), user_id),
    )
    for position, document in enumerate(documents):
        body = {
            "kind": "rulebook",
            "type": document["key"],
            "title": document["title"],
            "position": position,
            "rules": [{"id": clause["id"], "category": clause["category"], "rule_to_check": clause["text"]} for clause in document["clauses"]],
        }
        conn.execute(
            "INSERT INTO product.rule_versions(id, tenant_id, title, body, created_by, rulebook_revision_id) VALUES (%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), tenant_id, document["title"], Jsonb(body), user_id, ident),
        )
    return ident


def ensure_shipped(conn, tenant_id: str, root: Path) -> None:
    """Copy in the revisions shipped with the product that this enterprise does not have yet. A shipped revision that
    no longer matches what was copied is reported and left as copied: a stored revision never changes."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("rulebook-revisions:" + tenant_id,))
    for customer_id in rulebooks.shipped_customers(root):
        for row in rulebooks.list_revisions(root, customer_id):
            found = rulebooks.read_revision(root, customer_id, row["version"], row["revision"])
            if found is None:
                continue
            head = _head(conn, tenant_id, customer_id, row["version"], row["revision"])
            if head is None:
                changes = {clause["id"]: clause["change"] for document in found["documents"] for clause in document["clauses"] if clause.get("change")}
                _insert(conn, tenant_id, None, customer_id, row["version"], row["revision"], found["base"], found["documents"], changes, "shipped")
            elif head[4] == "shipped" and head[5] != rulebooks.digest(found["documents"]):
                emit("rulebook_shipped_revision_differs", level=logging.WARNING, customer_id=customer_id, version=row["version"], revision=row["revision"])


def _summary(head, customer_id: str, version: str, revision: int, documents: list[dict]) -> dict[str, Any]:
    changes = head[3] or {}
    counts = Counter(changes[clause["id"]]["kind"] for document in documents for clause in document["clauses"] if clause["id"] in changes)
    return {
        "id": str(head[0]),
        "customer_id": customer_id,
        "version": version,
        "revision": revision,
        "clause_count": sum(len(document["clauses"]) for document in documents),
        "base": {"version": head[1], "revision": head[2]} if head[1] else None,
        "changes": {kind: counts.get(kind, 0) for kind in ("same", "changed", "new")} if changes else None,
        "origin": head[4],
        "created_at": head[6].isoformat(),
        "clarified": head[7] is not None,
    }


def list_revisions(store, principal, customer_id: str, root: Path) -> dict:
    """Every revision of a customer's rulebook this enterprise has, oldest first."""
    with store._conn() as conn, conn.transaction():
        _member(conn, principal)
        ensure_shipped(conn, principal.tenant_id, root)
        keys = conn.execute("SELECT version, revision FROM product.rulebook_revisions WHERE tenant_id=%s AND customer_id=%s", (principal.tenant_id, customer_id)).fetchall()
        rows = []
        for version, revision in sorted(keys, key=lambda key: (tuple(int(part) for part in key[0][1:].split(".")), key[1])):
            head = _head(conn, principal.tenant_id, customer_id, version, revision)
            rows.append(_summary(head, customer_id, version, revision, _documents(conn, head[0])))
        return {"revisions": rows}


def read_revision(store, principal, customer_id: str, version: str, revision: int, root: Path) -> dict | None:
    """One revision: its clauses by document type, each with how it changed, on what grounds, and its reading."""
    with store._conn() as conn, conn.transaction():
        _member(conn, principal)
        ensure_shipped(conn, principal.tenant_id, root)
        head = _head(conn, principal.tenant_id, customer_id, version, revision)
        if head is None:
            return None
        documents = _documents(conn, head[0])
        readings = dict(conn.execute("SELECT clause_id, reading FROM product.rule_clause_readings WHERE revision_id=%s", (head[0],)).fetchall())
        changes = head[3] or {}
        for document in documents:
            for clause in document["clauses"]:
                clause["change"] = changes.get(clause["id"])
                clause["reading"] = readings.get(clause["id"])
                clause["digest"] = rulebooks.clause_digest(clause["text"])
        return {**_summary(head, customer_id, version, revision, documents), "documents": documents}


def create_revision(store, principal, customer_id: str, payload: Any, root: Path) -> dict:
    """Write one revision, once. The same content again is the revision already written; other content under the same
    name is refused. The revision after another in the same version closes that one's questionnaire."""
    required = {"version", "revision", "base", "documents", "changes", "origin"}
    if not isinstance(payload, dict) or set(payload) != required or customers.find(customer_id) is None:
        raise ValueError("invalid_rulebook_revision")
    version, revision, base, origin = payload["version"], payload["revision"], payload["base"], payload["origin"]
    if not isinstance(version, str) or not rulebooks._VERSION.fullmatch(version) or type(revision) is not int or revision < 0 or origin not in ORIGINS - {"shipped"}:
        raise ValueError("invalid_rulebook_revision")
    if base is not None and (
        not isinstance(base, dict)
        or set(base) != {"version", "revision"}
        or not isinstance(base["version"], str)
        or not rulebooks._VERSION.fullmatch(base["version"])
        or type(base["revision"]) is not int
    ):
        raise ValueError("invalid_rulebook_revision")
    if revision > 0 and base != {"version": version, "revision": revision - 1}:
        raise ValueError("rulebook_revision_base_required")
    documents, changes = payload["documents"], payload["changes"]
    with store._conn() as conn, conn.transaction():
        _member(conn, principal)
        ensure_shipped(conn, principal.tenant_id, root)
        previous = None
        if base is not None:
            previous = _head(conn, principal.tenant_id, customer_id, base["version"], base["revision"])
            if previous is None:
                raise ValueError("rulebook_revision_base_missing")
        problems = rulebooks.revision_problems(documents, changes, _documents(conn, previous[0]) if previous and revision > 0 else None)
        if problems:
            raise ValueError("invalid_rulebook_revision:" + "；".join(problems)[:1000])
        existing = _head(conn, principal.tenant_id, customer_id, version, revision)
        if existing is not None:
            if existing[5] != rulebooks.digest(documents) or (existing[3] or {}) != changes:
                raise ValueError("rulebook_revision_exists")
        else:
            _insert(conn, principal.tenant_id, principal.user_id, customer_id, version, revision, base, documents, changes, origin)
            if revision > 0:
                conn.execute(
                    """UPDATE product.rule_questionnaires SET closed_at=now()
                       WHERE tenant_id=%s AND customer_id=%s AND rulebook_version=%s AND rulebook_revision=%s AND closed_at IS NULL""",
                    (principal.tenant_id, customer_id, version, revision - 1),
                )
    found = read_revision(store, principal, customer_id, version, revision, root)
    assert found is not None
    return found


def clarify(store, principal, customer_id: str, version: str, revision: int, root: Path) -> dict:
    """Record that this revision has no open question left. Deciding that is the caller's; here it is only written, once."""
    with store._conn() as conn, conn.transaction():
        _member(conn, principal)
        head = _head(conn, principal.tenant_id, customer_id, version, revision)
        if head is None:
            raise KeyError("missing")
        conn.execute("UPDATE product.rulebook_revisions SET clarified_by=%s, clarified_at=now() WHERE id=%s AND clarified_at IS NULL", (principal.user_id, head[0]))
    found = read_revision(store, principal, customer_id, version, revision, root)
    assert found is not None
    return found


def save_readings(store, principal, customer_id: str, version: str, revision: int, payload: Any, root: Path) -> dict:
    """Put a reading beside clauses of a clarified revision. A clause's reading is written once."""
    readings = payload.get("readings") if isinstance(payload, dict) and set(payload) == {"readings"} else None
    if not isinstance(readings, dict) or not readings:
        raise ValueError("invalid_clause_readings")
    with store._conn() as conn, conn.transaction():
        _member(conn, principal)
        head = _head(conn, principal.tenant_id, customer_id, version, revision)
        if head is None:
            raise KeyError("missing")
        if head[7] is None:
            raise ValueError("rulebook_revision_not_clarified")
        clauses = rulebooks.clauses_of(_documents(conn, head[0]))
        existing = dict(conn.execute("SELECT clause_id, reading FROM product.rule_clause_readings WHERE revision_id=%s", (head[0],)).fetchall())
        for clause, reading in readings.items():
            if clause not in clauses:
                raise ValueError("invalid_clause_readings")
            problems = rulebooks.reading_problems(clauses[clause]["text"], reading)
            if problems:
                raise ValueError(f"invalid_clause_reading:{clause}：" + "；".join(problems)[:800])
            if clause in existing:
                if existing[clause] != reading:
                    raise ValueError("clause_reading_exists")
                continue
            conn.execute("INSERT INTO product.rule_clause_readings(revision_id, clause_id, reading, confirmed_by) VALUES (%s,%s,%s,%s)", (head[0], clause, Jsonb(reading), principal.user_id))
    found = read_revision(store, principal, customer_id, version, revision, root)
    assert found is not None
    return found
