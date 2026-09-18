"""User-visible round presentation. Maps product status; does not invent PASS."""
from __future__ import annotations

KNOWN_FINDING_STATUS = {"PASS", "NG", "ERR"}


def execution_state(status: str | None) -> str:
    if status == "succeeded":
        return "completed"
    return str(status or "")


def finding_status(status: object) -> str:
    value = str(status or "").strip().upper()
    if value in KNOWN_FINDING_STATUS:
        return value
    return "ERR"


def evidence_state(evidence: object) -> str:
    if isinstance(evidence, dict):
        sources = evidence.get("sources") or []
        if sources:
            return "available"
    return "missing"


def evidence_locator(evidence: object) -> str:
    if not isinstance(evidence, dict):
        return ""
    sources = evidence.get("sources") or []
    if sources and isinstance(sources[0], dict):
        return str(sources[0].get("locator") or "")
    return ""


def next_action(status: object) -> str:
    resolved = finding_status(status)
    if resolved == "NG":
        return "核对疑点并决定是否整改"
    if resolved == "ERR":
        return "检查未完成，补充资料或技术重试"
    return "记录复核意见（不改机器结果）"


def round_display(status: str, inputs: list[dict], results: list[dict]) -> dict:
    target_versions = {item.get("file_version_id") for item in inputs if item.get("role") == "target"}
    reference_count = sum(1 for item in inputs if item.get("role") == "reference")
    visible: list[dict] = []
    for result in results:
        if result.get("file_version_id") not in target_versions:
            continue
        for item in result.get("findings") or []:
            if not isinstance(item, dict):
                continue
            status_value = finding_status(item.get("status"))
            evidence = item.get("evidence")
            visible.append(
                {
                    "id": str(item.get("item") or item.get("classification") or "check"),
                    "status": status_value,
                    "evidence_state": evidence_state(evidence),
                    "locator": evidence_locator(evidence),
                    "next_action": next_action(status_value),
                    "file_version_id": result.get("file_version_id"),
                    "sha256": result.get("sha256"),
                }
            )
    return {
        "execution_state": execution_state(status),
        "visible_findings": visible,
        "coverage": {
            "target_count": len(target_versions),
            "reference_count": reference_count,
            "finding_count": len(visible),
        },
    }
