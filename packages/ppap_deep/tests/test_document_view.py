"""The reader must fit the frozen index supplied to the real audit stage."""

import json

import pytest
from ppap_deep.semantic.document_tools import main


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
    metadata, row = [json.loads(line) for line in output.splitlines()]
    assert metadata["document_id"] == "beta" and metadata["source"]["sha256"] == "beta"
    assert metadata["merges"] == [[1, 1, 2, 2]]
    assert set(row["cells"]) == {"A2", "B2"}
    assert row["cells"]["B2"]["value"] == "beta header"
    assert row["cells"]["B2"]["anchor"] == "A1"
    assert "alpha header" not in output and "outside selection" not in output


@pytest.mark.parametrize("arguments", [(), ("--document", "absent")])
def test_indexed_view_needs_an_existing_document_identity(tmp_path, monkeypatch, capsys, arguments):
    with pytest.raises(ValueError):
        invoke(tmp_path, monkeypatch, {"alpha": snapshot("alpha")}, *arguments)
    assert capsys.readouterr().out == ""


def test_single_snapshot_view_still_works(tmp_path, monkeypatch, capsys):
    assert invoke(tmp_path, monkeypatch, snapshot("alpha"), "--table", "Shared name", "--rows", "1", "--columns", "A") == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[1]["cells"]["A1"]["value"] == "alpha header"


def test_pdf_table_selection_reads_cells_instead_of_ignoring_the_table_option(tmp_path, monkeypatch, capsys):
    document = snapshot("pdf")
    document["source"]["format"] = ".pdf"
    document["pages"] = [{"page": 1, "blocks": [{"text": "whole page outside requested table view"}]}]
    assert invoke(tmp_path, monkeypatch, document, "--table", "Shared name", "--rows", "2", "--columns", "A") == 0
    output = capsys.readouterr().out
    rows = [json.loads(line) for line in output.splitlines()]
    assert rows[1]["row"] == 2 and rows[1]["cells"]["A2"]["anchor"] == "A1"
    assert "whole page outside" not in output
