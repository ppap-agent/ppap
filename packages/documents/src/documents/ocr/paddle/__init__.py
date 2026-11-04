"""Opt-in, isolated PaddleOCR document evidence extraction."""

from .client import PaddleOCRFormatError, PaddleStructureService
from .evidence import build_evidence
from .export import PaddleOCRStats, evaluate_pdf

__all__ = ["PaddleOCRFormatError", "PaddleStructureService", "PaddleOCRStats", "build_evidence", "evaluate_pdf"]
