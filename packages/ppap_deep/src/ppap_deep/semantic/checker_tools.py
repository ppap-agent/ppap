"""Narrow, source-addressed checks over a reviewed document binding.

These are observations for a planned rule, not customer-rule decisions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import Field

from ppap_deep.semantic.bindings import Binding, BindingScope
from ppap_deep.semantic.document_tools import col_number, effective, save
from ppap_deep.semantic.evidence import cell_locator
from ppap_deep.semantic.models import Artifact, Citation, RulePlan, Scalar
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit


class NonEmptyCheck(Artifact):
    kind: Literal["field_nonempty"]
    id: str = Field(min_length=1, max_length=128)
    field: str = Field(min_length=1, max_length=64)


class NumericAtLeastCheck(Artifact):
    kind: Literal["numeric_at_least"]
    id: str = Field(min_length=1, max_length=128)
    field: str = Field(min_length=1, max_length=64)
    minimum: float = Field(allow_inf_nan=False)


FieldCheck = Annotated[NonEmptyCheck | NumericAtLeastCheck, Field(discriminator="kind")]


class CheckRequest(Artifact):
    binding: Binding
    checks: list[FieldCheck] = Field(min_length=1, max_length=16)
    scope: BindingScope | None = None


class CellObservation(Artifact):
    row: int = Field(ge=1, strict=True)
    citation: Citation
    value: Scalar
    state: str


class LocalCheckResult(Artifact):
    id: str
    kind: Literal["field_nonempty", "numeric_at_least"]
    field: str
    local_condition_status: Literal["PASS", "NG", "UNABLE"]
    total: int = Field(ge=0, strict=True)
    checked: int = Field(ge=0, strict=True)
    unknown: int = Field(ge=0, strict=True)
    violations_count: int = Field(ge=0, strict=True)
    violation_rows: list[int]
    unknown_rows: list[int]
    violations: list[CellObservation]
    unknown_examples: list[CellObservation]


class CheckOutput(Artifact):
    document_id: str
    source: dict[str, str]
    binding: Binding
    checks: list[LocalCheckResult]
    limitation: Literal["local_condition_only; mapping_and_rule_applicability_not_verified"] = "local_condition_only; mapping_and_rule_applicability_not_verified"


def _citation(binding: Binding, source_format: str, row: int, column: int) -> Citation:
    return Citation(document_id=binding.document_id, locator=cell_locator(source_format, binding.table, row, column))


def check_fields(snapshot: dict[str, Any], request: CheckRequest) -> CheckOutput:
    """Replay two simple predicates; never infer binding or rule equivalence."""
    binding = request.binding
    source = snapshot.get("source")
    if not isinstance(source, dict) or source.get("format") not in {".xlsx", ".xlsm", ".xls", ".docx", ".pdf"}:
        raise ValueError("Field checker requires a supported table snapshot")
    if source.get("sha256") != binding.source_sha256:
        raise ValueError("Binding source version mismatch")
    if len({check.id for check in request.checks}) != len(request.checks):
        raise ValueError("Duplicate local check identity")
    table = next((item for item in snapshot.get("tables", []) if item.get("id") == binding.table), None)
    if table is None:
        raise ValueError("Binding table missing")
    results: list[LocalCheckResult] = []
    for check in request.checks:
        if check.field not in binding.columns and check.field not in binding.field_holes:
            raise ValueError("Checked field is absent from reviewed binding")
        if isinstance(check, NumericAtLeastCheck) and source["format"] == ".docx":
            raise ValueError("Numeric checker requires typed Excel values")
        predicate = (
            {"kind": "value_test", "field": check.field, "op": "not_empty"} if isinstance(check, NonEmptyCheck) else {"kind": "value_test", "field": check.field, "op": "ge", "value": check.minimum}
        )
        plan = RulePlan.model_validate(
            {
                "source_sha256": "local-condition",
                "plan_revision": "0" * 64,
                "rules": [
                    {
                        "id": check.id,
                        "source_locator": {"kind": "published_clause", "clause_id": check.id},
                        "program": {"kind": "records", "source": "records", "quantifier": "forall", "require": predicate},
                        "empty_scope_status": "UNABLE",
                        "empty_scope_basis": "A local predicate does not prove an empty business scope.",
                    }
                ],
            }
        )
        scope = request.scope or BindingScope(binding_id=binding.id)
        if scope.binding_id != binding.id:
            raise ValueError("Local check scope must reference its own binding")
        trace = AuditTrace.model_validate(
            {
                "bindings": [binding.model_dump(mode="json")],
                "uses": [{"audit_id": binding.document_id, "rule_id": check.id, "inputs": {"records": {"kind": "bound", "scopes": [scope.model_dump(mode="json")]}}}],
            }
        )
        evaluated = evaluate_audit(trace, {binding.document_id: snapshot}, {binding.document_id: plan})
        aggregate = evaluated.report.checks[0]
        status, total, checked, unknown = aggregate.status, aggregate.total, aggregate.checked, aggregate.unknown
        failures = set()
        violations: list[CellObservation] = []
        unknown_examples: list[CellObservation] = []
        unknown_rows: list[int] = []
        column = col_number(binding.columns[check.field]) if check.field in binding.columns else None
        for fact in evaluated.observations:
            if fact["role"] == "excluded" or fact["record"] is None:
                continue
            row = fact["record"]["row"]
            if fact["value"] is False:
                failures.add(row)
            elif fact["value"] is None:
                unknown_rows.append(row)
            if column is None or fact["value"] is True:
                continue
            item = effective(table, row, column)
            observation = CellObservation(
                row=row,
                citation=_citation(binding, source["format"], row, column),
                value=item["value"],
                state=item["state"] if fact["value"] is False else "unresolved_scope" if row in {entry.row for entry in scope.unresolved_rows} else item["state"],
            )
            examples = violations if fact["value"] is False else unknown_examples
            if len(examples) < 64:
                examples.append(observation)
        results.append(
            LocalCheckResult(
                id=check.id,
                kind=check.kind,
                field=check.field,
                local_condition_status=cast(Literal["PASS", "NG", "UNABLE"], status),
                total=total,
                checked=checked,
                unknown=unknown,
                violations_count=len(failures),
                violation_rows=sorted(failures),
                unknown_rows=unknown_rows,
                violations=violations,
                unknown_examples=unknown_examples,
            )
        )
    return CheckOutput(document_id=binding.document_id, source={key: str(source[key]) for key in ("file", "sha256", "format")}, binding=binding, checks=results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="Frozen document snapshot JSON")
    parser.add_argument("--request", required=True, help="JSON with binding and checks")
    parser.add_argument("--out", required=True, help="Complete local observations JSON")
    args = parser.parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text())
    request = CheckRequest.model_validate_json(Path(args.request).read_text())
    result = check_fields(snapshot, request)
    save(args.out, result.model_dump(mode="json"))
    print(
        json.dumps(
            {
                "output": args.out,
                "document_id": result.document_id,
                "checks": [
                    {"id": item.id, "local_condition_status": item.local_condition_status, "total": item.total, "unknown": item.unknown, "violations_count": item.violations_count}
                    for item in result.checks
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
