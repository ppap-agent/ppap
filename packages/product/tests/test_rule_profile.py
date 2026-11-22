"""A published rule body carries one executable rule profile."""

from __future__ import annotations

import pytest
from product.rules import normalize_rule_body


def _body(**extra: object) -> dict:
    value = {"kind": "engine", "type": "控制计划", "rules": [{"id": "1", "category": "基本信息", "priority": "P0", "rule_to_check": "必须填写零件号", "type_of_rule": "table"}]}
    value.update(extra)
    return value


@pytest.mark.parametrize(("raw", "expected"), [(None, "v2"), ("", "v2"), ("default", "v2"), ("v2", "v2"), (" V3 ", "v3")])
def test_published_body_profile_defaults_to_v2_and_keeps_an_explicit_one(raw: str | None, expected: str) -> None:
    body = _body() if raw is None else _body(rule_profile=raw)
    assert normalize_rule_body(body)["rule_profile"] == expected


@pytest.mark.parametrize(("raw", "code"), [("legacy", "invalid_rule_profile"), ({}, "invalid_rule_profile"), (1, "invalid_rule_profile"), ("v4", "product_preset_required")])
def test_published_body_rejects_unknown_profile_and_product_presets(raw: object, code: str) -> None:
    with pytest.raises(ValueError, match=code):
        normalize_rule_body(_body(rule_profile=raw))
