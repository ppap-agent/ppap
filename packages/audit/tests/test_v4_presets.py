"""Independent V4 source identities; these tests do not certify Checker coverage."""

import pytest
from audit.presets import preset_rule


def test_preset_matrix_uses_customer_identity_not_legacy_filename_numbers():
    assert (preset_rule("UAES", "U16").source_type, preset_rule("UAES", "U16").level) == ("T09", "L2")
    assert (preset_rule("理想", "L07").source_type, preset_rule("理想", "L07").level) == ("T10", "L1")
    assert preset_rule("蔚来", "N01").source_type == "T01"
    with pytest.raises(ValueError, match="unknown_customer_rule"):
        preset_rule("理想", "U07")
    with pytest.raises(ValueError, match="unknown_customer_rule"):
        preset_rule("UAES", "E12")


def test_repeated_source_numbers_have_distinct_stable_clause_identities():
    sample = preset_rule("UAES", "U01")
    assert [clause.source_number for clause in sample.clauses] == ["1", "2", "3", "1"]
    assert len({clause.id for clause in sample.clauses}) == 4
    assert sample.clauses[0].category == "尺寸测量报告覆盖性检查"
    assert "实测值的列数是否是3或3的倍数" in sample.clauses[0].text
    assert "气泡图独立文件" in sample.clauses[3].text
    assert sample.clauses[0].id != sample.clauses[3].id


def test_control_plan_customer_rules_preserve_actual_differences():
    uaes = preset_rule("UAES", "U08")
    li_auto = preset_rule("理想", "L08")
    nio = preset_rule("蔚来", "N03")
    assert "PGQP1" in "\n".join(clause.text for clause in uaes.clauses)
    assert "PGQP1" not in "\n".join(clause.text for clause in li_auto.clauses)
    assert "特殊特性清单" in "\n".join(clause.text for clause in nio.clauses)
    assert preset_rule("UAES", "U12").source_type == "T12"
    assert preset_rule("理想", "L12").source_type == "T12"
    assert preset_rule("蔚来", "N04").source_type == "T12"


def test_sources_without_summary_column_keep_the_rule_not_the_example():
    materials = preset_rule("理想", "L04")
    assert "单件重量" in materials.clauses[0].text
    assert "材料执行标准" in materials.clauses[1].text
    assert "文件中包含印章" in preset_rule("理想", "L03").clauses[3].text


def test_customer_resolver_never_borrows_another_customer_identity():
    from audit.checker_lib.nio.process_special_characteristic_agent import ProcessSpecialCharacteristicAgent
    from audit.engine.routing import resolve_engine_agent_class
    from audit.kinds import PpapDocumentType

    kind = PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST
    assert resolve_engine_agent_class(kind, "source.xlsx", "v4", preset_key="N04", preset_customer="蔚来") is ProcessSpecialCharacteristicAgent
    with pytest.raises(ValueError, match="unknown_customer_rule"):
        resolve_engine_agent_class(kind, "source.xlsx", "v4", preset_key="N04", preset_customer="UAES")
    with pytest.raises(ValueError, match="preset_document_type_mismatch"):
        resolve_engine_agent_class(PpapDocumentType.CONTROL_PLAN, "source.xlsx", "v4", preset_key="N04", preset_customer="蔚来")


def test_missing_v3_overlay_does_not_select_v2(monkeypatch):
    from audit.checker_lib.li.control_plan_agent import ControlPlanAgent
    from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent
    from audit.engine import routing
    from audit.kinds import PpapDocumentType

    kind = PpapDocumentType.CONTROL_PLAN
    monkeypatch.delitem(routing.V3_OVERLAY_EXCEL_AGENTS, kind)
    assert routing.resolve_engine_agent_class(kind, "control.xlsx", "v3") is None
    assert routing.resolve_engine_agent_class(kind, "control.xlsx", "v2") is ControlPlanAgent
    assert routing.resolve_engine_agent_class(kind, "control.xlsx", "v4", preset_key="U08", preset_customer="UAES") is UaesControlPlanAgent
