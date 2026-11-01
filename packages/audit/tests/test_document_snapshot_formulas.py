"""Native XLSX formula descriptors remain readable without inventing a cache."""

import json
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from audit.document_tools import snapshot
from audit.models import RulePlan
from audit.trace import AuditTrace, Binding, derive_report
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula


def workbook(tmp_path, formula, cached):
    path = tmp_path / "formulas.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Process"
    sheet.append(["Calculated result"])
    sheet["A2"] = formula
    book.create_sheet("Reference")["A1"] = "Required source remains present"
    book.save(path)
    book.close()
    if cached is not None:
        # Excel stores cached results in <v>; openpyxl does not calculate them.
        # Build that real OOXML state, then exercise the normal XLSX reader.
        with ZipFile(path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        xml = ET.fromstring(entries["xl/worksheets/sheet1.xml"])
        cell = next(item for item in xml.iter(ns + "c") if item.get("r") == "A2")
        value = cell.find(ns + "v")
        if value is None:
            value = ET.SubElement(cell, ns + "v")
        value.text = str(cached)
        entries["xl/worksheets/sheet1.xml"] = ET.tostring(xml)
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
    return path


@pytest.mark.parametrize("kind", ["array", "data_table", "scalar"])
@pytest.mark.parametrize("cached", [None, 0, 7])
def test_formula_text_attributes_and_cache_survive_real_xlsx(tmp_path, kind, cached):
    formula = {
        "array": ArrayFormula(ref="A2:B3", text="=SUM(Reference!A2:A3)"),
        "data_table": DataTableFormula(ref="A2:B3", r1="B1", dtr=True),
        "scalar": "=1+1",
    }[kind]
    path = workbook(tmp_path, formula, cached)
    original = path.read_bytes()
    observed = snapshot(path)
    assert json.loads(json.dumps(observed, allow_nan=False)) == observed
    assert [table["id"] for table in observed["tables"]] == ["Process", "Reference"]
    cell = observed["tables"][0]["cells"]["A2"]
    assert cell["value"] == cached
    assert cell["state"] == ("unknown_formula_cache" if cached is None else "known")
    if kind == "array":
        assert cell["formula"] == "=SUM(Reference!A2:A3)"
        assert cell["formula_attributes"] == {"t": "array", "ref": "A2:B3"}
    elif kind == "data_table":
        assert cell["formula"] is None
        assert cell["formula_attributes"] == {"t": "dataTable", "ref": "A2:B3", "dtr": "1", "r1": "B1"}
    else:
        assert cell["formula"] == "=1+1"
        assert "formula_attributes" not in cell
    bound = Binding(
        id="process",
        document_id="target",
        source_sha256=observed["source"]["sha256"],
        table="Process",
        header_rows=[1],
        first_row=2,
        last_row=2,
        columns={"result": "A"},
        mapping_basis="Actual result header",
        boundary_basis="Only formula result record",
    )
    plan = RulePlan.model_validate(
        {
            "source_sha256": "rule",
            "plan_revision": "a" * 64,
            "rules": [
                {
                    "id": "R1",
                    "source_locator": {"kind": "published_clause", "clause_id": "R1"},
                    "program": {"kind": "records", "source": "records", "quantifier": "forall", "require": {"kind": "value_test", "field": "result", "op": "not_empty"}},
                    "empty_scope_status": "UNABLE",
                    "empty_scope_basis": "No empty exception",
                }
            ],
        }
    )
    trace = AuditTrace.model_validate(
        {"bindings": [bound.model_dump()], "uses": [{"audit_id": "target", "rule_id": "R1", "inputs": {"records": {"kind": "bound", "scopes": [{"binding_id": bound.id}]}}}]}
    )
    check = derive_report(trace, {"target": observed}, {"target": plan}).checks[0]
    assert (check.status, check.total, check.checked, check.unknown) == (("UNABLE", 1, 0, 1) if cached is None else ("PASS", 1, 1, 0))
    assert path.read_bytes() == original
