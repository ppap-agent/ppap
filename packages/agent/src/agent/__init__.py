"""Bounded Deep execution protocol. Independent of ppap-agent orchestration."""

from agent.client import DeepClient, DeepError, DeepUnavailable
from agent.protocol import (
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
