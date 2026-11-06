"""Tenant Suite selection and host validation, using existing product transactions."""

from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from typing import TYPE_CHECKING

from audit.checker_core import checker_digest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from product.db import connect
from product.suite_releases import empty_validation, publication_fingerprint
from product.suites import canonical_json, content_digest, is_unimplemented_suite

if TYPE_CHECKING:
    from product.checker_validation import SuiteValidationReport


def _id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_suite_identifier") from None


def _key(value: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 200:
        raise ValueError("invalid_suite_key")
    return value


def _member(conn, principal) -> None:
    if not conn.execute(
        "SELECT 1 FROM product.memberships WHERE tenant_id = %s AND user_id = %s AND role = 'member'",
        (principal.tenant_id, principal.user_id),
    ).fetchone():
        raise KeyError("suite_access_denied")


def _read_lock(conn, tenant_id: str) -> None:
    # All callers acquire official before tenant locks. A platform release is also
    # an input change for tenants still following the automatic official default.
    conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended(%s, 0))", ("suite-publication:official",))
    conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended(%s, 0))", ("suite-publication:" + tenant_id,))


def _lock(conn, tenant_id: str) -> None:
    # Validation and publication share one small tenant lock. Related Suites cannot
    # change between proof comparison and adoption, and opposite dependency orders
    # cannot deadlock. No model work runs under this lock.
    if tenant_id != "official":
        conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended(%s, 0))", ("suite-publication:official",))
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("suite-publication:" + tenant_id,))


def _revision(conn, revision_id: str, tenant_id: str | None) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM product.suite_revisions WHERE id = %s AND (tenant_id = %s OR tenant_id IS NULL)", (_id(revision_id), tenant_id))
        row = cur.fetchone()
    if row is None:
        raise KeyError("suite_access_denied")
    return row


def _selected(conn, tenant_id: str | None, suite_key: str) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM product.tenant_suite_heads WHERE tenant_id = %s AND suite_key = %s", (tenant_id, suite_key))
        head = cur.fetchone()
        if head:
            row = _revision(conn, str(head["current_revision_id"]), tenant_id)
        else:
            cur.execute(
                """SELECT r.* FROM product.official_suite_heads h
                   JOIN product.suite_revisions r ON r.id = h.current_revision_id
                   WHERE h.suite_key = %s AND r.tenant_id IS NULL""",
                (suite_key,),
            )
            row = cur.fetchone()
    if row is None:
        raise KeyError("suite_access_denied")
    return {"row": row, "head": head}


def _related_digests(conn, tenant_id: str | None, revision: dict) -> dict[str, str]:
    result = {}
    for key in revision["related_suite_keys"]:
        try:
            result[key] = _selected(conn, tenant_id, key)["row"]["resolved_digest"]
        except KeyError:
            # Missing related content cannot compare equal to a proof.
            result[key] = "missing"
    return result


