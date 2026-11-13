"""Compare a Standard product round with a rule-index oracle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from audit.audits import ERR

MATCH = "match"
STATUS_MISMATCH = "status_mismatch"
MISSING_RULE = "missing_rule"
EXECUTION_FAILURE = "execution_failure"
SETUP_FAILURE = "setup_failure"

OK = "ok"
BLOCKING = "blocking"
KNOWN_GAP = "known_gap"


RuleIndex = TypeVar("RuleIndex", int, str)


@dataclass(frozen=True)
class RuleVerdict(Generic[RuleIndex]):
    file_name: str
    rule_index: RuleIndex
    kind: str
    expected: str
    actual: str | None = None
    detail: str = ""


def compare_file(
    *,
    file_name: str,
    version_id: str,
    expected: dict[RuleIndex, str],
    result: dict | None,
    setup_error: str | None,
    expected_coverage: str = "mapped",
) -> list[RuleVerdict[RuleIndex]]:
    if setup_error or result is None:
        return [RuleVerdict(file_name, index, SETUP_FAILURE, status, detail=setup_error or "round did not run") for index, status in sorted(expected.items())]
    coverage = result.get("coverage") or {}
    core = isinstance(coverage, list)
    items = [row for row in (coverage if core else coverage.get("rule_items") or []) if row.get("file_version_id") == version_id]
    by_index: dict[str, dict] = {}
    for row in items:
        key = str(row.get("clause_id") if core else row.get("id"))
        if key in by_index:
            return [RuleVerdict(file_name, index, SETUP_FAILURE, status, detail=f"duplicate result ID: {key}") for index, status in expected.items()]
        by_index[key] = row
    note = str(result.get("note") or "")
    verdicts: list[RuleVerdict[RuleIndex]] = []
    for index, status in sorted(expected.items()):
        row = by_index.get(str(index))
        if row is None:
            verdicts.append(RuleVerdict(file_name, index, MISSING_RULE, status, detail=note))
            continue
        if core:
            findings = [item for item in result.get("findings") or [] if item.get("file_version_id") == version_id and item.get("clause_id") == str(index)]
            if len(findings) != 1:
                verdicts.append(RuleVerdict(file_name, index, MISSING_RULE, status, detail=note))
                continue
            row = {**row, "status": findings[0].get("status")}
        actual = str(row.get("status") or "").upper()
        findings = findings if core else [item for item in result.get("findings") or [] if row.get("item_key") and item.get("item_key") == row["item_key"]]
        detail = str(findings[0].get("suggestions") or "") if len(findings) == 1 and str(findings[0].get("status") or "").upper() == actual else ""
        if row.get("coverage") == "execution_error" and actual == ERR:
            verdicts.append(RuleVerdict(file_name, index, EXECUTION_FAILURE, status, actual, detail or note))
        elif row.get("coverage") != expected_coverage:
            verdicts.append(RuleVerdict(file_name, index, MISSING_RULE, status, actual, note))
        elif actual == ERR:
            verdicts.append(RuleVerdict(file_name, index, EXECUTION_FAILURE, status, actual, detail or note))
        elif actual != status:
            verdicts.append(RuleVerdict(file_name, index, STATUS_MISMATCH, status, actual, detail))
        else:
            verdicts.append(RuleVerdict(file_name, index, MATCH, status, actual))
    return verdicts


def severity(verdict: RuleVerdict, gap_status: str = "") -> str:
    """Any difference from the human truth blocks, unless a known gap records exactly that result."""
    if verdict.kind == MATCH:
        return BLOCKING if gap_status else OK
    return KNOWN_GAP if gap_status and (verdict.actual or "ERR") == gap_status else BLOCKING


def format_verdicts(verdicts: list[RuleVerdict]) -> str:
    return "\n".join(f"{item.file_name} rule {item.rule_index}: {item.kind} expected={item.expected} actual={item.actual or '-'} {item.detail}".rstrip() for item in verdicts)
