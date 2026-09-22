"""Production Deep auditor: in-repo 检查项 engine on frozen files. Not the gVisor guest heuristic."""
from __future__ import annotations

from ppap_core.audits import ANALYSIS_DEEP, RoundInput, RoundWork
from ppap_core.engine_round import run_round_from_bytes
from ppap_deep.client import DeepError
from ppap_deep.protocol import DeepCoverage, DeepFinding, ExecuteRequest, ExecuteResponse


def round_work_from_spec(spec: ExecuteRequest) -> RoundWork:
    inputs = []
    for item in spec.files:
        inputs.append(
            RoundInput(
                role=item.role,
                file_id=item.file_version_id,
                file_version_id=item.file_version_id,
                filename=item.filename,
                status="ready",
                content_type=item.content_type or "",
                rule_version_id=item.rule_version_id,
                rule_title=item.rule_title,
                rule_body=item.rule_body,
                sha256=item.sha256,
                rule_execution=item.rule_execution,
            )
        )
    return RoundWork(spec.round_id, "", inputs, analysis_mode=ANALYSIS_DEEP)


def audit_spec(spec: ExecuteRequest, files: dict[str, bytes]) -> ExecuteResponse:
    work = round_work_from_spec(spec)
    for item in work.inputs:
        if item.role == "target" and item.file_version_id not in files:
            raise DeepError("deep_input_missing")
    try:
        raw = run_round_from_bytes(work, files)
    except DeepError:
        raise
    except Exception as exc:
        raise DeepError("deep_execution_failed") from exc
    findings = []
    for row in raw.get("findings") or []:
        findings.append(
            DeepFinding(
                item_key=str(row.get("item_key") or ""),
                classification=str(row.get("classification") or ""),
                item=str(row.get("item") or ""),
                priority=str(row.get("priority") or "P0"),
                status=row.get("status") or "ERR",  # type: ignore[arg-type]
                suggestions=str(row.get("suggestions") or ""),
                locator=row.get("locator"),
            )
        )
    coverage = raw.get("coverage") or {}
    return ExecuteResponse(
        protocol_revision=spec.protocol_revision,
        round_id=spec.round_id,
        files=list(spec.files),
        findings=findings,
        coverage=DeepCoverage(
            checked=list(coverage.get("checked") or []),
            unchecked=list(coverage.get("unchecked") or []),
            locator_gap=bool(coverage.get("locator_gap")),
            rule_items=list(coverage.get("rule_items") or []),
        ),
        note=str(raw.get("note") or "in-repo engine"),
    )


class ProcessRuntime:
    def execute(self, spec: ExecuteRequest, files: dict[str, bytes]) -> ExecuteResponse:
        return audit_spec(spec, files)
