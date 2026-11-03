"""Rule profile normalization helpers."""

from __future__ import annotations

from typing import Final

RULE_PROFILE_V2: Final[str] = "v2"
RULE_PROFILE_V3: Final[str] = "v3"
RULE_PROFILE_V4: Final[str] = "v4"
RULE_PROFILE_DEFAULT_INPUT: Final[str] = "default"


def normalize_rule_profile(profile: str | None) -> str:
    """Normalize user/system profile input to engine-executable profile."""
    normalized = str(profile or "").strip().lower()
    if normalized in {"", RULE_PROFILE_DEFAULT_INPUT, RULE_PROFILE_V2, "modern_v2"}:
        return RULE_PROFILE_V2
    if normalized in {RULE_PROFILE_V3, RULE_PROFILE_V4}:
        return normalized
    raise ValueError(f"Unsupported rule profile: {profile}")
