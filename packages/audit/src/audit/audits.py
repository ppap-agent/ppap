"""Audit draft/round/review semantics. Table names live in migrations; this is product logic."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

TARGET = "target"
REFERENCE = "reference"
ROLES = {TARGET, REFERENCE}

DRAFT_OPEN = "open"
DRAFT_ACCEPTED = "accepted"

EXEC_QUEUED = "queued"
EXEC_RUNNING = "running"
EXEC_COMPLETED = "completed"
EXEC_FAILED = "failed"

PASS = "PASS"
NG = "NG"
ERR = "ERR"
UNABLE = "UNABLE"
NA = "NA"
FINDING_STATUSES = {PASS, NG, ERR, UNABLE, NA}

KIND_PRESENCE = "presence"
KIND_ENGINE = "engine"

DEEP_PROTOCOL_REVISION = "ppap-deep-v2"


def normalize_idempotency_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key or len(key) > 128:
        raise ValueError("invalid_idempotency_key")
    if any(ord(ch) < 32 for ch in key):
        raise ValueError("invalid_idempotency_key")
    return key


def normalize_role(value: Any) -> str:
    role = str(value or "").strip()
    if role not in ROLES:
        raise ValueError("invalid_role")
    return role


def request_fingerprint(items: list[dict]) -> str:
    # All fields are validated UUIDs or fixed roles; delimiters cannot occur in them.
    # The forward migration uses this same canonical form for existing snapshots.
    canonical = sorted((row["file_version_id"], row["role"], row.get("rule_version_id") or "") for row in items)
    blob = ";".join(":".join(row) for row in canonical)
    return hashlib.sha256(blob.encode()).hexdigest()


def require_targets(items: list[dict[str, str]]) -> None:
    if not any(row["role"] == TARGET for row in items):
        raise ValueError("target_required")


@dataclass
class RoundInput:
    role: str
    file_id: str
    file_version_id: str
    filename: str
    status: str
    object_key: str = ""
    content_type: str = ""
    rule_version_id: str | None = None
    rule_title: str | None = None
    rule_body: dict[str, Any] | None = None
    sha256: str | None = None
    rule_execution: dict[str, Any] | None = None
    rule_source_file_version_id: str | None = None

    def rule_kind(self) -> str:
        return str((self.rule_body or {}).get("kind") or KIND_ENGINE)


@dataclass
class RoundWork:
    round_id: str
    tenant_id: str
    inputs: list[RoundInput]
    result_attempt: int | None = None
    initiating_user_id: str = ""
    analysis_mode: str = "standard"


@dataclass
class ClaimedJob:
    id: str
    kind: str
    payload: dict[str, Any]
    attempt: int
    round_id: str | None = None
    tenant_id: str | None = None


def execution_error_result(code: str, *, target: RoundInput | None = None) -> dict[str, Any]:
    return {
        "findings": [
            {
                "item_key": f"{target.file_version_id}:execution" if target else "execution",
                "classification": "执行",
                "item": "审核执行",
                "priority": "P0",
                "status": ERR,
                "suggestions": code,
                "locator": {"kind": "file_version", "file_id": target.file_id, "file_version_id": target.file_version_id, "filename": target.filename} if target else None,
            }
        ],
        "coverage": {"checked": [], "unchecked": ["执行未完成"], "locator_gap": True},
    }


def normalize_review(judgment: Any, rationale: Any, next_step: Any, version: Any) -> tuple[str, str, str, int]:
    text = str(judgment or "").strip()
    if not text or len(text) > 200:
        raise ValueError("invalid_review")
    why = str(rationale or "").strip()
    nxt = str(next_step or "").strip()
    if len(why) > 2000 or len(nxt) > 2000:
        raise ValueError("invalid_review")
    try:
        seen = int(version)
    except (TypeError, ValueError):
        raise ValueError("invalid_review") from None
    if seen < 0:
        raise ValueError("invalid_review")
    return text, why, nxt, seen
