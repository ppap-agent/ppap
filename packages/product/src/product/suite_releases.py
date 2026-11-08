"""Publication policy is independent of a Checker's business PASS/NG outcome."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from product.suites import ResolvedSuite, is_unimplemented_suite


def publication_fingerprint(*, suite_key: str, revision_id: str, expected_revision: int, force_unverified: bool, user_id: str) -> str:
    if type(expected_revision) is not int or expected_revision < 0 or type(force_unverified) is not bool:
        raise ValueError("invalid_suite_publication")
    value = {"suite_key": suite_key, "revision_id": revision_id, "expected_revision": expected_revision, "force_unverified": force_unverified, "user_id": user_id}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def partial_official_directory_slice(suite: ResolvedSuite) -> bool:
    """Official directory leaf whose checker does not bind every clause.

    The revision owner is what matters. A tenant-owned copy keeps the explicit
    ``force_unverified`` acknowledgement. Legacy carriers call ``check_clause``
    and are not this leaf, even when their binding is narrower than the rule list.
    """
    if suite.revision.tenant_id is not None:
        return False
    try:
        rules = json.loads(suite.files["rules.json"])
    except (KeyError, TypeError, ValueError):
        return False
    rows = rules.get("rules")
    binding = rules.get("checker_binding")
    if not isinstance(rows, list) or not isinstance(binding, dict):
        return False
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    if not ids or any(not isinstance(clause, str) for clause in ids) or set(binding) == set(ids):
        return False
    source = suite.files.get("checker.py", b"")
    return isinstance(source, bytes) and b"def create_audit" in source and b"check_clause" not in source


def adoption_block(suite: ResolvedSuite, *, verified: bool, force_unverified: bool) -> str | None:
    """Release-boundary refusal. Partial official leaves ignore force_unverified."""
    if partial_official_directory_slice(suite):
        return "partial_directory_suite_not_adoptable"
    if not verified and not force_unverified and not is_unimplemented_suite(suite):
        return "suite_confirmation_required"
    return None


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
