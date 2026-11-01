"""PDF grids keep source geometry, typed evidence and complete declared scopes."""

from copy import deepcopy

import pytest
from audit.checker_tools import CheckRequest, check_fields
from audit.document_tools import ref, snapshot
from audit.evidence import EvidenceBudget, cell_locator, resolve
from audit.models import PdfCell, RecordsProgram, RulePlan
from audit.trace import AuditTrace, Binding, derive_report, evaluate_audit, validate_trace
from audit.validation import validate_report, validate_snapshot


@pytest.fixture
def source():
    tables = []
    for page in (1, 2):
        cells = {}
        for row in range(1, 4):
            for column in (1, 2):
                cells[ref(row, column)] = {
                    "value": "Frequency" if row == 1 else "daily",
                    "state": "known",
                    "source": {"page": page, "bbox": [10 + (column - 1) * 100, 10 + (row - 1) * 30, 10 + column * 100, 10 + row * 30]},
                }
        tables.append({"id": f"page-{page}", "page": page, "rows": 3, "cols": 2, "cells": cells, "merges": [], "coordinate_system": "pdf_points_top_left"})
    return {
        "source": {"file": "control.pdf", "sha256": "f" * 64, "format": ".pdf"},
        "pages": [{"page": page, "width": 300, "height": 200, "rotation": 0, "crop_origin": [0, 0], "text": "", "blocks": [], "image_count": 0} for page in (1, 2)],
        "tables": tables,
        "coverage": {"page_count": 2, "grid_pages": [1, 2], "unresolved_pages": [], "unresolved_reasons": {}},
    }


def binding(source, page=1, **changes):
    return Binding.model_validate(
        {
            "id": f"body-{page}",
            "document_id": "target",
            "source_sha256": source["source"]["sha256"],
            "table": f"page-{page}",
            "header_rows": [1],
            "first_row": 2,
            "last_row": 3,
            "columns": {"frequency": "A"},
            "mapping_basis": "The frequency column is identified by the header.",
            "boundary_basis": "Rows 2 and 3 are the complete declared page body.",
            **changes,
        }
    )


def program(*, require=None, require_field=False):
    return RecordsProgram(
        kind="records",
        source="records",
        quantifier="forall",
        require=require or {"kind": "value_test", "field": "frequency", "op": "not_empty"},
        precondition={"kind": "column_exists", "input": "records", "field": "frequency"} if require_field else None,
    )


def plan(selected=None):
    return RulePlan.model_validate(
        {
            "source_sha256": "a" * 64,
            "plan_revision": "b" * 64,
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": (selected or program()).model_dump(),
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "原文没有空范围例外。",
                }
            ],
        }
    )


def query_trace(bounds, **scope_changes):
    return AuditTrace.model_validate(
        {
            "bindings": [bound.model_dump() for bound in bounds],
            "uses": [{"audit_id": "target", "rule_id": "R1", "inputs": {"records": {"kind": "bound", "scopes": [{"binding_id": bound.id, **scope_changes} for bound in bounds]}}}],
        }
    )


def counts(selected, bound, source, budget=None, **scope_changes):
    result = evaluate_audit(query_trace([bound], **scope_changes), {"target": source}, {"target": plan(selected)}, budget=budget)
    check = result.report.checks[0]
    return check.status, check.total, check.checked, check.unknown


def derive(source, trace, selected=None):
    plans, documents = {"target": selected or plan()}, {"target": source}
    report = derive_report(trace, documents, plans)
    validate_report(report, plans, documents)
    validate_trace(trace, report, documents, plans)
    return report.checks[0]


def claim(row=3, page=1):
    return {"row": row, "reason": "This row may be a page footer.", "evidence_locator": {"kind": "pdf_cell", "page": page, "row": row, "column": 1}}


@pytest.mark.parametrize("changes", [{"page": 0}, {"row": True}, {"column": "1"}, {"table": "page-1"}])
def test_pdf_cell_has_one_strict_page_row_column_identity(changes):
    with pytest.raises(ValueError):
        PdfCell.model_validate({"kind": "pdf_cell", "page": 1, "row": 2, "column": 1, **changes})


