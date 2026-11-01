"""Doc01 research: the model selects source addresses; code owns the decisions.

This is an explicit evaluation of the existing Doc01 policy, not a classifier or
a fallback for arbitrary user rules. Model inputs never include corpus labels.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from audit.document_tools import col_number, effective, ref
from audit.engine.checkers.dimension_measurement_policy import check_duplicates, check_qualitative_method, check_tolerance, inspect_measurements, is_strict_ok_text
from audit.engine.rule_engine.resources.doc01_dimension_measurement import _IMPORTANCE_CHECKED_TOKENS
from openpyxl.utils.cell import range_boundaries
from pydantic import BaseModel, ConfigDict, Field, model_validator

RULES_SHA256 = "aef4d2cd4030d623168db26677af1afa8ea47d8deb9455e7a62ad49807787223"


class Typed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Location(Typed):
    sheet: str = Field(min_length=1)
    range: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]*(?::[A-Z]{1,3}[1-9][0-9]*)?$")


class FieldBinding(Typed):
    state: Literal["bound", "absent", "unresolved"]
    locations: list[Location] = Field(max_length=16)
    reason: str = Field(max_length=500)

    @model_validator(mode="after")
    def source_required(self):
        if self.state in {"bound", "absent"} and not self.locations:
            raise ValueError("bound value or absence claim needs inspected source locations")
        if self.state != "bound" and not self.reason.strip():
            raise ValueError("absence or uncertainty needs an explanation")
        return self


class Footer(Typed):
    vehicle: FieldBinding
    part_name: FieldBinding
    part_number: FieldBinding
    supplier: FieldBinding
    owner: FieldBinding
    date: FieldBinding


ColumnName = Literal["standard", "measurements", "result", "method"]
ColumnSpan = str | None


class Columns(Typed):
    standard: ColumnSpan
    measurements: ColumnSpan
    result: ColumnSpan
    method: ColumnSpan


class Region(Typed):
    sheet: str = Field(min_length=1)
    section: Literal["dimension", "non_dimension", "other"]
    header_rows: list[int] = Field(max_length=32, description="Actual header rows on this sheet/page; empty on a continuation without headers")
    records: list[list[int]] = Field(min_length=1, max_length=4096)
    columns: Columns
    unresolved_columns: list[ColumnName] = Field(max_length=4)


class DocumentBinding(Typed):
    title: FieldBinding
    importance: FieldBinding
    footer: Footer
    regions: list[Region] = Field(max_length=64)
    scope_resolved: bool
    limitations: list[str] = Field(max_length=32)


def column_numbers(span: str | None) -> list[int]:
    if span is None:
        return []
    if not re.fullmatch(r"[A-Z]{1,3}(?::[A-Z]{1,3})?", span):
        raise ValueError("column span must be A or A:C")
    first, _, last = span.partition(":")
    lo, hi = col_number(first), col_number(last or first)
    if lo > hi:
        raise ValueError("column span is reversed")
    return list(range(lo, hi + 1))


def validate_binding(binding: DocumentBinding, snapshot: dict) -> None:
    tables = {table["id"]: table for table in snapshot["tables"]}
    field_anchors: set[tuple[str, str]] = set()
    for field in [binding.title, binding.importance, *[getattr(binding.footer, name) for name in Footer.model_fields]]:
        current_anchors: set[tuple[str, str]] = set()
        for location in field.locations:
            table = tables.get(location.sheet)
            a, b, c, d = range_boundaries(location.range)
            if table is None or not 1 <= a <= c <= table["cols"] or not 1 <= b <= d <= table["rows"]:
                raise ValueError(f"field location outside original: {location.sheet}!{location.range}")
            if field.state == "bound":
                current_anchors.update((location.sheet, effective(table, row, column)["anchor"]) for row in range(b, d + 1) for column in range(a, c + 1))
        if field_anchors.intersection(current_anchors):
            raise ValueError("different bound document fields share a source cell")
        field_anchors.update(current_anchors)
    seen: set[tuple[str, int]] = set()
    measurement_anchors: set[tuple[str, str]] = set()
    for index, region in enumerate(binding.regions):
        table = tables.get(region.sheet)
        if table is None:
            raise ValueError(f"regions[{index}].sheet does not exist")
        for row in region.header_rows:
            if type(row) is not int or not 1 <= row <= table["rows"]:
                raise ValueError(f"regions[{index}].header_rows outside original")
        for rows in region.records:
            if not rows or len(set(rows)) != len(rows):
                raise ValueError(f"regions[{index}] has empty or duplicate record rows")
            for row in rows:
                key = (region.sheet, row)
                if type(row) is not int or not 1 <= row <= table["rows"] or row in region.header_rows or key in seen:
                    raise ValueError(f"regions[{index}] has invalid or overlapping body row {row}")
                seen.add(key)
        selected: set[int] = set()
        for name in Columns.model_fields:
            columns = column_numbers(getattr(region.columns, name))
            if any(column > table["cols"] for column in columns) or selected.intersection(columns):
                raise ValueError(f"regions[{index}].columns.{name} exceeds the sheet or overlaps another field")
            selected.update(columns)
        for rows in region.records:
            record_anchors: set[tuple[str, str]] = set()
            for name in Columns.model_fields:
                anchors = {(region.sheet, effective(table, row, column)["anchor"]) for row in rows for column in column_numbers(getattr(region.columns, name))}
                if record_anchors.intersection(anchors):
                    raise ValueError(f"regions[{index}] field roles share a merged source cell")
                record_anchors.update(anchors)
                if name == "measurements":
                    if measurement_anchors.intersection(anchors):
                        raise ValueError("different records share a measurement source cell")
                    measurement_anchors.update(anchors)


def read_cells(table: dict, rows: list[int], columns: list[int], *, snapshot: dict | None = None, seen: set | None = None) -> dict:
    """Read native values once; image-only cells and parser failures stay unknown.

    A native value is the source for these cell-backed checks. A logo or stamp
    does not invalidate that value. This does not establish visual legibility.
    PDF's unknown_visual_evidence preserves a native value when one was read;
    other unknown states never receive this exception.
    """
    values, citations, unknown = [], [], []
    seen = set() if seen is None else seen
    for row in rows:
        for column in columns:
            cell = effective(table, row, column)
            anchor = cell["anchor"]
            key = (table["id"], anchor)
            if key in seen:
                continue
            seen.add(key)
            citations.append({"sheet": table["id"], "range": anchor})
            has_value = cell.get("value") is not None and bool(str(cell["value"]).strip())
            if cell["state"] != "known" and not (cell["state"] == "unknown_visual_evidence" and has_value):
                unknown.append(anchor)
            elif has_value:
                values.append(cell["value"])
            elif snapshot is not None:
                a, b, c, d = range_boundaries(cell.get("merge") or anchor)
                if image_at(snapshot, table["id"], list(range(b, d + 1)), list(range(a, c + 1))):
                    unknown.append("unobserved_drawing")
    return {"values": values, "evidence": citations, "unknown": unknown}


def field_value(binding: FieldBinding, tables: dict, snapshot: dict | None = None) -> dict:
    values, evidence, unknown = [], [], []
    seen: set[tuple[str, str]] = set()
    for location in binding.locations:
        a, b, c, d = range_boundaries(location.range)
        observed = read_cells(tables[location.sheet], list(range(b, d + 1)), list(range(a, c + 1)), snapshot=snapshot, seen=seen)
        values.extend(observed["values"])
        evidence.extend(observed["evidence"])
        unknown.extend(observed["unknown"])
    if binding.state == "unresolved":
        unknown.append("binding_unresolved")
    if binding.state == "absent":
        if values:
            unknown.append("absence_contradicts_source")
    return {"values": values, "evidence": evidence, "unknown": unknown}


def image_at(snapshot: dict, sheet: str, rows: list[int], columns: list[int]) -> bool:
    if not rows or not columns:
        return False
    for image in snapshot.get("drawing_anchors", []):
        if image.get("sheet") != sheet:
            continue
        start, end = image.get("from"), image.get("to")
        # One-cell/absolute anchors omit the far edge. Its absence is not a
        # zero-size image; a native-only evaluator cannot exclude overlap.
        if not start or not end:
            return True
        if any(start["row"] <= row <= end["row"] for row in rows) and any(start["column"] <= col <= end["column"] for col in columns):
            return True
    for image in snapshot.get("table_image_references", []):
        if sheet == f"table:{image['table']}" and image["row"] in rows and image["column"] in columns:
            return True
    return False


def evaluate(binding: DocumentBinding, snapshot: dict, *, rules_sha256: str) -> dict:
    validate_binding(binding, snapshot)
    tables = {table["id"]: table for table in snapshot["tables"]}
    observations: dict[str, list[dict]] = {f"R{i}": [] for i in range(1, 9)}

    def add(rule, status, reason, evidence, record=None):
        observations[rule].append({"status": status, "reason": reason, "record": record, "evidence": evidence})

    title = field_value(binding.title, tables, snapshot)
    title_text = "".join("".join(str(value).split()) for value in title["values"])
    add("R1", "UNABLE" if title["unknown"] else "PASS" if title_text == "全功能检验报告" else "NG", "原件标题精确比较", title["evidence"])
    importance = field_value(binding.importance, tables, snapshot)
    text = "".join(map(str, importance["values"]))
    checked_marks = sum(text.count(token) for token in set(_IMPORTANCE_CHECKED_TOKENS))
    add("R2", "UNABLE" if importance["unknown"] else "PASS" if checked_marks == 1 else "NG", f"原件选中标记数={checked_marks}", importance["evidence"])
    for name in Footer.model_fields:
        field = field_value(getattr(binding.footer, name), tables, snapshot)
        add("R8", "UNABLE" if field["unknown"] else "PASS" if field["values"] else "NG", f"表尾字段 {name}", field["evidence"])

    dimension_count = nondimension_count = method_count = 0
    for region in binding.regions:
        table = tables[region.sheet]
        for rows in region.records:
            record = {"sheet": region.sheet, "rows": rows, "section": region.section}
            fields = {name: read_cells(table, rows, column_numbers(getattr(region.columns, name)), snapshot=snapshot) for name in Columns.model_fields}
            for name in region.unresolved_columns:
                fields[name]["unknown"].append("binding_unresolved")
            method, measured, result, standard = [fields[name] for name in ("method", "measurements", "result", "standard")]
            method_text = " ".join(map(str, method["values"]))
            evidence = [ref for field in fields.values() for ref in field["evidence"]]
            if method["unknown"]:
                add("R4", "UNABLE", "检查方法未解决", method["evidence"], record)
            elif method_text:
                method_count += 1
                tokens = re.split(r"[/,，;；\s]+", method_text.lower())
                rejected = {"/", "-", "—", "－", "na", "n/a", "none", "null", "／", "ok", "合格", "pass", "正确"}
                invalid = "/" in method_text or "或" in method_text or method_text.lower().strip() in rejected or all(re.fullmatch(r"-?\d+(?:\.\d+)?", token) for token in tokens)
                add("R4", "NG" if invalid else "PASS", "原件检查方法文字检查", method["evidence"], record)
            if region.section == "non_dimension":
                nondimension_count += 1
                status = "UNABLE" if measured["unknown"] else "PASS" if measured["values"] and all(is_strict_ok_text(value) for value in measured["values"]) else "NG"
                add("R6", status, "非尺寸仅接受 OK/合格", measured["evidence"], record)
            if region.section != "dimension":
                continue
            dimension_count += 1
            raw = tuple(measured["values"])
            kind, numeric_groups = inspect_measurements(raw)
            standard_text = " ".join(map(str, standard["values"]))
            result_text = " ".join(map(str, result["values"]))
            required = [measured, result, method] if kind == "strict_ok_values" else [measured, result, standard]
            if any(field["unknown"] for field in required):
                add("R3", "UNABLE", "数值或必要字段仍未知；图片不按原生空白处理", evidence, record)
            elif not raw:
                add("R3", "UNABLE", "尺寸记录没有可检查数据", evidence, record)
            else:
                # Native PDF text can put several complete sample groups on
                # one line. Wrapped cell text retains the existing policy's
                # within-value normalization (18.2\n9 means 18.29 there).
                single_line = not any(isinstance(value, str) and ("\n" in value or "\r" in value) for value in raw)
                tolerance_values = tuple(number for group in numeric_groups for number in group) if kind == "numeric_values" and single_line else raw
                tolerance = check_tolerance(standard_text, tolerance_values, result_text, method_text)
                add("R3", tolerance.status, tolerance.reason or "按现有尺寸政策计算", evidence, record)
            if measured["unknown"] or not raw:
                add("R5", "UNABLE", "实测值未解决", measured["evidence"], record)
                add("R7", "UNABLE", "定性触发条件未解决", evidence, record)
            else:
                duplicate = check_duplicates(raw)
                add("R5", duplicate.status, duplicate.reason or "按记录检查数值重复", measured["evidence"], record)
                qualitative = check_qualitative_method(raw, method_text)
                if method["unknown"] and qualitative.status != "NA":
                    add("R7", "UNABLE", "定性检查方法未解决", evidence, record)
                else:
                    add("R7", qualitative.status, qualitative.reason or "按已确认的定性触发条件检查", evidence, record)

    if not dimension_count:
        for rule in ("R3", "R5", "R7"):
            add(rule, "UNABLE", "未建立尺寸源记录集合", [])
    if not nondimension_count:
        add("R6", "NG" if binding.scope_resolved else "UNABLE", "未发现非尺寸记录", [])
    if not method_count and not observations["R4"]:
        add("R4", "NG" if binding.scope_resolved else "UNABLE", "未发现检查方法数据", [])
    if not binding.scope_resolved:
        for rule in ("R3", "R4", "R5", "R6", "R7"):
            add(rule, "UNABLE", "源记录范围未解决", [])
    checks = []
    for rule, rule_observations in observations.items():
        status = "NG" if any(row["status"] == "NG" for row in rule_observations) else "UNABLE" if not rule_observations or any(row["status"] == "UNABLE" for row in rule_observations) else "PASS"
        checks.append({"rule_id": rule, "status": status, "unknown": sum(row["status"] == "UNABLE" for row in rule_observations), "observations": rule_observations})
    return {
        "document_sha256": snapshot["source"]["sha256"],
        "rules_sha256": rules_sha256,
        "binding": binding.model_dump(),
        "checks": checks,
        "semantic_binding": "unverified",
        "evidence_policy": "native_cell_values; image_only_unresolved; visual_legibility_unverified",
        "record_coverage": "model_declared" if binding.scope_resolved else "unresolved",
        "training_eligible": False,
    }


def compact_observation(snapshot: dict) -> str:
    """All native content, no sample-specific cropping or duplicate cell wrappers."""
    lines = ["Source SHA256: " + snapshot["source"]["sha256"]]
    for table in snapshot["tables"]:
        lines.append(json.dumps({"sheet": table["id"], "rows": table["rows"], "columns": table["cols"], "merges": table.get("merges", [])}, ensure_ascii=False, separators=(",", ":")))
        for row in range(1, table["rows"] + 1):
            cells = []
            for column in range(1, table["cols"] + 1):
                address = ref(row, column)
                cell = table["cells"].get(address)
                if cell:
                    cells.append(address + "=" + json.dumps(cell.get("value"), ensure_ascii=False, separators=(",", ":")) + (" [" + cell["state"] + "]" if cell["state"] != "known" else ""))
            if cells:
                lines.append("\t".join(cells))
    lines.append("Image anchors (pixels not yet observed): " + json.dumps(snapshot.get("drawing_anchors", []), ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines)
