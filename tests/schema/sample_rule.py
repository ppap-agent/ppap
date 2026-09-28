"""Schema-local sample rule body. Not a shared test library."""

from __future__ import annotations


def rule_body() -> dict:
    return {
        "kind": "engine",
        "rule_profile": "v3",
        "type": "控制计划",
        "customers": ["测试客户"],
        "models": ["M1"],
        "parts": ["P1"],
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
