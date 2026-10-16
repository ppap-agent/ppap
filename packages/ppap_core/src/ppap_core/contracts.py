"""Shared contracts for cross-package orchestration."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ppap_core.types import PpapDocumentType


class FileRef(BaseModel):
    """Unified file reference for local path or remote URL."""

    location: str

    @property
    def is_remote(self) -> bool:
        return self.location.startswith(("http://", "https://"))


class OcrArtifact(BaseModel):
    """OCR extraction artifact."""

    text: str = ""
    excel_path: Optional[str] = None
    html_path: Optional[str] = None
    provider: str = "unknown"
    raw_meta: Dict[str, Any] = Field(default_factory=dict)


class ConversionArtifact(BaseModel):
    """PDF -> Excel conversion artifact."""

    success: bool
    xlsx_path: Optional[str] = None
    strategy_used: str = "unknown"
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    temp_files: List[str] = Field(default_factory=list)


class RuleInput(BaseModel):
    """Engine-side rule input without ORM dependency."""

    rule_id: str
    rule_profile: str = "v2"
    type: Optional[str] = None
    rules: List[Dict[str, Any]] = Field(default_factory=list)
    customers: List[str] = Field(default_factory=list)
    models: List[str] = Field(default_factory=list)
    parts: List[str] = Field(default_factory=list)


class RuleItem(BaseModel):
    """Single parsed rule item used by checker implementations."""

    id: str
    category: str
    priority: Optional[str] = None
    rule_to_check: str
    type_of_rule: str


class AuditInput(BaseModel):
    """Engine-side audit input without ORM dependency."""

    audit_id: str
    file_url: str
    customer: Optional[str] = None
    model: Optional[str] = None
    part: Optional[str] = None
    content: Optional[str] = None
    file_metadata: Dict[str, Any] = Field(default_factory=dict)
    project_id: str = "default"


class GroupContextInput(BaseModel):
    """Engine-side group context."""

    task_group_id: Optional[str] = None
    by_doc_type: Dict[str, List[AuditInput]] = Field(default_factory=dict)
    by_audit_id: Dict[str, AuditInput] = Field(default_factory=dict)
    rules_by_id: Dict[str, RuleInput] = Field(default_factory=dict)
    # Frozen input ID -> document type; None means the failed input type is unknown.
    unavailable_files: Dict[str, Optional[str]] = Field(default_factory=dict)

    @staticmethod
    def _doc_type_key(doc_type: PpapDocumentType | str) -> str:
        if isinstance(doc_type, PpapDocumentType):
            return doc_type.get_type_name()
        return str(doc_type)

    def get_files_by_type(self, doc_type: PpapDocumentType | str) -> List[AuditInput]:
        key = self._doc_type_key(doc_type)
        if any(kind is None or kind == key for kind in self.unavailable_files.values()):
            raise OSError("本轮冻结关联资料无法读取，不能完成本项核对。")
        return self.by_doc_type.get(key, [])

    def has_doc_type(self, doc_type: PpapDocumentType | str) -> bool:
        return bool(self.get_files_by_type(doc_type))


class EvidenceDiagnostic(BaseModel):
    """Stable, searchable explanation of an evidence limitation or failure."""

    code: str
    stage: Literal["conversion", "structure", "visual", "evaluation"]
    severity: Literal["info", "warning", "error"]
    message: str


class EvidenceSource(BaseModel):
    """Sanitized location of evidence used for one audit decision."""

    kind: Literal["structure", "visual"]
    locator: str


class AuditEvidence(BaseModel):
    """Per-result evidence provenance exposed by the audit interface."""

    schema_version: Literal["1"] = "1"
    required: Literal["structure", "visual", "structure_or_visual"]
    used: Literal["none", "structure", "visual", "both"]
    source_format: Literal["xlsx", "xls", "xlsm", "pdf", "docx", "doc"]
    prepared_format: Literal["xlsx", "xls", "xlsm", "pdf", "docx"]
    conversion_strategies: tuple[str, ...] = ()
    sources: tuple[EvidenceSource, ...] = ()
    diagnostics: tuple[EvidenceDiagnostic, ...] = ()


class AuditResultItem(BaseModel):
    """Engine-side audit output item."""

    classification: str
    item: str
    priority: str
    status: str
    suggestions: str
    evidence: Optional[AuditEvidence] = None
