"""Runtime input adapters preserve values without owning persistent snapshots."""

from types import SimpleNamespace

import pytest
from audit.contracts import AuditInput, RuleInput
from audit.engine.contract_mapper import to_audit_input, to_rule_input


@pytest.mark.parametrize("contract_input", [False, True], ids=["attribute-input", "contract-input"])
def test_runtime_audit_adapter_preserves_the_file_identity_and_values(contract_input):
    values = {
        "audit_id": "file-version-two",
        "file_url": "/tmp/frozen.xlsx",
        "customer": "客户甲",
        "model": "车型甲",
        "part": "P-001",
        "content": "工序名称 IQC",
        "file_metadata": {"original_filename": "过程流程图.xlsx", "role": "target"},
        "project_id": "group-one",
    }
    source = AuditInput(**values) if contract_input else SimpleNamespace(**values)

    result = to_audit_input(source)

    assert result.model_dump() == values


@pytest.mark.parametrize("contract_input", [False, True], ids=["attribute-input", "contract-input"])
@pytest.mark.parametrize(
    "appendix",
    [None, [], [{"id": "confirmed-four", "text": "工序编号允许前导零", "scope": {"document_id": "file-version-two", "rule_ids": ["1"]}}]],
    ids=["absent-appendix", "empty-appendix", "confirmed-appendix"],
)
def test_runtime_rule_adapter_preserves_the_version_scope_and_normalized_profile(contract_input, appendix):
    expected = {
        "rule_id": "rule-version-four",
        "rule_profile": "v3",
        "preset_key": None,
        "type": "控制计划",
        "rules": [{"id": "1", "rule_to_check": "检查工序"}],
        "appendix": appendix or [],
        "customers": ["客户甲", "客户乙"],
        "models": ["车型甲"],
        "parts": ["P-001", "P-002"],
    }
    values = dict(expected)
    if appendix is None:
        values.pop("appendix")
    source = RuleInput(**values) if contract_input else SimpleNamespace(**{**values, "rule_profile": " V3 "})

    result = to_rule_input(source)

    assert result.model_dump() == expected
