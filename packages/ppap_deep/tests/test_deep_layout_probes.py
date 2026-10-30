"""Rule-directed navigation remains separate from complete audit evidence."""

from copy import deepcopy

import pytest
from openpyxl import Workbook
from ppap_deep.semantic.audit_tools import prepare_audit
from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.evidence import EvidenceBudget, EvidenceLimitError
from ppap_deep.semantic.guest import layout_probes
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.trace import derive_report


def rule_plan(rule_id, field):
    return RulePlan.model_validate(
        {
            "source_sha256": "a" * 64,
            "plan_revision": "b" * 64,
            "ontology": {"entities": [{"id": "records", "grain": "one inspection record", "attributes": {field: "string"}, "identity": [field]}]},
            "rules": [
                {
                    "id": rule_id,
                    "source_locator": {"kind": "published_clause", "clause_id": rule_id},
                    "program": {"kind": "records", "source": "records", "quantifier": "forall", "require": {"kind": "value_test", "field": field, "op": "not_empty"}},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "No empty-record policy is specified.",
                }
            ],
        }
    )


def test_layout_probes_keep_rule_requests_and_find_actual_headers_outside_top_left(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Inspection"
    sheet["A1"] = "检验清单"
    sheet["M17"] = "验收\n频次"
    sheet.merge_cells("M17:O17")
    sheet["M18"] = "=1+1"
    supplement = workbook.create_sheet("Supplement")
    supplement["Q25"] = "验收频次"
    path = tmp_path / "inspection.xlsx"
    workbook.save(path)
    observed = snapshot(path)
    other = Workbook()
    other.active["N9"] = "零件编号"
    other_path = tmp_path / "reference.xlsx"
    other.save(other_path)
    documents = {"inspection-file": observed, "reference-file": snapshot(other_path)}
    original = deepcopy(documents)
    plans = {"target-a": rule_plan("frequency", "验收频次"), "target-b": rule_plan("identity", "零件编号")}
    _trace, obligations = prepare_audit(plans)

    navigation = layout_probes(documents, obligations)

    assert navigation["requests"] == [
        {"audit_id": "target-a", "rule_id": "frequency", "input": "records", "fields": ["验收频次"]},
        {"audit_id": "target-b", "rule_id": "identity", "input": "records", "fields": ["零件编号"]},
    ]
    tables = navigation["documents"]["inspection-file"]["tables"]
    assert [table["id"] for table in tables] == ["Inspection", "Supplement"]
    assert tables[0]["text_matches"] == [{"attribute": "验收频次", "cells": ["M17"], "total": 1}]
    assert tables[1]["text_matches"] == [{"attribute": "验收频次", "cells": ["Q25"], "total": 1}]
    first = tables[0]["windows"][0]
    assert first["cells"]["M17"]["value"] == "验收\n频次"
    assert [17, 13, 17, 15] in first["merges"]
    assert first["cells"]["M18"]["state"] == "unknown_formula_cache"
    assert first["cells"]["M18"]["formula"] == "=1+1"
    reference = navigation["documents"]["reference-file"]["tables"][0]
    assert reference["text_matches"] == [{"attribute": "零件编号", "cells": ["N9"], "total": 1}]
    assert documents == original


def test_no_exact_attribute_hit_keeps_structure_and_does_not_decide_absence(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "审核记录"
    sheet["N9"] = "作业单元编号"
    sheet["N10"] = "010"
    path = tmp_path / "aliases.xlsx"
    workbook.save(path)
    documents = {"target": snapshot(path)}
    plans = {"target": rule_plan("identity", "工序编号")}
    initial, obligations = prepare_audit(plans)

    navigation = layout_probes(documents, obligations)

    table = navigation["documents"]["target"]["tables"][0]
    assert table["text_matches"] == []
    assert table["windows"] == []
    assert table["probe"]["cells"]["N9"]["value"] == "作业单元编号"
    assert navigation["requests"][0]["fields"] == ["工序编号"]
    assert initial.uses[0].inputs["records"].kind == "unresolved"
    assert derive_report(initial, documents, plans).checks[0].status == "UNABLE"


def test_offset_alias_region_is_visible_without_claiming_a_field_mapping(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "审核记录"
    sheet["Q25"] = "作业单元编号"
    sheet["Q26"] = "010"
    path = tmp_path / "offset-alias.xlsx"
    workbook.save(path)
    documents = {"target": snapshot(path)}
    original = deepcopy(documents)
    plans = {"target": rule_plan("identity", "工序编号")}
    initial, obligations = prepare_audit(plans)

    navigation = layout_probes(documents, obligations)

    table = navigation["documents"]["target"]["tables"][0]
    assert table["text_matches"] == []
    assert any(window["cells"].get("Q25", {}).get("value") == "作业单元编号" for window in table["windows"])
    assert any(window["cells"].get("Q26", {}).get("value") == "010" for window in table["windows"])
    assert initial.uses[0].inputs["records"].kind == "unresolved"
    assert derive_report(initial, documents, plans).checks[0].status == "UNABLE"
    assert documents == original


def test_structural_region_previews_are_bounded_and_keep_unshown_locations(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "审核记录"
    for index in range(12):
        sheet.cell(25 + index * 4, 17, "=1+1" if index == 0 else f"区段 {index}")
        sheet.cell(26 + index * 4, 17, "010")
    path = tmp_path / "separate-regions.xlsx"
    workbook.save(path)
    _initial, obligations = prepare_audit({"target": rule_plan("identity", "工序编号")})

    navigation = layout_probes({"target": snapshot(path)}, obligations)

    table = navigation["documents"]["target"]["tables"][0]
    assert table["text_matches"] == []
    assert table["structure_starts"] == [f"Q{row}" for row in (25, 29, 33, 37, 41, 45, 49, 53)]
    assert table["structure_start_total"] == 12
    assert table["unshown_structure_bounds"] == "Q57:Q69"
    assert len(table["windows"]) == 8
    assert table["windows"][0]["cells"]["Q25"]["state"] == "unknown_formula_cache"
    assert table["windows"][0]["cells"]["Q25"]["formula"] == "=1+1"


@pytest.mark.parametrize("known_field", [False, True])
def test_dense_offset_header_band_shows_a_distant_alias_without_using_its_meaning(tmp_path, known_field):
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "审核记录"
    for column in range(1, 18):
        sheet.cell(25, column, "作业单元编号" if column == 17 else f"字段 {column}")
        sheet.cell(26, column, "010")
    path = tmp_path / "offset-dense-header.xlsx"
    workbook.save(path)
    compiled = rule_plan("identity", "工序编号")
    if known_field:
        raw = compiled.model_dump(mode="json")
        raw["ontology"]["entities"][0]["attributes"]["字段 1"] = "string"
        raw["rules"][0]["program"]["require"] = {
            "kind": "all",
            "items": [
                {"kind": "value_test", "field": "字段 1", "op": "not_empty"},
                {"kind": "value_test", "field": "工序编号", "op": "not_empty"},
            ],
        }
        compiled = RulePlan.model_validate(raw)
    plans = {"target": compiled}
    initial, obligations = prepare_audit(plans)
    documents = {"target": snapshot(path)}

    navigation = layout_probes(documents, obligations)

    table = navigation["documents"]["target"]["tables"][0]
    assert [match["attribute"] for match in table["text_matches"]] == (["字段 1"] if known_field else [])
    assert table["structure_starts"] == ["A25"]
    assert any(window["cells"].get("Q25", {}).get("value") == "作业单元编号" for window in table["windows"])
    assert any(window["cells"].get("Q26", {}).get("value") == "010" for window in table["windows"])
    assert derive_report(initial, documents, plans).checks[0].status == "UNABLE"


def test_very_wide_offset_header_band_exposes_its_unshown_columns(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "审核记录"
    for column in range(1, 41):
        sheet.cell(25, column, f"字段 {column}")
        sheet.cell(26, column, "010")
    path = tmp_path / "offset-wide-header.xlsx"
    workbook.save(path)
    _initial, obligations = prepare_audit({"target": rule_plan("identity", "工序编号")})

    navigation = layout_probes({"target": snapshot(path)}, obligations)

    table = navigation["documents"]["target"]["tables"][0]
    assert table["structure_starts"] == ["A25"]
    assert len(table["windows"][0]["selection"]["columns"]) == 32
    assert table["unshown_structure_columns"] == [{"start": "A25", "rows": [24, 25, 26], "columns": [33, 40], "count": 8}]
    assert table["windows"][0]["cells"]["AF25"]["value"] == "字段 32"
    assert "AN25" not in table["windows"][0]["cells"]


def test_document_judge_search_fields_retain_their_own_target_and_rule():
    raw = rule_plan("approval", "id").model_dump(mode="json")
    raw["rules"][0]["program"] = {"kind": "document", "require": {"kind": "judge", "question": "核对批准日期", "fields": ["批准日期"]}}
    _initial, obligations = prepare_audit({"document-target": RulePlan.model_validate(raw)})

    navigation = layout_probes({}, obligations)

    assert navigation["requests"] == [{"audit_id": "document-target", "rule_id": "approval", "input": None, "fields": ["批准日期"]}]
    assert navigation["documents"] == {}


def test_sparse_wide_merged_header_keeps_anchor_and_geometry_without_expanding_span(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "审核记录"
    sheet["N9"] = "作业单元编号"
    sheet.merge_cells("N9:XFD9")
    path = tmp_path / "wide-header.xlsx"
    workbook.save(path)
    _initial, obligations = prepare_audit({"target": rule_plan("identity", "工序编号")})

    navigation = layout_probes({"target": snapshot(path)}, obligations)

    probe = navigation["documents"]["target"]["tables"][0]["probe"]
    assert probe["selection"]["columns"] == [1, 2, 3, 4, 5, 6, 7, 8, 14]
    assert probe["cells"]["N9"]["value"] == "作业单元编号"
    assert probe["merges"] == [[9, 14, 9, 16384]]


def test_adjacent_rule_attribute_neighborhoods_share_their_overlapping_window(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet["M17"] = "验收频次"
    sheet["N18"] = "检验方法"
    path = tmp_path / "neighboring-headers.xlsx"
    workbook.save(path)
    raw = rule_plan("requirements", "验收频次").model_dump(mode="json")
    raw["ontology"]["entities"][0]["attributes"]["检验方法"] = "string"
    raw["rules"][0]["program"]["require"] = {
        "kind": "all",
        "items": [
            {"kind": "value_test", "field": "验收频次", "op": "not_empty"},
            {"kind": "value_test", "field": "检验方法", "op": "not_empty"},
        ],
    }
    _initial, obligations = prepare_audit({"target": RulePlan.model_validate(raw)})

    navigation = layout_probes({"target": snapshot(path)}, obligations)

    windows = navigation["documents"]["target"]["tables"][0]["windows"]
    assert len(windows) == 1
    assert windows[0]["cells"]["M17"]["value"] == "验收频次"
    assert windows[0]["cells"]["N18"]["value"] == "检验方法"


def test_probe_resource_exhaustion_is_explicit_instead_of_claiming_no_header(tmp_path):
    workbook = Workbook()
    workbook.active["A1"] = "验收频次"
    path = tmp_path / "budget.xlsx"
    workbook.save(path)
    _initial, obligations = prepare_audit({"target": rule_plan("frequency", "验收频次")})

    with pytest.raises(EvidenceLimitError, match="budget exceeded"):
        layout_probes({"target": snapshot(path)}, obligations, budget=EvidenceBudget(remaining=0))
