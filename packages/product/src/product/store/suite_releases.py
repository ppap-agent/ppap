"""Tenant Suite selection and host validation, using existing product transactions."""

from __future__ import annotations

import ast
import json
import uuid
from contextlib import nullcontext
from typing import TYPE_CHECKING

from audit.checker_core import checker_digest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from product.db import connect
from product.files import ObjectMissing
from product.suite_releases import adoption_block, empty_validation, publication_fingerprint
from product.suites import MAX_OBJECT_BYTES, MAX_SUITE_DEPTH, canonical_json, content_digest

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
        cur.execute("SELECT * FROM product.suite_revisions WHERE id = %s AND NOT history_only AND (tenant_id = %s OR tenant_id IS NULL)", (_id(revision_id), tenant_id))
        row = cur.fetchone()
    if row is None:
        raise KeyError("suite_access_denied")
    return row


def _selected_or_none(conn, tenant_id: str | None, suite_key: str) -> dict | None:
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
    return {"row": row, "head": head} if row is not None else None


def _selected(conn, tenant_id: str | None, suite_key: str) -> dict:
    selected = _selected_or_none(conn, tenant_id, suite_key)
    if selected is None:
        raise KeyError("suite_access_denied")
    return selected


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


def _official_base(conn, revision: dict, tenant_id: str) -> dict | None:
    """Locate the nearest official ancestor; never confuse tenant revision IDs with updates."""
    seen = set()
    current = revision
    for _ in range(MAX_SUITE_DEPTH):
        if current["id"] in seen or current["suite_key"] != revision["suite_key"]:
            raise ValueError("invalid_suite_base")
        seen.add(current["id"])
        if current["tenant_id"] is None:
            return current
        if current["base_revision_id"] is None:
            return None
        current = _revision(conn, str(current["base_revision_id"]), tenant_id)
    raise ValueError("suite_cycle_or_depth")


def _upgrade_provenance(row) -> dict:
    return {"operation_id": str(row[0]), "source_revision_id": str(row[1]), "target_revision_id": str(row[2]), "expected_revision": row[3]}


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


class _CatalogObjects:
    """Bounded, request-local byte reuse; Suite resolution still checks every digest."""

    def __init__(self, objects):
        self.objects = objects
        self.cached: dict[str, bytes] = {}
        self.remaining = MAX_OBJECT_BYTES

    def get(self, key: str) -> bytes:
        if key in self.cached:
            return self.cached[key]
        body = self.objects.get(key)
        if len(body) <= self.remaining:
            self.cached[key] = body
            self.remaining -= len(body)
        return body


