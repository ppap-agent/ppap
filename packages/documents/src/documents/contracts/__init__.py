"""Neutral cross-module contracts for documents."""

from .geometry import LineLike
from .physical import ConversionArtifact, FileRef, OcrArtifact

__all__ = ["ConversionArtifact", "FileRef", "LineLike", "OcrArtifact"]