def test_valid_cell_and_merged_anchor_keep_pdf_points(source):
    table = source["tables"][0]
    table["merges"] = [[2, 1, 2, 2]]
    del table["cells"]["B2"]
    table["cells"]["A2"]["source"]["bbox"] = [10, 40, 210, 70]
    validate_snapshot(source, source["source"]["sha256"], ".pdf")
    locator = cell_locator(".pdf", "page-1", 2, 2)
    assert locator == PdfCell(kind="pdf_cell", page=1, row=2, column=2)
    assert resolve(source, locator).values == ("daily",)
    assert resolve(source, locator).known
    assert table["cells"]["A2"]["source"]["bbox"] == [10, 40, 210, 70]


@pytest.mark.parametrize("invalid", ["format", "page", "table_page", "row", "column", "source_page", "missing", "false_anchor", "missing_anchor", "overlapping_merges", "rotation", "crop"])
def test_pdf_citation_requires_the_actual_table_cell_and_anchor(source, invalid):
    locator = PdfCell(kind="pdf_cell", page=1, row=2, column=1)
    table = source["tables"][0]
    if invalid == "format":
        source["source"]["format"] = ".xlsx"
    elif invalid == "page":
        locator.page = 3
    elif invalid == "table_page":
        table["page"] = 2
    elif invalid == "row":
        locator.row = 4
    elif invalid == "column":
        locator.column = 3
    elif invalid == "source_page":
        table["cells"]["A2"]["source"]["page"] = 2
    elif invalid == "missing":
        del table["cells"]["A2"]
    elif invalid == "false_anchor":
        table["cells"]["A2"]["anchor"] = "B2"
    elif invalid == "missing_anchor":
        table["merges"] = [[2, 1, 2, 2]]
        del table["cells"]["A2"]
        locator.column = 2
    elif invalid == "overlapping_merges":
        table["merges"] = [[2, 1, 2, 2], [2, 1, 3, 1]]
    elif invalid == "rotation":
        source["pages"][0]["rotation"] = 90
    else:
        source["pages"][0]["crop_origin"] = [10, 0]
    with pytest.raises(ValueError):
        resolve(source, locator)


@pytest.mark.parametrize("bbox", [[-1, 40, 110, 70], [10, 40, 301, 70], [10, 40, 110, 201], [110, 40, 10, 70], [10, 40, float("nan"), 70], [10, 40, float("inf"), 70], [10, 40, 110]])
def test_unusable_pdf_geometry_is_never_a_known_query_fact(source, bbox):
    source["tables"][0]["cells"]["A2"]["source"]["bbox"] = bbox
    with pytest.raises(ValueError, match="geometry"):
        resolve(source, PdfCell(kind="pdf_cell", page=1, row=2, column=1))
    with pytest.raises(ValueError, match="geometry"):
        counts(program(), binding(source), source, EvidenceBudget())


@pytest.mark.parametrize("state,diagnostic", [("unknown_visual_evidence", "visual_only_evidence"), ("unknown_uncovered_grid", "unsupported_structure"), ("unknown_geometry", "unsupported_structure")])
def test_pdf_unknowns_survive_query_and_local_checker(source, state, diagnostic):
    item = source["tables"][0]["cells"]["A2"]
    item.update(value=None, state=state)
    if state == "unknown_geometry":
        item["source"]["bbox"][0] = -10
        with pytest.raises(ValueError, match="geometry"):
            resolve(source, PdfCell(kind="pdf_cell", page=1, row=2, column=1))
    else:
        assert not resolve(source, PdfCell(kind="pdf_cell", page=1, row=2, column=1)).known
    check = derive(source, query_trace([binding(source)]))
    assert (check.status, check.total, check.checked, check.unknown, check.diagnostic) == ("UNABLE", 2, 1, 1, diagnostic)
    assert check.evidence[0].locator == PdfCell(kind="pdf_cell", page=1, row=2, column=1)
    request = CheckRequest.model_validate({"binding": binding(source).model_dump(), "checks": [{"id": "local", "kind": "field_nonempty", "field": "frequency"}]})
    local = check_fields(source, request)
    assert (local.checks[0].local_condition_status, local.checks[0].unknown_rows) == ("UNABLE", [2])
    assert local.checks[0].unknown_examples[0].citation.locator == check.evidence[0].locator
    assert local.limitation == "local_condition_only; mapping_and_rule_applicability_not_verified"


