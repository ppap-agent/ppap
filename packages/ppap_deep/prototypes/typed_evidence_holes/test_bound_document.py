"""Host computation must not turn missing source evidence into a pass."""

import importlib.util
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("research_bound_document", Path(__file__).with_name("bound_document.py"))
assert SPEC is not None and SPEC.loader is not None
bound = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bound
SPEC.loader.exec_module(bound)


def source_and_binding():
    def field(address):
        return {"state": "bound", "locations": [{"sheet": "Report", "range": address}], "reason": ""}

    values = {
        "A1": "全功能检验报告",
        "B1": "☒",
        "A2": "车",
        "B2": "零件",
        "C2": "P1",
        "D2": "供应商",
        "E2": "责任人",
        "F2": "2026-10-28",
        "A4": "10±0.5",
        "B4": 9.5,
        "C4": 10.5,
        "D4": "OK",
        "E4": "卡尺",
        "B6": "OK",
        "C6": "合格",
        "D6": "OK",
        "E6": "目视",
    }
    snapshot = {
        "source": {"sha256": "a" * 64},
        "tables": [{"id": "Report", "rows": 8, "cols": 6, "merges": [], "cells": {address: {"value": value, "state": "known"} for address, value in values.items()}}],
    }
    columns = {"standard": "A", "measurements": "B:C", "result": "D", "method": "E"}
    binding = bound.DocumentBinding.model_validate(
        {
            "title": field("A1"),
            "importance": field("B1"),
            "footer": {name: field(f"{column}2") for name, column in zip(bound.Footer.model_fields, "ABCDEF", strict=True)},
            "regions": [
                {"sheet": "Report", "section": section, "header_rows": [3], "records": [[row]], "columns": columns, "unresolved_columns": []}
                for section, row in [("dimension", 4), ("non_dimension", 6)]
            ],
            "scope_resolved": True,
            "limitations": [],
        }
    )
    return snapshot, binding


def evaluate(snapshot, binding):
    result = bound.evaluate(binding, snapshot, rules_sha256="b" * 64)
    return result, {item["rule_id"]: item["status"] for item in result["checks"]}


def test_valid_native_records_compute_all_eight_rules_without_certifying_model_coverage():
    snapshot, binding = source_and_binding()
    result, statuses = evaluate(snapshot, binding)
    assert set(statuses.values()) == {"PASS"}
    assert result["semantic_binding"] == "unverified"
    assert result["record_coverage"] == "model_declared"
    assert result["training_eligible"] is False


@pytest.mark.parametrize("mode", ["blank", "missing_column", "unknown_formula"])
def test_empty_or_unknown_measurements_cannot_pass_any_dimension_check(mode):
    snapshot, binding = source_and_binding()
    cells = snapshot["tables"][0]["cells"]
    cells.pop("B4")
    cells.pop("C4")
    if mode == "missing_column":
        binding.regions[0].columns.measurements = None
    elif mode == "unknown_formula":
        cells["B4"] = {"value": None, "state": "unknown_formula_cache", "formula": "=1+1"}
    _, statuses = evaluate(snapshot, binding)
    assert {statuses[rule] for rule in ("R3", "R5", "R7")} == {"UNABLE"}


@pytest.mark.parametrize(("address", "rule"), [("A1", "R1"), ("B1", "R2"), ("F2", "R8"), ("A4", "R3"), ("B4", "R5"), ("E4", "R4"), ("B6", "R6")])
def test_native_cell_policy_keeps_known_values_when_a_logo_or_stamp_overlaps(address, rule):
    snapshot, binding = source_and_binding()
    a, b, _, _ = bound.range_boundaries(address)
    snapshot["drawing_anchors"] = [{"sheet": "Report", "from": {"row": b, "column": a}, "to": {"row": b, "column": a}, "media_paths": ["xl/media/image1.png"]}]
    _, statuses = evaluate(snapshot, binding)
    assert statuses[rule] == "PASS"


def test_absence_claim_cannot_erase_source_value_and_duplicate_locations_do_not_count_twice():
    snapshot, binding = source_and_binding()
    binding.title.state, binding.title.reason = "absent", "Expected area inspected"
    binding.importance.locations.append(binding.importance.locations[0])
    _, statuses = evaluate(snapshot, binding)
    assert statuses["R1"] == "UNABLE"
    assert statuses["R2"] == "PASS"


def test_blank_absence_is_ng_and_unresolved_scope_blocks_record_passes():
    snapshot, binding = source_and_binding()
    snapshot["tables"][0]["cells"].pop("F2")
    binding.footer.date.state, binding.footer.date.reason = "absent", "Expected area empty"
    binding.scope_resolved = False
    _, statuses = evaluate(snapshot, binding)
    assert statuses["R8"] == "NG"
    assert {statuses[f"R{i}"] for i in range(3, 8)} == {"UNABLE"}


