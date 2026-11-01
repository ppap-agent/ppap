"""The reader must fit the frozen index supplied to the real audit stage."""

import json
from copy import deepcopy

import pytest
from audit.document_tools import main


def snapshot(identity):
    return {
        "source": {"file": identity + ".xlsx", "sha256": identity, "format": ".xlsx"},
        "tables": [
            {
                "id": "Shared name",
                "rows": 6,
                "cols": 3,
                "merges": [[1, 1, 2, 2], [5, 1, 6, 2]],
                "cells": {"A1": {"value": identity + " header", "state": "known"}, "A5": {"value": "outside selection", "state": "known"}},
            }
        ],
    }


def invoke(tmp_path, monkeypatch, value, *arguments):
    path = tmp_path / "documents.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr("sys.argv", ["document_tools", "view", str(path), *arguments])
    return main()


def test_indexed_view_selects_exact_document_and_keeps_merge_anchor_context(tmp_path, monkeypatch, capsys):
    assert invoke(tmp_path, monkeypatch, {"alpha": snapshot("alpha"), "beta": snapshot("beta")}, "--document", "beta", "--table", "Shared name", "--rows", "2", "--columns", "A:B") == 0
    output = capsys.readouterr().out
    metadata, table, *records = [json.loads(line) for line in output.splitlines()]
    assert metadata["document_id"] == "beta" and metadata["source"]["sha256"] == "beta"
    assert metadata["next_offset"] is None
    assert table["selection"] == {"rows": [2], "columns": [1, 2]}
    assert records == [
        {"kind": "merge", "table": "Shared name", "range": [1, 1, 2, 2]},
        {"kind": "cell", "table": "Shared name", "cell": "A1", "value": "beta header", "state": "known"},
    ]
    assert output.count("beta header") == 1
    assert "alpha header" not in output and "outside selection" not in output


@pytest.mark.parametrize("arguments", [(), ("--document", "absent")])
def test_indexed_view_needs_an_existing_document_identity(tmp_path, monkeypatch, capsys, arguments):
    with pytest.raises(ValueError):
        invoke(tmp_path, monkeypatch, {"alpha": snapshot("alpha")}, *arguments)
    assert capsys.readouterr().out == ""


def test_single_snapshot_view_still_works(tmp_path, monkeypatch, capsys):
    assert invoke(tmp_path, monkeypatch, snapshot("alpha"), "--table", "Shared name", "--rows", "1", "--columns", "A") == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert next(row for row in rows if row.get("kind") == "cell")["value"] == "alpha header"


def test_pdf_table_selection_reads_cells_instead_of_ignoring_the_table_option(tmp_path, monkeypatch, capsys):
    document = snapshot("pdf")
    document["source"]["format"] = ".pdf"
    document["pages"] = [{"page": 1, "blocks": [{"text": "whole page outside requested table view"}]}]
    assert invoke(tmp_path, monkeypatch, document, "--table", "Shared name", "--rows", "2", "--columns", "A") == 0
    output = capsys.readouterr().out
    rows = [json.loads(line) for line in output.splitlines()]
    assert rows[1]["selection"]["rows"] == [2]
    assert any(row.get("cell") == "A1" and row["value"] == "pdf header" for row in rows)
    assert "whole page outside" not in output


def test_compact_view_retains_types_unknown_formula_and_visual_source_without_changing_snapshot(tmp_path, monkeypatch, capsys):
    document = snapshot("alpha")
    document["tables"][0]["cells"].update(
        {
            "A3": {"value": False, "state": "known"},
            "B3": {"value": 0, "state": "known"},
            "C3": {"value": None, "state": "unknown_formula_cache", "formula": "=SUM(A1:A2)", "source": {"page": 2, "bbox": [1, 2, 3, 4]}, "visual_overlap": True},
        }
    )
    before = deepcopy(document)
    assert invoke(tmp_path, monkeypatch, document, "--table", "Shared name", "--rows", "3") == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    cells = {row["cell"]: row for row in lines if row.get("kind") == "cell"}
    assert type(cells["A3"]["value"]) is bool and cells["A3"]["value"] is False
    assert type(cells["B3"]["value"]) is int and cells["B3"]["value"] == 0
    assert cells["C3"] == {"kind": "cell", "table": "Shared name", "cell": "C3", **document["tables"][0]["cells"]["C3"]}
    assert document == before