def test_all_pdf_pages_replay_and_known_ng_survives_an_unknown_page_row(source):
    bounds = [binding(source), binding(source, page=2, header_rows=[])]
    assert derive(source, query_trace(bounds)).status == "PASS"
    source["tables"][0]["cells"]["A2"]["value"] = ""
    source["tables"][1]["cells"]["A3"].update(value=None, state="unknown_uncovered_grid")
    check = derive(source, query_trace(bounds))
    assert (check.status, check.total, check.checked, check.unknown) == ("NG", 4, 3, 1)
    citation = check.evidence[0]
    assert citation.locator == PdfCell(kind="pdf_cell", page=1, row=2, column=1)
    assert citation.locator.page != 2


def test_native_pdf_number_text_is_not_silently_coerced(source):
    source["tables"][0]["cells"]["A2"]["value"] = "6"
    source["tables"][0]["cells"]["A3"]["value"] = "7"
    request = CheckRequest.model_validate({"binding": binding(source).model_dump(), "checks": [{"id": "numeric", "kind": "numeric_at_least", "field": "frequency", "minimum": 5}]})
    result = check_fields(source, request).checks[0]
    assert (result.local_condition_status, result.checked, result.unknown) == ("UNABLE", 0, 2)


def test_pdf_missing_field_needs_actual_readable_headers(source):
    bound = binding(source, columns={}, field_holes={"frequency": {"state": "absent", "reason": "Every declared header column was inspected."}})
    selected = plan(program(require_field=True))
    check = derive(source, query_trace([bound]), selected)
    assert check.status == "NG"
    assert all(isinstance(item.locator, PdfCell) and item.locator.row == 1 for item in check.evidence)
    source["tables"][0]["cells"]["B1"].update(value=None, state="unknown_visual_evidence")
    with pytest.raises(ValueError, match="readable searched headers"):
        derive(source, query_trace([bound]), selected)


@pytest.mark.parametrize("invalid", ["first_page", "excel", "word", "absent_field"])
def test_only_pdf_continuations_can_omit_headers_without_proving_absence(source, invalid):
    bound = binding(source, page=2, header_rows=[])
    if invalid == "first_page":
        bound.table = "page-1"
    elif invalid in {"excel", "word"}:
        source["source"]["format"] = ".xlsx" if invalid == "excel" else ".docx"
    else:
        bound = binding(source, page=2, header_rows=[], columns={}, field_holes={"frequency": {"state": "absent", "reason": "No header here."}})
    with pytest.raises(ValueError, match="header"):
        counts(program(require_field=True), bound, source, EvidenceBudget())


def test_pdf_scope_claims_keep_page_identity_and_require_known_exclusions(source):
    bound = binding(source)
    assert counts(program(), bound, source, unresolved_rows=[claim()]) == ("UNABLE", 2, 1, 1)
    bad = claim(page=2)
    with pytest.raises(ValueError, match="claimed record"):
        counts(program(), bound, source, unresolved_rows=[bad])
    excluded = {**claim(), "reason_type": "footer"}
    assert counts(program(), bound, source, excluded_rows=[excluded]) == ("PASS", 2, 2, 0)
    source["tables"][0]["cells"]["A3"].update(value=None, state="unknown_visual_evidence")
    with pytest.raises(ValueError, match="known source evidence"):
        counts(program(), bound, source, excluded_rows=[excluded])


