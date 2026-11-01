"""Quality gate between legacy format conversion and DOCX business agents."""

from __future__ import annotations

from documents.word import WordDocument, WordTableBlock, extract_docx_structure, word_document_to_workbook

from audit.engine.checkers.docx_workbook_agent import (
    EQUIPMENT_DOCX_PRIMARY_TABLE,
    PSW_DOCX_PRIMARY_TABLE,
    DocxPrimaryTableContract,
    label_docx_primary_table,
)
from audit.engine.rule_engine import GuardStop
from audit.engine.rule_engine.registry import get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType


class DocxAuditQualityError(ValueError):
    """Converted DOCX is structurally readable but unsafe for the selected audit."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


_PRIMARY_TABLE_CONTRACTS: dict[PpapDocumentType, DocxPrimaryTableContract] = {
    PpapDocumentType.EQUIPMENT_LIST: EQUIPMENT_DOCX_PRIMARY_TABLE,
    PpapDocumentType.PSW: PSW_DOCX_PRIMARY_TABLE,
}


def validate_converted_docx_audit_quality(
    file_content: bytes,
    doc_type: PpapDocumentType,
) -> WordDocument:
    """Require structure and document-type evidence before normal DOCX routing."""
    document = extract_docx_structure(file_content)
    tables = [block for block in document.blocks if isinstance(block, WordTableBlock)]
    if not document.valid or not tables or not all(table.valid for table in tables):
        raise DocxAuditQualityError(
            "invalid_structure",
            "转换后的 DOCX 表格结构无效或缺失，已停止审核。",
        )

    contract = _PRIMARY_TABLE_CONTRACTS.get(doc_type)
    if contract is None:
        raise DocxAuditQualityError(
            "unsupported_doc_type",
            f"旧 DOC 当前仅支持设备清单和零件提交保证书，不支持 {doc_type.get_type_name()}。",
        )

    workbook = word_document_to_workbook(document)
    try:
        label_docx_primary_table(
            workbook,
            contract,
            get_doc_resource_by_doc_type(doc_type),
        )
    except GuardStop as stop:
        if stop.status == "ERR":
            raise
        raise DocxAuditQualityError(
            "insufficient_key_evidence",
            f"转换后的 DOCX 对 {doc_type.get_type_name()} 的关键证据不足，已停止审核：{stop.message}",
        ) from stop
    finally:
        workbook.close()
    return document
