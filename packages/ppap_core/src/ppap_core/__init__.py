"""Shared product and audit-engine library. Not a network service."""

from .contracts import (
    AuditEvidence,
    AuditInput,
    AuditResultItem,
    ConversionArtifact,
    EvidenceDiagnostic,
    EvidenceSource,
    FileRef,
    GroupContextInput,
    OcrArtifact,
    RuleInput,
)
from .rule_profile import normalize_rule_profile
from .settings import Settings, get_settings
from .token_tracker import (
    RequestTokenSummary,
    TokenTracker,
    TokenUsage,
    end_request_tracking,
    get_current_request_id,
    get_token_tracker,
    record_token_usage,
    set_current_request_id,
    start_request_tracking,
)
from .types import PpapDocumentType, TokenUsageInfo

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "TokenUsageInfo",
    "PpapDocumentType",
    "Settings",
    "get_settings",
    "normalize_rule_profile",
    "FileRef",
    "OcrArtifact",
    "ConversionArtifact",
    "RuleInput",
    "AuditInput",
    "GroupContextInput",
    "AuditResultItem",
    "AuditEvidence",
    "EvidenceDiagnostic",
    "EvidenceSource",
    "TokenUsage",
    "RequestTokenSummary",
    "TokenTracker",
    "get_token_tracker",
    "start_request_tracking",
    "end_request_tracking",
    "record_token_usage",
    "get_current_request_id",
    "set_current_request_id",
]