def test_nonrectangular_word_aliases_keep_their_selected_positions_and_outside_anchor(tmp_path, monkeypatch, capsys):
    document = snapshot("word")
    document["source"]["format"] = ".docx"
    table = document["tables"][0]
    table.update(
        merges=[],
        cells={
            "A1": {"value": "shared text", "state": "known", "anchor": "A1"},
            "B2": {"value": "shared text", "state": "known", "anchor": "A1"},
            "C3": {"value": "shared text", "state": "known", "anchor": "A1"},
        },
    )
    assert invoke(tmp_path, monkeypatch, document, "--rows", "2:3", "--columns", "B:C") == 0
    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines()][1:]
    assert [record for record in records if record["kind"] == "alias"] == [
        {"kind": "alias", "table": "Shared name", "cell": "B2", "anchor": "A1"},
        {"kind": "alias", "table": "Shared name", "cell": "C3", "anchor": "A1"},
    ]
    assert [record for record in records if record["kind"] == "cell"] == [{"kind": "cell", "table": "Shared name", "cell": "A1", "value": "shared text", "state": "known"}]
    assert output.count("shared text") == 1


def test_alias_specific_metadata_is_not_silently_replaced_by_anchor_metadata(tmp_path, monkeypatch, capsys):
    document = snapshot("word")
    table = document["tables"][0]
    table.update(
        merges=[],
        cells={
            "A1": {"value": "anchor", "state": "known", "anchor": "A1"},
            "B2": {"value": None, "state": "unknown_visual_evidence", "anchor": "A1", "source": {"page": 1, "bbox": [1, 2, 3, 4]}},
        },
    )
    assert invoke(tmp_path, monkeypatch, document, "--rows", "2", "--columns", "B") == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()][1:]
    cells = {record["cell"]: record for record in records if record["kind"] == "cell"}
    assert cells["A1"]["value"] == "anchor" and cells["A1"]["state"] == "known"
    assert cells["B2"] == {"kind": "cell", "table": "Shared name", "cell": "B2", **table["cells"]["B2"]}
    assert not any(record["kind"] == "alias" for record in records)


@pytest.mark.parametrize("anchor_value,alias_value", [(0, False), (False, 0), (1, True), (1, 1.0)])
def test_alias_deduplication_does_not_merge_distinct_native_scalar_types(tmp_path, monkeypatch, capsys, anchor_value, alias_value):
    document = snapshot("word")
    table = document["tables"][0]
    table.update(
        merges=[],
        cells={
            "A1": {"value": anchor_value, "state": "known", "anchor": "A1"},
            "B2": {"value": alias_value, "state": "known", "anchor": "A1"},
        },
    )
    assert invoke(tmp_path, monkeypatch, document, "--rows", "2", "--columns", "B") == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    cells = {record["cell"]: record for record in records if record.get("kind") == "cell"}
    assert type(cells["A1"]["value"]) is type(anchor_value)
    assert type(cells["B2"]["value"]) is type(alias_value)
    assert cells["B2"]["anchor"] == "A1"


def test_real_workbook_view_keeps_merge_and_unknown_formula_observation(tmp_path, monkeypatch, capsys):
    from audit.document_tools import snapshot as read_snapshot
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Actual"
    sheet.merge_cells("C1:D2")
    sheet["C1"] = "特性类别"
    sheet["C3"] = "=1+1"
    sheet["D3"] = False
    path = tmp_path / "original.xlsx"
    workbook.save(path)
    workbook.close()
    document = read_snapshot(path)
    assert invoke(tmp_path, monkeypatch, document, "--table", "Actual", "--rows", "2:3", "--columns", "C:D") == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()][1:]
    assert {"kind": "merge", "table": "Actual", "range": [1, 3, 2, 4]} in records
    cells = {record["cell"]: record for record in records if record["kind"] == "cell"}
    assert cells["C1"]["value"] == "特性类别"
    assert cells["C3"]["state"] == "unknown_formula_cache" and cells["C3"]["formula"] == "=1+1"
    assert cells["D3"]["value"] is False


