"""Rule profile normalization and published body profile."""

from __future__ import annotations

import pytest
from ppap_core.rule_profile import RULE_PROFILE_V2, RULE_PROFILE_V3, normalize_rule_profile
from ppap_core.rules import normalize_rule_body


def _body(**extra: object) -> dict:
    value = {
        "kind": "engine",
        "type": "控制计划",
        "rules": [
            {
                "id": "1",
                "category": "基本信息",
                "priority": "P0",
                "rule_to_check": "必须填写零件号",
                "type_of_rule": "table",
            }
        ],
    }
    value.update(extra)
    return value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, RULE_PROFILE_V2),
        ("", RULE_PROFILE_V2),
        ("default", RULE_PROFILE_V2),
        ("v2", RULE_PROFILE_V2),
        ("modern_v2", RULE_PROFILE_V2),
        ("v3", RULE_PROFILE_V3),
    ],
)
def test_normalize_rule_profile(raw: str | None, expected: str) -> None:
    assert normalize_rule_profile(raw) == expected


def test_normalize_rule_profile_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        normalize_rule_profile("legacy")


@pytest.mark.parametrize("raw", [None, "", "default", "v2", "modern_v2"])
def test_published_body_uses_v2_when_profile_is_default(raw: str | None) -> None:
    body = _body()
    if raw is not None:
        body["rule_profile"] = raw
    assert normalize_rule_body(body)["rule_profile"] == RULE_PROFILE_V2


def test_published_body_keeps_explicit_v3() -> None:
    assert normalize_rule_body(_body(rule_profile="v3"))["rule_profile"] == RULE_PROFILE_V3


@pytest.mark.parametrize("raw", ["legacy", {}, 1])
def test_published_body_rejects_unknown_profile(raw: object) -> None:
    with pytest.raises(ValueError, match="invalid_rule_profile"):
        normalize_rule_body(_body(rule_profile=raw))
