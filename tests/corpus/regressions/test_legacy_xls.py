"""Legacy .xls workbooks stay loadable for the Excel audit route."""

from __future__ import annotations

from corpus_contracts.oracle import original
from documents.excel.loader import load_excel_workbook


def test_legacy_xls_workbook_remains_loadable() -> None:
    fixture = original("5-模具验收报告-上海亚大.xls")
    workbook = load_excel_workbook(str(fixture))
    try:
        assert workbook.sheetnames
    finally:
        workbook.close()