def test_pdf_semantic_questions_preserve_every_declared_row(source):
    selected = plan(program(require={"kind": "judge", "question": "Does this frequency satisfy the rule?", "fields": ["frequency"]}))
    trace = query_trace([binding(source)])
    initial = evaluate_audit(trace, {"target": source}, {"target": selected})
    assert len(initial.needs) == 2
    raw = trace.model_dump(mode="json")
    raw["answers"] = [{"need_id": initial.needs[0].id, "value": "true", "reason": "Checked original PDF cell"}]
    check = derive(source, AuditTrace.model_validate(raw), selected)
    assert (check.status, check.total, check.unknown) == ("UNABLE", 2, 1)


@pytest.mark.parametrize("invalid", ["wrong_sha", "unauthorized", "overlapping"])
def test_pdf_addition_preserves_source_and_scope_guards(source, invalid):
    bounds = [binding(source)]
    if invalid == "wrong_sha":
        bounds[0].source_sha256 = "0" * 64
    elif invalid == "unauthorized":
        bounds[0].document_id = "another-upload"
    else:
        bounds.append(binding(source, id="duplicate-body"))
    with pytest.raises(ValueError):
        derive(source, query_trace(bounds))


def test_native_pdf_reader_to_binding_query_and_evidence(tmp_path):
    import fitz

    path = tmp_path / "native-grid.pdf"
    with fitz.open() as document:
        page = document.new_page(width=300, height=200)
        for x in (20, 140, 260):
            page.draw_line((x, 20), (x, 140))
        for y in (20, 60, 100, 140):
            page.draw_line((20, y), (260, y))
        for row, values in enumerate((("Frequency", "Method"), ("daily", "inspection"), ("", "inspection"))):
            for column, value in enumerate(values):
                page.insert_text((30 + column * 120, 45 + row * 40), value, fontsize=10)
        document.save(path)
    observed = snapshot(path)
    validate_snapshot(observed, observed["source"]["sha256"], ".pdf")
    bound = binding(observed)
    check = derive(observed, query_trace([bound]))
    assert (check.status, check.total, check.checked, check.unknown) == ("NG", 2, 2, 0)
    assert check.evidence[0].locator == PdfCell(kind="pdf_cell", page=1, row=3, column=1)
    assert resolve(observed, check.evidence[0].locator).values == ("",)
    request = CheckRequest.model_validate({"binding": bound.model_dump(), "checks": [{"id": "frequency", "kind": "field_nonempty", "field": "frequency"}]})
    assert check_fields(observed, request).checks[0].violation_rows == [3]
    forged = deepcopy(observed)
    forged["tables"][0]["cells"]["A3"]["source"]["bbox"][2] = 301
    with pytest.raises(ValueError, match="geometry"):
        derive(forged, query_trace([bound]))


@pytest.mark.parametrize("mark", [False, True])
@pytest.mark.parametrize("operation", ["empty", "not_empty"])
def test_original_pdf_vector_mark_cannot_be_replayed_as_a_known_blank(tmp_path, mark, operation):
    import fitz

    path = tmp_path / "marked-grid.pdf"
    with fitz.open() as document:
        page = document.new_page(width=280, height=120)
        for x in (20, 140, 260):
            page.draw_line((x, 20), (x, 100))
        for y in (20, 60, 100):
            page.draw_line((20, y), (260, y))
        page.insert_text((30, 45), "Frequency")
        page.insert_text((150, 45), "Method")
        page.insert_text((150, 85), "inspection")
        if mark:
            page.draw_circle((80, 80), 8, fill=(0, 0, 0))
        document.save(path)
    original = path.read_bytes()
    observed = snapshot(path)
    validate_snapshot(observed, observed["source"]["sha256"], ".pdf")
    selected = plan(program(require={"kind": "value_test", "field": "frequency", "op": operation}))
    check = derive(observed, query_trace([binding(observed, last_row=2, boundary_basis="Row 2 is the complete page body.")]), selected)
    status = "UNABLE" if mark else "PASS" if operation == "empty" else "NG"
    assert (check.status, check.total, check.checked, check.unknown) == (status, 1, int(not mark), int(mark))
    assert resolve(observed, PdfCell(kind="pdf_cell", page=1, row=2, column=1)).known == (not mark)
    assert path.read_bytes() == original
