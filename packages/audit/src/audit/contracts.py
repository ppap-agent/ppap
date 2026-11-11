"""Shared contracts for cross-package orchestration."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_serializer

from audit.fact_questions import BooleanFactEvaluation, FactQuestion
from audit.kinds import PpapDocumentType


class RuleInput(BaseModel):
    """Engine-side rule input without ORM dependency."""

    rule_id: str
    rule_profile: str = "v2"
    preset_key: Optional[str] = None
    type: Optional[str] = None
    rules: List[Dict[str, Any]] = Field(default_factory=list)
    appendix: List[Dict[str, Any]] = Field(default_factory=list)
    customers: List[str] = Field(default_factory=list)
    models: List[str] = Field(default_factory=list)
    parts: List[str] = Field(default_factory=list)
    # Frozen confirmed formalizations. Absent on every historical rule input.
    formalizations: Optional[Dict[str, Any]] = None

    @model_serializer(mode="wrap")
    def _omit_unformalized(self, handler):
        data = handler(self)
        if isinstance(data, dict) and data.get("formalizations") is None:
            data.pop("formalizations", None)
        return data


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


class UnavailableSourceError(OSError):
    """A frozen related source is unavailable for this execution attempt."""


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
            raise UnavailableSourceError("本轮冻结关联资料无法读取，不能完成本项核对。")
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
    excerpt: str = Field(default="", max_length=500)


class AuditEvidence(BaseModel):
    """Per-result evidence provenance exposed by the audit interface."""

    schema_version: Literal["1"] = "1"
    required: Literal["structure", "visual", "structure_or_visual"]
    used: Literal["none", "structure", "visual", "both"]
    source_format: Literal["xlsx", "xls", "xlsm", "pdf", "docx", "doc", "png", "jpg", "jpeg", "webp", "txt", "md"]
    prepared_format: Literal["xlsx", "xls", "xlsm", "pdf", "docx", "png", "jpg", "jpeg", "webp", "txt", "md"]
    conversion_strategies: tuple[str, ...] = ()
    sources: tuple[EvidenceSource, ...] = ()
    diagnostics: tuple[EvidenceDiagnostic, ...] = ()


class SupplyNeed(BaseModel):
    """A related original this clause needs: absent, or present for another part."""

    kind: Literal["missing", "source_mismatch"]
    document: str = Field(min_length=1, max_length=200)
    part_number: Optional[str] = Field(default=None, max_length=200)
    found_part_number: Optional[str] = Field(default=None, max_length=200)
    found_file_version_id: Optional[str] = Field(default=None, max_length=200)


class AuditResultItem(BaseModel):
    """Engine-side audit output item."""

    classification: str
    item: str
    priority: str
    status: str
    suggestions: str
    evidence: Optional[AuditEvidence] = None
    clause_id: Optional[str] = None
    checks_performed: Optional[int] = None
    empty_scope_basis: Optional[str] = None
    scope: Optional[dict[str, int]] = None
    clarifications: list[dict[str, Any]] = Field(default_factory=list)
    fact_questions: list[FactQuestion] = Field(default_factory=list, max_length=64)
    fact_evaluation: BooleanFactEvaluation | None = None
    supply: SupplyNeed | None = None
