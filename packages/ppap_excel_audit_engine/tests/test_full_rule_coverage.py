from copy import deepcopy

import pytest
from ppap_excel_audit_engine.checkers.rule_declarations import freeze_target_rule_execution


def test_full_start_inspection_requirement_keeps_its_identity_and_checker_index():
    requirement = "识别“工序名称”列是否存在，如果存在则继续识别；当“工序名称”列下包含“进料检验”或“IQC”或相近词义的内容则通过，否则不通过"
    body = {
        "kind": "engine",
        "type": "过程流程图",
        "rule_profile": "v3",
        "rules": [{"id": "customer-start", "category": "完整入料要求", "priority": None, "rule_to_check": requirement}],
    }
    original = deepcopy(body)

    frozen = freeze_target_rule_execution(body, "process.xlsx")

    assert frozen["items"][0] == {
        "id": "customer-start",
        "category": "完整入料要求",
        "priority": None,
        "rule_to_check": requirement,
        "coverage": "mapped",
        "checker_method": "rule_6",
        "checker_index": 5,
        "checker_description": "起始工序拆解完整性识别",
    }
    assert body == original


@pytest.mark.parametrize(
    "profile, text",
    [
        ("v2", "识别“工序名称”列是否存在，如果存在则继续识别；当“工序名称”列下包含“进料检验”或“IQC”或相近词义的内容则通过，否则不通过"),
        ("v3", "识别“工序名称”列是否存在，如果存在则继续识别；当“工序名称”列下包含“进料检验”或“IQC”或相近词义的内容则通过，否则通过"),
    ],
)
def test_full_requirement_is_not_bound_to_another_profile_or_changed_predicate(profile, text):
    frozen = freeze_target_rule_execution({"kind": "engine", "type": "过程流程图", "rule_profile": profile, "rules": [{"id": "6", "rule_to_check": text}]}, "process.xlsx")

    assert frozen["items"][0]["coverage"] == "unsupported"
    assert "checker_index" not in frozen["items"][0]
