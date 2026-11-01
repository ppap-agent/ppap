"""Legacy .xls workbooks stay loadable for the Excel audit route."""

from __future__ import annotations

from pathlib import Path

from documents.excel.loader import load_excel_workbook


def test_legacy_xls_workbook_remains_loadable() -> None:
    fixture = Path(__file__).resolve().parents[3] / "assets/v2.0plus/fallback/5-模具验收报告-上海亚大.xls"
    workbook = load_excel_workbook(str(fixture))
    try:
        assert workbook.sheetnames
    finally:
        workbook.close()
