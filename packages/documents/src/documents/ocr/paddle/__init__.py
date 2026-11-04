"""Standalone PP-StructureV3 OCR evidence evaluation. Never on the default PDF route."""

from .client import PaddleOCRFormatError, PaddleStructureService
from .evidence import build_evidence
from .export import PaddleOCRStats, evaluate_pdf, export_paddle_result

__all__ = ["PaddleOCRFormatError", "PaddleStructureService", "PaddleOCRStats", "build_evidence", "evaluate_pdf", "export_paddle_result"]