def test_empty_table_has_an_explicit_empty_selection(tmp_path, monkeypatch, capsys):
    document = snapshot("empty")
    document["tables"] = [{"id": "Empty", "rows": 0, "cols": 0, "merges": [], "cells": {}}]
    assert invoke(tmp_path, monkeypatch, document) == 0
    header, table = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert header["next_offset"] is None
    assert table["selection"] == {"rows": [], "columns": []}
    assert table["default_cell"] == {"value": None, "state": "known"}


def test_view_paginates_by_utf8_bytes_and_recovers_large_cells_and_multiple_tables(tmp_path, monkeypatch, capsys):
    document = snapshot("alpha")
    document["tables"][0]["cells"]["C3"] = {"value": "长文😀\\\n" * 1500, "state": "known", "formula": '="large"'}
    document["tables"].append({"id": "Other", "rows": 1, "cols": 1, "merges": [], "cells": {"A1": {"value": "last table", "state": "known"}}})
    offset, records, fragments, pages = 0, [], {}, 0
    while True:
        assert invoke(tmp_path, monkeypatch, document, "--rows", "1:3", "--max-bytes", "4096", "--offset", str(offset)) == 0
        output = capsys.readouterr().out
        assert len(output.encode("utf-8")) <= 4096
        header, *page_records = [json.loads(line) for line in output.splitlines()]
        assert header["offset"] == offset
        assert page_records
        for record in page_records:
            if record["kind"] == "json_fragment":
                fragments.setdefault(record["record_id"], []).append(record)
            else:
                records.append(record)
        pages += 1
        if header["next_offset"] is None:
            break
        assert header["next_offset"] > offset
        offset = header["next_offset"]
        assert pages < 100
    assert pages > 1
    for parts in fragments.values():
        assert [part["part"] for part in parts] == list(range(parts[0]["parts"]))
        records.append(json.loads("".join(part["text"] for part in parts)))
    assert next(row for row in records if row.get("cell") == "C3")["value"] == document["tables"][0]["cells"]["C3"]["value"]
    assert next(row for row in records if row.get("cell") == "C3")["formula"] == '="large"'
    assert next(row for row in records if row.get("table") == "Other" and row.get("kind") == "cell")["value"] == "last table"


def test_pdf_page_view_pagination_restores_the_complete_page_object(tmp_path, monkeypatch, capsys):
    document = snapshot("pdf")
    document["source"]["format"] = ".pdf"
    document["pages"] = [{"page": 1, "width": 500, "height": 600, "blocks": [{"text": "正文" * 5000, "bbox": [0, 0, 20, 30]}], "images": [{"bbox": [1, 2, 3, 4]}]}]
    fragments, offset = [], 0
    while True:
        assert invoke(tmp_path, monkeypatch, document, "--pages", "1", "--max-bytes", "4096", "--offset", str(offset)) == 0
        output = capsys.readouterr().out
        assert len(output.encode()) <= 4096
        header, *items = [json.loads(line) for line in output.splitlines()]
        fragments.extend(items)
        if header["next_offset"] is None:
            break
        assert header["next_offset"] > offset
        offset = header["next_offset"]
    restored = json.loads("".join(item["text"] for item in fragments))
    assert restored == {"kind": "page", **document["pages"][0]}


@pytest.mark.parametrize("arguments", [("--offset", "-1"), ("--max-bytes", "1024"), ("--offset", "999")])
def test_invalid_view_page_request_fails_without_partial_stdout(tmp_path, monkeypatch, capsys, arguments):
    with pytest.raises(ValueError):
        invoke(tmp_path, monkeypatch, snapshot("alpha"), *arguments)
    assert capsys.readouterr().out == ""
