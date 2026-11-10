"""One hash for a formal snapshot and one hash for the reading the user saw.

Product stores these digests. The checker prompt checks the same bytes.
Neither side may keep a second key list.
"""

from __future__ import annotations

import hashlib
import json

FORMAL_DIGEST_KEYS = (
    "format_version",
    "formal_profile",
    "owl_content",
    "clif_content",
    "vocabulary",
    "source_rule_version_id",
    "clause_id",
    "obligations",
    "unresolved",
    "assumptions",
)

CNL_RENDERER_VERSION = "cnl-zh-1"


def _canonical(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def formal_digest(spec: dict) -> str:
    return _canonical({key: spec[key] for key in FORMAL_DIGEST_KEYS})


def display_digest(cnl: str, formal_digest_value: str, unresolved: list, completeness: str, *, renderer: str = CNL_RENDERER_VERSION) -> str:
    return _canonical({"renderer": renderer, "cnl": cnl, "formal_digest": formal_digest_value, "unresolved": unresolved, "completeness": completeness})