class SuiteReleaseStore:
    def __init__(self, settings, objects, role: str = "api"):
        from product.store.suites import SuiteStore

        self.settings, self.objects, self.role = settings, objects, role
        self.suites = SuiteStore(settings, objects, role=role)

    def _conn(self):
        return connect(self.settings, self.role)

    def list_suites(self, principal) -> dict:
        suites, unavailable = [], []
        # Do not retain object bytes across requests or principals. Reuse only
        # content reads, never membership checks, selection or validation state.
        reader = SuiteReleaseStore(self.settings, _CatalogObjects(self.objects), role=self.role)
        with self._conn() as conn:
            with conn.transaction():
                _member(conn, principal)
                rows = conn.execute(
                    "SELECT DISTINCT suite_key FROM product.suite_revisions WHERE NOT history_only AND (tenant_id IS NULL OR tenant_id = %s) ORDER BY suite_key", (principal.tenant_id,)
                ).fetchall()
            for (key,) in rows:
                try:
                    suites.append(reader.active(principal, key, conn=conn))
                except (KeyError, ValueError, ObjectMissing):
                    # A broken or inaccessible Suite is visible as unavailable,
                    # never omitted or represented as an adoptable empty state.
                    # Membership loss still fails the entire request.
                    with conn.transaction():
                        _member(conn, principal)
                    unavailable.append({"suite_key": key, "error": "suite_unavailable"})
            with conn.transaction():
                _member(conn, principal)
                predefined, predefined_unavailable, predefined_error = self._predefined_sources(principal, conn)
        payload: dict = {"suites": suites, **({"unavailable": unavailable} if unavailable else {})}
        if predefined_error:
            payload["predefined_error"] = predefined_error
        else:
            payload["predefined"] = predefined
            if predefined_unavailable:
                payload["predefined_unavailable"] = predefined_unavailable
        return payload

    def _predefined_sources(self, principal, conn) -> tuple[list[dict], list[dict], str | None]:
        """Bind each installed directory leaf to its official source revision.

        ``revision_id`` is the tenant-null row for that ``source_path``. It is
        never the tenant's adopted revision. A broken scan lists no paths.
        """
        from audit.standard_suites import predefined_sources, validate_source_identity

        from product.seed_standard_suites import persisted_source_path

        try:
            discovered = predefined_sources()
        except ValueError:
            return [], [], "predefined_catalog_unavailable"
        ready, blocked = [], []
        for source in discovered:
            path, suite = source.path, source.suite
            if suite is None:
                ready.append({"path": path, "title": source.title, "availability": "historical"})
                continue
            visible = {"path": path, "title": source.title}
            if persisted_source_path(path) != path:
                blocked.append({"path": path, "error": "invalid"})
                continue
            try:
                revision_id = self.suites.official_revision_id_for_source(path, conn=conn)
            except ValueError:
                blocked.append({"path": path, "error": "invalid"})
                ready.append({**visible, "availability": "unavailable"})
                continue
            if revision_id is None:
                blocked.append({"path": path, "error": "unseeded"})
                ready.append({**visible, "availability": "unseeded"})
                continue
            row = conn.execute(
                "SELECT suite_key, tenant_id, source_path FROM product.suite_revisions WHERE id = %s AND NOT history_only",
                (revision_id,),
            ).fetchone()
            if row is None or row[1] is not None or row[2] != path or row[0] != suite.suite_key:
                blocked.append({"path": path, "error": "unavailable"})
                ready.append({**visible, "availability": "unavailable"})
                continue
            try:
                resolved = self.suites.resolve_suite(revision_id, principal.tenant_id, conn=conn)
                if resolved.revision.tenant_id is not None or resolved.revision.suite_key != suite.suite_key:
                    raise KeyError("suite_access_denied")
                rules = json.loads(resolved.files["rules.json"])
                if not isinstance(rules, dict):
                    raise ValueError("invalid_suite_rules")
                validate_source_identity(path, rules)
                from product.suite_releases import leaf_capability

                capability = leaf_capability(resolved.files)
            except (KeyError, ValueError, ObjectMissing):
                blocked.append({"path": path, "error": "unavailable"})
                ready.append({**visible, "availability": "unavailable"})
                continue
            ready.append({**visible, "availability": "installed", "suite_key": suite.suite_key, "revision_id": revision_id, **capability})
        return ready, blocked, None

    def active(self, principal, suite_key: str, *, conn=None) -> dict:
        suite_key = _key(suite_key)
        with nullcontext(conn) if conn is not None else self._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            _member(conn, principal)
            _read_lock(conn, principal.tenant_id)
            if _selected_or_none(conn, principal.tenant_id, suite_key) is None:
                # No adoption is a readable state only for an accessible candidate.
                # Do not turn a missing dependency or denied/corrupt selected Suite
                # into an empty state that invites another publication.
                candidate = conn.execute(
                    """SELECT id FROM product.suite_revisions WHERE suite_key = %s AND NOT history_only AND (tenant_id IS NULL OR tenant_id = %s)
                       ORDER BY tenant_id NULLS LAST, revision_number DESC LIMIT 1""",
                    (suite_key, principal.tenant_id),
                ).fetchone()
                if candidate is None:
                    raise KeyError("suite_access_denied")
                resolved = self.suites.resolve_suite(str(candidate[0]), principal.tenant_id, conn=conn)
                rules = json.loads(resolved.files["rules.json"])
                return {
                    "title": rules.get("title") or rules.get("type") or "文件检查",
                    "candidate_revision_id": str(candidate[0]),
                    "version": 0,
                    "origin": "enterprise" if resolved.revision.tenant_id else "official",
                    "selection_mode": "candidate_only",
                    "suite_key": suite_key,
                    "revision": 0,
                    "current_revision_id": None,
                    "latest_official_revision_id": None,
                    "latest_official_version": None,
                    "official_base_revision_id": None,
                    "official_update_available": False,
                    "official_update_notice": False,
                    "verification_status": "unverified",
                    "force_unverified": False,
                    "validation": empty_validation(),
                    "history": [],
                }
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
            from product.store.rule_impact import published_rule_update, rule_impact

            rule_update = published_rule_update(conn, self.suites, principal.tenant_id, resolved)
            official_rule_impact = rule_impact(resolved, self.suites.resolve_suite(str(official["current_revision_id"]), principal.tenant_id, conn=conn)) if official else None
            base = _official_base(conn, _revision(conn, selection["revision_id"], principal.tenant_id), principal.tenant_id)
            update = bool(official and base and base["id"] != official["current_revision_id"] and selection["selection_mode"] == "pinned")
            needs_rule_validation = bool(rule_update or update and official_rule_impact and official_rule_impact["changed"])
            seen = (
                not update
                or conn.execute(
                    "SELECT 1 FROM product.suite_official_update_views WHERE tenant_id=%s AND user_id=%s AND official_revision_id=%s",
                    (principal.tenant_id, principal.user_id, official["current_revision_id"]),
                ).fetchone()
                is not None
            )
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
            "official_base_revision_id": str(base["id"]) if base else None,
            "rule_update": rule_update,
            "official_rule_impact": official_rule_impact if update else None,
            "official_update_available": update,
            "official_update_notice": not seen,
            "verification_status": "stale" if needs_rule_validation else selection["verification_status"],
            "force_unverified": selection["force_unverified"],
            "validation": (
                {**selection["validation"], "status": "stale", "verified": False, "reasons": [*selection["validation"]["reasons"], "published_rules_changed"]}
                if needs_rule_validation
                else selection["validation"]
            ),
            "history": history,
        }

    def acknowledge_official_update(self, principal, suite_key: str, official_revision_id: str) -> dict:
        suite_key, official_revision_id = _key(suite_key), _id(official_revision_id)
        with self._conn() as conn, conn.transaction():
            _member(conn, principal)
            _read_lock(conn, principal.tenant_id)
            official = conn.execute("SELECT current_revision_id FROM product.official_suite_heads WHERE suite_key=%s", (suite_key,)).fetchone()
            if official is None or str(official[0]) != official_revision_id:
                raise ValueError("suite_official_head_conflict")
            conn.execute(
                """INSERT INTO product.suite_official_update_views(tenant_id,user_id,suite_key,official_revision_id)
                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (principal.tenant_id, principal.user_id, suite_key, official_revision_id),
            )
        return self.active(principal, suite_key)

    def upgrade_official(self, principal, suite_key: str, source_revision_id: str, target_revision_id: str, expected_revision: int, operation_id: str) -> dict:
        """Save a candidate only. Existing optimization and explicit publication own the next steps."""
        from product.suite_upgrades import carry_enterprise_changes

        suite_key, source_revision_id, target_revision_id, operation_id = _key(suite_key), _id(source_revision_id), _id(target_revision_id), _id(operation_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid_suite_upgrade")
        fingerprint = content_digest(
            canonical_json(
                {
                    "suite_key": suite_key,
                    "source_revision_id": source_revision_id,
                    "target_revision_id": target_revision_id,
                    "expected_revision": expected_revision,
                    "created_by": principal.user_id,
                }
            )
        )
        with self._conn() as conn, conn.transaction():
            _member(conn, principal)
            _lock(conn, principal.tenant_id)
            prior = conn.execute(
                "SELECT revision_id,request_fingerprint FROM product.suite_upgrade_operations WHERE tenant_id=%s AND operation_id=%s",
                (principal.tenant_id, operation_id),
            ).fetchone()
            if prior:
                if prior[1] != fingerprint:
                    raise ValueError("suite_operation_conflict")
                candidate_id = str(prior[0])
            else:
                source = _revision(conn, source_revision_id, principal.tenant_id)
                if source["suite_key"] != suite_key:
                    raise ValueError("suite_key_mismatch")
                selected = _selected(conn, principal.tenant_id, suite_key)
                if (selected["head"]["revision"] if selected["head"] else 0) != expected_revision:
                    raise ValueError("suite_head_conflict")
                official = conn.execute("SELECT current_revision_id FROM product.official_suite_heads WHERE suite_key=%s", (suite_key,)).fetchone()
                if official is None or str(official[0]) != target_revision_id:
                    raise ValueError("suite_official_head_conflict")
                base = _official_base(conn, source, principal.tenant_id)
                if base is None or str(base["id"]) == target_revision_id:
                    raise ValueError("suite_upgrade_not_available")
                changes = carry_enterprise_changes(
                    self.suites.resolve_suite(source_revision_id, principal.tenant_id, conn=conn),
                    self.suites.resolve_suite(str(base["id"]), principal.tenant_id, conn=conn),
                    self.suites.resolve_suite(target_revision_id, principal.tenant_id, conn=conn),
                )
                candidate = self.suites.create_revision(
                    tenant_id=principal.tenant_id,
                    suite_key=suite_key,
                    files=changes.files,
                    deleted_paths=changes.deleted_paths,
                    created_by=principal.user_id,
                    base_revision_id=target_revision_id,
                    related_suite_keys=changes.related_suite_keys,
                    conn=conn,
                )
                candidate_id = candidate.id
                conn.execute(
                    """INSERT INTO product.suite_upgrade_operations
                       (tenant_id,operation_id,suite_key,source_revision_id,target_revision_id,revision_id,expected_revision,request_fingerprint,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (principal.tenant_id, operation_id, suite_key, source_revision_id, target_revision_id, candidate_id, expected_revision, fingerprint, principal.user_id),
                )
                conn.execute(
                    """INSERT INTO product.suite_official_update_views(tenant_id,user_id,suite_key,official_revision_id)
                       VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (principal.tenant_id, principal.user_id, suite_key, target_revision_id),
                )
        return self.detail(principal, candidate_id)

    def read_upgrade(self, principal, suite_key: str, operation_id: str) -> dict:
        suite_key, operation_id = _key(suite_key), _id(operation_id)
        with self._conn() as conn:
            _member(conn, principal)
            row = conn.execute(
                "SELECT revision_id FROM product.suite_upgrade_operations WHERE tenant_id=%s AND suite_key=%s AND operation_id=%s",
                (principal.tenant_id, suite_key, operation_id),
            ).fetchone()
        if row is None:
            raise ValueError("suite_candidate_not_found")
        return self.detail(principal, str(row[0]))

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
            blocked = adoption_block(resolved, verified=bool(validation["verified"]), force_unverified=force_unverified)
            if blocked:
                raise ValueError(blocked)
            if any(_selected_or_none(conn, principal.tenant_id, key) is None for key in revision["related_suite_keys"]):
                raise ValueError("suite_dependencies_not_adopted")
            from product.legacy_head_cutover import refuse_related_carrier

            # A directory head cannot adopt while a related head is still a carrier.
            # Do not replace that related head with another revision of the same key.
            refuse_related_carrier(self, conn, principal.tenant_id, revision["related_suite_keys"])
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

    def save_candidate(self, principal, base_revision_id: str, files: dict[str, str], *, suite_key: str | None = None, operation_id: str | None = None, conn=None) -> dict:
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

        def _persist(conn) -> str:
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
                if {"checker.py", "resources.py"} & files.keys():
                    selected = self.suites.resolve_suite(base_revision_id, principal.tenant_id, conn=conn)
                    merged = {**selected.files, **{path: value.encode() for path, value in files.items()}}
                    program = {path: body for path, body in merged.items() if path in {"checker.py", "resources.py"}}
                    previous = {path: body for path, body in selected.files.items() if path in {"checker.py", "resources.py"}}
                    if program != previous:
                        factory = False
                        for snapshot in (selected.files, merged):
                            try:
                                rules = json.loads(snapshot["rules.json"])
                                binding = rules.get("checker_binding") or {}
                                factory |= isinstance(binding, dict) and "checker:create_audit" in binding.values()
                                tree = ast.parse(snapshot["checker.py"])
                                factory |= any(
                                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                                    and node.name == "create_audit"
                                    or isinstance(node, ast.Name)
                                    and isinstance(node.ctx, ast.Store)
                                    and node.id == "create_audit"
                                    or isinstance(node, ast.alias)
                                    and (node.asname or node.name) == "create_audit"
                                    for node in ast.walk(tree)
                                )
                            except (SyntaxError, ValueError, AttributeError):
                                pass  # Content validation below still owns malformed revisions.
                        if factory:
                            from audit.checker_lib.imports import import_violations

                            for source in program.values():
                                try:
                                    tree = ast.parse(source)
                                except (SyntaxError, ValueError):
                                    raise ValueError("invalid_suite_candidate") from None
                                if import_violations(tree):
                                    raise ValueError("checker_cross_version_import")
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
            return revision_id

        if conn is None:
            with self._conn() as owned, owned.transaction():
                revision_id = _persist(owned)
            return self.detail(principal, revision_id)
        return {"id": _persist(conn)}

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
            unadopted_related = [key for key in row["related_suite_keys"] if _selected_or_none(conn, principal.tenant_id, key) is None]
            original = self.suites.resolve_suite(str(row["base_revision_id"]), principal.tenant_id, conn=conn) if row["base_revision_id"] else None
            from product.store.rule_impact import rule_impact

            impact = rule_impact(original, resolved) if original else None
            official_base = _official_base(conn, row, principal.tenant_id)
            changed = [path for path, digest in resolved.object_digests.items() if original is None or original.object_digests.get(path) != digest]
            if original is not None:
                changed.extend(set(original.files) - set(resolved.files))
            rounds = self._rounds(conn, principal, revision_id)
            rules = json.loads(resolved.files["rules.json"])
            title = rules.get("title") or rules.get("type") or "文件检查"
            creator_name = _display_name(conn, row["created_by"], principal.tenant_id) if row["tenant_id"] else "PPAP"
            upgrade = conn.execute(
                "SELECT operation_id,source_revision_id,target_revision_id,expected_revision FROM product.suite_upgrade_operations WHERE tenant_id=%s AND revision_id=%s",
                (principal.tenant_id, revision_id),
            ).fetchone()
        return {
            **({"upgrade": _upgrade_provenance(upgrade)} if upgrade else {}),
            "rule_impact": impact,
            "title": title,
            "version": row["revision_number"],
            "created_by_name": creator_name,
            "id": str(row["id"]),
            "tenant_id": str(row["tenant_id"]) if row["tenant_id"] else None,
            "suite_key": row["suite_key"],
            "base_revision_id": str(row["base_revision_id"]) if row["base_revision_id"] else None,
            "official_base_revision_id": str(official_base["id"]) if official_base else None,
            "created_by": str(row["created_by"]) if row["created_by"] else None,
            "created_at": row["created_at"].isoformat(),
            "resolved_digest": row["resolved_digest"],
            "related_suite_keys": list(row["related_suite_keys"]),
            "unadopted_related_suite_keys": unadopted_related,
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

    def official_context(self, revision_id: str) -> dict:
        """Read one frozen official candidate, dependency selection and proof together.

        Host-only release tooling uses this for planning and uncertain-write
        reconciliation. It never executes code, records a proof or publishes.
        """
        if self.role != "migrator":
            raise PermissionError("official_validation_read_forbidden")
        with self._conn() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended('suite-publication:official', 0))")
            row = _revision(conn, revision_id, None)
            suite = self.suites.resolve_suite(str(row["id"]), "00000000-0000-0000-0000-000000000000", conn=conn)
            related = {key: _selected(conn, None, key)["row"] for key in row["related_suite_keys"]}
            related_suites = {key: self.suites.resolve_suite(str(dependency["id"]), "00000000-0000-0000-0000-000000000000", conn=conn) for key, dependency in related.items()}
            cur.execute("SELECT * FROM product.official_suite_heads WHERE suite_key = %s", (row["suite_key"],))
            head = self._official_receipt(row["suite_key"], cur.fetchone())
            cur.execute("SELECT proof FROM product.suite_validations WHERE tenant_id IS NULL AND revision_id = %s", (revision_id,))
            proof = cur.fetchone()
            return {
                "suite": suite,
                "head": head,
                "validation": _validation(conn, None, row),
                "report": proof["proof"].get("report") if proof else None,
                "related_suites": related_suites,
                "related_suite_digests": {key: value["resolved_digest"] for key, value in related.items()},
                "related_revision_ids": tuple(str(related[key]["id"]) for key in sorted(related)),
            }

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

    def publish_official(self, revision_id: str, *, expected_revision: int, expected_validation_revision: int, expected_proof_digest: str | None = None) -> dict:
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
            if expected_proof_digest is not None:
                if validation["proof_digest"] != expected_proof_digest:
                    raise ValueError("suite_validation_conflict")
                cur.execute("SELECT proof FROM product.suite_validations WHERE tenant_id IS NULL AND revision_id = %s", (revision_id,))
                stored = cur.fetchone()
                selected_ids = tuple(str(_selected(conn, None, key)["row"]["id"]) for key in sorted(revision["related_suite_keys"]))
                if stored is None or tuple(stored["proof"].get("report", {}).get("related_revision_ids", ())) != selected_ids:
                    raise ValueError("suite_validation_inputs_changed")
                for dependency_id in selected_ids:
                    self.suites.resolve_suite(dependency_id, "00000000-0000-0000-0000-000000000000", conn=conn)
            if validation["write_revision"] != expected_validation_revision:
                raise ValueError("suite_validation_conflict")
            if not validation["verified"] or validation["samples_not_rerun"]:
                raise ValueError("official_suite_validation_required")
            # Proof identity is insufficient if an object disappeared or drifted.
            resolved = self.suites.resolve_suite(str(revision["id"]), "00000000-0000-0000-0000-000000000000", conn=conn)
            blocked = adoption_block(resolved, verified=True, force_unverified=False)
            if blocked:
                raise ValueError(blocked)
            from product.legacy_head_cutover import refuse_related_carrier

            refuse_related_carrier(self, conn, None, revision["related_suite_keys"])
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
            if tenant_id is None and proof.related_revision_ids:
                selected_ids = tuple(str(_selected(conn, None, key)["row"]["id"]) for key in sorted(revision["related_suite_keys"]))
                if proof.related_revision_ids != selected_ids:
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
