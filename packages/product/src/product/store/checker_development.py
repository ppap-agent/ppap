"""User-confirmed samples and durable, non-retrying checker optimization requests."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4, uuid5

from audit.checker_core import RuleBinding, checker_digest, runtime_revision
from audit.contracts import RuleInput
from audit.models import public_locator
from audit.standard_suites import library_revision
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from product.feedback_groups import REASON_CATEGORIES, group_feedback
from product.identity import Principal, can_read, can_review

# SQL candidate filtering matches AuthStore._resource + can_read for alias r.
# Keep the normal object authorization check before returning each record.
_ROUND_READ_ACCESS = """(
    EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id=r.id AND g.user_id=%s AND g.capability IN ('read','review'))
    OR NOT EXISTS (SELECT 1 FROM product.audit_drafts d WHERE d.id=r.source_draft_id AND d.task_fingerprint IS NOT NULL))"""


def _id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_checker_identifier") from None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _rule_semantics(body: dict) -> dict:
    return RuleInput.model_validate({**body, "rule_id": "", "rule_profile": body.get("rule_profile") or "v3"}).model_dump(mode="json", exclude={"rule_id"})


def _member(conn, principal, *, lock=False) -> None:
    query = "SELECT 1 FROM product.memberships WHERE tenant_id=%s AND user_id=%s AND role='member'"
    if not conn.execute(query + (" FOR KEY SHARE" if lock else ""), (principal.tenant_id, principal.user_id)).fetchone():
        raise KeyError("checker_access_denied")


def _feedback(row: dict) -> dict:
    fields = (
        "id",
        "operation_id",
        "round_id",
        "file_version_id",
        "rule_version_id",
        "rule_clause_id",
        "result_attempt",
        "original_status",
        "suite_revision_id",
        "suite_key",
        "ordinal",
        "expected_status",
        "rationale",
        "reason_category",
        "evidence",
        "revision",
    )
    return {**{name: str(row[name]) if isinstance(row[name], UUID) else row[name] for name in fields}, "case_ordinal": row.get("case_ordinal", row["ordinal"])}


def _case_ordinal(conn, row):
    return conn.execute(
        """SELECT case_ordinal FROM (
             SELECT round_id,file_version_id,dense_rank() OVER (ORDER BY min(ordinal)) AS case_ordinal
             FROM product.checker_feedback WHERE tenant_id=%s AND suite_key=%s GROUP BY round_id,file_version_id
           ) cases WHERE round_id=%s AND file_version_id=%s""",
        (row["tenant_id"], row["suite_key"], row["round_id"], row["file_version_id"]),
    ).fetchone()[0]


def _receipt(row: dict) -> dict:
    state = row["state"]
    # A dead lease after dispatch is never permission to consume the provider again.
    if state in {"queued", "running"} and row.get("lease_expired"):
        state = "unknown" if row.get("model_started_at") else "failed"
    return {
        "operation_id": str(row["operation_id"]),
        "suite_revision_id": str(row["suite_revision_id"]),
        "suite_key": row["frozen"]["suite_key"],
        "state": state,
        "candidate_revision_id": str(row["candidate_revision_id"]) if row["candidate_revision_id"] else None,
        "report": row["report"],
        "usage": {**row["usage"], "uncertain": True} if state == "unknown" else row["usage"],
        "failure": row["failure"] or ("execution_outcome_unknown" if state == "unknown" else "worker_lost_before_dispatch" if state == "failed" and row.get("lease_expired") else None),
        "cancel_requested": row["cancel_requested"],
        "created_at": row["created_at"].isoformat(),
    }


class CheckerDevelopmentStore:
    def __init__(self, store):
        from product.store.suites import SuiteStore

        self.store = store
        self.suites = SuiteStore(store.settings, store.objects, role=store.role)

    def _authorize_round(self, conn, principal, round_id, *, write=False):
        _member(conn, principal)
        resource = self.store._resource(conn, round_id)
        grants = self.store._grants(conn, round_id, principal.user_id) if resource else set()
        if not resource or resource.kind != "audit_round" or not can_read(principal, resource, grants) or write and not can_review(principal, resource, grants):
            raise KeyError("checker_access_denied")

    def _frozen_source(self, conn, principal, round_id: str, version_id: str):
        """The frozen Suite of this round's target is the code that produced its result."""
        snapshot = self.suites.read_snapshot(round_id, principal.tenant_id, conn=conn)
        target = next((item for item in snapshot.targets if item.file_version_id == version_id), None) if snapshot else None
        if target is None:
            raise ValueError("checker_artifact_unavailable")
        return self.suites.resolve_suite(target.suite_revision_id, principal.tenant_id, conn=conn)

    def _evidence(self, conn, principal, round_id, rows):
        if not isinstance(rows, list) or not rows or len(rows) > 20:
            raise ValueError("checker_feedback_evidence_required")
        versions = {str(row[0]) for row in conn.execute("SELECT file_version_id FROM product.round_inputs WHERE round_id=%s", (round_id,)).fetchall()}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"file_version_id", "locator", "excerpt"} or row["file_version_id"] not in versions:
                raise ValueError("checker_feedback_evidence_invalid")
            if public_locator(row["locator"]) is None or not isinstance(row["excerpt"], str) or len(row["excerpt"]) > 500:
                raise ValueError("checker_feedback_evidence_invalid")
        # Being a frozen round input is not a current grant to read its excerpt.
        self.store._prepared_items(conn, principal, [{"file_version_id": version, "role": "reference"} for version in sorted({row["file_version_id"] for row in rows})])
        return rows

    def save_feedback(self, principal, round_id, payload):
        round_id, operation_id = _id(round_id), _id(payload["operation_id"])
        version_id, rule_id = _id(payload["file_version_id"]), _id(payload["rule_version_id"])
        clause = payload["rule_clause_id"]
        category = payload.get("reason_category", "unspecified")
        if category not in REASON_CATEGORIES:
            raise ValueError("checker_feedback_invalid")
        if payload["expected_status"] not in {"PASS", "NG", "NA"} or not isinstance(payload["rationale"], str) or not payload["rationale"].strip() or len(payload["rationale"]) > 8000:
            raise ValueError("checker_feedback_invalid")
        fingerprint = _digest({"round_id": round_id, "user_id": principal.user_id, **payload})
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            from product.store.suite_releases import _lock

            _lock(conn, principal.tenant_id)
            self._authorize_round(conn, principal, round_id, write=True)
            self.store._prepared_items(conn, principal, [{"file_version_id": version_id, "role": "reference"}])
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("checker-feedback-operation:" + principal.tenant_id + ":" + operation_id,))
            cur.execute("SELECT * FROM product.checker_feedback WHERE tenant_id=%s AND operation_id=%s", (principal.tenant_id, operation_id))
            prior = cur.fetchone()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise ValueError("checker_operation_conflict")
                if prior["deleted_at"] is not None:
                    raise ValueError("checker_feedback_deleted")
                self._evidence(conn, principal, round_id, prior["evidence"])
                return _feedback({**prior, "case_ordinal": _case_ordinal(conn, prior)})
            head = conn.execute("SELECT result_attempt FROM product.audit_rounds WHERE id=%s AND tenant_id=%s FOR SHARE", (round_id, principal.tenant_id)).fetchone()
            if not head or head[0] != payload["result_attempt"]:
                raise ValueError("checker_result_changed")
            frozen_rule = conn.execute(
                "SELECT rule_body FROM product.round_inputs WHERE round_id=%s AND file_version_id=%s AND rule_version_id=%s AND role='target'", (round_id, version_id, rule_id)
            ).fetchone()
            if not frozen_rule or clause not in {str(row.get("id")) for row in (frozen_rule[0] or {}).get("rules", [])}:
                raise ValueError("checker_feedback_scope_mismatch")
            rows = conn.execute("SELECT status,locator FROM product.findings WHERE round_id=%s", (round_id,)).fetchall()
            matched = [
                row
                for row in rows
                if (row[1] or {}).get("file_version_id") == version_id and str(((row[1] or {}).get("semantic") or {}).get("clause_id") or (row[1] or {}).get("rule_item_id") or "") == clause
            ]
            if len(matched) != 1 or matched[0][0] != payload["original_status"]:
                raise ValueError("checker_result_changed")
            suite = self._frozen_source(conn, principal, round_id, version_id)
            evidence = self._evidence(conn, principal, round_id, payload["evidence"])
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("checker-samples:" + principal.tenant_id + ":" + suite.revision.suite_key,))
            ordinal = conn.execute("SELECT coalesce(max(ordinal),0)+1 FROM product.checker_feedback WHERE tenant_id=%s AND suite_key=%s", (principal.tenant_id, suite.revision.suite_key)).fetchone()[0]
            cur.execute(
                """INSERT INTO product.checker_feedback
                   (id,operation_id,tenant_id,created_by,round_id,file_version_id,rule_version_id,rule_clause_id,result_attempt,original_status,
                    suite_revision_id,suite_key,ordinal,expected_status,rationale,evidence,request_fingerprint,reason_category)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    uuid4(),
                    operation_id,
                    principal.tenant_id,
                    principal.user_id,
                    round_id,
                    version_id,
                    rule_id,
                    clause,
                    head[0],
                    matched[0][0],
                    suite.revision.id,
                    suite.revision.suite_key,
                    ordinal,
                    payload["expected_status"],
                    payload["rationale"].strip(),
                    Jsonb(evidence),
                    fingerprint,
                    category,
                ),
            )
            saved = cur.fetchone()
            saved["case_ordinal"] = _case_ordinal(conn, saved)
            self._invalidate_proofs(conn, principal.tenant_id, suite.revision.suite_key)
            return _feedback(saved)

    def edit_feedback(self, principal, round_id, feedback_id, *, expected_revision, payload=None):
        round_id, feedback_id = _id(round_id), _id(feedback_id)
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            from product.store.suite_releases import _lock

            _lock(conn, principal.tenant_id)
            self._authorize_round(conn, principal, round_id, write=True)
            cur.execute(
                "SELECT * FROM product.checker_feedback WHERE id=%s AND round_id=%s AND tenant_id=%s AND created_by=%s AND deleted_at IS NULL FOR UPDATE",
                (feedback_id, round_id, principal.tenant_id, principal.user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError("checker_feedback_missing")
            if type(expected_revision) is not int or row["revision"] != expected_revision:
                raise ValueError("checker_feedback_revision_conflict")
            self.store._prepared_items(conn, principal, [{"file_version_id": str(row["file_version_id"]), "role": "reference"}])
            self._invalidate_proofs(conn, principal.tenant_id, row["suite_key"])
            if payload is None:
                cur.execute("UPDATE product.checker_feedback SET deleted_at=now(),revision=revision+1,updated_at=now() WHERE id=%s", (feedback_id,))
                return {"id": feedback_id, "deleted": True, "revision": expected_revision + 1}
            category = payload.get("reason_category", "unspecified")
            if category not in REASON_CATEGORIES or payload["expected_status"] not in {"PASS", "NG", "NA"} or not payload["rationale"].strip():
                raise ValueError("checker_feedback_invalid")
            evidence = self._evidence(conn, principal, round_id, payload["evidence"])
            cur.execute(
                "UPDATE product.checker_feedback SET expected_status=%s,rationale=%s,evidence=%s,reason_category=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *",
                (payload["expected_status"], payload["rationale"].strip(), Jsonb(evidence), category, feedback_id),
            )
            saved = cur.fetchone()
            saved["case_ordinal"] = _case_ordinal(conn, saved)
            return _feedback(saved)

    def list_feedback(self, principal, round_id):
        round_id = _id(round_id)
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            self._authorize_round(conn, principal, round_id)
            cur.execute("SELECT * FROM product.checker_feedback WHERE round_id=%s AND tenant_id=%s AND deleted_at IS NULL ORDER BY ordinal", (round_id, principal.tenant_id))
            feedback = self._readable_feedback(conn, principal, cur.fetchall())
            cur.execute(
                """SELECT o.*, (j.lease_until < now()) AS lease_expired FROM product.checker_optimizations o
                   LEFT JOIN engine.jobs j ON j.id=o.job_id WHERE o.tenant_id=%s AND o.initiating_user_id=%s
                   AND o.frozen->'source_round_ids' ? %s ORDER BY o.created_at DESC LIMIT 50""",
                (principal.tenant_id, principal.user_id, round_id),
            )
            optimizations = []
            for row in cur.fetchall():
                try:
                    self._authorize_frozen(conn, principal, row["frozen"])
                except KeyError:
                    continue
                optimizations.append(_receipt(row))
            sources = []
            rows = conn.execute(
                """SELECT i.file_version_id,v.file_id,r.result_attempt FROM product.round_inputs i
                   JOIN product.file_versions v ON v.id=i.file_version_id AND v.tenant_id=%s
                   JOIN product.audit_rounds r ON r.id=i.round_id AND r.tenant_id=v.tenant_id
                   WHERE i.round_id=%s AND i.role='target' AND r.result_attempt IS NOT NULL ORDER BY i.file_version_id""",
                (principal.tenant_id, round_id),
            ).fetchall()
            for version_id, file_id, attempt in rows:
                resource = self.store._resource(conn, str(file_id))
                if resource is None or not can_read(principal, resource, self.store._grants(conn, str(file_id), principal.user_id)):
                    continue
                try:
                    suite = self._frozen_source(conn, principal, round_id, str(version_id))
                except ValueError as exc:
                    if str(exc) == "checker_artifact_unavailable":
                        continue
                    raise
                sources.append({"file_version_id": str(version_id), "suite_revision_id": suite.revision.id, "suite_key": suite.revision.suite_key})
            return {"round_id": round_id, "feedback": feedback, "optimizations": optimizations, "sources": sources}

    def list_suite_feedback(self, principal, revision_id, *, after=0, limit=64):
        """One bounded page; groups are suggestions within the returned page."""
        if not 1 <= limit <= 64 or after < 0:
            raise ValueError("checker_page_invalid")
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            revision = self.suites.get_revision(_id(revision_id), principal.tenant_id, conn=conn)
            if revision is None:
                raise KeyError("checker_access_denied")
            cur.execute(
                """SELECT f.* FROM product.checker_feedback f
                   JOIN product.audit_rounds r ON r.id=f.round_id AND r.tenant_id=f.tenant_id
                   JOIN product.file_versions v ON v.id=f.file_version_id AND v.tenant_id=f.tenant_id AND v.status='ready'
                   JOIN product.resources vr ON vr.id=v.file_id AND vr.kind='file' AND vr.tenant_id=f.tenant_id
                   WHERE f.tenant_id=%s AND f.suite_key=%s AND f.deleted_at IS NULL AND f.ordinal>%s
                     AND EXISTS (SELECT 1 FROM product.resource_grants g WHERE g.resource_id=v.file_id AND g.user_id=%s AND g.capability IN ('read','review'))
                     AND NOT EXISTS (
                         SELECT 1 FROM jsonb_array_elements(f.evidence) e WHERE NOT EXISTS (
                             SELECT 1 FROM product.file_versions ev
                             JOIN product.resources er ON er.id=ev.file_id AND er.kind='file' AND er.tenant_id=f.tenant_id
                             JOIN product.round_inputs ei ON ei.file_version_id=ev.id AND ei.round_id=f.round_id
                             JOIN product.resource_grants eg ON eg.resource_id=ev.file_id AND eg.user_id=%s AND eg.capability IN ('read','review')
                             WHERE ev.id::text=e->>'file_version_id' AND ev.tenant_id=f.tenant_id AND ev.status='ready'))
                     AND """
                + _ROUND_READ_ACCESS
                + " ORDER BY f.ordinal LIMIT %s",
                (principal.tenant_id, revision.suite_key, after, principal.user_id, principal.user_id, principal.user_id, limit + 1),
            )
            rows = cur.fetchall()
            feedback = self._readable_feedback(conn, principal, rows[:limit])
            return {"feedback": feedback, "groups": group_feedback(feedback, tenant_id=principal.tenant_id), "next_cursor": rows[limit - 1]["ordinal"] if len(rows) > limit else None}

    def affected_rounds(self, principal, revision_id, clause_id, *, after=None, limit=25, exclude_round_id=None):
        """Bound the scan, not just the matches, and authorize before returning history."""
        if not 1 <= limit <= 50 or not clause_id or len(clause_id) > 200:
            raise ValueError("checker_page_invalid")
        cursor = _id(after) if after else "00000000-0000-0000-0000-000000000000"
        excluded = _id(exclude_round_id) if exclude_round_id else None
        with self.store._conn() as conn, conn.transaction():
            _member(conn, principal)
            revision = self.suites.get_revision(_id(revision_id), principal.tenant_id, conn=conn)
            if revision is None:
                raise KeyError("checker_access_denied")
            rows = conn.execute(
                # Match the round grant boundary before pagination: neither the
                # cursor nor the scanned count may reveal unreadable round IDs.
                """SELECT r.id,r.created_at,r.result_attempt FROM product.audit_rounds r
                   WHERE r.tenant_id=%s AND r.id>%s AND """
                + _ROUND_READ_ACCESS
                + " ORDER BY r.id LIMIT %s",
                (principal.tenant_id, cursor, principal.user_id, limit + 1),
            ).fetchall()
            items = []
            for round_id, created_at, attempt in rows[:limit]:
                if str(round_id) == excluded or attempt is None:
                    continue
                try:
                    self._authorize_round(conn, principal, str(round_id))
                except KeyError:
                    continue
                findings = conn.execute(
                    """SELECT f.status,i.file_version_id FROM product.findings f
                       JOIN product.round_inputs i ON i.round_id=f.round_id AND i.file_version_id::text=f.locator->>'file_version_id' AND i.role='target'
                       WHERE f.round_id=%s AND coalesce(f.locator->'semantic'->>'clause_id', f.locator->>'rule_item_id')=%s ORDER BY i.file_version_id LIMIT 64""",
                    (round_id, clause_id),
                ).fetchall()
                for status, version_id in findings:
                    try:
                        self.store._prepared_items(conn, principal, [{"file_version_id": str(version_id), "role": "reference"}])
                        source = self._frozen_source(conn, principal, str(round_id), str(version_id))
                    except (KeyError, ValueError):
                        continue
                    if source.revision.suite_key == revision.suite_key:
                        items.append({"round_id": str(round_id), "file_version_id": str(version_id), "rule_clause_id": clause_id, "status": status, "created_at": created_at.isoformat()})
                        break
            return {"items": items, "scanned_rounds": min(limit, len(rows)), "next_cursor": str(rows[limit - 1][0]) if len(rows) > limit else None}

    def _readable_feedback(self, conn, principal, rows):
        result = []
        for row in rows:
            try:
                self._authorize_round(conn, principal, str(row["round_id"]))
                self.store._prepared_items(conn, principal, [{"file_version_id": str(row["file_version_id"]), "role": "reference"}])
                self._evidence(conn, principal, str(row["round_id"]), row["evidence"])
            except (KeyError, ValueError):
                continue
            result.append(_feedback({**row, "case_ordinal": _case_ordinal(conn, row)}))
        return result

    @staticmethod
    def _invalidate_proofs(conn, tenant_id, suite_key):
        # Feedback edits are not a replacement Oracle for an old proof. The old
        # report stays readable; adoption must explicitly revalidate current labels.
        conn.execute(
            """UPDATE product.suite_validations v SET status='stale',revision=revision+1,
                      proof=jsonb_set(v.proof,'{reasons}',coalesce(v.proof->'reasons','[]'::jsonb) || '["oracle_changed"]'::jsonb)
               FROM product.suite_revisions r WHERE v.revision_id=r.id AND v.tenant_id=%s
                 AND r.suite_key=%s AND v.status<>'stale' AND v.proof->'report'->>'oracle_revision' IS NOT NULL""",
            (tenant_id, suite_key),
        )

    def _freeze_files(self, conn, principal, sample, suite):
        """Persist exact source identities and effective rules, never query latest on dispatch."""
        rows = conn.execute(
            """SELECT i.role,v.id,v.file_id,v.original_filename,v.sha256,v.content_type,
                      i.rule_version_id,i.rule_title,i.rule_body,i.rule_execution,v.status
               FROM product.round_inputs i JOIN product.file_versions v ON v.id=i.file_version_id
               WHERE i.round_id=%s AND v.tenant_id=%s ORDER BY v.id""",
            (sample["round_id"], principal.tenant_id),
        ).fetchall()
        result = []
        for row in rows:
            target = str(row[1]) == str(sample["file_version_id"])
            if not target and row[0] != "reference":
                continue
            resource = self.store._resource(conn, str(row[2]))
            grants = self.store._grants(conn, str(row[2]), principal.user_id)
            if resource is None or not can_read(principal, resource, grants):
                raise KeyError("checker_access_denied")
            if row[10] != "ready" or not row[4]:
                raise ValueError("checker_input_unavailable")
            body = json.loads(suite.files["rules.json"]) if target else row[8]
            if target:
                body = {**body, "appendix": json.loads(suite.files.get("appendix.json", b"[]"))}
            result.append(
                {
                    "file_version_id": str(row[1]),
                    "file_id": str(row[2]),
                    "role": "target" if target else "reference",
                    "filename": row[3],
                    "sha256": row[4],
                    "content_type": row[5] or "",
                    "rule_version_id": str(row[6]) if row[6] else None,
                    "rule_title": row[7],
                    "rule_body": body,
                    "rule_execution": None if target else row[9],
                }
            )
        if not any(row["role"] == "target" for row in result):
            raise ValueError("checker_input_unavailable")
        return result

    def _authorize_frozen(self, conn, principal, frozen):
        _member(conn, principal)
        for round_id in frozen["source_round_ids"]:
            self._authorize_round(conn, principal, round_id)
        files = [item for sample in frozen["samples"] for item in sample["files"]] + frozen.get("generation", {}).get("example", [])
        for item in files:
            resource = self.store._resource(conn, item["file_id"])
            if resource is None or not can_read(principal, resource, self.store._grants(conn, item["file_id"], principal.user_id)):
                raise KeyError("checker_access_denied")

    def start(self, principal, suite_revision_id, payload):
        from product.store.suite_releases import _read_lock, current_selection

        suite_revision_id, operation_id = _id(suite_revision_id), _id(payload["operation_id"])
        feedback_ids = sorted({_id(value) for value in payload["feedback_ids"]})
        if not feedback_ids or len(feedback_ids) > 64 or payload["action"] not in {"save_only", "run_tests"}:
            raise ValueError("checker_optimization_invalid")
        fingerprint = _digest({"suite_revision_id": suite_revision_id, "user_id": principal.user_id, **payload, "feedback_ids": feedback_ids})
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            # An accepted operation cannot appear after membership retirement has
            # already cancelled that member's jobs. Deletion waits for admission.
            _member(conn, principal, lock=True)
            _read_lock(conn, principal.tenant_id)
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("checker-optimization:" + operation_id,))
            cur.execute(
                "SELECT o.*, (j.lease_until < now()) AS lease_expired FROM product.checker_optimizations o LEFT JOIN engine.jobs j ON j.id=o.job_id WHERE o.operation_id=%s AND o.tenant_id=%s",
                (operation_id, principal.tenant_id),
            )
            prior = cur.fetchone()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise ValueError("checker_operation_conflict")
                self._authorize_frozen(conn, principal, prior["frozen"])
                return _receipt(prior)
            suite = self.suites.resolve_suite(suite_revision_id, principal.tenant_id, conn=conn)
            from product.legacy_head_cutover import legacy_carrier_program

            # A new optimization is a new task. It must not execute a legacy carrier.
            # An already queued operation returned above keeps its frozen bytes.
            if legacy_carrier_program(suite.files):
                raise ValueError("suite_version_confirmation_required")
            related = {}
            for key in suite.revision.related_suite_keys:
                selected = current_selection(conn, principal.tenant_id, key)
                related_suite = self.suites.resolve_suite(selected["revision_id"], principal.tenant_id, conn=conn)
                if legacy_carrier_program(related_suite.files):
                    raise ValueError("suite_version_confirmation_required")
                related[key] = selected["revision_id"]
            cur.execute(
                "SELECT * FROM product.checker_feedback WHERE tenant_id=%s AND suite_key=%s AND deleted_at IS NULL ORDER BY ordinal LIMIT 65 FOR SHARE", (principal.tenant_id, suite.revision.suite_key)
            )
            samples = [{**row, "case_ordinal": _case_ordinal(conn, row)} for row in cur.fetchall()]
            if not set(feedback_ids) <= {str(row["id"]) for row in samples}:
                raise ValueError("checker_feedback_scope_mismatch")
            if len(samples) > 64:
                raise ValueError("checker_sample_limit")
            confirmed_rules = payload.get("confirmed_rule_revision_id")
            if confirmed_rules is not None and (confirmed_rules != suite_revision_id or payload.get("expected_feedback_revisions") != {str(row["id"]): row["revision"] for row in samples}):
                raise ValueError("checker_rule_confirmation_changed")
            current_rules = json.loads(suite.files["rules.json"])
            current_rules["appendix"] = json.loads(suite.files.get("appendix.json", b"[]"))
            current_clauses = {str(clause["id"]) for clause in current_rules["rules"]}
            from product.store.rule_impact import rule_impact

            try:
                selected_id = current_selection(conn, principal.tenant_id, suite.revision.suite_key)["revision_id"]
            except KeyError:
                selected_id = suite.revision.base_revision_id or suite.revision.id
            changes = rule_impact(self.suites.resolve_suite(selected_id, principal.tenant_id, conn=conn), suite)
            frozen_samples = []
            for row in samples:
                self._authorize_round(conn, principal, str(row["round_id"]), write=confirmed_rules is not None)
                original_rule = conn.execute(
                    "SELECT rule_body FROM product.round_inputs WHERE round_id=%s AND file_version_id=%s AND rule_version_id=%s AND role='target'",
                    (row["round_id"], row["file_version_id"], row["rule_version_id"]),
                ).fetchone()
                if original_rule is None:
                    raise ValueError("checker_feedback_scope_mismatch")
                frozen_samples.append(
                    {
                        **_feedback(row),
                        "confirmed_by": principal.user_id if confirmed_rules and row["rule_clause_id"] in current_clauses else str(row["created_by"]) if row["created_by"] else "deleted-member",
                        "original_confirmed_by": str(row["created_by"]) if row["created_by"] else "deleted-member",
                        "expected_rule_body": current_rules if confirmed_rules and row["rule_clause_id"] in current_clauses else original_rule[0],
                        "original_expected_rule_body": original_rule[0],
                        "rule_reconfirmed_by": principal.user_id if confirmed_rules and row["rule_clause_id"] in current_clauses else None,
                        "files": self._freeze_files(conn, principal, row, suite),
                    }
                )
            from product.checker_samples import feedback_cases

            feedback_cases(frozen_samples)  # Conflicting labels must be resolved before any model call.
            training = [row for row in frozen_samples if row["case_ordinal"] % 4 and row["rule_clause_id"] in current_clauses]
            if not training:
                raise ValueError("checker_training_sample_required")
            target = next((row for row in training if row["id"] in feedback_ids), None)
            if target is None:
                raise ValueError("checker_training_sample_required")
            visible = {item["sha256"] for row in training for item in row["files"]}
            if any(visible.intersection(item["sha256"] for item in row["files"]) for row in frozen_samples if row["case_ordinal"] % 4 == 0):
                raise ValueError("checker_holdout_input_exposed")
            selection = self.store.model_selection(principal.tenant_id, conn=conn) if payload["action"] == "run_tests" else None
            model_identity = selection.identity if selection is not None else {}
            if "expected_model_revision" in payload and payload["action"] == "run_tests" and payload["expected_model_revision"] != model_identity["revision"]:
                raise ValueError("model_configuration_changed")
            if selection is not None and payload.get("consume_model") is True:
                # Readiness only; no model call and no secret enters frozen JSON.
                selection.access("coding")
            frozen = {
                "suite_key": suite.revision.suite_key,
                "samples": frozen_samples,
                "target_sample_id": target["id"],
                "selected_feedback_ids": feedback_ids,
                "source_round_ids": sorted({str(row["round_id"]) for row in samples}),
                "suite_digest": suite.revision.resolved_digest,
                "related_revision_ids": related,
                "related_suite_digests": {key: self.suites.resolve_suite(value, principal.tenant_id, conn=conn).revision.resolved_digest for key, value in related.items()},
                "executor_revision": runtime_revision(self.store.settings.deep_execution_revision(model_identity), library_revision()) if payload["action"] == "run_tests" else "",
                "library_revision": library_revision(),
                "model_identity": model_identity,
                "consume_model": payload.get("consume_model") is True,
                "rule_changes": changes["clauses"],
                "confirmed_rule_revision_id": confirmed_rules,
            }
            frozen["oracle_revision"] = _digest(frozen_samples)
            candidate = None
            if payload["action"] == "save_only":
                candidate = self.suites.create_revision(
                    tenant_id=principal.tenant_id,
                    suite_key=suite.revision.suite_key,
                    files={},
                    created_by=principal.user_id,
                    base_revision_id=suite.revision.id,
                    related_suite_keys=suite.revision.related_suite_keys,
                    conn=conn,
                )
            job_id = uuid4() if payload["action"] == "run_tests" else None
            if job_id:
                conn.execute(
                    "INSERT INTO engine.jobs(id,tenant_id,kind,payload,status) VALUES (%s,%s,'checker_optimization',%s,'queued')",
                    (job_id, principal.tenant_id, Jsonb({"operation_id": operation_id, "initiating_user_id": principal.user_id, "executor_revision": frozen["executor_revision"]})),
                )
            cur.execute(
                """INSERT INTO product.checker_optimizations(operation_id,tenant_id,initiating_user_id,suite_revision_id,action,request_fingerprint,frozen,job_id,state,candidate_revision_id,report)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    operation_id,
                    principal.tenant_id,
                    principal.user_id,
                    suite_revision_id,
                    payload["action"],
                    fingerprint,
                    Jsonb(frozen),
                    job_id,
                    "queued" if job_id else "saved",
                    candidate.id if candidate else None,
                    Jsonb({"verified": False, "reasons": ["tests_not_run"]}) if candidate else None,
                ),
            )
            return _receipt(cur.fetchone())

    def start_generation(self, principal, rule_version_id, payload):
        """A program for an uploaded rule from its confirmed check points plus one unlabeled example file.

        Unconfirmed clauses stay unbound and are judged by people. The first program replaces the
        never-audited starter and is adopted unverified; a later one is a version the person adopts.
        """
        from product.store.formalizations import FormalizationStore
        from product.store.suite_releases import _read_lock, current_selection
        from product.suites import canonical_json, is_unimplemented_suite

        rule_version_id, operation_id, file_version_id = _id(rule_version_id), _id(payload["operation_id"]), _id(payload["file_version_id"])
        fingerprint = _digest({"generation": rule_version_id, "user_id": principal.user_id, "file_version_id": file_version_id})

        def replay(conn, cur):
            cur.execute(
                "SELECT o.*, (j.lease_until < now()) AS lease_expired FROM product.checker_optimizations o LEFT JOIN engine.jobs j ON j.id=o.job_id WHERE o.operation_id=%s AND o.tenant_id=%s",
                (operation_id, principal.tenant_id),
            )
            prior = cur.fetchone()
            if prior is None:
                return None
            if prior["request_fingerprint"] != fingerprint:
                raise ValueError("checker_operation_conflict")
            self._authorize_frozen(conn, principal, prior["frozen"])
            return _receipt(prior)

        def generatable(conn, suite_key):
            # Only the newest rule version carries the clauses the next program must check.
            latest = conn.execute(
                "SELECT id::text FROM product.rule_versions WHERE tenant_id=%s AND body->>'suite_key'=%s ORDER BY created_at DESC, id DESC LIMIT 1", (principal.tenant_id, suite_key)
            ).fetchone()
            if latest is None or latest[0] != rule_version_id:
                raise ValueError("checker_rule_version_stale")
            selection = current_selection(conn, principal.tenant_id, suite_key)
            return selection, is_unimplemented_suite(self.suites.resolve_suite(selection["revision_id"], principal.tenant_id, conn=conn))

        with self.store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            if (receipt := replay(conn, cur)) is not None:
                return receipt
        rule = self.store.get_rule(principal.tenant_id, rule_version_id)
        if rule is None:
            raise KeyError("checker_access_denied")
        body = rule.get("body") or {}
        suite_key, base_id = body.get("suite_key"), body.get("suite_revision_id")
        if not isinstance(suite_key, str) or not suite_key.startswith("custom:") or not isinstance(base_id, str):
            raise ValueError("checker_generation_unavailable")
        with self.store._conn() as conn, conn.transaction():
            selection, first = generatable(conn, suite_key)
            # A new program builds on the one in use while that one checks this version's clauses;
            # after a rule change the rule's own candidate already carries the program in use.
            current = self.suites.resolve_suite(selection["revision_id"], principal.tenant_id, conn=conn)
            published = self.suites.resolve_suite(base_id, principal.tenant_id, conn=conn)
            if not first and json.loads(current.files["rules.json"]).get("rules") == json.loads(published.files["rules.json"]).get("rules"):
                base_id = selection["revision_id"]
        # Copies only confirmed, complete check points; replays by its derived operation.
        materialized = FormalizationStore(self.store).materialize_candidate(principal, rule_version_id, {"operation_id": str(uuid5(_GENERATION, operation_id)), "suite_revision_id": base_id})
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal, lock=True)
            _read_lock(conn, principal.tenant_id)
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("checker-optimization:" + operation_id,))
            if (receipt := replay(conn, cur)) is not None:
                return receipt
            selection, first = generatable(conn, suite_key)
            source = self.suites.resolve_suite(materialized["id"], principal.tenant_id, conn=conn)
            if source.revision.related_suite_keys:
                raise ValueError("checker_generation_unavailable")
            rules = json.loads(source.files["rules.json"])
            clause_ids = sorted(rules.get("formalizations") or {})
            # Clauses the program in use already checks stay bound; newly confirmed ones join them.
            rules["checker_binding"] = dict.fromkeys(sorted({*clause_ids, *(rules.get("checker_binding") or {})}), "checker:create_audit")
            bound = self.suites.create_revision(
                tenant_id=principal.tenant_id,
                suite_key=suite_key,
                files={"rules.json": canonical_json(rules)},
                created_by=principal.user_id,
                base_revision_id=source.revision.id,
                related_suite_keys=(),
                conn=conn,
            )
            bound_suite = self.suites.resolve_suite(bound.id, principal.tenant_id, conn=conn)
            example = self._freeze_example(conn, principal, file_version_id, bound_suite, rule)
            model = self.store.model_selection(principal.tenant_id, conn=conn)
            model.access("coding")  # Readiness only; no model call and no secret enters frozen JSON.
            frozen = {
                "suite_key": suite_key,
                "samples": [],
                "target_sample_id": None,
                "selected_feedback_ids": [],
                "source_round_ids": [],
                "suite_digest": bound.resolved_digest,
                "related_revision_ids": {},
                "related_suite_digests": {},
                "executor_revision": runtime_revision(self.store.settings.deep_execution_revision(model.identity), library_revision()),
                "library_revision": library_revision(),
                "model_identity": model.identity,
                "consume_model": True,
                "rule_changes": [],
                "confirmed_rule_revision_id": None,
                "oracle_revision": _digest([]),
                "generation": {
                    "rule_version_id": rule_version_id,
                    "example": example,
                    "clause_ids": clause_ids,
                    "base_revision_id": base_id,
                    "head_revision": selection["head_revision"],
                    "adopt": first,
                },
            }
            job_id = uuid4()
            conn.execute(
                "INSERT INTO engine.jobs(id,tenant_id,kind,payload,status) VALUES (%s,%s,'checker_optimization',%s,'queued')",
                (job_id, principal.tenant_id, Jsonb({"operation_id": operation_id, "initiating_user_id": principal.user_id, "executor_revision": frozen["executor_revision"]})),
            )
            cur.execute(
                """INSERT INTO product.checker_optimizations(operation_id,tenant_id,initiating_user_id,suite_revision_id,action,request_fingerprint,frozen,job_id,state)
                   VALUES (%s,%s,%s,%s,'run_tests',%s,%s,%s,'queued') RETURNING *""",
                (operation_id, principal.tenant_id, principal.user_id, bound.id, fingerprint, Jsonb(frozen), job_id),
            )
            return _receipt(cur.fetchone())

    def generation_view(self, principal, rule_version_id):
        """What the rule page shows: whether the rule has a program and this member's latest generation."""
        from product.store.suite_releases import SuiteReleaseStore, current_selection
        from product.suites import is_unimplemented_suite

        rule = self.store.get_rule(principal.tenant_id, _id(rule_version_id))
        if rule is None:
            raise KeyError("checker_access_denied")
        body = rule.get("body") or {}
        suite_key = body.get("suite_key")
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            if not isinstance(suite_key, str) or not suite_key.startswith("custom:"):
                return {"rule_version_id": str(rule["id"]), "program": "unavailable", "operation": None}
            selection = current_selection(conn, principal.tenant_id, suite_key)
            current = self.suites.resolve_suite(selection["revision_id"], principal.tenant_id, conn=conn)
            program = "missing" if is_unimplemented_suite(current) else "verified" if selection["verification_status"] == "verified" else "unverified"
            current_revision_id = selection["revision_id"]
            cur.execute(
                """SELECT o.*, (j.lease_until < now()) AS lease_expired FROM product.checker_optimizations o LEFT JOIN engine.jobs j ON j.id=o.job_id
                   WHERE o.tenant_id=%s AND o.initiating_user_id=%s AND o.frozen->'generation'->>'rule_version_id'=%s ORDER BY o.created_at DESC LIMIT 1""",
                (principal.tenant_id, principal.user_id, str(rule["id"])),
            )
            row = cur.fetchone()
            operation, refusal = None, None
            if row is not None:
                self._authorize_frozen(conn, principal, row["frozen"])
                operation = _receipt(row)
            if program == "missing" and operation and operation["state"] == "completed" and operation["candidate_revision_id"] and row["frozen"]["generation"].get("adopt", True):
                # The member asked for a usable program. Adoption needs the API's authority, so the first read after
                # completion adopts it unverified; a rule published meanwhile keeps the starter and is reported.
                releases = SuiteReleaseStore(self.store.settings, self.store.objects, role=self.store.role)
                try:
                    with conn.transaction():
                        releases.publish(
                            principal,
                            suite_key,
                            operation["candidate_revision_id"],
                            row["frozen"]["generation"]["head_revision"],
                            str(uuid5(_GENERATION, "adopt:" + operation["operation_id"])),
                            force_unverified=True,
                            conn=conn,
                        )
                    program, current_revision_id = "unverified", operation["candidate_revision_id"]
                except ValueError as exc:
                    refusal = str(exc)
        return {
            "rule_version_id": str(rule["id"]),
            "suite_key": suite_key,
            "program": program,
            "current_revision_id": current_revision_id,
            "operation": operation,
            **({"adoption_refusal": refusal} if refusal else {}),
        }

    def _freeze_example(self, conn, principal, file_version_id, suite, rule):
        """The example is the generation target only; nobody has labeled its answers."""
        row = conn.execute(
            "SELECT v.id,v.file_id,v.original_filename,v.sha256,v.content_type,v.status FROM product.file_versions v WHERE v.id=%s AND v.tenant_id=%s",
            (file_version_id, principal.tenant_id),
        ).fetchone()
        if row is None:
            raise KeyError("checker_access_denied")
        resource = self.store._resource(conn, str(row[1]))
        if resource is None or not can_read(principal, resource, self.store._grants(conn, str(row[1]), principal.user_id)):
            raise KeyError("checker_access_denied")
        if row[5] != "ready" or not row[3]:
            raise ValueError("checker_input_unavailable")
        body = {**json.loads(suite.files["rules.json"]), "appendix": json.loads(suite.files.get("appendix.json", b"[]"))}
        return [
            {
                "file_version_id": str(row[0]),
                "file_id": str(row[1]),
                "role": "target",
                "filename": row[2],
                "sha256": row[3],
                "content_type": row[4] or "",
                "rule_version_id": str(rule["id"]),
                "rule_title": rule.get("title"),
                "rule_body": body,
                "rule_execution": None,
            }
        ]

    def read(self, principal, operation_id):
        with self.store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            cur.execute(
                """SELECT o.*, (j.lease_until < now()) AS lease_expired FROM product.checker_optimizations o
                   LEFT JOIN engine.jobs j ON j.id=o.job_id WHERE o.operation_id=%s AND o.tenant_id=%s AND o.initiating_user_id=%s""",
                (_id(operation_id), principal.tenant_id, principal.user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError("checker_optimization_missing")
            self._authorize_frozen(conn, principal, row["frozen"])
            return _receipt(row)

    def notification_context(self, conn, principal, operation_id):
        """Read the original terminal operation under the recipient's current access."""
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT * FROM product.checker_optimizations WHERE operation_id=%s AND tenant_id=%s
                   AND initiating_user_id=%s AND completed_at IS NOT NULL
                   AND state IN ('completed','failed','unknown','stopped')""",
                (_id(operation_id), principal.tenant_id, principal.user_id),
            )
            row = cur.fetchone()
        if row is None:
            raise KeyError("checker_optimization_missing")
        self._authorize_frozen(conn, principal, row["frozen"])
        target = next(sample for sample in row["frozen"]["samples"] if sample["id"] == row["frozen"]["target_sample_id"])
        return _receipt(row), target

    def stop(self, principal, operation_id):
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            cur.execute(
                """SELECT o.*, (j.lease_until < clock_timestamp()) AS lease_expired FROM product.checker_optimizations o
                   LEFT JOIN engine.jobs j ON j.id=o.job_id
                   WHERE o.operation_id=%s AND o.tenant_id=%s AND o.initiating_user_id=%s FOR UPDATE OF o""",
                (_id(operation_id), principal.tenant_id, principal.user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError("checker_optimization_missing")
            self._authorize_frozen(conn, principal, row["frozen"])
            if row["state"] in {"queued", "running"}:
                cur.execute(
                    """UPDATE product.checker_optimizations SET cancel_requested=true,
                       state=CASE WHEN state='queued' THEN 'stopped' ELSE state END,
                       completed_at=CASE WHEN state='queued' THEN now() ELSE completed_at END
                       WHERE operation_id=%s RETURNING *""",
                    (operation_id,),
                )
                row = {**cur.fetchone(), "lease_expired": row["lease_expired"]}
                if row["state"] == "stopped":
                    conn.execute("UPDATE engine.jobs SET status='failed',lease_until=NULL,updated_at=now() WHERE id=%s AND status IN ('queued','leased')", (row["job_id"],))
                    conn.execute("UPDATE engine.attempts SET finished_at=now(),outcome='failed' WHERE job_id=%s AND finished_at IS NULL", (row["job_id"],))
            return _receipt(row)

    def operation_for_dispatch(self, request):
        """Read-only preflight. The final dispatch CAS runs after inputs are materialized."""
        with self.store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM product.checker_optimizations WHERE operation_id=%s AND tenant_id=%s AND job_id=%s", (_id(request.operation_id), _id(request.tenant_id), _id(request.job_id)))
            row = cur.fetchone()
            if row is None or not self._owns(conn, request):
                raise KeyError("checker_operation_unauthorized")
            self._authorize_frozen(conn, principal_for_operation(row), row["frozen"])
            return row

    @staticmethod
    def _owns(conn, request, *, lock=False):
        return (
            conn.execute(
                """SELECT 1 FROM engine.jobs WHERE id=%s AND tenant_id=%s AND kind='checker_optimization'
               AND attempt=%s AND lease_owner=%s AND status='leased' AND lease_until>clock_timestamp()"""
                + (" FOR UPDATE" if lock else ""),
                (request.job_id, request.tenant_id, request.attempt, request.lease_owner),
            ).fetchone()
            is not None
        )

    def dispatch(self, request):
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM product.checker_optimizations WHERE operation_id=%s AND tenant_id=%s AND job_id=%s FOR UPDATE", (_id(request.operation_id), _id(request.tenant_id), _id(request.job_id))
            )
            row = cur.fetchone()
            if row is None or not self._owns(conn, request):
                raise KeyError("checker_operation_unauthorized")
            if row["state"] != "queued" or row["model_started_at"] or row["cancel_requested"]:
                return None
            self._authorize_frozen(conn, principal_for_operation(row), row["frozen"])
            if not self._owns(conn, request, lock=True):
                raise KeyError("checker_operation_unauthorized")
            cur.execute("UPDATE product.checker_optimizations SET state='running',model_started_at=now() WHERE operation_id=%s", (request.operation_id,))
            return row

    def active(self, request):
        with self.store._conn() as conn:
            return (
                self._owns(conn, request)
                and conn.execute(
                    """SELECT 1 FROM product.checker_optimizations o
                       JOIN product.memberships m ON m.tenant_id=o.tenant_id AND m.user_id=o.initiating_user_id
                       WHERE o.operation_id=%s AND o.job_id=%s AND o.state='running' AND o.cancel_requested=false""",
                    (request.operation_id, request.job_id),
                ).fetchone()
                is not None
            )

    def finish(self, request, outcome):
        """Atomic candidate/proof/receipt/job completion under the original live fence."""
        from product.store.suite_releases import SuiteReleaseStore, _lock

        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM product.checker_optimizations WHERE operation_id=%s AND tenant_id=%s AND job_id=%s FOR UPDATE", (request.operation_id, request.tenant_id, request.job_id))
            row = cur.fetchone()
            if row is None or row["state"] not in {"queued", "running"} or not self._owns(conn, request, lock=True):
                return False
            principal = principal_for_operation(row)
            candidate, report = None, outcome.receipt()
            if row["cancel_requested"]:
                state, failure = "stopped", "checker_cancelled"
            elif outcome.usage_uncertain:
                state, failure = "unknown", outcome.error_code or "execution_outcome_unknown"
            elif outcome.error_code:
                state, failure = "failed", outcome.error_code
            elif outcome.artifact is None or outcome.validation is None:
                state, failure = "failed", "checker_artifact_unavailable"
            else:
                state, failure = "completed", None
            # Preserve an actual fixed artifact even if a later trial failed. It is
            # never adopted, and a failed/unknown run cannot receive verified proof.
            # The candidate owns every file. It does not inherit the source revision.
            if outcome.artifact is not None and not row["cancel_requested"]:
                _lock(conn, principal.tenant_id)
                try:
                    base = self.suites.resolve_suite(str(row["suite_revision_id"]), principal.tenant_id, conn=conn)
                    if str(base.revision.id) != str(row["suite_revision_id"]) or base.revision.resolved_digest != row["frozen"]["suite_digest"]:
                        raise ValueError("checker_source_mismatch")
                    standalone_files = _standalone_candidate_files(base, outcome.artifact)
                    candidate = self.suites.create_revision(
                        tenant_id=principal.tenant_id,
                        suite_key=base.revision.suite_key,
                        files=standalone_files,
                        deleted_paths=(),
                        created_by=principal.user_id,
                        base_revision_id=None,
                        related_suite_keys=base.revision.related_suite_keys,
                        conn=conn,
                    )
                except (ValueError, KeyError) as exc:
                    candidate = None
                    if state == "completed":
                        state, failure = "failed", str(exc) if str(exc) in _STANDALONE_REFUSALS else "checker_source_incomplete"
                    if outcome.validation is not None:
                        report["validation"] = outcome.validation.model_copy(update={"reasons": (*outcome.validation.reasons, "candidate_rejected")}).receipt()
                if candidate is not None and outcome.validation is not None:
                    _lock(conn, principal.tenant_id)
                    proof = outcome.validation.model_copy(update={"suite_revision_id": candidate.id, "suite_digest": candidate.resolved_digest})
                    if any(
                        _rule_semantics(sample["expected_rule_body"]) != _rule_semantics(next(item["rule_body"] for item in sample["files"] if item["role"] == "target"))
                        for sample in row["frozen"]["samples"]
                        if sample["rule_clause_id"] in {str(clause["id"]) for clause in next(item["rule_body"] for item in sample["files"] if item["role"] == "target")["rules"]}
                    ):
                        proof = proof.model_copy(update={"reasons": tuple(dict.fromkeys((*proof.reasons, "oracle_rules_changed")))})
                    current_samples = conn.execute(
                        "SELECT id,revision FROM product.checker_feedback WHERE tenant_id=%s AND suite_key=%s AND deleted_at IS NULL ORDER BY ordinal",
                        (principal.tenant_id, base.revision.suite_key),
                    ).fetchall()
                    frozen_samples = [(item["id"], item["revision"]) for item in row["frozen"]["samples"]]
                    if [(str(item[0]), item[1]) for item in current_samples] != frozen_samples:
                        proof = proof.model_copy(update={"reasons": (*proof.reasons, "oracle_changed")})
                    if state != "completed":
                        proof = proof.model_copy(update={"reasons": (*proof.reasons, "development_incomplete")})
                    if _saved_binding_unverified(standalone_files, outcome.artifact):
                        proof = proof.model_copy(update={"reasons": tuple(dict.fromkeys((*proof.reasons, "oracle_clause_coverage_incomplete")))})
                    # Keep a proof of the frozen dependencies. If adoption changed
                    # during execution this proof is stored but exposed as stale.
                    _lock(conn, principal.tenant_id)
                    releases = SuiteReleaseStore(self.store.settings, self.store.objects, role=self.store.role)
                    try:
                        with conn.transaction():
                            releases._record_validation(conn, principal.tenant_id, principal.user_id, candidate.id, proof, 0)
                    except ValueError as exc:
                        if str(exc) != "suite_validation_inputs_changed":
                            raise
                        proof = proof.model_copy(update={"reasons": (*proof.reasons, "related_suite_changed")})
                    report["validation"] = proof.receipt()
            generation = row["frozen"].get("generation")
            if not self._owns(conn, request):
                raise ValueError("checker_lease_lost")
            cur.execute(
                """UPDATE product.checker_optimizations SET state=%s,candidate_revision_id=%s,report=%s,usage=%s,failure=%s,completed_at=now()
                   WHERE operation_id=%s""",
                (
                    state,
                    candidate.id if candidate else None,
                    Jsonb(report),
                    Jsonb({**outcome.usage, "source": outcome.usage_source, "uncertain": outcome.usage_uncertain}),
                    failure,
                    request.operation_id,
                ),
            )
            conn.execute(
                "UPDATE engine.jobs SET status=%s,result=%s,lease_until=NULL,updated_at=now() WHERE id=%s",
                ("succeeded" if state == "completed" else "failed", Jsonb({"operation_id": request.operation_id, "state": state}), request.job_id),
            )
            conn.execute(
                "UPDATE engine.attempts SET finished_at=now(),outcome=%s,diagnostic=%s WHERE job_id=%s AND attempt=%s",
                ("succeeded" if state == "completed" else "failed", Jsonb({"state": state, "failure": failure}), request.job_id, request.attempt),
            )
            if not generation:
                target = next(sample for sample in row["frozen"]["samples"] if sample["id"] == row["frozen"]["target_sample_id"])
                self.store._queue_task_notification(conn, None, "optimization_completed", target["round_id"], operation_id=request.operation_id)
            return True


def principal_for_operation(row: dict) -> Principal:
    if row["initiating_user_id"] is None:
        raise KeyError("checker_operator_removed")
    return Principal(str(row["initiating_user_id"]), str(row["tenant_id"]), "", "", "", "", "member")


def _candidate_files(base, artifact):
    if getattr(artifact, "binding", None) is None:
        _require_independent_program(artifact.files)
    rules = json.loads(base.files["rules.json"])
    binding = getattr(artifact, "binding", None)
    if binding is not None:
        if not isinstance(binding, RuleBinding) or binding.suite_key != base.revision.suite_key or binding.checker_digest != checker_digest(artifact.files):
            raise ValueError("checker_artifact_identity_mismatch")
        rules["checker_binding"] = dict(binding.entrypoints)
    else:
        rules["checker_binding"] = {item["id"]: "checker:create_audit" for item in rules["rules"]}
    return {**dict(artifact.files), "rules.json": json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()}


def _deleted_program_paths(base, artifact):
    # Artifact.files is the complete executed program, not a sparse edit. An
    # omitted optional module must not reappear through the Suite's base overlay.
    return ("resources.py",) if "resources.py" in base.files and "resources.py" not in artifact.files else ()


_STANDALONE_REFUSALS = frozenset(
    {
        "checker_source_mismatch",
        "checker_source_incomplete",
        "checker_binding_required",
        "checker_binding_expanded",
        "checker_artifact_unavailable",
        "checker_artifact_identity_mismatch",
        "checker_cross_version_import",
    }
)
_PROGRAM_PATHS = frozenset({"checker.py", "resources.py"})
# Derives the check-point copy and the adoption of one generation from its operation id.
_GENERATION = UUID("6f1d2c3a-8b4e-4f5a-9c7d-0e1f2a3b4c5d")


def _require_independent_program(files):
    import ast

    from audit.checker_lib.imports import import_violations

    for source in files.values():
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            raise ValueError("checker_source_incomplete") from None
        if import_violations(tree):
            raise ValueError("checker_cross_version_import")


def _standalone_factory_binding(rules: dict, binding: dict, clauses: set[str]) -> dict[str, str]:
    """Factory entrypoints for the frozen binding. Partial coverage does not gain clauses."""
    if set(binding) == clauses:
        # Same clause map _candidate_files writes for a generated factory.
        return {item["id"]: "checker:create_audit" for item in rules["rules"]}
    return {clause: "checker:create_audit" for clause in binding}


def _standalone_candidate_files(base, artifact) -> dict[str, bytes]:
    """Copy the selected revision.

    A native program keeps that revision's checker binding. A generated factory
    records ``checker:create_audit`` on this copy only.
    """
    from audit.checker_core import clause_ids

    from product.suites import REQUIRED_PATHS, SUITE_PATHS

    if set(artifact.files) - _PROGRAM_PATHS or "checker.py" not in artifact.files:
        raise ValueError("checker_artifact_unavailable")
    files = dict(base.files)
    if not REQUIRED_PATHS <= files.keys() or not set(files) <= SUITE_PATHS:
        raise ValueError("checker_source_incomplete")
    try:
        rules = json.loads(files["rules.json"])
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise ValueError("checker_source_incomplete") from None
    if not isinstance(rules, dict) or not isinstance(rules.get("rules"), list):
        raise ValueError("checker_source_incomplete")
    binding = rules.get("checker_binding")
    if not isinstance(binding, dict) or not binding:
        raise ValueError("checker_binding_required")
    try:
        clauses = set(clause_ids(rules))
    except ValueError:
        raise ValueError("checker_source_incomplete") from None
    if not set(binding) <= clauses:
        raise ValueError("checker_binding_expanded")
    artifact_binding = getattr(artifact, "binding", None)
    if artifact_binding is not None:
        if not isinstance(artifact_binding, RuleBinding) or artifact_binding.suite_key != base.revision.suite_key or artifact_binding.checker_digest != checker_digest(artifact.files):
            raise ValueError("checker_artifact_identity_mismatch")
        if not set(artifact_binding.entrypoints.items()) <= set(binding.items()):
            raise ValueError("checker_binding_expanded")
    elif getattr(artifact, "entrypoint", "checker:create_audit") != "checker:create_audit":
        raise ValueError("checker_artifact_identity_mismatch")
    if artifact_binding is None:
        _require_independent_program(artifact.files)
    files["checker.py"] = artifact.files["checker.py"]
    if "resources.py" in artifact.files:
        files["resources.py"] = artifact.files["resources.py"]
    else:
        files.pop("resources.py", None)
    if artifact_binding is None:
        mapped = _standalone_factory_binding(rules, binding, clauses)
        # A factory whose saved entrypoints are not the program it runs is not publishable.
        if set(mapped) > clauses or any(value != "checker:create_audit" for value in mapped.values()):
            raise ValueError("checker_artifact_identity_mismatch")
        if mapped != binding:
            rules["checker_binding"] = mapped
            files["rules.json"] = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return files


def _saved_binding_unverified(files: dict[str, bytes], artifact) -> bool:
    """A proof must not claim clauses or entrypoints the saved suite will not run."""
    from audit.checker_core import clause_ids

    rules = json.loads(files["rules.json"])
    binding = rules["checker_binding"]
    if set(binding) != set(clause_ids(rules)):
        return True
    return getattr(artifact, "binding", None) is None and any(entrypoint != "checker:create_audit" for entrypoint in binding.values())
