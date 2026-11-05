"""Single product projection over validated Core findings and explicit coverage."""

from __future__ import annotations

from audit.checker_core import ExecutionSnapshot

from agent.mapping import checker_response
from agent.protocol import ExecuteRequest, ExecuteResponse


def core_response(spec: ExecuteRequest, snapshot: ExecutionSnapshot, result: dict) -> ExecuteResponse:
    targets = {target.file_version_id: target for target in snapshot.targets}
    executed = [(item, [finding for finding in result["findings"] if finding["file_version_id"] == item.file_version_id]) for item in spec.files if item.role == "target"]
    usage = {key: result[key] for key in ("input_tokens", "output_tokens", "model_requests") if type(result.get(key)) is int and result[key] >= 0}
    response = checker_response(spec, executed, usage=usage, usage_source="provider" if len(usage) == 3 else "unknown")
    unsupported = {(row["file_version_id"], row["clause_id"]) for row in result["coverage"] if row["coverage"] == "unsupported"}
    response.findings = [item for item in response.findings if ((item.locator or {}).get("file_version_id"), (item.locator or {}).get("rule_item_id")) not in unsupported]
    for row in response.coverage.rule_items:
        version, clause = row["file_version_id"], row["rule_item_id"]
        target = targets[version]
        row["suite_revision_id"] = target.suite_revision_id
        row["checker_digest"] = target.binding.checker_digest
        row["verification_status"] = target.verification
        row["samples_not_rerun"] = target.samples_not_rerun
        if (version, clause) in unsupported:
            row.update(coverage="unsupported", reason="未检查：没有现成的检查，可明确选择让 AI 深度分析。")
            row.pop("item_key", None)
            row.pop("status", None)
    response.coverage.checker_execution = {
        "input_digest": snapshot.input_digest,
        "runtime_revision": snapshot.runtime_revision,
        "library_revision": snapshot.library_revision,
        "targets": [{"file_version_id": target.file_version_id, "suite_revision_id": target.suite_revision_id, "suite_digest": target.suite_digest,
                     "checker_digest": target.binding.checker_digest, "verification_status": target.verification, "samples_not_rerun": target.samples_not_rerun} for target in snapshot.targets],
    }
    response.note = "checker_core_audit"
    return response
