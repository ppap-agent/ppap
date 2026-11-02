"""Narrow, source-addressed checks over a reviewed document binding.

These are observations for a planned rule, not customer-rule decisions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from audit.bindings import Binding, BindingScope, binding_scope, row_claims
from audit.document_tools import TableReader, col_number, save
from audit.document_tools import TableReader as _SnapshotReader
from audit.evidence import EvidenceBudget, cell_locator
from audit.models import Artifact, Citation, Predicate, Scalar
from audit.values import predicate_result


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


def _local_predicate(check: FieldCheck) -> Predicate:
    if isinstance(check, NonEmptyCheck):
        return Predicate(field=check.field, op="not_empty")
    return Predicate(field=check.field, op="ge", value=check.minimum)


def check_fields(snapshot: dict[str, Any], request: CheckRequest) -> CheckOutput:
    """Replay two simple predicates from the frozen table. This does not build a RulePlan or AuditTrace."""
    binding = request.binding
    source = snapshot.get("source")
    if not isinstance(source, dict) or source.get("format") not in {".xlsx", ".xlsm", ".xls", ".docx", ".pdf"}:
        raise ValueError("Field checker requires a supported table snapshot")
    if source.get("sha256") != binding.source_sha256:
        raise ValueError("Binding source version mismatch")
    if len({check.id for check in request.checks}) != len(request.checks):
        raise ValueError("Duplicate local check identity")
    table = next((item for item in snapshot.get("tables", []) if isinstance(item, dict) and item.get("id") == binding.table), None)
    if table is None:
        raise ValueError("Binding table missing")
    budget = EvidenceBudget()
    binding_scope(binding, snapshot, budget)
    scope = request.scope or BindingScope(binding_id=binding.id)
    if scope.binding_id != binding.id:
        raise ValueError("Local check scope must reference its own binding")
    excluded = row_claims(scope.excluded_rows, binding, table, snapshot, budget)
    unresolved = row_claims(scope.unresolved_rows, binding, table, snapshot, budget)
    if excluded & unresolved:
        raise ValueError("A row cannot be both excluded and unresolved")
    results: list[LocalCheckResult] = []
    for check in request.checks:
        if check.field not in binding.columns and check.field not in binding.field_holes:
            raise ValueError("Checked field is absent from reviewed binding")
        if isinstance(check, NumericAtLeastCheck) and source["format"] == ".docx":
            raise ValueError("Numeric checker requires typed Excel values")
        predicate = _local_predicate(check)
        column = col_number(binding.columns[check.field]) if check.field in binding.columns else None
        outcomes: dict[int, bool | None] = {}
        unknown_rows: list[int] = []
        failures: set[int] = set()
        total = binding.last_row - binding.first_row + 1
        predicate_reader = _SnapshotReader(table, EvidenceBudget())
        for row in range(binding.first_row, binding.last_row + 1):
            if row in excluded:
                continue
            if column is None or row in unresolved:
                outcomes[row] = None
                unknown_rows.append(row)
                continue
            item = predicate_reader.cell(row, column)
            if item.get("state") != "known":
                outcomes[row] = None
                unknown_rows.append(row)
                continue
            value = predicate_result(predicate, item.get("value"))
            outcomes[row] = value
            if value is False:
                failures.add(row)
            elif value is None:
                unknown_rows.append(row)
        applicable = total - len(excluded)
        unknown = len(unknown_rows)
        if failures:
            status: Literal["PASS", "NG", "UNABLE"] = "NG"
        elif unknown or applicable == 0:
            status = "UNABLE"
        else:
            status = "PASS"
        violations: list[CellObservation] = []
        unknown_examples: list[CellObservation] = []
        reader: TableReader | None = None
        for row, value in outcomes.items():
            examples = violations if value is False else unknown_examples if value is None else None
            if examples is None or column is None or len(examples) >= 64:
                continue
            if reader is None:
                reader = TableReader(table, EvidenceBudget())
            item = reader.cell(row, column)
            examples.append(
                CellObservation(
                    row=row,
                    citation=_citation(binding, source["format"], row, column),
                    value=item["value"],
                    state=item["state"] if value is False else "unresolved_scope" if row in unresolved else item["state"],
                )
            )
        results.append(
            LocalCheckResult(
                id=check.id,
                kind=check.kind,
                field=check.field,
                local_condition_status=status,
                total=total,
                checked=total - unknown,
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
