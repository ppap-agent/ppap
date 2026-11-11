"""Publication policy is independent of a Checker's business PASS/NG outcome."""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from product.suites import ResolvedSuite, is_unimplemented_suite


def publication_fingerprint(*, suite_key: str, revision_id: str, expected_revision: int, force_unverified: bool, user_id: str) -> str:
    if type(expected_revision) is not int or expected_revision < 0 or type(force_unverified) is not bool:
        raise ValueError("invalid_suite_publication")
    value = {"suite_key": suite_key, "revision_id": revision_id, "expected_revision": expected_revision, "force_unverified": force_unverified, "user_id": user_id}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def partial_official_directory_slice(suite: ResolvedSuite) -> bool:
    """Official leaf declared partial whose binding does not cover every clause.

    Tenant copies keep explicit ``force_unverified``. The decision is the leaf's
    ``directory_coverage`` and exact binding. Checker source text is not read.
    """
    if suite.revision.tenant_id is not None:
        return False
    try:
        rules = json.loads(suite.files["rules.json"])
    except (KeyError, TypeError, ValueError):
        return False
    if rules.get("directory_coverage") != "partial":
        return False
    rows = rules.get("rules")
    binding = rules.get("checker_binding")
    if not isinstance(rows, list) or not isinstance(binding, dict):
        return False
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    if not ids or any(not isinstance(clause, str) for clause in ids):
        return False
    return set(binding) != set(ids)


def adoption_block(suite: ResolvedSuite, *, verified: bool, force_unverified: bool) -> str | None:
    """Release-boundary refusal. Partial official leaves ignore force_unverified."""
    if partial_official_directory_slice(suite):
        return "partial_directory_suite_not_adoptable"
    from product.legacy_head_cutover import legacy_carrier_program

    # force_unverified can adopt an unverified directory or enterprise program.
    # It cannot make a historical class carrier the next head.
    if legacy_carrier_program(suite.files):
        return "suite_version_confirmation_required"
    if not verified and not force_unverified and not is_unimplemented_suite(suite):
        return "suite_confirmation_required"
    return None


def leaf_capability(files: Mapping[str, bytes]) -> dict[str, Any]:
    """Counts already stored on one leaf. This is not a business-correctness verdict.

    ``evidence_reader_clause_ids`` are the clause ids named by ``RESOURCE["methods"]``
    (company leaves) or ``RESOURCE["clauses"]`` (authored leaves).
    An empty list means no reader is declared. ``directory_coverage`` is not copied
    here: a complete binding is not evidence that a clause can be judged.
    """
    try:
        rules = json.loads(files.get("rules.json", b""))
    except (UnicodeDecodeError, json.JSONDecodeError):
        rules = {}
    if not isinstance(rules, dict):
        rules = {}
    rows = rules.get("rules")
    clause_ids: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            clause_id = row.get("id") if isinstance(row, dict) else None
            if isinstance(clause_id, str):
                clause_ids.append(clause_id)
    binding = rules.get("checker_binding")
    known = set(clause_ids)
    bound = sum(1 for clause_id in binding if isinstance(clause_id, str) and clause_id in known) if isinstance(binding, dict) else 0
    return {"clause_count": len(clause_ids), "bound_clause_count": bound, "evidence_reader_clause_ids": _declared_reader_clause_ids(files.get("resources.py", b""))}


def _declared_reader_clause_ids(source: bytes) -> list[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "RESOURCE" for target in node.targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError, MemoryError):
            return []
        # Company leaves name an implementation method per clause; authored leaves name readers.
        clauses = (value.get("methods") or value.get("clauses")) if isinstance(value, dict) else None
        if not isinstance(clauses, dict):
            return []
        return [clause_id for clause_id in clauses if isinstance(clause_id, str)]
    return []


def empty_validation() -> dict[str, Any]:
    return {
        "status": "unverified",
        "revision": 0,
        "write_revision": 0,
        "proof_scope": "none",
        "total_samples": 0,
        "passed_samples": 0,
        "new_errors": 0,
        "verified": False,
        "samples_not_rerun": False,
        "reasons": ["no_validation_proof"],
    }
