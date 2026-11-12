"""Programs a deep-mode round wrote for its targets, kept so history names the exact code.

Only the Deep Controller writes here, inside the audit job's live lease. Each generated
program becomes an unadopted Suite revision on top of the target's frozen Suite; it is
never published or offered to other rounds.
"""

from __future__ import annotations

import json

from audit.checker_core import checker_digest

from product.store.suites import SuiteStore


def _require_independent_program(files) -> None:
    """A generated program may not import another company's or version's reader."""
    import ast

    from audit.checker_lib.imports import import_violations

    for source in files.values():
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            raise ValueError("checker_source_incomplete") from None
        if import_violations(tree):
            raise ValueError("checker_cross_version_import")


def _candidate_files(base, artifact) -> dict[str, bytes]:
    _require_independent_program(artifact.files)
    rules = json.loads(base.files["rules.json"])
    rules["checker_binding"] = {item["id"]: "checker:create_audit" for item in rules["rules"]}
    return {**dict(artifact.files), "rules.json": json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()}


def _deleted_program_paths(base, artifact) -> tuple[str, ...]:
    # Artifact.files is the complete executed program, not a sparse edit. An
    # omitted optional module must not reappear through the Suite's base overlay.
    return ("resources.py",) if "resources.py" in base.files and "resources.py" not in artifact.files else ()


class DeepArtifactStore:
    def __init__(self, store):
        self.store = store
        self.suites = SuiteStore(store.settings, store.objects, store.role)

    def save_round_artifacts(self, spec, original_snapshot, artifacts) -> dict[str, dict[str, str]]:
        """Fenced by the job lease; a replayed attempt returns the same revisions."""
        result: dict[str, dict[str, str]] = {}
        with self.store._conn() as conn, conn.transaction():
            job = conn.execute(
                """SELECT j.id FROM engine.jobs j JOIN product.audit_rounds r ON r.id=j.audit_round_id
                   WHERE j.id=%s AND j.tenant_id=%s AND j.attempt=%s AND j.kind='audit'
                     AND j.status='leased' AND j.lease_until>clock_timestamp() AND r.id=%s AND r.tenant_id=j.tenant_id
                     AND r.created_by=%s AND r.deep_mode FOR UPDATE OF j""",
                (spec.job_id, spec.tenant_id, spec.attempt, spec.round_id, spec.initiating_user_id),
            ).fetchone()
            if job is None:
                raise ValueError("checker_lease_lost")
            frozen = self.suites.read_snapshot(spec.round_id, spec.tenant_id, conn=conn)
            if frozen is None or frozen.to_dict() != original_snapshot.to_dict():
                raise ValueError("checker_snapshot_mismatch")
            from product.store.suite_releases import _lock

            _lock(conn, spec.tenant_id)
            targets = {item.file_version_id: item for item in frozen.targets}
            if not artifacts or not set(artifacts) <= targets.keys():
                raise ValueError("checker_artifact_scope_mismatch")
            for version_id, artifact in artifacts.items():
                # The controller validated execution and identity. Recheck the
                # immutable byte identity before giving it a durable Suite identity.
                if artifact.executor_revision != spec.executor_revision or artifact.program_digest != checker_digest(artifact.files):
                    raise ValueError("checker_artifact_identity_mismatch")
                prior = conn.execute(
                    """SELECT suite_revision_id,program_digest,input_digest FROM product.deep_round_programs
                       WHERE round_id=%s AND file_version_id=%s AND result_attempt=%s AND tenant_id=%s""",
                    (spec.round_id, version_id, spec.attempt, spec.tenant_id),
                ).fetchone()
                if prior:
                    if (prior[1], prior[2]) != (artifact.program_digest, artifact.input_digest):
                        raise ValueError("checker_artifact_identity_mismatch")
                    candidate = self.suites.resolve_suite(str(prior[0]), spec.tenant_id, conn=conn).revision
                else:
                    base = self.suites.resolve_suite(targets[version_id].suite_revision_id, spec.tenant_id, conn=conn)
                    candidate = self.suites.create_revision(
                        tenant_id=spec.tenant_id,
                        suite_key=base.revision.suite_key,
                        files=_candidate_files(base, artifact),
                        deleted_paths=_deleted_program_paths(base, artifact),
                        created_by=spec.initiating_user_id,
                        base_revision_id=base.revision.id,
                        related_suite_keys=base.revision.related_suite_keys,
                        conn=conn,
                    )
                    conn.execute(
                        """INSERT INTO product.deep_round_programs
                           (round_id,file_version_id,result_attempt,tenant_id,suite_revision_id,program_digest,input_digest)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (spec.round_id, version_id, spec.attempt, spec.tenant_id, candidate.id, artifact.program_digest, artifact.input_digest),
                    )
                result[version_id] = {"suite_revision_id": candidate.id, "suite_digest": candidate.resolved_digest, "program_digest": artifact.program_digest, "input_digest": artifact.input_digest}
            # Slow object storage cannot commit a success after this attempt expires.
            if not conn.execute("SELECT 1 FROM engine.jobs WHERE id=%s AND lease_until>clock_timestamp()", (spec.job_id,)).fetchone():
                raise ValueError("checker_lease_lost")
        return result
