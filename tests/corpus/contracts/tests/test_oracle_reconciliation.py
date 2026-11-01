from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from corpus_contracts.v2_assets_shared import (
    build_cases_from_long_feedback,
)

_LONG_FEEDBACK_HEADERS = [
    "文件类型",
    "修复状态",
    "回归测试结果",
    "问题大类",
    "问题描述",
    "解决方案",
    "规则编号",
    "规则",
    "人工结果",
    "人工依据",
    "AI结果",
    "AI依据",
    "文件",
    "文件格式",
    "测试链接",
    "修复测试链接",
]


def _write_long_feedback(path: Path, files: list[tuple[str, str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_LONG_FEEDBACK_HEADERS)
        writer.writeheader()
        for file_name, file_format, file_type in files:
            writer.writerow(
                {
                    "文件类型": file_type,
                    "规则编号": "1",
                    "规则": "title",
                    "人工结果": "PASS",
                    "人工依据": "manual",
                    "文件": file_name,
                    "文件格式": file_format,
                }
            )


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.write_text(json.dumps({"rows": rows}), encoding="utf-8")


def test_required_v2_entry_does_not_skip_missing_oracle_assets() -> None:
    import corpus_contracts.v2_assets_shared as v2_assets_shared

    source = Path(v2_assets_shared.__file__).read_text()
    assert "def reconcile_v2_oracle_assets" in source
    assert "V2 oracle assets missing" in source
    assert "long-feedback files not found" not in source
    assert "Skipped" not in source


def test_partial_missing_v2_oracle_asset_fails(tmp_path: Path) -> None:
    present = tmp_path / "1-present.xlsx"
    present.write_bytes(b"present")
    csv_path = tmp_path / "oracle.csv"
    _write_long_feedback(
        csv_path,
        [
            ("1-present.xlsx", "Excel", "1"),
            ("1-missing.xlsx", "Excel", "1"),
        ],
    )

    with pytest.raises(AssertionError, match=r"V2 oracle assets missing") as exc_info:
        build_cases_from_long_feedback(
            dataset_root=tmp_path,
            expected_csv=csv_path,
            ftype_filter="excel",
        )
    message = str(exc_info.value)
    assert "file=1-missing.xlsx" in message
    assert "file_type=excel" in message
    assert "1-present.xlsx" not in message.split("V2 oracle assets missing", 1)[1]


def test_all_missing_v2_oracle_assets_fail_closed(tmp_path: Path) -> None:
    csv_path = tmp_path / "oracle.csv"
    _write_long_feedback(
        csv_path,
        [
            ("1-missing-a.xlsx", "Excel", "1"),
            ("1-missing-b.xlsx", "Excel", "1"),
        ],
    )
    with pytest.raises(AssertionError, match=r"V2 oracle assets missing") as exc_info:
        build_cases_from_long_feedback(
            dataset_root=tmp_path,
            expected_csv=csv_path,
            ftype_filter="excel",
        )
    message = str(exc_info.value)
    assert "file=1-missing-a.xlsx" in message
    assert "file=1-missing-b.xlsx" in message
    assert "file_type=excel" in message


def test_ambiguous_v2_oracle_asset_fails(tmp_path: Path) -> None:
    first = tmp_path / "first.xlsx"
    second = tmp_path / "second.xlsx"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    csv_path = tmp_path / "oracle.csv"
    _write_long_feedback(csv_path, [("1-sample.xlsx", "Excel", "1")])
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "file_name": "1-sample.xlsx",
                "file_format": "Excel",
                "local_path": str(first),
            },
            {
                "file_name": "1-sample.xlsx",
                "file_format": "Excel",
                "local_path": str(second),
            },
        ],
    )

    with pytest.raises(AssertionError, match=r"V2 oracle assets ambiguous") as exc_info:
        build_cases_from_long_feedback(
            dataset_root=tmp_path,
            expected_csv=csv_path,
            ftype_filter="excel",
            manifest_path=manifest,
        )
    message = str(exc_info.value)
    assert "file=1-sample.xlsx" in message
    assert "file_type=excel" in message


def test_manifest_fallback_still_resolves_existing_dataset_name(tmp_path: Path) -> None:
    asset = tmp_path / "1-sample.xlsx"
    asset.write_bytes(b"ok")
    csv_path = tmp_path / "oracle.csv"
    _write_long_feedback(csv_path, [("1-sample.xlsx", "Excel", "1")])
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "file_name": "1-sample.xlsx",
                "file_format": "Excel",
                "local_path": str(tmp_path / "missing-machine-path.xlsx"),
            }
        ],
    )

    cases = build_cases_from_long_feedback(
        dataset_root=tmp_path,
        expected_csv=csv_path,
        ftype_filter="excel",
        manifest_path=manifest,
    )
    assert [case.file_name for case in cases] == ["1-sample.xlsx"]
    assert cases[0].file_path == asset.resolve()


@pytest.mark.parametrize("local_present", [True, False])
def test_manifest_cannot_read_another_workspaces_asset(tmp_path: Path, local_present: bool) -> None:
    dataset = tmp_path / "selected-dataset"
    dataset.mkdir()
    local = dataset / "1-sample.xlsx"
    foreign = tmp_path / "other-workspace.xlsx"
    foreign.write_bytes(b"foreign corpus data")
    if local_present:
        local.write_bytes(b"selected corpus data")
    oracle = tmp_path / "oracle.csv"
    _write_long_feedback(oracle, [("1-sample.xlsx", "Excel", "1")])
    manifest = dataset / "manifest.json"
    _write_manifest(manifest, [{"file_name": "1-sample.xlsx", "file_format": "Excel", "local_path": str(foreign)}])

    if local_present:
        cases = build_cases_from_long_feedback(dataset_root=dataset, expected_csv=oracle, ftype_filter="excel", manifest_path=manifest)
        assert cases[0].file_path == local.resolve()
    else:
        with pytest.raises(AssertionError, match="V2 oracle assets missing"):
            build_cases_from_long_feedback(dataset_root=dataset, expected_csv=oracle, ftype_filter="excel", manifest_path=manifest)


def test_complete_v2_oracle_assets_build_cases(tmp_path: Path) -> None:
    first = tmp_path / "1-first.xlsx"
    second = tmp_path / "1-second.xlsx"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    csv_path = tmp_path / "oracle.csv"
    _write_long_feedback(
        csv_path,
        [
            ("1-first.xlsx", "Excel", "1"),
            ("1-second.xlsx", "Excel", "1"),
        ],
    )
    cases = build_cases_from_long_feedback(
        dataset_root=tmp_path,
        expected_csv=csv_path,
        ftype_filter="excel",
    )
    assert [case.file_name for case in cases] == ["1-first.xlsx", "1-second.xlsx"]