@pytest.mark.parametrize("kind", ["document_fields", "column_roles", "split_record"])
def test_merged_source_cannot_be_reused_as_distinct_required_evidence(kind):
    snapshot, binding = source_and_binding()
    if kind == "document_fields":
        snapshot["tables"][0]["merges"] = [[2, 1, 2, 2]]
    elif kind == "column_roles":
        snapshot["tables"][0]["merges"] = [[4, 1, 4, 2]]
    else:
        snapshot["tables"][0]["merges"] = [[4, 2, 5, 2]]
        binding.regions[0].records.append([5])
    with pytest.raises(ValueError, match="share"):
        evaluate(snapshot, binding)


def test_multirow_measurement_merge_is_read_once_and_repeat_headers_are_allowed():
    snapshot, binding = source_and_binding()
    snapshot["tables"][0]["merges"] = [[4, 2, 5, 2]]
    binding.regions[0].records = [[4, 5]]
    binding.regions[0].header_rows = [3, 7]
    _, statuses = evaluate(snapshot, binding)
    assert statuses["R5"] == "PASS"
    observed = bound.read_cells(snapshot["tables"][0], [4, 5], [2])
    assert observed["values"] == [9.5]
    assert observed["evidence"] == [{"sheet": "Report", "range": "B4"}]


def test_image_extent_missing_is_unknown_instead_of_a_zero_size_drawing():
    snapshot, binding = source_and_binding()
    snapshot["tables"][0]["cells"].pop("B4")
    snapshot["drawing_anchors"] = [{"sheet": "Report", "from": {"row": 8, "column": 6}, "to": None, "media_paths": ["xl/media/image1.png"]}]
    _, statuses = evaluate(snapshot, binding)
    assert {statuses[rule] for rule in ("R3", "R5", "R7")} == {"UNABLE"}
    assert statuses["R1"] == "PASS"


@pytest.mark.parametrize(("mode", "expected"), [("exact", "PASS"), ("wrong", "NG"), ("blank", "UNABLE"), ("formula", "UNABLE"), ("unresolved", "UNABLE")])
def test_title_native_authority_does_not_claim_visual_understanding(mode, expected):
    snapshot, binding = source_and_binding()
    snapshot["drawing_anchors"] = [{"sheet": "Report", "from": {"row": 1, "column": 1}, "to": {"row": 1, "column": 1}}]
    if mode == "wrong":
        snapshot["tables"][0]["cells"]["A1"]["value"] = "尺寸检验报告"
    elif mode == "blank":
        snapshot["tables"][0]["cells"].pop("A1")
    elif mode == "formula":
        snapshot["tables"][0]["cells"]["A1"]["state"] = "unknown_formula_cache"
    elif mode == "unresolved":
        binding.title.state, binding.title.reason = "unresolved", "Title identity is uncertain"
    result, statuses = evaluate(snapshot, binding)
    assert statuses["R1"] == expected
    assert "visual_legibility_unverified" in result["evidence_policy"]


def test_one_native_sample_does_not_resolve_another_image_only_sample():
    snapshot, binding = source_and_binding()
    snapshot["tables"][0]["cells"].pop("C4")
    snapshot["drawing_anchors"] = [{"sheet": "Report", "from": {"row": 4, "column": 3}, "to": {"row": 4, "column": 3}}]
    _, statuses = evaluate(snapshot, binding)
    assert {statuses[rule] for rule in ("R3", "R5", "R7")} == {"UNABLE"}


def test_pdf_visual_overlap_retains_known_native_text_but_not_parser_failures():
    snapshot, binding = source_and_binding()
    cell = snapshot["tables"][0]["cells"]["A1"]
    cell["state"] = "unknown_visual_evidence"
    _, statuses = evaluate(snapshot, binding)
    assert statuses["R1"] == "PASS"
    cell["state"] = "unknown_uncovered_grid"
    _, statuses = evaluate(snapshot, binding)
    assert statuses["R1"] == "UNABLE"


def test_continuation_without_headers_keeps_body_records_and_rejects_fake_headers():
    snapshot, binding = source_and_binding()
    raw = binding.model_dump()
    raw["regions"][0]["header_rows"] = []
    continuation = bound.DocumentBinding.model_validate(raw)
    _, statuses = evaluate(snapshot, continuation)
    assert statuses["R3"] == "PASS"
    continuation.regions[0].header_rows = [4]
    with pytest.raises(ValueError, match="overlapping body"):
        evaluate(snapshot, continuation)


@pytest.mark.parametrize(("last", "expected"), [("10.5", "PASS"), ("10.6", "NG")])
def test_numeric_groups_in_one_native_cell_keep_sample_boundaries(last, expected):
    snapshot, binding = source_and_binding()
    snapshot["tables"][0]["cells"]["B4"]["value"] = f"9.5/9.6 10.4/{last}"
    snapshot["tables"][0]["cells"].pop("C4")
    _, statuses = evaluate(snapshot, binding)
    assert statuses["R3"] == expected


def test_wrapped_numeric_cell_keeps_existing_within_value_normalization():
    snapshot, binding = source_and_binding()
    cells = snapshot["tables"][0]["cells"]
    cells["A4"]["value"] = "3XΦ18（0.5，0）"
    cells["B4"]["value"] = "18.22/18.21/18.2\n9"
    cells["C4"]["value"] = "18.27/18.26/18.3\n1"
    _, statuses = evaluate(snapshot, binding)
    assert statuses["R3"] == "PASS"
