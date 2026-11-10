"""Durable clause formalizations bound to one published rule version.

Candidates are immutable. Confirmation is a current pointer plus history.
A model call is one operation. The same operation id reads back; it does not call again.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from documents.model_access import UNCONFIGURED_MODEL_IDENTITY, ModelUnavailable
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from product.formalization.propose import accept_model_proposal, hold_if_undecided
from product.formalization.spec import FormalizationError


def _id(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("invalid_formalization") from None


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _require_clause_text(clause: dict) -> None:
    text = clause.get("rule_to_check")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("invalid_formalization")


def source_binding(rule: dict, clause_id: str) -> tuple[dict, str]:
    body = rule.get("body") or {}
    rules = body.get("rules") if isinstance(body, dict) else None
    clause = next((item for item in rules or [] if str(item.get("id")) == clause_id), None)
    if clause is None:
        raise ValueError("clause_not_found")
    payload = {
        "rule_version_id": rule["id"],
        "clause": {key: clause.get(key) for key in ("id", "category", "priority", "rule_to_check", "type_of_rule")},
        "type": body.get("type"),
        "customers": body.get("customers") or [],
        "models": body.get("models") or [],
        "parts": body.get("parts") or [],
        "case_context": rule.get("case_context") or {},
    }
    return clause, _digest(payload)


def _is_member(conn, tenant_id: str, user_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM product.memberships WHERE tenant_id=%s AND user_id=%s AND role='member'",
            (tenant_id, user_id),
        ).fetchone()
        is not None
    )


def _member(conn, principal) -> None:
    if not _is_member(conn, principal.tenant_id, principal.user_id):
        raise KeyError("access_denied")


def _lock(conn, name: str) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (name,))


class FormalizationStore:
    def __init__(self, store):
        self.store = store

    def allows(self, tenant_id: str, user_id: str) -> bool:
        with self.store._conn() as conn:
            return _is_member(conn, tenant_id, user_id)

    def list_clauses(self, principal, rule_version_id: str) -> dict:
        rule = self._visible_rule(principal, rule_version_id)
        with self.store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            cur.execute(
                """SELECT * FROM product.rule_formalizations
                   WHERE tenant_id=%s AND rule_version_id=%s ORDER BY clause_id, formalization_revision""",
                (principal.tenant_id, rule["id"]),
            )
            candidates = cur.fetchall()
            cur.execute(
                """SELECT * FROM product.rule_formalization_confirmations
                   WHERE tenant_id=%s AND rule_version_id=%s AND current""",
                (principal.tenant_id, rule["id"]),
            )
            confirmed = {row["clause_id"]: row for row in cur.fetchall()}
        grouped: dict[str, list] = {}
        for row in candidates:
            grouped.setdefault(row["clause_id"], []).append(row)
        clauses = []
        for clause in rule["body"].get("rules") or []:
            rows = grouped.get(str(clause["id"]), [])
            latest = rows[-1] if rows else None
            current = confirmed.get(str(clause["id"]))
            _bound, digest = source_binding(rule, str(clause["id"]))
            clauses.append(_summary(str(clause["id"]), latest, current, digest))
        return {"rule_version_id": rule["id"], "clauses": clauses}

    def read_clause(self, principal, rule_version_id: str, clause_id: str) -> dict:
        rule = self._visible_rule(principal, rule_version_id)
        clause, digest = source_binding(rule, clause_id)
        with self.store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            cur.execute(
                """SELECT * FROM product.rule_formalizations
                   WHERE tenant_id=%s AND rule_version_id=%s AND clause_id=%s ORDER BY formalization_revision""",
                (principal.tenant_id, rule["id"], clause_id),
            )
            candidates = [_candidate(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT * FROM product.rule_formalization_confirmations
                   WHERE tenant_id=%s AND rule_version_id=%s AND clause_id=%s ORDER BY confirmed_at, id""",
                (principal.tenant_id, rule["id"], clause_id),
            )
            confirmations = [_confirmation(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT o.*, (j.lease_until IS NOT NULL AND j.lease_until < now()) AS lease_expired
                   FROM product.rule_formalization_operations o
                   LEFT JOIN engine.jobs j ON j.id=o.job_id
                   WHERE o.tenant_id=%s AND o.rule_version_id=%s AND o.clause_id=%s AND o.initiating_user_id=%s
                   ORDER BY o.created_at DESC LIMIT 1""",
                (principal.tenant_id, rule["id"], clause_id, principal.user_id),
            )
            operation_row = cur.fetchone()
        current = next((item for item in confirmations if item["current"]), None)
        latest = candidates[-1] if candidates else None
        operation = _operation(operation_row) if operation_row and operation_row["source_rule_digest"] == digest else None
        return {
            "rule_version_id": rule["id"],
            "clause_id": clause_id,
            "clause_text": clause.get("rule_to_check") or "",
            "source_rule_digest": digest,
            "state": _summary(clause_id, latest and _row_from_public(latest), current and _row_from_confirmation(current), digest)["state"],
            "candidates": candidates,
            "confirmations": confirmations,
            "operation": operation,
        }

    def start_generation(self, principal, rule_version_id: str, clause_id: str, body: dict) -> dict:
        rule = self._visible_rule(principal, rule_version_id)
        self._require_source(principal, rule)
        clause, digest = source_binding(rule, clause_id)
        _require_clause_text(clause)
        operation_id = _id(body["operation_id"])
        if body.get("expected_source_digest") != digest:
            raise ValueError("source_changed")
        based_on = _id(body["based_on_candidate_id"]) if body.get("based_on_candidate_id") else None
        clarification = body.get("clarification_answer") or {}
        if not isinstance(clarification, dict) or len(json.dumps(clarification, ensure_ascii=False)) > 4000:
            raise ValueError("invalid_formalization")
        if clarification and (not based_on or not isinstance(clarification.get("choice_id"), str) or not clarification["choice_id"].strip()):
            raise ValueError("invalid_formalization")
        instruction = body.get("user_instruction")
        if instruction is not None and (not isinstance(instruction, str) or len(instruction) > 2000):
            raise ValueError("invalid_formalization")
        if body.get("consume_model") is not True:
            raise ValueError("model_consumption_refused")
        identity = self.store.personal_model_identity(principal.tenant_id, principal.user_id)
        if identity == UNCONFIGURED_MODEL_IDENTITY or identity.get("revision") in {None, "", "unconfigured"}:
            raise ModelUnavailable()
        if body.get("expected_model_revision") not in {None, identity["revision"]}:
            raise ModelUnavailable()
        fingerprint = _digest(
            {
                "rule_version_id": rule["id"],
                "clause_id": clause_id,
                "expected_source_digest": digest,
                "based_on_candidate_id": based_on,
                "clarification_answer": clarification,
                "user_instruction": instruction,
                "expected_model_revision": body.get("expected_model_revision"),
            }
        )
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            _lock(conn, f"rule-formalization-user:{principal.tenant_id}:{rule['id']}:{clause_id}:{principal.user_id}")
            _lock(conn, "rule-formalization-op:" + operation_id)
            cur.execute(
                """SELECT o.*, (j.lease_until < now()) AS lease_expired
                   FROM product.rule_formalization_operations o LEFT JOIN engine.jobs j ON j.id=o.job_id
                   WHERE o.operation_id=%s AND o.tenant_id=%s""",
                (operation_id, principal.tenant_id),
            )
            prior = cur.fetchone()
            if prior:
                if prior["request_fingerprint"] != fingerprint or str(prior["initiating_user_id"]) != principal.user_id:
                    raise ValueError("operation_conflict")
                return _operation(prior)
            if based_on:
                cur.execute(
                    "SELECT unresolved FROM product.rule_formalizations WHERE id=%s AND tenant_id=%s AND rule_version_id=%s AND clause_id=%s",
                    (based_on, principal.tenant_id, rule["id"], clause_id),
                )
                found = cur.fetchone()
                if found is None:
                    raise ValueError("candidate_stale")
                if clarification and not _clarification_matches(found["unresolved"], clarification):
                    raise ValueError("invalid_formalization")
            cur.execute(
                """SELECT o.*, (j.lease_until < now()) AS lease_expired
                   FROM product.rule_formalization_operations o
                   LEFT JOIN engine.jobs j ON j.id=o.job_id
                   WHERE o.tenant_id=%s AND o.rule_version_id=%s AND o.clause_id=%s AND o.initiating_user_id=%s
                     AND o.source_rule_digest=%s AND o.state IN ('queued','running','unknown')
                   ORDER BY o.created_at DESC LIMIT 1""",
                (principal.tenant_id, rule["id"], clause_id, principal.user_id, digest),
            )
            inflight = cur.fetchone()
            if inflight and (inflight["state"] == "unknown" or not inflight.get("lease_expired") or inflight.get("model_started_at")):
                if inflight["request_fingerprint"] != fingerprint:
                    raise ValueError("operation_conflict")
                return _operation(inflight)
            job_id = uuid4()
            conn.execute(
                "INSERT INTO engine.jobs(id,tenant_id,kind,payload,status) VALUES (%s,%s,'rule_formalization',%s,'queued')",
                (job_id, principal.tenant_id, Jsonb({"operation_id": operation_id, "initiating_user_id": principal.user_id, "model_identity": identity})),
            )
            cur.execute(
                """INSERT INTO product.rule_formalization_operations(
                       operation_id,tenant_id,initiating_user_id,rule_version_id,clause_id,request_fingerprint,
                       source_rule_digest,based_on_candidate_id,clarification,user_instruction,model_identity,job_id,state)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued') RETURNING *""",
                (operation_id, principal.tenant_id, principal.user_id, rule["id"], clause_id, fingerprint, digest, based_on, Jsonb(clarification), instruction, Jsonb(identity), job_id),
            )
            return _operation(cur.fetchone())

    def read_operation(self, principal, operation_id: str) -> dict:
        with self.store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            cur.execute(
                """SELECT o.*, (j.lease_until < now()) AS lease_expired
                   FROM product.rule_formalization_operations o LEFT JOIN engine.jobs j ON j.id=o.job_id
                   WHERE o.operation_id=%s AND o.tenant_id=%s AND o.initiating_user_id=%s""",
                (_id(operation_id), principal.tenant_id, principal.user_id),
            )
            row = cur.fetchone()
        if row is None:
            raise KeyError("access_denied")
        self._visible_rule(principal, str(row["rule_version_id"]))
        return _operation(row)

    def confirm(self, principal, rule_version_id: str, clause_id: str, body: dict) -> dict:
        rule = self._visible_rule(principal, rule_version_id)
        self._require_source(principal, rule)
        clause, digest = source_binding(rule, clause_id)
        _require_clause_text(clause)
        operation_id = _id(body["operation_id"])
        candidate_id = _id(body["candidate_id"])
        expected_revision = body["expected_candidate_revision"]
        if not isinstance(expected_revision, int) or expected_revision < 1:
            raise ValueError("invalid_formalization")
        if body.get("expected_source_digest") != digest:
            raise ValueError("source_changed")
        from product.rules import clause_source_checked

        if clause_source_checked(rule.get("case_context"), clause_id) is False:
            raise ValueError("source_unchecked")
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            _lock(conn, f"rule-formalization:{principal.tenant_id}:{rule['id']}:{clause_id}")
            cur.execute(
                "SELECT * FROM product.rule_formalization_confirmations WHERE tenant_id=%s AND operation_id=%s",
                (principal.tenant_id, operation_id),
            )
            prior = cur.fetchone()
            if prior:
                if str(prior["candidate_id"]) != candidate_id or prior["formalization_revision"] != expected_revision or prior["display_digest"] != body.get("expected_display_digest"):
                    raise ValueError("operation_conflict")
                return _confirmation(prior)
            cur.execute(
                "SELECT body, case_context FROM product.rule_versions WHERE id=%s AND tenant_id=%s FOR UPDATE",
                (rule["id"], principal.tenant_id),
            )
            fresh = cur.fetchone()
            if fresh is None:
                raise KeyError("access_denied")
            fresh_rule = {**rule, "body": fresh["body"], "case_context": fresh["case_context"] or {}}
            fresh_clause, fresh_digest = source_binding(fresh_rule, clause_id)
            _require_clause_text(fresh_clause)
            if fresh_digest != body.get("expected_source_digest"):
                raise ValueError("source_changed")
            if clause_source_checked(fresh_rule.get("case_context"), clause_id) is False:
                raise ValueError("source_unchecked")
            digest = fresh_digest
            cur.execute(
                """SELECT * FROM product.rule_formalizations
                   WHERE id=%s AND tenant_id=%s AND rule_version_id=%s AND clause_id=%s""",
                (candidate_id, principal.tenant_id, rule["id"], clause_id),
            )
            candidate = cur.fetchone()
            if candidate is None:
                raise ValueError("candidate_stale")
            if candidate["formalization_revision"] != expected_revision:
                raise ValueError("candidate_stale")
            if candidate["display_digest"] != body.get("expected_display_digest"):
                raise ValueError("display_changed")
            if candidate["source_rule_digest"] != digest:
                raise ValueError("source_changed")
            if candidate["completeness"] != "complete" or candidate["unresolved"]:
                raise ValueError("not_confirmable")
            cur.execute(
                "SELECT MAX(formalization_revision) AS revision FROM product.rule_formalizations WHERE tenant_id=%s AND rule_version_id=%s AND clause_id=%s",
                (principal.tenant_id, rule["id"], clause_id),
            )
            if cur.fetchone()["revision"] != expected_revision:
                raise ValueError("candidate_stale")
            cur.execute(
                """SELECT * FROM product.rule_formalization_confirmations
                   WHERE tenant_id=%s AND rule_version_id=%s AND clause_id=%s AND current FOR UPDATE""",
                (principal.tenant_id, rule["id"], clause_id),
            )
            current = cur.fetchone()
            expected_current = body.get("expected_current_confirmation_id")
            if expected_current is not None:
                expected_current = _id(expected_current)
            if current and str(current["candidate_id"]) != candidate_id:
                if expected_current != str(current["id"]):
                    raise ValueError("conflicting_confirmation")
                cur.execute("UPDATE product.rule_formalization_confirmations SET current=false WHERE id=%s", (current["id"],))
            elif current and str(current["candidate_id"]) == candidate_id:
                raise ValueError("conflicting_confirmation")
            elif expected_current is not None:
                raise ValueError("conflicting_confirmation")
            cur.execute(
                """INSERT INTO product.rule_formalization_confirmations(
                       id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                       source_rule_digest,display_digest,confirmed_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (uuid4(), operation_id, principal.tenant_id, rule["id"], clause_id, candidate_id, expected_revision, digest, candidate["display_digest"], principal.user_id),
            )
            return _confirmation(cur.fetchone())

    def parent_snapshot(self, row) -> dict | None:
        if not row.get("based_on_candidate_id"):
            return None
        with self.store._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT owl_content, clif_content, formal_digest FROM product.rule_formalizations
                   WHERE id=%s AND tenant_id=%s AND rule_version_id=%s AND clause_id=%s""",
                (row["based_on_candidate_id"], row["tenant_id"], row["rule_version_id"], row["clause_id"]),
            )
            found = cur.fetchone()
        if found is None:
            return None
        return {"owl_content": found["owl_content"], "clif_content": found["clif_content"], "formal_digest": found["formal_digest"]}

    def dispatch(self, claimed, worker_ident: str) -> dict | None:
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            row = self._owned(cur, conn, claimed, worker_ident, lock=True)
            if row is None:
                return None
            if row["state"] != "queued" or row["model_started_at"]:
                return None
            cur.execute("UPDATE product.rule_formalization_operations SET state='running', model_started_at=now() WHERE operation_id=%s", (row["operation_id"],))
            row["state"] = "running"
            return row

    def finish(
        self,
        claimed,
        worker_ident: str,
        *,
        raw: str | None = None,
        error_code: str | None = None,
        usage_uncertain: bool = False,
        usage: dict | None = None,
        allow_expired: bool = False,
    ) -> str | None:
        with self.store._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            row = self._owned(cur, conn, claimed, worker_ident, lock=True, allow_expired=allow_expired)
            if row is None or row["state"] not in {"queued", "running"}:
                return None
            rule = self.store.get_rule(str(row["tenant_id"]), str(row["rule_version_id"]))
            state = "failed"
            failure: str | None = error_code or "formalization_failed"
            candidate_id = None
            if error_code == "stopped":
                state, failure = "stopped", "stopped"
            elif usage_uncertain:
                state, failure = "unknown", error_code or "execution_outcome_unknown"
            elif raw and rule is not None:
                try:
                    clause, digest = source_binding(rule, row["clause_id"])
                    if digest != row["source_rule_digest"]:
                        raise FormalizationError("source_changed")
                    checked = accept_model_proposal(
                        raw,
                        source_rule_version_id=str(row["rule_version_id"]),
                        clause_id=row["clause_id"],
                        clause_text=clause.get("rule_to_check") or "",
                        clarification=row.get("clarification") if isinstance(row.get("clarification"), dict) else None,
                        instruction=row.get("user_instruction") if isinstance(row.get("user_instruction"), str) else None,
                    )
                    prior_unresolved = None
                    clarification = row.get("clarification") or {}
                    if isinstance(clarification, dict) and clarification.get("choice_id") == "undecided" and row.get("based_on_candidate_id"):
                        cur.execute(
                            "SELECT unresolved FROM product.rule_formalizations WHERE id=%s AND tenant_id=%s AND rule_version_id=%s AND clause_id=%s",
                            (row["based_on_candidate_id"], row["tenant_id"], row["rule_version_id"], row["clause_id"]),
                        )
                        prior = cur.fetchone()
                        prior_unresolved = prior["unresolved"] if prior else None
                    checked = hold_if_undecided(
                        checked,
                        clarification,
                        clause_text=clause.get("rule_to_check") or "",
                        prior_unresolved=prior_unresolved,
                    )
                    candidate_id = self._insert_candidate(cur, row, checked, digest)
                    state, failure = "completed", None
                except FormalizationError as exc:
                    state, failure = "failed", exc.code
            elif rule is None:
                state, failure = "failed", "access_denied"
            if not self._owns(conn, claimed, worker_ident, allow_expired=allow_expired):
                return None
            reported = usage or {}
            usage_record = {**reported, "uncertain": usage_uncertain or not usage, "source": "unreported" if not usage else reported.get("source", "reported")}
            cur.execute(
                """UPDATE product.rule_formalization_operations
                   SET state=%s, candidate_id=%s, usage=%s, failure=%s, completed_at=now()
                   WHERE operation_id=%s AND state IN ('queued','running')""",
                (state, candidate_id, Jsonb(usage_record), failure, row["operation_id"]),
            )
            conn.execute(
                "UPDATE engine.jobs SET status=%s, result=%s, lease_until=NULL, updated_at=now() WHERE id=%s AND attempt=%s AND lease_owner=%s AND status='leased'",
                ("succeeded" if state == "completed" else "failed", Jsonb({"operation_id": str(row["operation_id"]), "state": state, "failure": failure}), claimed.id, claimed.attempt, worker_ident),
            )
            conn.execute(
                "UPDATE engine.attempts SET finished_at=now(), outcome=%s, diagnostic=%s WHERE job_id=%s AND attempt=%s",
                ("succeeded" if state == "completed" else "failed", Jsonb({"state": state, "failure": failure}), claimed.id, claimed.attempt),
            )
            return state

    def _insert_candidate(self, cur, row, checked: dict, digest: str):
        cur.execute(
            "SELECT COALESCE(MAX(formalization_revision), 0) AS revision FROM product.rule_formalizations WHERE tenant_id=%s AND rule_version_id=%s AND clause_id=%s",
            (row["tenant_id"], row["rule_version_id"], row["clause_id"]),
        )
        revision = int(cur.fetchone()["revision"]) + 1
        candidate_id = uuid4()
        spec = checked["spec"]
        cur.execute(
            """INSERT INTO product.rule_formalizations(
                   id,tenant_id,rule_version_id,clause_id,formalization_revision,parent_candidate_id,source_rule_digest,
                   formal_profile,owl_content,clif_content,vocabulary,obligations,assumptions,unresolved,completeness,
                   cnl_text,cnl_renderer_version,formal_digest,display_digest,created_by,operation_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                candidate_id,
                row["tenant_id"],
                row["rule_version_id"],
                row["clause_id"],
                revision,
                row["based_on_candidate_id"],
                digest,
                spec["formal_profile"],
                spec["owl_content"],
                spec["clif_content"],
                Jsonb(spec["vocabulary"]),
                Jsonb(spec["obligations"]),
                Jsonb(spec["assumptions"]),
                Jsonb(spec["unresolved"]),
                checked["completeness"],
                checked["cnl_text"],
                checked["cnl_renderer_version"],
                checked["formal_digest"],
                checked["display_digest"],
                row["initiating_user_id"],
                row["operation_id"],
            ),
        )
        return candidate_id

    def materialize_candidate(self, principal, rule_version_id: str, body: dict) -> dict:
        """Copy confirmed clause semantics into one unadopted Suite candidate."""
        from product.rules import clause_source_checked
        from product.store.suite_releases import SuiteReleaseStore
        from product.store.suites import SuiteStore

        rule = self._visible_rule(principal, rule_version_id)
        revision_id = _id(body["suite_revision_id"])
        operation_id = _id(body["operation_id"])
        suites = SuiteStore(self.store.settings, self.store.objects, role=self.store.role)
        resolved = suites.resolve_suite(revision_id, principal.tenant_id)
        if resolved.revision.tenant_id not in {None, principal.tenant_id}:
            raise KeyError("access_denied")
        rules = json.loads(resolved.files["rules.json"])
        if not isinstance(rules, dict) or not isinstance(rules.get("rules"), list):
            raise ValueError("invalid_formalization")
        clause_ids = {str(item.get("id")) for item in rules["rules"] if isinstance(item, dict)}
        releases = SuiteReleaseStore(self.store.settings, self.store.objects, role=self.store.role)
        with self.store._conn() as conn, conn.transaction():
            _member(conn, principal)
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT body, case_context FROM product.rule_versions WHERE id=%s AND tenant_id=%s FOR UPDATE",
                    (rule["id"], principal.tenant_id),
                )
                fresh = cur.fetchone()
                if fresh is None:
                    raise KeyError("access_denied")
                fresh_rule = {**rule, "body": fresh["body"], "case_context": fresh["case_context"] or {}}
                suite_key = (fresh_rule.get("body") or {}).get("suite_key")
                if not isinstance(suite_key, str) or not suite_key:
                    raise ValueError("not_confirmable")
                if resolved.revision.suite_key != suite_key:
                    raise ValueError("suite_key_mismatch")
                rule_clauses = {str(item.get("id")): item for item in (fresh_rule.get("body") or {}).get("rules") or []}
                cur.execute(
                    """SELECT c.clause_id, c.confirmed_by, c.confirmed_at, f.*
                       FROM product.rule_formalization_confirmations c
                       JOIN product.rule_formalizations f ON f.id=c.candidate_id
                       WHERE c.tenant_id=%s AND c.rule_version_id=%s AND c.current AND f.completeness='complete'""",
                    (principal.tenant_id, rule["id"]),
                )
                rows = cur.fetchall()
            snapshot = {}
            for row in rows:
                clause_id = row["clause_id"]
                if clause_id not in clause_ids or clause_id not in rule_clauses:
                    raise ValueError("not_confirmable")
                if row["source_rule_digest"] != source_binding(fresh_rule, clause_id)[1]:
                    raise ValueError("source_changed")
                if clause_source_checked(fresh_rule.get("case_context"), clause_id) is False:
                    raise ValueError("source_unchecked")
                if rule_clauses[clause_id].get("rule_to_check") != next(item.get("rule_to_check") for item in rules["rules"] if str(item.get("id")) == clause_id):
                    raise ValueError("source_changed")
                if row["unresolved"]:
                    raise ValueError("not_confirmable")
                snapshot[clause_id] = {
                    "format_version": "1",
                    "rule_version_id": str(rule["id"]),
                    "source_rule_version_id": str(rule["id"]),
                    "clause_id": clause_id,
                    "confirmed_candidate_id": str(row["id"]),
                    "source_suite_revision_id": revision_id,
                    "source_rule_digest": row["source_rule_digest"],
                    "formal_profile": row["formal_profile"],
                    "owl_content": row["owl_content"],
                    "clif_content": row["clif_content"],
                    "vocabulary": row["vocabulary"],
                    "obligations": row["obligations"],
                    "assumptions": row["assumptions"],
                    "unresolved": row["unresolved"],
                    "completeness": "complete",
                    "cnl_text": row["cnl_text"],
                    "cnl_renderer_version": row["cnl_renderer_version"],
                    "formal_digest": row["formal_digest"],
                    "display_digest": row["display_digest"],
                    "confirmed_by": str(row["confirmed_by"]),
                    "confirmed_at": row["confirmed_at"].isoformat(),
                }
            if not snapshot:
                raise ValueError("not_confirmable")
            rules["formalizations"] = snapshot
            encoded = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            saved = releases.save_candidate(
                principal,
                revision_id,
                {"rules.json": encoded},
                suite_key=suite_key,
                operation_id=operation_id,
                conn=conn,
            )
            candidate_revision_id = saved["id"]
        return releases.detail(principal, candidate_revision_id)

    def _visible_rule(self, principal, rule_version_id: str) -> dict:
        """Reading a rule's formalizations needs only the rule; its original matters when a reading is made or confirmed."""
        rule = self.store.get_rule(principal.tenant_id, rule_version_id)
        if rule is None:
            raise KeyError("access_denied")
        return rule

    def _require_source(self, principal, rule: dict) -> None:
        if not rule.get("source_file_version_id"):
            return
        try:
            self.store.readable_version(principal, rule["source_file_version_id"])
        except (KeyError, ValueError):
            raise ValueError("source_unreadable") from None

    def _owned(self, cur, conn, claimed, worker_ident: str, *, lock: bool, allow_expired: bool = False):
        if not self._owns(conn, claimed, worker_ident, lock=lock, allow_expired=allow_expired):
            return None
        cur.execute("SELECT * FROM product.rule_formalization_operations WHERE job_id=%s AND tenant_id=%s FOR UPDATE", (claimed.id, claimed.tenant_id))
        return cur.fetchone()

    def _owns(self, conn, claimed, worker_ident: str, *, lock: bool = False, allow_expired: bool = False) -> bool:
        lease = "" if allow_expired else " AND lease_until>clock_timestamp()"
        row = conn.execute(
            """SELECT 1 FROM engine.jobs WHERE id=%s AND tenant_id=%s AND kind='rule_formalization'
               AND attempt=%s AND lease_owner=%s AND status='leased'"""
            + lease
            + (" FOR UPDATE" if lock else ""),
            (claimed.id, claimed.tenant_id, claimed.attempt, worker_ident),
        ).fetchone()
        return row is not None


def _summary(clause_id: str, latest, current, source_digest: str | None = None) -> dict:
    latest_digest = None if latest is None else latest.get("source_rule_digest")
    current_digest = None if current is None else current.get("source_rule_digest")
    current_is_latest = current is not None and latest is not None and str(current.get("candidate_id")) == str(latest.get("id"))
    latest_mismatch = latest is not None and latest_digest != source_digest
    current_mismatch = current_is_latest and current_digest != source_digest
    if source_digest is not None and (latest_mismatch or current_mismatch):
        state = "source_changed"
    elif latest is None:
        state = "not_started"
    elif current and str(current["candidate_id"]) == str(latest["id"]) and current["formalization_revision"] == latest["formalization_revision"]:
        state = "confirmed"
    elif latest["unresolved"]:
        state = "needs_clarification"
    elif latest["completeness"] != "complete":
        state = "partial"
    else:
        state = "candidate"
    return {
        "clause_id": clause_id,
        "state": state,
        "completeness": None if latest is None else latest["completeness"],
        "formalization_revision": None if latest is None else latest["formalization_revision"],
        "candidate_id": None if latest is None else str(latest["id"]),
        "display_digest": None if latest is None else latest["display_digest"],
        "confirmed_at": current["confirmed_at"].isoformat() if current and hasattr(current["confirmed_at"], "isoformat") else None,
    }


def _candidate(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "formalization_revision": row["formalization_revision"],
        "parent_candidate_id": str(row["parent_candidate_id"]) if row["parent_candidate_id"] else None,
        "formal_profile": row["formal_profile"],
        "completeness": row["completeness"],
        "owl_content": row["owl_content"],
        "clif_content": row["clif_content"],
        "vocabulary": row["vocabulary"],
        "obligations": row["obligations"],
        "assumptions": row["assumptions"],
        "unresolved": row["unresolved"],
        "cnl_text": row["cnl_text"],
        "cnl_renderer_version": row["cnl_renderer_version"],
        "formal_digest": row["formal_digest"],
        "display_digest": row["display_digest"],
        "source_rule_digest": row["source_rule_digest"],
        "created_at": row["created_at"].isoformat(),
    }


def _confirmation(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "operation_id": str(row["operation_id"]),
        "candidate_id": str(row["candidate_id"]),
        "formalization_revision": row["formalization_revision"],
        "source_rule_digest": row["source_rule_digest"],
        "display_digest": row["display_digest"],
        "confirmed_by": str(row["confirmed_by"]),
        "confirmed_at": row["confirmed_at"].isoformat(),
        "current": row["current"],
    }


def _clarification_matches(unresolved, clarification: dict) -> bool:
    """Undecided is allowed only for a question that this candidate actually asks."""
    wanted = clarification.get("unresolved_id")
    choice = clarification.get("choice_id")
    if not isinstance(unresolved, list) or not isinstance(wanted, str) or not wanted.strip() or not isinstance(choice, str):
        return False
    for item in unresolved:
        if not isinstance(item, dict) or item.get("id") != wanted:
            continue
        if choice == "undecided":
            return bool(item.get("choices"))
        for option in item.get("choices") or []:
            if isinstance(option, dict) and option.get("id") == choice:
                return True
    return False


def _public_model_identity(value) -> dict:
    if not isinstance(value, dict):
        return {}
    public = {}
    for key in ("provider", "model", "deployment", "revision"):
        item = value.get(key)
        if isinstance(item, str) and item and len(item) <= 200:
            public[key] = item
    return public


def _operation(row: dict) -> dict:
    state = row["state"]
    if state in {"queued", "running"} and row.get("lease_expired"):
        state = "unknown" if row.get("model_started_at") else "failed"
    return {
        "operation_id": str(row["operation_id"]),
        "rule_version_id": str(row["rule_version_id"]),
        "clause_id": row["clause_id"],
        "state": state,
        "candidate_id": str(row["candidate_id"]) if row["candidate_id"] else None,
        "usage": {**(row["usage"] or {}), "uncertain": True} if state == "unknown" else (row["usage"] or {}),
        "model_identity": _public_model_identity(row.get("model_identity")),
        "failure": row["failure"] or ("execution_outcome_unknown" if state == "unknown" else None),
        "created_at": row["created_at"].isoformat(),
    }


def _row_from_public(item: dict) -> dict:
    return {
        "id": item["id"],
        "formalization_revision": item["formalization_revision"],
        "completeness": item["completeness"],
        "unresolved": item["unresolved"],
        "display_digest": item["display_digest"],
        "source_rule_digest": item.get("source_rule_digest"),
    }


def _row_from_confirmation(item: dict) -> dict:
    return {"candidate_id": item["candidate_id"], "formalization_revision": item["formalization_revision"], "confirmed_at": item["confirmed_at"], "source_rule_digest": item.get("source_rule_digest")}
