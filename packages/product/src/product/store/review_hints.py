"""Read-time review suggestions and explicit user dispositions on existing findings."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePath

from psycopg.types.json import Jsonb

from product.identity import can_read, can_review
from product.review_hints import MAX_REVIEW_HINTS, HintResult, review_hints
from product.store.suites import SuiteStore
from product.store.task_view import file_round_audit


def _authorize(store, conn, principal, round_id, *, write=False):
    resource = store._resource(conn, round_id)
    grants = store._grants(conn, round_id, principal.user_id) if resource else set()
    if not resource or resource.kind != "audit_round" or not can_read(principal, resource, grants) or write and not can_review(principal, resource, grants):
        raise KeyError("missing")
    if not conn.execute("SELECT 1 FROM product.memberships WHERE tenant_id=%s AND user_id=%s AND role='member'", (principal.tenant_id, principal.user_id)).fetchone():
        raise KeyError("missing")


def _results(store, conn, principal, body):
    store._prepared_items(conn, principal, body["inputs"])
    if body["execution"] != "completed":
        return []
    snapshot = SuiteStore(store.settings, store.objects, role=store.role).read_snapshot(body["id"], principal.tenant_id, conn=conn)
    if snapshot is None:
        return []
    targets = {target.file_version_id: target for target in snapshot.targets}
    inputs = {item.file_version_id: item for item in snapshot.inputs}
    artifacts = {
        str(row[0]): str(row[1])
        for row in conn.execute(
            """SELECT file_version_id, suite_revision_id FROM product.checker_round_artifacts
           WHERE round_id=%s AND tenant_id=%s AND result_attempt=%s""",
            (body["id"], principal.tenant_id, body["result_attempt"]),
        ).fetchall()
    }
    results = []
    for frozen in body["inputs"]:
        version = frozen["file_version_id"]
        target, source = targets.get(version), inputs.get(version)
        if frozen["role"] != "target" or target is None or source is None or not frozen["rule"]:
            continue
        # Deep's generated artifact, not its starting Suite, owns its result.
        suite = artifacts.get(version) or (target.suite_revision_id if body["analysis_mode"] != "deep" else None)
        if suite is None:
            continue
        for check in file_round_audit(body, frozen)["checks"]:
            finding = check["finding"]
            if finding and check["execution"] == "completed" and check["coverage"] == "executed":
                results.append(
                    HintResult(body["id"], version, check["review_item_key"], check["rule_item"]["id"], suite, source.sha256, finding["status"], frozen["original_filename"], body["result_attempt"])
                )
    return results


def _pairs(body):
    """Only an explicitly declared frozen PDF/Excel relationship can pair files."""
    targets = {row["file_version_id"]: row for row in body["inputs"] if row["role"] == "target"}
    pairs = set()
    declarations = body.get("review_document_pairs", [])
    if not isinstance(declarations, list):
        return pairs
    for declaration in declarations:
        if not isinstance(declaration, dict):
            continue
        pdf, excel = declaration.get("pdf_file_version_id"), declaration.get("excel_file_version_id")
        if not isinstance(pdf, str) or not isinstance(excel, str) or pdf not in targets or excel not in targets:
            continue
        if PurePath(targets[pdf]["original_filename"]).suffix.lower() == ".pdf" and PurePath(targets[excel]["original_filename"]).suffix.lower() in {".xls", ".xlsx"}:
            pairs.add(frozenset({pdf, excel}))
    # Conflicting declarations are ambiguous, not evidence of a relationship.
    return {pair for pair in pairs if all(sum(version in other for other in pairs) == 1 for version in pair)}


def read_hints(store, conn, principal, body):
    _authorize(store, conn, principal, body["id"])
    try:
        current = _results(store, conn, principal, body)
    except (KeyError, ValueError):
        return []
    if not current:
        return []
    handled = {
        row[0]: row[1]["hint"] for row in conn.execute("SELECT item_key, review_hint_action FROM product.findings WHERE round_id=%s AND review_hint_action IS NOT NULL", (body["id"],)).fetchall()
    }
    candidates = conn.execute(
        """SELECT r.id FROM product.audit_rounds r
           WHERE r.tenant_id=%s AND r.execution='completed' AND r.id<>%s
             AND (r.created_at,r.id)<(SELECT created_at,id FROM product.audit_rounds WHERE id=%s)
             AND EXISTS (SELECT 1 FROM product.round_inputs i JOIN product.file_versions f ON f.id=i.file_version_id
                         WHERE i.round_id=r.id AND i.role='target' AND f.sha256=ANY(%s))
           ORDER BY r.created_at DESC,r.id DESC LIMIT 50""",
        (principal.tenant_id, body["id"], body["id"], list({row.sha256 for row in current})),
    ).fetchall()
    earlier = []
    for (round_id,) in candidates:
        try:
            _authorize(store, conn, principal, str(round_id))
            compared = store._round_public(conn, str(round_id))
            earlier.extend(_results(store, conn, principal, compared))
            if len(review_hints(current, earlier, _pairs(body), handled)) == MAX_REVIEW_HINTS:
                break
        except (KeyError, ValueError):
            continue
    return review_hints(current, earlier, _pairs(body), handled)


def annotate(store, conn, principal, body):
    hints = {hint["item_key"]: hint for hint in read_hints(store, conn, principal, body)}
    for finding in body["findings"]:
        if finding["item_key"] in hints:
            finding["review_hint"] = hints[finding["item_key"]]


def respond(store, principal, round_id, item_key, action, expected_hint):
    if action not in {"accepted", "ignored"} or not isinstance(item_key, str):
        raise ValueError("invalid_review_hint_action")
    with store._conn() as conn, conn.transaction():
        _authorize(store, conn, principal, round_id, write=True)
        existing = conn.execute("SELECT review_hint_action FROM product.findings WHERE round_id=%s AND item_key=%s FOR UPDATE", (round_id, item_key)).fetchone()
        if not existing:
            raise KeyError("missing")
        body = store._round_public(conn, round_id)
        store._prepared_items(conn, principal, body["inputs"])
        if existing[0] is not None and existing[0]["hint"] == expected_hint:
            compared_id = existing[0]["hint"]["compared_round_id"]
            _authorize(store, conn, principal, compared_id)
            compared = store._round_public(conn, compared_id)
            store._prepared_items(conn, principal, compared["inputs"])
            if existing[0]["action"] != action:
                raise ValueError("review_hint_response_conflict")
            return existing[0]
        hint = next((hint for hint in read_hints(store, conn, principal, body) if hint["item_key"] == item_key), None)
        if hint is None or hint != expected_hint:
            raise ValueError("review_hint_unavailable")
        receipt = {"round_id": round_id, "item_key": item_key, "action": action, "hint": hint, "user_id": principal.user_id, "recorded_at": datetime.now(timezone.utc).isoformat()}
        conn.execute("UPDATE product.findings SET review_hint_action=%s WHERE round_id=%s AND item_key=%s", (Jsonb(receipt), round_id, item_key))
        return receipt


def response_counts(store, principal):
    # The denominator is explicit dispositions, not inferred impressions from reads.
    with store._conn() as conn:
        rows = conn.execute(
            """SELECT r.id, f.review_hint_action FROM product.findings f
            JOIN product.audit_rounds r ON r.id=f.round_id WHERE r.tenant_id=%s AND f.review_hint_action IS NOT NULL""",
            (principal.tenant_id,),
        ).fetchall()
        counts = {"accepted": 0, "ignored": 0}
        for round_id, receipt in rows:
            try:
                _authorize(store, conn, principal, str(round_id))
                body = store._round_public(conn, str(round_id))
                store._prepared_items(conn, principal, body["inputs"])
                compared_id = receipt["hint"]["compared_round_id"]
                _authorize(store, conn, principal, compared_id)
                compared = store._round_public(conn, compared_id)
                store._prepared_items(conn, principal, compared["inputs"])
            except (KeyError, ValueError):
                continue
            if receipt["action"] in counts:
                counts[receipt["action"]] += 1
        total = sum(counts.values())
        return {**counts, "responded": total, "acceptance_ratio": counts["accepted"] / total if total else None, "ignore_ratio": counts["ignored"] / total if total else None}


def declare_pair(store, principal, round_id, pdf_id, excel_id):
    """An explicit user relation is metadata, not a change to the executed inputs."""
    from io import BytesIO
    from zipfile import BadZipFile, ZipFile

    import fitz
    import xlrd

    with store._conn() as conn, conn.transaction():
        _authorize(store, conn, principal, round_id, write=True)
        conn.execute("SELECT id FROM product.audit_rounds WHERE id=%s FOR UPDATE", (round_id,))
        body = store._round_public(conn, round_id)
        store._prepared_items(conn, principal, body["inputs"])
        targets = {row["file_version_id"] for row in body["inputs"] if row["role"] == "target"}
        if not isinstance(pdf_id, str) or not isinstance(excel_id, str) or pdf_id == excel_id or pdf_id not in targets or excel_id not in targets:
            raise ValueError("review_document_pair_invalid")
        pair = {"pdf_file_version_id": pdf_id, "excel_file_version_id": excel_id}
        pairs = body["review_document_pairs"]
        if pair in pairs:
            return {"round_id": round_id, "pairs": pairs}
        if any(pdf_id in existing.values() or excel_id in existing.values() for existing in pairs):
            raise ValueError("review_document_pair_conflict")
        pdf = store.readable_version(principal, pdf_id)
        excel = store.readable_version(principal, excel_id)
        pdf_bytes, excel_bytes = store.version_bytes(pdf), store.version_bytes(excel)
        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
                if not document.is_pdf or document.page_count < 1:
                    raise ValueError("review_document_pair_invalid")
            if excel_bytes.startswith(b"PK"):
                with ZipFile(BytesIO(excel_bytes)) as workbook:
                    if "xl/workbook.xml" not in workbook.namelist():
                        raise ValueError("review_document_pair_invalid")
            else:
                workbook = xlrd.open_workbook(file_contents=excel_bytes, on_demand=True)
                workbook.release_resources()
        except (ValueError, RuntimeError, BadZipFile, xlrd.XLRDError) as exc:
            raise ValueError("review_document_pair_invalid") from exc
        pairs = [*pairs, pair]
        conn.execute("UPDATE product.audit_rounds SET review_document_pairs=%s WHERE id=%s", (Jsonb(pairs), round_id))
        return {"round_id": round_id, "pairs": pairs}
