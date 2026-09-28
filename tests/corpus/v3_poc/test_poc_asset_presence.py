from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tests.corpus.v3_poc.poc_oracle import (
    DEFAULT_POC_DATASET_ROOT,
    DEFAULT_POC_EXPECTED_XLSX,
    PocCase,
    load_poc_cases,
)


def test_required_v3_entry_does_not_skip_missing_assets() -> None:
    source = Path(__file__).with_name("test_poc_assets_grouped.py").read_text()
    assert "pytest.skip" not in source
    assert "allow_module_level" not in source


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
