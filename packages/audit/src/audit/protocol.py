"""Bounded Deep guest/controller faults. No caller-selected paths or commands."""

from __future__ import annotations

from typing import Literal

from .models import Artifact

ErrorCode = Literal[
    "CONTROLLER_UNAVAILABLE",
    "ISOLATION_UNAVAILABLE",
    "INPUT_INTEGRITY_ERROR",
    "PARSER_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    "OUTPUT_VALIDATION_ERROR",
    "EVIDENCE_VALIDATION_ERROR",
    "CANCELLED",
    "RESOURCE_LIMIT",
    "INVALID_REQUEST",
]


class GuestFault(Artifact):
    """Bounded guest diagnostics; no exception text or caller-controlled payload."""

    ok: Literal[False] = False
    code: Literal["INPUT_INTEGRITY_ERROR", "PARSER_LIMIT", "MODEL_TIMEOUT", "MODEL_PROTOCOL_ERROR", "OUTPUT_VALIDATION_ERROR", "EVIDENCE_VALIDATION_ERROR", "RESOURCE_LIMIT"]


class ControllerFault(RuntimeError):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code)
