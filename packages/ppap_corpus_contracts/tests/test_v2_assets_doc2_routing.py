from __future__ import annotations

from ppap_corpus_contracts.v2_assets_shared import (
    EXCEL_AGENT_MAP,
    PDF_AGENT_MAP,
    parse_doc_key,
)


def test_parse_doc_key_supports_doc2_minor_keys() -> None:
    assert parse_doc_key("2-1-计划型.xlsx") == "2-1"
    assert parse_doc_key("2-2-报告型.xlsx") == "2-2"
    assert parse_doc_key("2-试验计划.xlsx") == 2


def test_v2_assets_agent_maps_split_doc2_routing() -> None:
    assert EXCEL_AGENT_MAP["2-1"].__name__ == "DvpRExcelAgent"
    assert EXCEL_AGENT_MAP["2-2"].__name__ == "DpvrExcelAgent"
    assert PDF_AGENT_MAP["2-1"].__name__ == "DvpRPdfExcelDelegatorAgent"
    assert PDF_AGENT_MAP["2-2"].__name__ == "DpvrPdfExcelDelegatorAgent"
