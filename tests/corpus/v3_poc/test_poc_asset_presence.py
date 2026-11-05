from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest
from corpus_contracts.poc_oracle import (
    DEFAULT_POC_DATASET_ROOT,
    DEFAULT_POC_EXPECTED_XLSX,
    PocCase,
    deferred_full_scan_reason,
    load_poc_cases,
)


def test_required_v3_entry_does_not_skip_missing_assets() -> None:
    source = Path(__file__).with_name("test_poc_assets_grouped.py").read_text()
    tree = ast.parse(source)
    # Issue #246 permits only the content-frozen scan exclusion. Missing
    # datasets/oracles remain fatal in load_poc_cases before cases are run.
    skips = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "pytest" and node.func.attr == "skip"
    ]
    assert len(skips) == 1
    entry = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "test_poc_assets_grouped")
    permitted = ast.parse("reason = deferred_full_scan_reason(case.file_path)\nif reason:\n    pytest.skip(reason)").body
    assert [ast.dump(node) for node in entry.body[:2]] == [ast.dump(node) for node in permitted]
    assert skips[0] is entry.body[1].body[0].value
    assert "allow_module_level" not in source


@pytest.mark.parametrize("name", ["19-BLANC尾-生产控制计划.pdf", "PV试验报告2024-07-03 13.35.pdf"])
def test_deferred_scan_with_missing_asset_still_fails(tmp_path: Path, name: str) -> None:
    with pytest.raises(FileNotFoundError):
        deferred_full_scan_reason(tmp_path / name)


@pytest.mark.parametrize("name", ["19-BLANC尾-生产控制计划.pdf", "PV试验报告2024-07-03 13.35.pdf"])
def test_deferred_scan_name_without_frozen_content_is_not_exempt(tmp_path: Path, name: str) -> None:
    source = tmp_path / name
    source.write_bytes(b"different source content")
    assert deferred_full_scan_reason(source) is None


def test_missing_v3_dataset_fails(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="Required V3 dataset is missing"):
        load_poc_cases(dataset_root=tmp_path / "missing-dataset", expected_xlsx=tmp_path / "oracle.xlsx")


def test_missing_v3_expected_xlsx_fails(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    with pytest.raises(AssertionError, match="Required V3 expected XLSX is missing"):
        load_poc_cases(dataset_root=dataset, expected_xlsx=tmp_path / "missing.xlsx")


def test_v3_oracle_without_data_rows_fails(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    xlsx = tmp_path / "oracle.xlsx"
    pd.DataFrame([["文件名", "序号", "备注"]]).to_excel(xlsx, header=False, index=False)
    with pytest.raises(AssertionError, match="Expected xlsx has no data rows"):
        load_poc_cases(dataset_root=dataset, expected_xlsx=xlsx)


def test_empty_v3_oracle_fails(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    xlsx = tmp_path / "oracle.xlsx"
    # Keep three header rows plus a data row with no file name so pandas does
    # not drop the sheet, while parse still yields zero valid cases.
    rows = [
        ["文件名", "序号", "备注", "r1", "s1", "why1", "x", "y"],
        ["h", "h", "h", "h", "h", "h", "h", "h"],
        ["h", "h", "h", "h", "h", "h", "h", "h"],
        ["", "", "skip-me", "h", "h", "h", "h", "h"],
    ]
    pd.DataFrame(rows).to_excel(xlsx, header=False, index=False)
    with pytest.raises(AssertionError, match="No valid V3 cases"):
        load_poc_cases(dataset_root=dataset, expected_xlsx=xlsx)


def test_v3_file_without_rule_expectations_fails(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "zero-assert.xlsx").write_bytes(b"x")
    xlsx = tmp_path / "oracle.xlsx"
    width = 3 + 12 * 5
    rows = [
        ["文件名", "序号", "备注"] + [f"h{i}" for i in range(width - 3)],
        ["h"] * width,
        ["h"] * width,
        ["zero-assert.xlsx", "8", ""] + [""] * (width - 3),
    ]
    pd.DataFrame(rows).to_excel(xlsx, header=False, index=False)
    with pytest.raises(AssertionError, match=r"V3 oracle incomplete: file=zero-assert.xlsx seq=8 has 0 PASS/NG"):
        load_poc_cases(dataset_root=dataset, expected_xlsx=xlsx)


def test_poc_case_rejects_empty_expected(tmp_path: Path) -> None:
    path = tmp_path / "empty.xlsx"
    path.write_bytes(b"x")
    with pytest.raises(AssertionError, match=r"V3 oracle incomplete: file=empty.xlsx seq=8 has 0 PASS/NG"):
        PocCase(
            file_name="empty.xlsx",
            file_seq="8",
            file_path=path,
            bundle_id="bundle",
            expected={},
        )


def test_default_v3_assets_load_at_least_one_case() -> None:
    assert DEFAULT_POC_DATASET_ROOT.is_dir()
    assert DEFAULT_POC_EXPECTED_XLSX.is_file()
    assert load_poc_cases()
