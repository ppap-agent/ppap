"""Thin adapter: current RoundWork ↔ bounded Deep spec, Deep result ↔ product findings."""
from __future__ import annotations

from ppap_core.audits import REFERENCE, TARGET, RoundWork
from ppap_deep.client import DeepError
from ppap_deep.protocol import DeepCoverage, DeepFinding, ExecuteRequest, ExecuteResponse, FrozenFile


def spec_from_work(work: RoundWork, executor: str, executor_revision: str, protocol_revision: str) -> ExecuteRequest:
    if executor != "ppap-deep":
        raise DeepError("deep_executor_mismatch")
    files = []
    for item in work.inputs:
        if item.role not in {TARGET, REFERENCE}:
            raise DeepError("deep_input_invalid")
        files.append(
            FrozenFile(
                file_version_id=item.file_version_id,
                file_id=item.file_id,
                role=TARGET if item.role == TARGET else REFERENCE,
                filename=item.filename,
                sha256=item.sha256 or "",
                content_type=item.content_type or "",
                rule_version_id=item.rule_version_id,
                rule_title=item.rule_title,
                rule_body=item.rule_body,
                rule_execution=item.rule_execution,
                rule_source_file_version_id=item.rule_source_file_version_id,
            )
        )
    return ExecuteRequest(
        protocol_revision=protocol_revision,
        round_id=work.round_id,
        files=files,
        executor="ppap-deep",
        executor_revision=executor_revision,
        tenant_id=work.tenant_id,
    )


def product_result(response: ExecuteResponse, work: RoundWork) -> dict:
    known = {item.file_version_id for item in work.inputs}
    if response.round_id != work.round_id:
        raise DeepError("deep_output_identity_mismatch")
    if [row.file_version_id for row in response.files] != [item.file_version_id for item in work.inputs]:
        raise DeepError("deep_output_identity_mismatch")
    findings: list[dict] = []
    seen: set[str] = set()
    for row in response.findings:
        if row.item_key in seen:
            raise DeepError("deep_output_invalid")
        seen.add(row.item_key)
        locator = row.locator
        version_id = (locator or {}).get("file_version_id")
        if version_id and version_id not in known:
            raise DeepError("deep_output_identity_mismatch")
        findings.append(row.model_dump())
    coverage = DeepCoverage.model_validate(response.coverage.model_dump()).model_dump()
    return {
        "findings": findings,
        "coverage": coverage,
        "note": response.note or "deep_audit",
        "usage": response.usage,
    }


def passing_response(spec: ExecuteRequest) -> ExecuteResponse:
    """Deterministic ScriptedRuntime helper used by tests, not production Settings."""
    findings: list[DeepFinding] = []
    checked: list[str] = []
    unchecked: list[str] = []
    for item in spec.files:
        if item.role == TARGET:
            findings.append(
                DeepFinding(
                    item_key=f"target:{item.file_version_id}",
                    classification="资料",
                    item="深度审核已完成",
                    priority="P0",
                    status="PASS",
                    suggestions="",
                    locator={
                        "kind": "file_version",
                        "file_version_id": item.file_version_id,
                        "filename": item.filename,
                    },
                )
            )
            checked.append(item.filename)
        else:
            unchecked.append(f"{item.filename}（参考，未作为待审完整检查）")
    return ExecuteResponse(
        protocol_revision=spec.protocol_revision,
        round_id=spec.round_id,
        files=list(spec.files),
        findings=findings,
        coverage=DeepCoverage(checked=checked, unchecked=unchecked, locator_gap=False),
        note="deep_audit",
    )
