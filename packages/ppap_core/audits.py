"""Audit draft/round/review semantics. Table names live in migrations; this is product logic."""
from __future__ import annotations

import hashlib
import json
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


def request_fingerprint(rule_version_id: str, items: list[dict[str, str]]) -> str:
    canonical = {
        "rule_version_id": rule_version_id,
        "items": sorted(
            ({"file_version_id": row["file_version_id"], "role": row["role"]} for row in items),
            key=lambda row: (row["role"], row["file_version_id"]),
        ),
    }
    blob = json.dumps(canonical, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
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


@dataclass
class RoundWork:
    round_id: str
    tenant_id: str
    rule_title: str
    rule_body: dict[str, Any]
    inputs: list[RoundInput]
    result_attempt: int | None = None

    def rule_kind(self) -> str:
        kind = str((self.rule_body or {}).get("kind") or KIND_ENGINE)
        return kind if kind in {KIND_PRESENCE, KIND_ENGINE} else KIND_ENGINE


@dataclass
class ClaimedJob:
    id: str
    kind: str
    payload: dict[str, Any]
    attempt: int
    round_id: str | None = None


def _presence_finding(item: RoundInput) -> dict[str, Any]:
    ready = item.status == "ready"
    if item.role == TARGET:
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
    return {
        "item_key": f"reference:{item.file_version_id}",
        "classification": "资料",
        "item": "参考文件已引用",
        "priority": "P2",
        "status": PASS if ready else NG,
        "suggestions": "被引用不等于已完整审核。",
        "locator": {
            "kind": "file_version",
            "file_id": item.file_id,
            "file_version_id": item.file_version_id,
            "filename": item.filename,
        },
    }


def evaluate_presence(work: RoundWork) -> dict[str, Any]:
    findings = [_presence_finding(item) for item in work.inputs]
    checked = [item.filename for item in work.inputs if item.role == TARGET]
    unchecked = [
        f"{item.filename}（参考，未作为待审完整检查）"
        for item in work.inputs
        if item.role == REFERENCE
    ]
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


def findings_from_engine_results(results: list[Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    checked: list[str] = []
    locator_gap = False
    for index, result in enumerate(results, start=1):
        status = getattr(result, "status", ERR)
        if status not in FINDING_STATUSES:
            status = ERR
        locator = None
        evidence = getattr(result, "evidence", None)
        sources = getattr(evidence, "sources", None) if evidence is not None else None
        if sources:
            source = sources[0]
            locator = {"kind": getattr(source, "kind", "structure"), "locator": getattr(source, "locator", "")}
            checked.append(str(getattr(result, "item", "")))
        else:
            locator_gap = True
        findings.append(
            {
                "item_key": f"engine:{index}",
                "classification": str(getattr(result, "classification", "")),
                "item": str(getattr(result, "item", "")),
                "priority": str(getattr(result, "priority", "")),
                "status": status,
                "suggestions": str(getattr(result, "suggestions", "")),
                "locator": locator,
            }
        )
    if not findings:
        return execution_error_result("empty_engine_result")
    return {
        "findings": findings,
        "coverage": {"checked": checked, "unchecked": [], "locator_gap": locator_gap},
    }


def evaluate_round(work: RoundWork, engine_runner=None) -> dict[str, Any]:
    if work.rule_kind() == KIND_PRESENCE:
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
