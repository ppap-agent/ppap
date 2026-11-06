"""Import legacy Round identities without executing an audit or a Checker.

Default is a database READ ONLY dry-run. --apply additionally requires the
exact --expect-digest printed by a reviewed dry-run. Production use is a
separately authorized #218 operation, never an application startup step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re

from agent.checker_program import group_context_for_spec, input_digest
from agent.checker_spec import CheckerArtifact
from agent.protocol import ExecuteRequest, FrozenFile
from product import build_product_store
from product.checker_history import HistoryImportError, import_history, read_history
from product.files import ObjectMissing
from product.identity import Principal
from product.settings import Settings
from product.suites import MAX_OBJECT_BYTES, content_digest


def artifact_loader(objects):
    """Resolve only the exact saved attempt namespace, never a reusable cache.

    Existing input-digest code is used only to verify bytes, not to run legacy
    execution. Unsupported old formats stay blocked instead of guessing.
    """

    def load(source: dict) -> dict:
        head, jobs = source["round"], source["jobs"]
        for item in source["inputs"]:
            body = objects.get(item["object_key"], max_bytes=32 * 1024 * 1024)
            if content_digest(body) != item["sha256"]:
                raise HistoryImportError("history_original_digest_mismatch")
        if head["analysis_mode"] != "deep":
            return {}
        if len(jobs) != 1 or not head["result_attempt"]:
            raise HistoryImportError("history_execution_identity_missing")
        job = jobs[0]
        execution = job["execution_source"]
        revision = execution.get("executor_revision", "")
        if (
            execution.get("executor") != "ppap-deep"
            or not isinstance(revision, str)
            or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision)
            or job["attempt"] != head["result_attempt"]
            or job["finished_at"] is None
        ):
            raise HistoryImportError("history_execution_identity_unknown")
        files = []
        for item in source["inputs"]:
            files.append(
                FrozenFile(
                    file_version_id=item["file_version_id"],
                    file_id=item["file_id"],
                    role=item["role"],
                    filename=item["original_filename"],
                    sha256=item["sha256"],
                    content_type=item["content_type"],
                    rule_version_id=item["rule_version_id"],
                    rule_title=item["rule_title"],
                    rule_body=item["rule_body"],
                    rule_execution=item["rule_execution"],
                )
            )
        spec = ExecuteRequest(
            protocol_revision=execution.get("protocol_revision", "history-read-only"),
            executor="ppap-deep",
            executor_revision=revision,
            round_id=head["id"],
            tenant_id=head["tenant_id"],
            job_id=job["id"],
            attempt=head["result_attempt"],
            files=files,
        )
        group = group_context_for_spec(spec)
        if group is None:
            raise HistoryImportError("history_inputs_missing")
        prefix = f"deep/{hashlib.sha256(head['tenant_id'].encode()).hexdigest()}/{revision}/rounds/{head['id']}/{hashlib.sha256(job['id'].encode()).hexdigest()}-{head['result_attempt']}"
        found = {}
        for item in files:
            if item.role != "target":
                continue
            digest = input_digest(group.by_audit_id[item.file_version_id], group.rules_by_id[item.file_version_id], file_sha256=item.sha256, role=item.role, group_context=group)
            key = f"{prefix}/checkers/{digest}.json"
            try:
                raw = objects.get(key, max_bytes=2 * MAX_OBJECT_BYTES + 65536)
            except ObjectMissing:
                continue
            artifact = CheckerArtifact.model_validate_json(raw)
            if artifact.input_digest != digest or artifact.executor_revision != revision:
                raise HistoryImportError("history_artifact_input_mismatch")
            found[item.file_version_id] = {
                "input_digest": digest,
                "executor_revision": revision,
                "entrypoint": artifact.entrypoint,
                "files": dict(artifact.files),
                "program_digest": artifact.program_digest,
                "object_key": key,
                "object_digest": content_digest(raw),
            }
        return found

    return load


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Exact tenant UUID; no all-tenant import.")
    parser.add_argument("--round-id", action="append", help="Optional exact Round UUIDs; otherwise all legacy Rounds in this tenant.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-digest", help="Required with --apply; reviewed dry-run plan digest.")
    parser.add_argument("--verify-user-id", help="Read back selected Rounds with this existing user's current membership and object grants; no writes.")
    args = parser.parse_args(argv)
    if args.apply and (not args.expect_digest or args.verify_user_id):
        parser.error("--apply requires --expect-digest and cannot combine with --verify-user-id")
    if args.verify_user_id and not args.round_id:
        parser.error("--verify-user-id requires one or more --round-id")
    try:
        store = build_product_store(Settings(), role="api" if args.verify_user_id else "migrator")
        if args.verify_user_id:
            principal = Principal(args.verify_user_id, args.tenant_id, "", "", "", "", "member")
            result = {"writes": False, "rounds": [{"round_id": ident, "identities": read_history(store, principal, ident)} for ident in args.round_id]}
        else:
            result = import_history(store, tenant_id=args.tenant_id, artifact_loader=artifact_loader(store.objects), apply=args.apply, expected_digest=args.expect_digest, round_ids=args.round_id)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0 if result.get("complete", True) else 2
    except HistoryImportError as exc:
        print(json.dumps({"complete": False, "error": str(exc)}))
        return 2
    except Exception:
        print(json.dumps({"complete": False, "error": "history_operation_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
