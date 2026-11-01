"""Compare a Standard product round with a rule-index oracle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from audit.audits import ERR, RoundInput, RoundWork
from audit.engine.checkers.rule_declarations import (
    declarations_for_target,
    freeze_target_rule_execution,
)
from audit.engine.routing import resolve_engine_agent_class
from audit.kinds import PpapDocumentType

MATCH = "match"
STATUS_MISMATCH = "status_mismatch"
MISSING_RULE = "missing_rule"
EXECUTION_FAILURE = "execution_failure"
SETUP_FAILURE = "setup_failure"


@dataclass(frozen=True)
class RuleVerdict:
    file_name: str
    rule_index: int
    kind: str
    expected: str
    actual: str | None = None
    detail: str = ""


def bound_type_name(agent_cls: type, filename: str, profile: str) -> str | None:
    """Document type whose current route selects this corpus agent."""
    for doc_type in PpapDocumentType:
        if doc_type is PpapDocumentType.UNKNOWN:
            continue
        if resolve_engine_agent_class(doc_type, filename, profile) is agent_cls:
            return doc_type.get_type_name()
    return None


def engine_rule_body(doc_type: str, filename: str, profile: str) -> tuple[dict, dict | None]:
    declarations = declarations_for_target(doc_type, filename, profile)
    rules = [
        {
            "id": str(index),
            "category": description,
            "priority": "P0",
            "rule_to_check": description,
            "type_of_rule": "text",
        }
        for index, (_method, description) in enumerate(declarations, 1)
    ]
    body = {"kind": "engine", "type": doc_type, "rule_profile": profile, "rules": rules}
    return body, freeze_target_rule_execution(body, filename)


def build_product_round(
    files: list[tuple[str, Path, str]],
    *,
    profile: str,
    round_id: str,
) -> tuple[RoundWork | None, dict[str, bytes], dict[str, str], str | None]:
    """Build one round. ``files`` is ``(filename, path, bound document type)``."""
    inputs: list[RoundInput] = []
    blobs: dict[str, bytes] = {}
    version_by_name: dict[str, str] = {}
    for index, (filename, path, doc_type) in enumerate(files, 1):
        if not path.is_file():
            return None, {}, {}, f"missing file: {path}"
        version_id = f"v{index}"
        version_by_name[filename] = version_id
        body, execution = engine_rule_body(doc_type, filename, profile)
        if not body["rules"]:
            return None, {}, version_by_name, f"no builtin checks for {filename} type={doc_type}"
        inputs.append(
            RoundInput(
                "target",
                f"f{index}",
                version_id,
                filename,
                "ready",
                rule_version_id=f"rule-{index}",
                rule_title=doc_type,
                rule_body=body,
                rule_execution=execution,
            )
        )
        blobs[version_id] = path.read_bytes()
    return RoundWork(round_id, "corpus", inputs), blobs, version_by_name, None


def execute_product_round(files, *, profile: str, round_id: str, runner):
    """Build a round and run it with the product runner."""
    work, blobs, versions, setup_error = build_product_round(files, profile=profile, round_id=round_id)
    if setup_error or work is None:
        return None, versions, setup_error
    try:
        return runner(work, blobs), versions, None
    except ValueError as exc:
        return None, versions, str(exc)


def compare_file(
    *,
    file_name: str,
    version_id: str,
    expected: dict[int, str],
    result: dict | None,
    setup_error: str | None,
    expected_coverage: str = "mapped",
) -> list[RuleVerdict]:
    if setup_error or result is None:
        return [RuleVerdict(file_name, index, SETUP_FAILURE, status, detail=setup_error or "round did not run") for index, status in sorted(expected.items())]
    items = [row for row in (result.get("coverage") or {}).get("rule_items") or [] if row.get("file_version_id") == version_id]
    by_index: dict[int, dict] = {}
    for row in items:
        try:
            by_index[int(row.get("id"))] = row
        except (TypeError, ValueError):
            continue
    note = str(result.get("note") or "")
    verdicts: list[RuleVerdict] = []
    for index, status in sorted(expected.items()):
        row = by_index.get(index)
        if row is None:
            verdicts.append(RuleVerdict(file_name, index, MISSING_RULE, status, detail=note))
            continue
        actual = str(row.get("status") or "").upper()
        findings = [item for item in result.get("findings") or [] if row.get("item_key") and item.get("item_key") == row["item_key"]]
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


def failing_verdicts(verdicts: list[RuleVerdict]) -> list[RuleVerdict]:
    return [item for item in verdicts if item.kind != MATCH]


def format_verdicts(verdicts: list[RuleVerdict]) -> str:
    return "\n".join(f"{item.file_name} rule {item.rule_index}: {item.kind} expected={item.expected} actual={item.actual or '-'} {item.detail}".rstrip() for item in verdicts)
