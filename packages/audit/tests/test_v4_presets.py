"""Independent V4 source identities; these tests do not certify Checker coverage."""

import pytest
from audit.kinds import PpapDocumentType
from audit.presets import preset_rule, preset_rule_body


def test_preset_matrix_uses_customer_identity_not_legacy_filename_numbers():
    assert (preset_rule("UAES", "U16").source_type, preset_rule("UAES", "U16").level) == ("T09", "L2")
    assert (preset_rule("理想", "L07").source_type, preset_rule("理想", "L07").level) == ("T10", "L1")
    assert preset_rule("蔚来", "N01").source_type == "T01a"
    # The dictionary code changed; the rule's document type and so its stored body did not.
    assert preset_rule_body("蔚来", "N01")["type"] == preset_rule_body("理想", "L01")["type"] == "全功能检验报告"
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


@pytest.mark.parametrize(
    ("rule_type", "expected"),
    [
        ("控制计划", PpapDocumentType.CONTROL_PLAN),
        ("Control plan", PpapDocumentType.CONTROL_PLAN),
        ("PFMEA", PpapDocumentType.PFMEA),
        ("全功能检验报告", PpapDocumentType.DIMENSION_MEASUREMENT_REPORT),
        ("尺寸检验报告", PpapDocumentType.DIMENSION_MEASUREMENT_REPORT),
        ("特殊特性清单", PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST),
        ("检具认可报告", PpapDocumentType.CHECKING_FIXTURE_APPROVAL_REPORT),
        ("测量系统分析报告", PpapDocumentType.MSA_REPORT),
        ("CP", PpapDocumentType.CONTROL_PLAN),
        ("not-exists", PpapDocumentType.UNKNOWN),
        ("", PpapDocumentType.UNKNOWN),
    ],
)
def test_document_type_names_and_aliases(rule_type: str, expected: PpapDocumentType) -> None:
    assert PpapDocumentType.from_string(rule_type) is expected
