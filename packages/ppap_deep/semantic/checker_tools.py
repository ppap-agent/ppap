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

if __name__ == "__main__" and not __package__:
    # The guest calls this file by its stable /opt/ppap/semantic path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ppap_deep.semantic.document_tools import col_number, effective, ref, save
from ppap_deep.semantic.evidence import EvidenceBudget
from ppap_deep.semantic.models import Artifact, Citation, Predicate, QueryProgram, Scalar
from ppap_deep.semantic.trace import Binding, predicate_result, query_counts


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
    limitation: Literal["local_condition_only; mapping_and_rule_applicability_not_verified"] = (
        "local_condition_only; mapping_and_rule_applicability_not_verified"
    )


def _citation(binding: Binding, source_format: str, row: int, column: int) -> Citation:
    if source_format in {".xlsx", ".xlsm", ".xls"}:
        locator: dict[str, Any] = {"kind": "xlsx_cell", "sheet": binding.table, "cell": ref(row, column)}
    elif source_format == ".docx" and binding.table.startswith("table:"):
        locator = {"kind": "docx_cell", "table": int(binding.table.removeprefix("table:")), "row": row, "column": column}
    else:
        raise ValueError("Field checker requires an Excel or Word table snapshot")
    return Citation.model_validate({"document_id": binding.document_id, "locator": locator})


def check_fields(snapshot: dict[str, Any], request: CheckRequest) -> CheckOutput:
    """Replay two simple predicates; never infer binding or rule equivalence."""
    binding = request.binding
    source = snapshot.get("source")
    if not isinstance(source, dict) or source.get("format") not in {".xlsx", ".xlsm", ".xls", ".docx"}:
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
        if check.field not in binding.columns:
            raise ValueError("Checked field is absent from reviewed binding")
        if isinstance(check, NumericAtLeastCheck) and source["format"] == ".docx":
            raise ValueError("Numeric checker requires typed Excel values")
        predicate = Predicate(field=check.field, op="not_empty") if isinstance(check, NonEmptyCheck) else Predicate(field=check.field, op="ge", value=check.minimum)
        program = QueryProgram(kind="query", predicates=[predicate], quantifier="forall")
        status, total, checked, unknown, failures = query_counts(program, binding, snapshot, EvidenceBudget())
        column = col_number(binding.columns[check.field])
        excluded = {item.row for item in binding.excluded_rows}
        violations: list[CellObservation] = []
        unknown_examples: list[CellObservation] = []
        unknown_rows: list[int] = []
        for row in range(binding.first_row, binding.last_row + 1):
            if row in excluded:
                continue
            item = effective(table, row, column)
            if row in failures:
                if len(violations) < 64:
                    violations.append(CellObservation(
                        row=row, citation=_citation(binding, source["format"], row, column),
                        value=item["value"], state=item["state"],
                    ))
            elif item["state"] != "known" or predicate_result(predicate, item["value"]) is None:
                unknown_rows.append(row)
                if len(unknown_examples) < 64:
                    unknown_examples.append(CellObservation(
                        row=row, citation=_citation(binding, source["format"], row, column),
                        value=item["value"], state=item["state"],
                    ))
        if len(unknown_rows) != unknown:
            raise ValueError("Local evidence count disagrees with deterministic replay")
        results.append(LocalCheckResult(
            id=check.id,
            kind=check.kind,
            field=check.field,
            local_condition_status=status,
            total=total,
            checked=checked,
            unknown=unknown,
            violations_count=len(failures),
            violation_rows=sorted(failures),
            unknown_rows=unknown_rows,
            violations=violations,
            unknown_examples=unknown_examples,
        ))
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
    print(json.dumps({"output": args.out, "document_id": result.document_id, "checks": [
        {"id": item.id, "local_condition_status": item.local_condition_status,
         "total": item.total, "unknown": item.unknown, "violations_count": item.violations_count}
        for item in result.checks
    ]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
