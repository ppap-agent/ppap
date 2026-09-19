"""Local usage events. Not S07 Live and not a billing policy."""
from __future__ import annotations

USAGE_OBSERVED = "observed"
USAGE_TRIAL = "trial"
USAGE_PENDING_REPORT = "pending_report"
USAGE_CONFIRMED = "confirmed"
USAGE_FAILED = "failed"
USAGE_UNKNOWN = "unknown"
USAGE_RECONCILING = "reconciling"
USAGE_STATES = {
    USAGE_OBSERVED,
    USAGE_TRIAL,
    USAGE_PENDING_REPORT,
    USAGE_CONFIRMED,
    USAGE_FAILED,
    USAGE_UNKNOWN,
    USAGE_RECONCILING,
}
USAGE_KIND_AUDIT_ROUND = "audit_round"


def business_key_for_round(round_id: str) -> str:
    return f"audit_round:{round_id}"


def public_usage(row: dict) -> dict:
    """Never expose a fabricated balance, quota, or historical 500/10000 default."""
    state = row["state"]
    return {
        "id": row["id"],
        "round_id": row["round_id"],
        "kind": row["kind"],
        "state": state,
        "business_key": row["business_key"],
        "charged": state == USAGE_CONFIRMED,
        "external_ref": row.get("external_ref"),
        "detail": row.get("detail") or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
