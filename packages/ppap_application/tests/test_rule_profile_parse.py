"""Parsed rule files keep the profile contract without the old task API."""

from __future__ import annotations

import json

import pytest
from ppap_application import parse_rule_file
from ppap_core.rule_profile import RULE_PROFILE_V2, RULE_PROFILE_V3
from ppap_core.rules import RuleParseError


def _item() -> dict:
    return {
        "id": "1",
        "category": "基本信息",
        "priority": "P0",
        "rule_to_check": "必须填写零件号",
        "type_of_rule": "table",
    }


def test_json_parse_without_profile_becomes_v2() -> None:
    payload = {"kind": "engine", "type": "控制计划", "rules": [_item()]}
    body = parse_rule_file("rules.json", json.dumps(payload).encode())
    assert body["rule_profile"] == RULE_PROFILE_V2
    assert body["type"] == "控制计划"


def test_json_parse_keeps_explicit_v3() -> None:
    payload = {"kind": "engine", "type": "控制计划", "rule_profile": "v3", "rules": [_item()]}
    body = parse_rule_file("rules.json", json.dumps(payload).encode())
    assert body["rule_profile"] == RULE_PROFILE_V3


def test_json_parse_maps_default_profile_to_v2() -> None:
    payload = {"kind": "engine", "type": "控制计划", "rule_profile": "default", "rules": [_item()]}
    body = parse_rule_file("rules.json", json.dumps(payload).encode())
    assert body["rule_profile"] == RULE_PROFILE_V2


def test_json_parse_rejects_a_different_requested_document_type() -> None:
    payload = {"kind": "engine", "type": "控制计划", "rules": [_item()]}
    content = json.dumps(payload).encode()
    with pytest.raises(RuleParseError, match="^rule_document_type_mismatch$"):
        parse_rule_file("rules.json", content, document_type="全功能检验报告")
    assert parse_rule_file("rules.json", content, document_type="控制计划")["type"] == "控制计划"
