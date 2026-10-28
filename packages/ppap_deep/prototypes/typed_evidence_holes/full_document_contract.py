"""Research-only typed proposals and source checks; never a business acceptance gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Annotated, Literal
from zipfile import ZipFile

from docx import Document
from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

RuleId = Literal["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]
Status = Literal["resolved", "unresolved", "absent"]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Reason = Annotated[str, Field(min_length=1, max_length=2000)]


class Typed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class CellEvidence(Typed):
    kind: Literal["cells"]
    source: Literal["document"]
    sheet: str = Field(min_length=1)
    range: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}(?::[A-Z]{1,3}[1-9][0-9]{0,6})?$")


class MediaEvidence(Typed):
    kind: Literal["media"]
    source: Literal["document", "rules"]
    media_path: str = Field(pattern=r"^(?:xl|word)/media/[^/]+$")


class RuleCellEvidence(Typed):
    kind: Literal["rule_cell"]
    source: Literal["rules"]
    table: int = Field(ge=1)
    row: int = Field(ge=1)
    column: int = Field(ge=1)


EvidenceRef = Annotated[CellEvidence | MediaEvidence | RuleCellEvidence, Field(discriminator="kind")]


class Record(Typed):
    id: Identifier
    sheet: str = Field(min_length=1)
    rows: list[Annotated[int, Field(ge=1, le=1048576)]] = Field(min_length=1, max_length=4096)
    section: Literal["dimension", "non_dimension", "other"]

    @model_validator(mode="after")
    def unique_rows(self) -> Record:
        if len(self.rows) != len(set(self.rows)):
            raise ValueError("record_rows_duplicate")
        return self


class RecordHole(Typed):
    kind: Literal["records"]
    id: Identifier
    status: Status
    records: list[Record] = Field(max_length=4096)
    evidence: list[EvidenceRef] = Field(max_length=256)
    reason: Reason

    @model_validator(mode="after")
    def resolved_records(self) -> RecordHole:
        if self.status == "resolved" and (not self.records or not self.evidence):
            raise ValueError("resolved_records_require_records_and_evidence")
        if self.status == "absent" and (self.records or not self.evidence):
            raise ValueError("absent_records_require_empty_records_and_scope_evidence")
        # An unresolved set may retain known records; its coverage is still unknown.
        return self


class ValueHole(Typed):
    kind: Literal["value"]
    id: Identifier
    status: Status
    value_type: Literal["text", "number", "boolean", "text_list", "number_list"]
    value: str | int | float | bool | list[str] | list[int | float] | None
    evidence: list[EvidenceRef] = Field(max_length=256)
    reason: Reason

    @model_validator(mode="after")
    def typed_value(self) -> ValueHole:
        if self.status != "resolved":
            if self.value is not None or (self.status == "absent" and not self.evidence):
                raise ValueError("unresolved_value_must_be_null_absence_needs_scope_evidence")
            return self
        if not self.evidence:
            raise ValueError("resolved_value_requires_evidence")

        def number(value: object) -> bool:
            return type(value) is int or (type(value) is float and math.isfinite(value))

        valid = {
            "text": type(self.value) is str,
            "number": number(self.value),
            "boolean": type(self.value) is bool,
            "text_list": type(self.value) is list and all(type(item) is str for item in self.value),
            "number_list": type(self.value) is list and all(number(item) for item in self.value),
        }
        if not valid[self.value_type]:
            raise ValueError("value_does_not_match_value_type")
        return self


Hole = Annotated[RecordHole | ValueHole, Field(discriminator="kind")]


class PlannedRule(Typed):
    rule_id: RuleId
    source_text: str = Field(min_length=1, max_length=4000)
    interpretation: Reason


class RuleCheck(Typed):
    rule_id: RuleId
    status: Literal["PASS", "NG", "UNABLE"]
    required_hole_ids: list[Identifier] = Field(min_length=1, max_length=4096)
    record_ids: list[Identifier] = Field(max_length=4096)
    unknown_record_ids: list[Identifier] = Field(max_length=4096)
    reason: Reason


class FullDocumentResult(Typed):
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rules_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rule_plan: list[PlannedRule] = Field(min_length=8, max_length=8)
    holes: list[Hole] = Field(min_length=1, max_length=16384)
    checks: list[RuleCheck] = Field(min_length=8, max_length=8)
    limitations: list[Reason] = Field(max_length=128)

    @model_validator(mode="after")
    def consistent_identities(self) -> FullDocumentResult:
        required_rules = {f"R{n}" for n in range(1, 9)}
        if {rule.rule_id for rule in self.rule_plan} != required_rules or {check.rule_id for check in self.checks} != required_rules:
            raise ValueError("rule_plan_and_checks_must_cover_R1_through_R8_once")
        holes = {hole.id: hole for hole in self.holes}
        records = [record.id for hole in self.holes if isinstance(hole, RecordHole) for record in hole.records]
        if len(holes) != len(self.holes) or len(records) != len(set(records)):
            raise ValueError("duplicate_hole_or_record_id")
        for check in self.checks:
            if any(len(items) != len(set(items)) for items in (check.required_hole_ids, check.record_ids, check.unknown_record_ids)):
                raise ValueError("duplicate_check_dependency_or_record")
            if not set(check.required_hole_ids) <= holes.keys() or not set(check.record_ids) <= set(records) or not set(check.unknown_record_ids) <= set(check.record_ids):
                raise ValueError("unknown_hole_or_record_reference")
            unresolved = any(holes[key].status != "resolved" for key in check.required_hole_ids)
            empty = any(isinstance(holes[key], ValueHole) and (holes[key].value == [] or (isinstance(holes[key].value, str) and not holes[key].value.strip())) for key in check.required_hole_ids)
            if check.status == "PASS" and (check.unknown_record_ids or unresolved or empty):
                raise ValueError("PASS_requires_resolved_dependencies_and_no_unknown_records")
        return self


def validate_result(raw: dict, document: Path, rules: Path) -> dict:
    """Check shape, source identity and reference existence, never infer correctness."""
    summary = {
        "errors": [],
        "schema_valid": False,
        "source_refs_valid": False,
        "rule_texts_valid": False,
        "semantic_binding": "unverified",
        "record_coverage": "unverified",
        "business_verdicts": "proposals_not_independent_acceptance",
        "training_eligible": False,
    }
    errors = summary["errors"]
    try:
        result = FullDocumentResult.model_validate(raw)
    except ValidationError as error:
        # Fixed validator messages/locations only; do not echo supplied document data.
        summary["errors"] = ["schema:" + ".".join(map(str, item["loc"])) + ":" + item["msg"] for item in error.errors(include_input=False, include_url=False)]
        return summary
    summary["schema_valid"] = True
    for name, path, expected in (("document", document, result.document_sha256), ("rules", rules, result.rules_sha256)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(name + "_sha256_mismatch")
    rule_document = Document(rules)
    expected_text = {f"R{n}": row.cells[1].text for n, row in enumerate(rule_document.tables[0].rows[1:], 1)}

    def normalize(text: str) -> str:
        return re.sub(r"\s+", "", text)

    for rule in result.rule_plan:
        if normalize(rule.source_text) != normalize(expected_text.get(rule.rule_id, "")):
            errors.append("rule_text_mismatch:" + rule.rule_id)
    summary["rule_texts_valid"] = not any(error.startswith("rule_text_mismatch:") for error in errors)
    workbook = load_workbook(document, data_only=False)
    try:
        with ZipFile(document) as document_zip, ZipFile(rules) as rules_zip:
            entries = {"document": set(document_zip.namelist()), "rules": set(rules_zip.namelist())}
        for hole in result.holes:
            if isinstance(hole, RecordHole):
                for record in hole.records:
                    if record.sheet not in workbook.sheetnames or any(row > workbook[record.sheet].max_row for row in record.rows):
                        errors.append("record_outside_source:" + record.id)
            for ref in hole.evidence:
                valid = False
                if isinstance(ref, CellEvidence) and ref.sheet in workbook.sheetnames:
                    a, b, c, d = range_boundaries(ref.range)
                    sheet = workbook[ref.sheet]
                    valid = a <= c <= sheet.max_column and b <= d <= sheet.max_row
                elif isinstance(ref, MediaEvidence):
                    prefix = "xl/media/" if ref.source == "document" else "word/media/"
                    valid = ref.media_path.startswith(prefix) and ref.media_path in entries[ref.source]
                elif isinstance(ref, RuleCellEvidence):
                    if ref.table <= len(rule_document.tables):
                        table = rule_document.tables[ref.table - 1]
                        valid = ref.row <= len(table.rows) and ref.column <= len(table.rows[ref.row - 1].cells)
                if not valid:
                    errors.append("source_ref_invalid:" + hole.id)
    finally:
        workbook.close()
    summary["source_refs_valid"] = not any(not error.startswith("rule_text_mismatch:") for error in errors)
    summary["source_references_checked"] = sum(len(hole.evidence) for hole in result.holes)
    summary["proposed_statuses"] = {check.rule_id: check.status for check in result.checks}
    summary["unresolved_holes"] = [hole.id for hole in result.holes if hole.status != "resolved"]
    summary["absence_claims_verified"] = False
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = json.loads(args.result.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(json.dumps({"errors": ["result_file_unreadable_or_invalid_json"], "schema_valid": False}, ensure_ascii=False))
        return 1
    summary = validate_result(raw, args.document, args.rules)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(bool(summary["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
