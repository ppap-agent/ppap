"""Single product projection over validated Core findings and explicit coverage."""

from __future__ import annotations

from audit.checker_core import ExecutionSnapshot

from agent.mapping import checker_response
from agent.protocol import ExecuteRequest, ExecuteResponse


def core_response(
    spec: ExecuteRequest, snapshot: ExecutionSnapshot, result: dict, *, deep: dict[str, dict[str, str]] | None = None, reviews: dict[tuple[str, str], dict] | None = None
) -> ExecuteResponse:
    """``deep`` is given exactly for a deep-mode round: the review state of each target for which deep review applies.

    ``reviews`` holds the deep reviewer's record per (file, clause): confirmed, corrected
    (the finding already carries the corrected verdict) or rejected (the program verdict stays).
    """
    targets = {target.file_version_id: target for target in snapshot.targets}
    executed = [(item, [finding for finding in result["findings"] if finding["file_version_id"] == item.file_version_id]) for item in spec.files if item.role == "target"]
    usage = {key: result[key] for key in ("input_tokens", "output_tokens", "model_requests") if type(result.get(key)) is int and result[key] >= 0}
    unsupported = frozenset((row["file_version_id"], row["clause_id"]) for row in result["coverage"] if row["coverage"] == "unsupported")
    response = checker_response(
        spec,
        executed,
        unsupported=unsupported,
        unavailable_sources=frozenset(result.get("unavailable_sources", ())),
        usage=usage,
        usage_source="provider" if len(usage) == 3 else "unknown",
    )
    for finding in response.findings:
        version = (finding.locator or {}).get("file_version_id")
        if version in targets:
            target = targets[version]
            finding.locator = {**(finding.locator or {}), "suite_revision_id": target.suite_revision_id, "checker_digest": target.binding.checker_digest, "verification_status": target.verification}
            record = (reviews or {}).get((version, str((finding.locator or {}).get("rule_item_id"))))
            if record is not None and isinstance(finding.locator.get("semantic"), dict):
                # Kept apart from human review; evidence of a correction is already the finding's sources.
                finding.locator["semantic"]["deep_review"] = {key: value for key, value in record.items() if key not in {"clause_id", "evidence"}}
    for row in response.coverage.rule_items:
        target = targets[row["file_version_id"]]
        row["suite_revision_id"] = target.suite_revision_id
        row["checker_digest"] = target.binding.checker_digest
        row["verification_status"] = target.verification
        row["samples_not_rerun"] = target.samples_not_rerun
    response.coverage.checker_execution = {
        "input_digest": spec.checker_snapshot_digest,
        "execution_input_digest": result["input_digest"],
        "runtime_revision": snapshot.runtime_revision,
        "library_revision": snapshot.library_revision,
        "unavailable_file_version_ids": result.get("unavailable_sources", []),
        "targets": [
            {
                "file_version_id": target.file_version_id,
                "suite_revision_id": target.suite_revision_id,
                "suite_digest": target.suite_digest,
                "checker_digest": target.binding.checker_digest,
                "verification_status": target.verification,
                "samples_not_rerun": target.samples_not_rerun,
                **({"deep_review": dict(deep[target.file_version_id])} if deep is not None and target.file_version_id in deep else {}),
            }
            for target in snapshot.targets
        ],
    }
    if deep is not None:
        response.coverage.checker_execution["analysis_mode"] = "deep"
    response.note = "checker_core_audit"
    return response
