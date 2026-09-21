"""Audit draft/round/review semantics. Table names live in migrations; this is product logic."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
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
FINDING_STATUSES = {PASS, NG, ERR}

KIND_PRESENCE = "presence"
KIND_ENGINE = "engine"

ANALYSIS_STANDARD = "standard"
ANALYSIS_DEEP = "deep"
ANALYSIS_MODES = {ANALYSIS_STANDARD, ANALYSIS_DEEP}

EXECUTOR_STANDARD = "ppap-standard"
EXECUTOR_DEEP = "ppap-deep"
STANDARD_EXECUTOR_REVISION = "in-repo-engine-v1"
DEEP_PROTOCOL_REVISION = "ppap-deep-v1"


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


def normalize_analysis_mode(value: Any) -> str:
    mode = str(value or ANALYSIS_STANDARD).strip() or ANALYSIS_STANDARD
    if mode not in ANALYSIS_MODES:
        raise ValueError("invalid_analysis_mode")
    return mode


def frozen_executor(analysis_mode: str, deep_revision: str = "") -> tuple[str, str]:
    mode = normalize_analysis_mode(analysis_mode)
    if mode == ANALYSIS_DEEP:
        revision = str(deep_revision or "").strip() or DEEP_PROTOCOL_REVISION
        return EXECUTOR_DEEP, revision
    return EXECUTOR_STANDARD, STANDARD_EXECUTOR_REVISION


def job_payload(round_id: str, analysis_mode: str, deep_revision: str = "") -> dict[str, str]:
    executor, revision = frozen_executor(analysis_mode, deep_revision)
    return {"round_id": round_id, "executor": executor, "executor_revision": revision}


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

    def rule_kind(self) -> str:
        return str((self.rule_body or {}).get("kind") or KIND_ENGINE)


@dataclass
class RoundWork:
    round_id: str
    tenant_id: str
    inputs: list[RoundInput]
    result_attempt: int | None = None
    analysis_mode: str = ANALYSIS_STANDARD


@dataclass
class ClaimedJob:
    id: str
    kind: str
    payload: dict[str, Any]
    attempt: int
    round_id: str | None = None
    tenant_id: str | None = None


def presence_finding(item: RoundInput) -> dict[str, Any]:
    ready = item.status == "ready"
    return {
        "item_key": f"target:{item.file_version_id}",
        "classification": "资料",
        "item": "待审文件版本已就绪",
        "priority": "P0",
        "status": PASS if ready else NG,
        "suggestions": "" if ready else "未完成的暂存不能被审核受理。",
        "locator": {
            "kind": "file_version",
            "file_id": item.file_id,
            "file_version_id": item.file_version_id,
            "filename": item.filename,
        },
    }


def unused_reference_note(filename: str, detail: str = "未作为待审完整检查") -> str:
    return f"{filename}（参考，{detail}）"


def evaluate_presence(work: RoundWork) -> dict[str, Any]:
    findings = [presence_finding(item) for item in work.inputs if item.role == TARGET]
    checked = [item.filename for item in work.inputs if item.role == TARGET]
    unchecked = [unused_reference_note(item.filename) for item in work.inputs if item.role == REFERENCE]
    locator_gap = any(row.get("locator") is None for row in findings)
    return {
        "findings": findings,
        "coverage": {"checked": checked, "unchecked": unchecked, "locator_gap": locator_gap},
    }


def execution_error_result(code: str) -> dict[str, Any]:
    return {
        "findings": [
            {
                "item_key": "execution",
                "classification": "执行",
                "item": "审核执行",
                "priority": "P0",
                "status": ERR,
                "suggestions": code,
                "locator": None,
            }
        ],
        "coverage": {"checked": [], "unchecked": ["执行未完成"], "locator_gap": True},
    }


def findings_from_engine_results(results: list[Any], *, target: RoundInput) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    checked: list[str] = []
    locator_gap = False
    prefix = f"{target.file_version_id}:"
    file_locator = {
        "kind": "file_version",
        "file_id": target.file_id,
        "file_version_id": target.file_version_id,
        "filename": target.filename,
    }
    for index, result in enumerate(results, start=1):
        status = getattr(result, "status", ERR)
        if status not in FINDING_STATUSES:
            status = ERR
        evidence = getattr(result, "evidence", None)
        sources = getattr(evidence, "sources", None) if evidence is not None else None
        locator = {**file_locator, "evidence": evidence.model_dump(mode="json") if evidence is not None else None}
        if sources:
            locator.update(kind=sources[0].kind, locator=sources[0].locator)
            checked.append(str(getattr(result, "item", "")))
        else:
            locator_gap = True
        findings.append(
            {
                "item_key": f"{prefix}engine:{index}",
                "classification": str(getattr(result, "classification", "")),
                "item": str(getattr(result, "item", "")),
                "priority": str(getattr(result, "priority", "")),
                "status": status,
                "suggestions": str(getattr(result, "suggestions", "")),
                "locator": locator,
            }
        )
    if not findings:
        empty = execution_error_result("empty_engine_result")
        for row in empty["findings"]:
            row["item_key"] = f"{prefix}{row['item_key']}"
            row["locator"] = file_locator
        return empty
    return {
        "findings": findings,
        "coverage": {"checked": checked, "unchecked": [], "locator_gap": locator_gap},
    }


def evaluate_round(work: RoundWork, engine_runner=None) -> dict[str, Any]:
    targets = [item for item in work.inputs if item.role == TARGET]
    if targets and all(item.rule_kind() == KIND_PRESENCE for item in targets):
        return evaluate_presence(work)
    if engine_runner is None:
        return execution_error_result("engine_unavailable")
    return engine_runner(work)


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
