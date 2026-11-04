"""Independent V4 source identities; these tests do not certify Checker coverage."""

from collections import Counter

import pytest
from audit.presets import customer_presets, preset_rule


def test_preset_matrix_uses_customer_identity_not_legacy_filename_numbers():
    assert Counter(rule.customer for rule in customer_presets()) == {"UAES": 19, "理想": 17, "蔚来": 5}
    assert Counter(rule.level for rule in customer_presets()) == {"L1": 19, "L2": 22}
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


def test_preset_execution_requires_source_identity_and_explicit_equivalence():
    from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
    from audit.presets import preset_rule_body

    nio = preset_rule_body("蔚来", "N04")
    frozen = freeze_target_rule_execution(nio, "特殊特性清单.xlsx")
    assert [row["coverage"] for row in frozen["items"]] == ["mapped", "mapped"]
    assert [row["checker_method"] for row in frozen["items"]] == ["rule_1", "rule_2"]
    uaes = freeze_target_rule_execution(preset_rule_body("UAES", "U07"), "PFMEA.xlsx")
    assert all(row["coverage"] == "mapped" for row in uaes["items"])
    assert [row["checker_method"] for row in uaes["items"]] == ["rule_1", "rule_2", "rule_3", "rule_4"]
    assert uaes["checker"]["class"] == "audit.checkers.v4.uaes.fmea.UaesPfmeaAgent"
    nio["rules"].reverse()
    assert [row["checker_method"] for row in freeze_target_rule_execution(nio, "特殊特性清单.xlsx")["items"]] == ["rule_2", "rule_1"]
    nio["rules"][0]["rule_to_check"] = "不同客户的条款"
    with pytest.raises(ValueError, match="invalid_preset_clauses"):
        freeze_target_rule_execution(nio, "特殊特性清单.xlsx")


def test_customer_resolver_never_borrows_another_customer_identity():
    from audit.checkers.v4.nio.special_characteristics import NioSpecialCharacteristicsAgent
    from audit.engine.routing import resolve_engine_agent_class
    from audit.kinds import PpapDocumentType

    kind = PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST
    assert resolve_engine_agent_class(kind, "source.xlsx", "v4", preset_key="N04", preset_customer="蔚来") is NioSpecialCharacteristicsAgent
    with pytest.raises(ValueError, match="unknown_customer_rule"):
        resolve_engine_agent_class(kind, "source.xlsx", "v4", preset_key="N04", preset_customer="UAES")
    with pytest.raises(ValueError, match="preset_document_type_mismatch"):
        resolve_engine_agent_class(PpapDocumentType.CONTROL_PLAN, "source.xlsx", "v4", preset_key="N04", preset_customer="蔚来")


def test_missing_v4_carrier_selects_generic_v3_without_falsely_mapping_customer_clauses():
    from audit.checkers.v3.equipment_list_docx_agent import EquipmentListDocxAgent
    from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
    from audit.engine.routing import resolve_engine_agent_class
    from audit.kinds import PpapDocumentType
    from audit.presets import preset_rule_body

    assert resolve_engine_agent_class(PpapDocumentType.EQUIPMENT_LIST, "equipment.docx", "v4", preset_key="L13", preset_customer="理想") is EquipmentListDocxAgent
    frozen = freeze_target_rule_execution(preset_rule_body("理想", "L13"), "equipment.docx")
    assert frozen["checker"]["profile"] == "v3"
    assert frozen["checker"]["class"] == "audit.checkers.v3.equipment_list_docx_agent.EquipmentListDocxAgent"
    assert all(row["coverage"] == "unsupported" for row in frozen["items"])


def test_missing_v4_and_v3_carrier_selects_v2_then_none_when_none_is_available(monkeypatch):
    from audit.checkers.v2.equipment_list_docx_agent import EquipmentListDocxAgent
    from audit.engine import routing
    from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
    from audit.kinds import PpapDocumentType
    from audit.presets import preset_rule_body

    kind = PpapDocumentType.EQUIPMENT_LIST
    monkeypatch.delitem(routing.V3_OVERLAY_DOCX_AGENTS, kind)
    assert routing.resolve_engine_agent_class(kind, "equipment.docx", "v4", preset_key="L13", preset_customer="理想") is EquipmentListDocxAgent
    monkeypatch.delitem(routing.V2_DOCX_AGENTS, kind)
    assert routing.resolve_engine_agent_class(kind, "equipment.docx", "v4", preset_key="L13", preset_customer="理想") is None
    frozen = freeze_target_rule_execution(preset_rule_body("理想", "L13"), "equipment.docx")
    assert frozen["checker"]["class"] is None and frozen["checker"]["profile"] is None
    assert all(row["coverage"] == "unsupported" for row in frozen["items"])


def test_registered_customer_checker_failure_does_not_select_a_generic_checker(monkeypatch):
    from audit.engine import routing
    from audit.kinds import PpapDocumentType

    class FailingChecker:
        def check(self):
            raise OSError("original unavailable")

    monkeypatch.setitem(routing.li_auto_checkers.EXCEL_AGENTS, "L13", FailingChecker)
    selected = routing.resolve_engine_agent_class(PpapDocumentType.EQUIPMENT_LIST, "equipment.xlsx", "v4", preset_key="L13", preset_customer="理想")
    assert selected is FailingChecker
    with pytest.raises(OSError, match="original unavailable"):
        selected().check()
