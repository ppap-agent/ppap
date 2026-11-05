"""Publication policy is independent of a Checker's business PASS/NG outcome."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def publication_fingerprint(*, suite_key: str, revision_id: str, expected_revision: int, force_unverified: bool, user_id: str) -> str:
    if type(expected_revision) is not int or expected_revision < 0 or type(force_unverified) is not bool:
        raise ValueError("invalid_suite_publication")
    value = {"suite_key": suite_key, "revision_id": revision_id, "expected_revision": expected_revision, "force_unverified": force_unverified, "user_id": user_id}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def empty_validation() -> dict[str, Any]:
    return {"status": "unverified", "revision": 0, "total_samples": 0, "passed_samples": 0, "new_errors": 0, "verified": False, "samples_not_rerun": False, "reasons": ["no_validation_proof"]}
