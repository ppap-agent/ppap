"""The temporary scan exclusion names exactly two immutable source assets."""

import shutil

import pytest
from corpus_contracts.poc_oracle import REPO_ROOT, deferred_full_scan_reason, load_poc_cases


@pytest.mark.parametrize(
    "relative",
    [
        "assets/poc测试/POC测试样本/Blanc Spoiler-有特殊特性清单/19-BLANC尾-生产控制计划.pdf",
        "assets/v2.0plus/fallback/PV试验报告2024-07-03 13.35.pdf",
    ],
)
def test_only_frozen_full_scan_sources_are_deferred(relative, tmp_path):
    source = REPO_ROOT / relative
    assert "Issue #246" in deferred_full_scan_reason(source)
    copied = tmp_path / source.name
    shutil.copyfile(source, copied)
    assert deferred_full_scan_reason(copied) is not None
    copied.write_bytes(copied.read_bytes() + b"\n")
    assert deferred_full_scan_reason(copied) is None


def test_native_poc_cases_are_retained_and_missing_unknown_files_are_not_silently_skipped(tmp_path):
    cases = load_poc_cases()
    skipped = [case for case in cases if deferred_full_scan_reason(case.file_path)]
    assert [case.file_name for case in skipped] == ["19-BLANC尾-生产控制计划.pdf"]
    assert len(cases) - len(skipped) == 26
    assert deferred_full_scan_reason(tmp_path / "original-psw.pdf") is None
    assert deferred_full_scan_reason(tmp_path / "native-29-page-PFMEA.pdf") is None
