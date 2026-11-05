"""Physical file references and conversion observations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FileRef(BaseModel):
    """Unified file reference for a local path or a remote URL."""

    location: str

    @property
    def is_remote(self) -> bool:
        return self.location.startswith(("http://", "https://"))


class ConversionArtifact(BaseModel):
    """PDF to Excel conversion artifact."""

    success: bool
    xlsx_path: Optional[str] = None
    strategy_used: str = "unknown"
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    temp_files: List[str] = Field(default_factory=list)
