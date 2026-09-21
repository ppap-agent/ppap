"""Bounded Deep execution protocol. Independent of ppap-agent orchestration."""

from ppap_deep.client import DeepClient, DeepError, DeepUnavailable
from ppap_deep.protocol import (
    DeepCoverage,
    DeepFinding,
    ErrorEnvelope,
    ExecuteRequest,
    ExecuteResponse,
    FrozenFile,
    RequestEnvelope,
    ResultEnvelope,
)

__all__ = [
    "DeepClient",
    "DeepCoverage",
    "DeepError",
    "DeepFinding",
    "DeepUnavailable",
    "ErrorEnvelope",
    "ExecuteRequest",
    "ExecuteResponse",
    "FrozenFile",
    "RequestEnvelope",
    "ResultEnvelope",
]
