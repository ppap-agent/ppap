"""Explicit table types retain DVP and DPVR routing without filename prefixes."""

import pytest
from audit.engine.routing import resolve_engine_agent_class
from audit.kinds import PpapDocumentType


@pytest.mark.parametrize(
    "doc_type,suffix,agent",
    [
        (PpapDocumentType.DVP_R, ".xlsx", "DvpRExcelAgent"),
        (PpapDocumentType.DPVR, ".xlsx", "DpvrExcelAgent"),
        (PpapDocumentType.DVP_R, ".pdf", "DvpRPdfExcelDelegatorAgent"),
        (PpapDocumentType.DPVR, ".pdf", "DpvrPdfExcelDelegatorAgent"),
    ],
)
def test_explicit_doc_type_keeps_distinct_route(doc_type, suffix, agent):
    assert resolve_engine_agent_class(doc_type, "unrelated" + suffix, "v2").__name__ == agent
