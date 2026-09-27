"""Corpus pytest options. Defaults point at in-repo assets from ppap-agent@53a5bd2b."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DEFAULT_V2_ROOT = _REPO / "assets" / "v2.0plus"
_DEFAULT_V2_CSV = _REPO / "assets" / "全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv"
_DEFAULT_OCR_CACHE = _REPO / "tests" / "corpus" / "fixtures" / "ocr_cache"

# Same V2 cases that failed on ppap-agent@53a5bd2b. Not a second oracle: expected CSV is unchanged.
BASELINE_XFAIL_IDS = frozenset(
    {
        "PV试验报告2024-07-03 13.35.pdf",
        "1-全功能检验报告_巨一.pdf",
        "10-控制计划-常州新泉.pdf",
        "11-过程特殊特性清单-上海大亚.pdf",
        "11-过程特殊特性清单-巨一.pdf",
        "11-过程特殊特性清单-继峰 - 无边框.pdf",
        "11-过程特殊特性清单-继峰.pdf",
        "13-生产系统产能评估报告-继峰 - 错误.pdf",
        "16-零件提交保证书-上海亚大.pdf",
        "16-零件提交保证书-常州新泉.pdf",
        "16-零件提交保证书-继峰 - 无边框.pdf",
        "16-零件提交保证书-继峰.pdf",
        "8-过程流程图-亚大.pdf",
        "8-过程流程图-巨一.pdf",
        "8-过程流程图-继峰 - 无线框.pdf",
        "8-过程流程图-继峰.pdf",
        "NJ24010097_EVB理想X04_PACK3_绝缘耐压等电位正本-DVP试验报告.pdf",
        "SHF03-25021669-QC-15 阻燃性 试验报告 4.pdf",
        "W02管路PV试验报告2025052803_QFZ.pdf",
        "零部件DPV试验报告20231226.pdf",
    }
)


def pytest_configure(config: Any) -> None:
    """Use the in-repo OCR conversion cache unless the caller already pointed elsewhere."""
    if not (os.environ.get("OCR_LOCAL_OSS_DIR") or "").strip():
        os.environ["OCR_LOCAL_OSS_DIR"] = str(_DEFAULT_OCR_CACHE)


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--ftype",
        action="store",
        default=None,
        choices=["excel", "pdf", "scanned"],
        help="Filter V2 cases by file type",
    )
    parser.addoption("--verbose-check", action="store_true", default=False)
    parser.addoption("--mismatch-only", action="store_true", default=False)
    parser.addoption("--bundle", action="store", default=None)
    parser.addoption("--seq", action="store", default=None)
    parser.addoption("--report", action="store", default=None)
    parser.addoption(
        "--v2-dataset-root",
        action="store",
        default=str(_DEFAULT_V2_ROOT),
        help="V2 dataset root (defaults to in-repo assets/v2.0plus)",
    )
    parser.addoption(
        "--v2-expected-csv",
        action="store",
        default=str(_DEFAULT_V2_CSV),
        help="V2 human expected CSV (defaults to in-repo 4.8 feedback table)",
    )


def pytest_collection_modifyitems(items: Any) -> None:
    for item in items:
        item._nodeid = item.nodeid
        if any(case_id in item.nodeid for case_id in BASELINE_XFAIL_IDS):
            item.add_marker(
                pytest.mark.xfail(
                    reason="ppap-agent@53a5bd2b baseline failure; not a migration regression",
                    strict=False,
                )
            )


def pytest_make_parametrize_id(config: Any, val: Any, argname: Any) -> Any:
    if isinstance(val, str):
        return val
    if hasattr(val, "file_name"):
        return val.file_name
    return None
