"""How long one file's standard audit takes in this tenant, measured from finished rounds.

A Round's targets are audited together and their results are committed together,
so a file's time is its Round's time shared by its targets:

    (result commit of the Round's result attempt - Round acceptance) / target files

Acceptance is ``audit_rounds.created_at`` (acceptance and enqueue are one
transaction); the result commit is ``engine.attempts.finished_at`` of the audit
job attempt that wrote ``audit_rounds.result_attempt`` (same transaction as the
result). Queueing, retries and reading are inside the interval; report PDFs are
not. Deep-mode Rounds are excluded. With fewer than ``MIN_FILES`` measured files
there is no estimate, never a fixed fallback.
"""

from __future__ import annotations

import math
from typing import Any

WINDOW_DAYS = 90
MIN_FILES = 10


def _percentile(ordered: list[float], fraction: float) -> float:
    """Linear interpolation between closest ranks (PostgreSQL percentile_cont)."""
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def per_file_interval(rounds: list[tuple[float, int]]) -> dict[str, float] | None:
    """The median to 90th-percentile seconds of one file, from (round seconds, target files)."""
    files = sorted(seconds / count for seconds, count in rounds if count > 0 and seconds >= 0 for _ in range(count))
    if len(files) < MIN_FILES:
        return None
    return {"low": round(_percentile(files, 0.5), 1), "high": round(_percentile(files, 0.9), 1)}


def audit_duration_estimate(conn: Any, tenant_id: str) -> dict:
    rows = conn.execute(
        """SELECT EXTRACT(EPOCH FROM (finished.at - r.created_at))::float8,
                  (SELECT count(*) FROM product.round_inputs i WHERE i.round_id = r.id AND i.role = 'target')::int
           FROM product.audit_rounds r
           JOIN LATERAL (
               SELECT min(a.finished_at) AS at FROM engine.jobs j
               JOIN engine.attempts a ON a.job_id = j.id AND a.attempt = r.result_attempt AND a.outcome = 'succeeded'
               WHERE j.audit_round_id = r.id AND j.kind = 'audit' AND j.tenant_id = r.tenant_id
           ) finished ON finished.at IS NOT NULL
           WHERE r.tenant_id = %s AND r.execution = 'completed' AND NOT r.deep_mode
             AND r.created_at >= now() - make_interval(days => %s)""",
        (tenant_id, WINDOW_DAYS),
    ).fetchall()
    return {"per_file_seconds": per_file_interval([(float(row[0]), int(row[1])) for row in rows])}