def _validation(conn, tenant_id: str | None, revision: dict) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT * FROM product.suite_validations WHERE (tenant_id = %s OR tenant_id IS NULL) AND revision_id = %s
                       ORDER BY tenant_id NULLS LAST LIMIT 1""",
            (tenant_id, revision["id"]),
        )
        row = cur.fetchone()
    if row is None:
        return empty_validation()
    from audit.standard_suites import library_revision

    proof = row["proof"]
    status = row["status"]
    reasons = list(proof.get("reasons", []))
    if row["suite_digest"] != revision["resolved_digest"] or row["related_suite_digests"] != _related_digests(conn, tenant_id, revision):
        status = "stale"
        reasons.append("suite_inputs_changed")
    owns_write_scope = (row["tenant_id"] is None and tenant_id is None) or str(row["tenant_id"]) == tenant_id
    return {
        "status": status,
        "revision": row["revision"],
        # A global fallback is readable but is not this tenant's CAS write head.
        "write_revision": row["revision"] if owns_write_scope else 0,
        "proof_scope": "official" if row["tenant_id"] is None else "tenant",
        "total_samples": proof["total_samples"],
        "passed_samples": proof["passed_samples"],
        "new_errors": proof["new_errors"],
        "verified": status == "verified",
        "samples_not_rerun": proof["library_revision"] != library_revision(),
        "reasons": reasons,
        "library_revision": proof["library_revision"],
        "proof_digest": proof["proof_digest"],
    }


def current_selection(conn, tenant_id: str, suite_key: str) -> dict:
    """Freeze this return value inside the caller's Round acceptance transaction.

    Shared lock is compatible with other admissions and excludes tenant publishing
    until the frozen Round is committed. The caller must own a transaction.
    """
    _read_lock(conn, tenant_id)
    selected = _selected(conn, tenant_id, _key(suite_key))
    row, head = selected["row"], selected["head"]
    validation = _validation(conn, tenant_id, row)
    related = {key: str(_selected(conn, tenant_id, key)["row"]["id"]) for key in row["related_suite_keys"]}
    return {
        "suite_key": suite_key,
        "revision_id": str(row["id"]),
        "resolved_digest": row["resolved_digest"],
        "head_revision": head["revision"] if head else 0,
        "selection_mode": "pinned" if head else "automatic_official",
        "version": row["revision_number"],
        "origin": "enterprise" if row["tenant_id"] else "official",
        "verification_status": validation["status"],
        "force_unverified": head["force_unverified"] if head else False,
        "related_revision_ids": related,
        "validation": validation,
    }


def _display_name(conn, user_id, tenant_id: str) -> str:
    if user_id is None:
        return "已删除成员"
    row = conn.execute(
        """SELECT i.display_name FROM product.identities i JOIN product.tenants t ON t.id = %s
           WHERE i.user_id = %s AND i.tenant_key = t.external_key AND i.provider = t.provider
           AND i.display_name <> '' ORDER BY i.created_at, i.id LIMIT 1""",
        (tenant_id, user_id),
    ).fetchone()
    return row[0] if row else "企业成员"


def _receipt(row: dict, *, published_by_name: str | None = None) -> dict:
    return {
        "operation_id": str(row["operation_id"]),
        "suite_key": row["suite_key"],
        "revision_id": str(row["revision_id"]),
        "previous_revision_id": str(row["previous_revision_id"]) if row["previous_revision_id"] else None,
        "expected_revision": row["expected_revision"],
        "revision": row["revision"],
        "verification_status": row["verification_status"],
        "force_unverified": row["force_unverified"],
        "published_by": str(row["published_by"]) if row["published_by"] else None,
        "published_at": row["published_at"].isoformat(),
        "published_by_name": published_by_name or ("企业成员" if row["published_by"] else "已删除成员"),
    }


class SuiteReleaseStore:
    def __init__(self, settings, objects, role: str = "api"):
        from product.store.suites import SuiteStore

        self.settings, self.objects, self.role = settings, objects, role
        self.suites = SuiteStore(settings, objects, role=role)

    def _conn(self):
        return connect(self.settings, self.role)

    def list_suites(self, principal) -> dict:
        with self._conn() as conn, conn.transaction():
            _member(conn, principal)
            rows = conn.execute("SELECT DISTINCT suite_key FROM product.suite_revisions WHERE tenant_id IS NULL OR tenant_id = %s ORDER BY suite_key", (principal.tenant_id,)).fetchall()
        # Candidate-only keys are visible but never silently become active.
        suites = []
        for (key,) in rows:
            try:
                suites.append(self.active(principal, key))
            except KeyError:
                suites.append(
                    {
                        "title": "文件检查",
                        "version": 0,
                        "origin": "enterprise",
                        "selection_mode": "candidate_only",
                        "suite_key": key,
                        "revision": 0,
                        "current_revision_id": None,
                        "latest_official_revision_id": None,
                        "latest_official_version": None,
                        "verification_status": "unverified",
                        "force_unverified": False,
                        "validation": empty_validation(),
                        "history": [],
                    }
                )
        return {"suites": suites}

    def active(self, principal, suite_key: str) -> dict:
        with self._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            selection = current_selection(conn, principal.tenant_id, suite_key)
            cur.execute("SELECT * FROM product.suite_publications WHERE tenant_id = %s AND suite_key = %s ORDER BY revision DESC LIMIT 100", (principal.tenant_id, suite_key))
            history = [_receipt(row, published_by_name=_display_name(conn, row["published_by"], principal.tenant_id)) for row in cur.fetchall()]
            resolved = self.suites.resolve_suite(selection["revision_id"], principal.tenant_id, conn=conn)
            rules = json.loads(resolved.files["rules.json"])
            cur.execute(
                """SELECT h.current_revision_id, r.revision_number FROM product.official_suite_heads h
                   JOIN product.suite_revisions r ON r.id = h.current_revision_id WHERE h.suite_key = %s""",
                (suite_key,),
            )
            official = cur.fetchone()
        return {
            "title": rules.get("title") or rules.get("type") or "文件检查",
            "version": selection["version"],
            "origin": selection["origin"],
            "selection_mode": selection["selection_mode"],
            "suite_key": suite_key,
            "revision": selection["head_revision"],
            "current_revision_id": selection["revision_id"],
            "latest_official_revision_id": str(official["current_revision_id"]) if official else None,
            "latest_official_version": official["revision_number"] if official else None,
            "verification_status": selection["verification_status"],
            "force_unverified": selection["force_unverified"],
            "validation": selection["validation"],
            "history": history,
        }

    def publish(self, principal, suite_key: str, revision_id: str, expected_revision: int, operation_id: str, force_unverified: bool = False, *, conn=None) -> dict:
        suite_key, revision_id, operation_id = _key(suite_key), _id(revision_id), _id(operation_id)
        fingerprint = publication_fingerprint(suite_key=suite_key, revision_id=revision_id, expected_revision=expected_revision, force_unverified=force_unverified, user_id=principal.user_id)
        with nullcontext(conn) if conn is not None else self._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            _lock(conn, principal.tenant_id)
            revision = _revision(conn, revision_id, principal.tenant_id)
            if revision["suite_key"] != suite_key:
                raise ValueError("suite_key_mismatch")
            cur.execute("SELECT * FROM product.suite_publications WHERE tenant_id = %s AND operation_id = %s", (principal.tenant_id, operation_id))
            prior = cur.fetchone()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise ValueError("suite_operation_conflict")
                return _receipt(prior, published_by_name=_display_name(conn, prior["published_by"], principal.tenant_id))
            cur.execute("SELECT * FROM product.tenant_suite_heads WHERE tenant_id = %s AND suite_key = %s FOR UPDATE", (principal.tenant_id, suite_key))
            head = cur.fetchone()
            if (head["revision"] if head else 0) != expected_revision:
                raise ValueError("suite_head_conflict")
            validation = _validation(conn, principal.tenant_id, revision)
            # Resolve bytes at the adoption boundary: missing/corrupt artifacts are
            # never accepted merely because the caller acknowledges validation risk.
            resolved = self.suites.resolve_suite(revision_id, principal.tenant_id, conn=conn)
            if not validation["verified"] and not force_unverified and not is_unimplemented_suite(resolved):
                raise ValueError("suite_confirmation_required")
            previous = str(head["current_revision_id"]) if head else None
            if previous is None:
                try:
                    previous = str(_selected(conn, principal.tenant_id, suite_key)["row"]["id"])
                except KeyError:
                    pass
            next_revision = expected_revision + 1
            cur.execute(
                """INSERT INTO product.tenant_suite_heads (tenant_id, suite_key, current_revision_id, revision, force_unverified, published_by)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, suite_key) DO UPDATE SET
                     current_revision_id = EXCLUDED.current_revision_id, revision = EXCLUDED.revision,
                     force_unverified = EXCLUDED.force_unverified, published_by = EXCLUDED.published_by, published_at = now()""",
                (principal.tenant_id, suite_key, revision_id, next_revision, force_unverified, principal.user_id),
            )
            cur.execute(
                """INSERT INTO product.suite_publications
                   (tenant_id, operation_id, suite_key, revision_id, previous_revision_id, expected_revision, revision,
                    request_fingerprint, verification_status, force_unverified, published_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (principal.tenant_id, operation_id, suite_key, revision_id, previous, expected_revision, next_revision, fingerprint, validation["status"], force_unverified, principal.user_id),
            )
            result = _receipt(cur.fetchone(), published_by_name=_display_name(conn, principal.user_id, principal.tenant_id))
        return result

    def save_candidate(self, principal, base_revision_id: str, files: dict[str, str], *, suite_key: str | None = None, operation_id: str | None = None) -> dict:
        base_revision_id = _id(base_revision_id)
        operation_id = _id(operation_id) if operation_id is not None else str(uuid.uuid4())
        if not isinstance(files, dict) or not files or any(not isinstance(value, str) for value in files.values()):
            raise ValueError("invalid_suite_candidate")
        if "templates.json" in files:
            try:
                templates = json.loads(files["templates.json"])
            except (ValueError, TypeError):
                raise ValueError("invalid_template_reference") from None
            if not isinstance(templates, list) or any(not isinstance(item, dict) or (item.get("kind") == "licensed_blank" and item.get("distributable") is not True) for item in templates):
                raise ValueError("template_distribution_not_authorized")
        with self._conn() as conn, conn.transaction():
            _member(conn, principal)
            base = _revision(conn, base_revision_id, principal.tenant_id)
            if suite_key is not None and base["suite_key"] != _key(suite_key):
                raise ValueError("suite_key_mismatch")
            fingerprint = content_digest(canonical_json({"base_revision_id": base_revision_id, "files": files, "created_by": principal.user_id}))
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("suite-candidate:" + principal.tenant_id + ":" + operation_id,))
            prior = conn.execute(
                "SELECT revision_id,request_fingerprint FROM product.suite_candidate_operations WHERE tenant_id=%s AND operation_id=%s", (principal.tenant_id, operation_id)
            ).fetchone()
            if prior:
                if prior[1] != fingerprint:
                    raise ValueError("suite_operation_conflict")
                revision_id = str(prior[0])
            else:
                revision = self.suites.create_revision(
                    tenant_id=principal.tenant_id,
                    suite_key=base["suite_key"],
                    files={path: value.encode() for path, value in files.items()},
                    created_by=principal.user_id,
                    base_revision_id=base_revision_id,
                    related_suite_keys=tuple(base["related_suite_keys"]),
                    conn=conn,
                )
                revision_id = revision.id
                conn.execute(
                    """INSERT INTO product.suite_candidate_operations (tenant_id,operation_id,base_revision_id,revision_id,request_fingerprint,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (principal.tenant_id, operation_id, base_revision_id, revision_id, fingerprint, principal.user_id),
                )
        return self.detail(principal, revision_id)

    def read_candidate(self, principal, base_revision_id: str, operation_id: str) -> dict:
        """Read an uncertain save by its original operation, without repeating it."""
        base_revision_id, operation_id = _id(base_revision_id), _id(operation_id)
        with self._conn() as conn:
            _member(conn, principal)
            _revision(conn, base_revision_id, principal.tenant_id)
            row = conn.execute(
                "SELECT revision_id FROM product.suite_candidate_operations WHERE tenant_id=%s AND operation_id=%s AND base_revision_id=%s",
                (principal.tenant_id, operation_id, base_revision_id),
            ).fetchone()
        if row is None:
            raise ValueError("suite_candidate_not_found")
        return self.detail(principal, str(row[0]))

    def detail(self, principal, revision_id: str) -> dict:
        with self._conn() as conn, conn.transaction():
            _member(conn, principal)
            _read_lock(conn, principal.tenant_id)
            row = _revision(conn, revision_id, principal.tenant_id)
            resolved = self.suites.resolve_suite(revision_id, principal.tenant_id, conn=conn)
            validation = _validation(conn, principal.tenant_id, row)
            original = self.suites.resolve_suite(str(row["base_revision_id"]), principal.tenant_id, conn=conn) if row["base_revision_id"] else None
            changed = [path for path, digest in resolved.object_digests.items() if original is None or original.object_digests.get(path) != digest]
            if original is not None:
                changed.extend(set(original.files) - set(resolved.files))
            rounds = self._rounds(conn, principal, revision_id)
            rules = json.loads(resolved.files["rules.json"])
            title = rules.get("title") or rules.get("type") or "文件检查"
            creator_name = _display_name(conn, row["created_by"], principal.tenant_id) if row["tenant_id"] else "PPAP"
        return {
            "title": title,
            "version": row["revision_number"],
            "created_by_name": creator_name,
            "id": str(row["id"]),
            "tenant_id": str(row["tenant_id"]) if row["tenant_id"] else None,
            "suite_key": row["suite_key"],
            "base_revision_id": str(row["base_revision_id"]) if row["base_revision_id"] else None,
            "created_by": str(row["created_by"]) if row["created_by"] else None,
            "created_at": row["created_at"].isoformat(),
            "resolved_digest": row["resolved_digest"],
            "related_suite_keys": list(row["related_suite_keys"]),
            "files": {path: body.decode("utf-8") for path, body in resolved.files.items()},
            "changed_paths": sorted(changed),
            "diff": [{"path": path, "before": original.files.get(path, b"").decode("utf-8") if original else "", "after": resolved.files.get(path, b"").decode("utf-8")} for path in sorted(changed)],
            "validation": validation,
            "round_count": len(rounds),
            "rounds": rounds,
        }

    @staticmethod
    def _rounds(conn, principal, revision_id: str) -> list[dict]:
        # Reuse Round/Task resource grants. Tenant membership does not grant access
        # to another member's private audit tasks or source documents.
        rows = conn.execute(
            """SELECT DISTINCT r.id, r.source_draft_id, r.created_at
               FROM product.audit_rounds r JOIN product.checker_execution_snapshots s ON s.round_id = r.id AND s.tenant_id = r.tenant_id
               CROSS JOIN LATERAL jsonb_array_elements(s.body->'targets') t
               WHERE r.tenant_id = %s AND t->>'suite_revision_id' = %s
                 AND (r.created_by = %s OR EXISTS (
                     SELECT 1 FROM product.resource_grants g WHERE g.user_id = %s
                     AND g.resource_id IN (r.id, r.source_draft_id) AND g.capability IN ('read', 'review')))
               ORDER BY r.created_at DESC, r.id""",
            (principal.tenant_id, revision_id, principal.user_id, principal.user_id),
        ).fetchall()
        return [{"id": str(row[0]), "task_id": str(row[1]) if row[1] else None, "created_at": row[2].isoformat()} for row in rows]

    def record_validation(self, principal, revision_id: str, proof: SuiteValidationReport, *, expected_revision: int) -> dict:
        """Host-only completion. There is intentionally no HTTP endpoint accepting proof."""
        with self._conn() as conn, conn.transaction():
            _member(conn, principal)
            _lock(conn, principal.tenant_id)
            return self._record_validation(conn, principal.tenant_id, principal.user_id, revision_id, proof, expected_revision)

    def record_official_validation(self, revision_id: str, proof: SuiteValidationReport, *, expected_revision: int) -> dict:
        """Platform corpus runner only; never infer verification from an official label."""
        if self.role != "migrator":
            raise PermissionError("official_validation_write_forbidden")
        with self._conn() as conn, conn.transaction():
            _lock(conn, "official")
            return self._record_validation(conn, None, None, revision_id, proof, expected_revision)

    def official_active(self, suite_key: str) -> dict:
        """Platform release readback; an absent head is not the latest candidate."""
        with self._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended('suite-publication:official', 0))")
            cur.execute("SELECT * FROM product.official_suite_heads WHERE suite_key = %s", (_key(suite_key),))
            head = cur.fetchone()
        return self._official_receipt(suite_key, head)

    @staticmethod
    def _official_receipt(suite_key: str, head: dict | None) -> dict:
        return {
            "suite_key": suite_key,
            "current_revision_id": str(head["current_revision_id"]) if head else None,
            "revision": head["revision"] if head else 0,
            "validation_revision": head["validation_revision"] if head else None,
            "published_at": head["published_at"].isoformat() if head else None,
        }

    def publish_official(self, revision_id: str, *, expected_revision: int, expected_validation_revision: int) -> dict:
        """Activate exactly the reviewed proof under the admission/publication lock.

        Saving sources and recording tests never call this method. A late test
        cannot promote its old candidate, and a stale publisher cannot overwrite
        a newer release. Deliberate rollback is the same explicit CAS operation.
        """
        if self.role != "migrator":
            raise PermissionError("official_suite_write_forbidden")
        if type(expected_revision) is not int or expected_revision < 0 or type(expected_validation_revision) is not int or expected_validation_revision < 1:
            raise ValueError("invalid_official_suite_publication")
        with self._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            _lock(conn, "official")
            revision = _revision(conn, revision_id, None)
            cur.execute("SELECT * FROM product.official_suite_heads WHERE suite_key = %s FOR UPDATE", (revision["suite_key"],))
            head = cur.fetchone()
            if (head["revision"] if head else 0) != expected_revision:
                raise ValueError("suite_head_conflict")
            validation = _validation(conn, None, revision)
            if validation["write_revision"] != expected_validation_revision:
                raise ValueError("suite_validation_conflict")
            if not validation["verified"] or validation["samples_not_rerun"]:
                raise ValueError("official_suite_validation_required")
            # Proof identity is insufficient if an object disappeared or drifted.
            self.suites.resolve_suite(str(revision["id"]), "00000000-0000-0000-0000-000000000000", conn=conn)
            if head:
                cur.execute(
                    """UPDATE product.official_suite_heads SET current_revision_id = %s, revision = revision + 1,
                         validation_revision = %s, published_at = now() WHERE suite_key = %s AND revision = %s RETURNING *""",
                    (revision["id"], expected_validation_revision, revision["suite_key"], expected_revision),
                )
            else:
                cur.execute(
                    """INSERT INTO product.official_suite_heads (suite_key, current_revision_id, revision, validation_revision)
                       VALUES (%s, %s, 1, %s) RETURNING *""",
                    (revision["suite_key"], revision["id"], expected_validation_revision),
                )
            return self._official_receipt(revision["suite_key"], cur.fetchone())

    def _record_validation(self, conn, tenant_id: str | None, user_id: str | None, revision_id: str, proof: SuiteValidationReport, expected_revision: int) -> dict:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid_suite_validation")
        lookup_tenant = tenant_id or "00000000-0000-0000-0000-000000000000"
        with conn.cursor(row_factory=dict_row) as cur:
            revision = _revision(conn, revision_id, lookup_tenant)
            if tenant_id is None and revision["tenant_id"] is not None:
                raise ValueError("official_validation_requires_official_suite")
            resolved = self.suites.resolve_suite(revision_id, lookup_tenant, conn=conn)
            if not proof.library_revision or proof.suite_revision_id != str(revision["id"]) or proof.suite_digest != revision["resolved_digest"]:
                raise ValueError("suite_validation_identity_mismatch")
            if proof.program_digest != checker_digest({path: body for path, body in resolved.files.items() if path in {"checker.py", "resources.py"}}):
                raise ValueError("suite_validation_identity_mismatch")
            related_digests = _related_digests(conn, tenant_id, revision)
            if dict(proof.related_suite_digests) != related_digests or "missing" in related_digests.values():
                raise ValueError("suite_validation_inputs_changed")
            cur.execute("SELECT revision FROM product.suite_validations WHERE tenant_id IS NOT DISTINCT FROM %s::uuid AND revision_id = %s", (tenant_id, revision_id))
            previous = cur.fetchone()
            if (previous["revision"] if previous else 0) != expected_revision:
                raise ValueError("suite_validation_conflict")
            expected_clauses = {item["id"] for item in json.loads(resolved.files["rules.json"])["rules"]}
            coverage_complete = bool(expected_clauses) and all(len(case.clauses) == len(expected_clauses) and {item.clause_id for item in case.clauses} == expected_clauses for case in proof.cases)
            reasons = list(proof.reasons)
            if not coverage_complete:
                reasons.append("suite_clause_coverage_incomplete")
            stored = {
                "proof_digest": proof.proof_digest,
                "total_samples": proof.total_samples,
                "passed_samples": proof.passed_samples,
                "new_errors": proof.new_errors,
                "library_revision": proof.library_revision,
                "reasons": reasons,
                "report": proof.model_dump(mode="json"),
            }
            cur.execute(
                """INSERT INTO product.suite_validations
                   (tenant_id, revision_id, status, revision, suite_digest, related_suite_digests, proof, recorded_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (scope_key, revision_id) DO UPDATE SET
                     status = EXCLUDED.status, revision = EXCLUDED.revision, suite_digest = EXCLUDED.suite_digest,
                     related_suite_digests = EXCLUDED.related_suite_digests, proof = EXCLUDED.proof,
                     recorded_by = EXCLUDED.recorded_by, recorded_at = now()""",
                (
                    tenant_id,
                    revision_id,
                    "verified" if proof.verified and coverage_complete else "unverified",
                    expected_revision + 1,
                    revision["resolved_digest"],
                    Jsonb(related_digests),
                    Jsonb(stored),
                    user_id,
                ),
            )
            return _validation(conn, tenant_id, revision)
