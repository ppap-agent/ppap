"""Native conversion fallback using selected-page Paddle tables only."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from documents.contracts import OcrArtifact

from .paddle.client import validate_pdf
from .paddle.export import _workbook
from .paddle.reader import PaddleEvidenceUnavailable, read_pdf_page_evidence


def convert_local_pdf_to_excel(path: str, *, enable_cache: bool = True) -> OcrArtifact:
    """Do not OCR an unselected multipage document or reuse LLM cache data.

    Native conversion already ran before this fallback. For multipage sources,
    a rule must select a page through ``read_pdf_page_evidence`` instead.
    ``enable_cache`` remains accepted for existing conversion callers; task
    owners reuse their own page result, without a second shared cache.
    """
    temporary: str | None = None
    try:
        source, page_count = validate_pdf(path)
        if page_count != 1:
            raise PaddleEvidenceUnavailable("需先选择审核条款所需的原始页；未对整份文件执行OCR。", code="paddle_page_selection_required")
        evidence = read_pdf_page_evidence(source, 1)
        pages = evidence["pages"]
        tables = [table for page in pages for table in page["tables"]]
        if not tables or any(table.get("pdf_bbox") is None for table in tables):
            raise PaddleEvidenceUnavailable("Paddle未提供可定位的完整表格，不能确定表格内容。", code="paddle_table_unconfirmed")
        if any(item.get("element") == "table" for item in evidence.get("diagnostics", [])):
            raise PaddleEvidenceUnavailable("部分表格无法可靠读取，不能生成完整审核工作簿。", code="paddle_table_incomplete")
        workbook = _workbook(evidence)
        if workbook is None:
            raise PaddleEvidenceUnavailable("Paddle未提供可用表格。", code="paddle_table_unconfirmed")
        fd, temporary = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            workbook.save(temporary)
        finally:
            workbook.close()
        if any(item.get("element") == "table" for item in evidence.get("diagnostics", [])):
            raise PaddleEvidenceUnavailable("部分表格无法导出，不能生成完整审核工作簿。", code="paddle_table_incomplete")
        return OcrArtifact(
            provider="paddleocr_ppstructure_v3",
            excel_path=temporary,
            raw_meta={"source_sha256": evidence["source_sha256"], "source_pages": [1], "status": evidence["status"], "diagnostics": evidence["diagnostics"]},
        )
    except BaseException as exc:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, PaddleEvidenceUnavailable):
            code, message = exc.code, str(exc)
        else:
            code, message = "paddle_extraction_failed", f"Paddle页面读取未完成（{type(exc).__name__}）。"
        return OcrArtifact(provider="paddleocr_ppstructure_v3", raw_meta={"error_code": code, "error": message})
