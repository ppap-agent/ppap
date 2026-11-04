"""Original offline templates exercise native readers, never act as passing goldens.

Source-clause business oracles remain in the customer owner tests. These cases
prove actual template execution and invariance under a supplier file rename.
"""

from pathlib import Path

import pytest
from audit.audits import RoundInput, RoundWork
from audit.document_tools import snapshot
from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
from audit.presets import customer_presets, preset_rule_body
from product import run_round_from_bytes

ROOT = Path(__file__).resolve().parents[4]
TEMPLATES = ROOT / "assets/PPAP PRD v3.7/客户归类模板与规则_备份/模板"


@pytest.mark.parametrize(
    ("customer", "entry", "filename"),
    [
        ("UAES", "U01", "F-PU-207_样品检验报告_Sample Inspection Report_V01-.xls (1).xls"),
        ("UAES", "U02", "F-PU-166_测量比较报告_Measure Comparison Report_V02.xls (1).xls"),
        ("UAES", "U04", "7. 材料及分供方清单模板 (1).xlsx"),
        ("UAES", "U17", "新-F-PU-206_零件提交保证书_PartSubmissionWarrant_V03 (1).xls"),
        ("UAES", "U18", "零件追溯检查单-模板 20250609.xlsx"),
        ("UAES", "U19", "F01RC0YA8601 样品认可报告 (1).xls"),
        ("蔚来", "N01", "1-2 尺寸测量报告.xlsm"),
        ("蔚来", "N02", "9 潜在过程失效模式及后果分析.xlsx"),
        ("蔚来", "N03", "10 控制计划.xlsx"),
        ("蔚来", "N04", "11 特殊特性清单.xlsx"),
        ("蔚来", "N05", "8 过程流程图.xlsx"),
    ],
)
def test_actual_customer_template_runs_with_original_and_supplier_filename(customer, entry, filename):
    source = TEMPLATES / customer / filename
    body = preset_rule_body(customer, entry)
    declaration = next(item for item in customer_presets() if item.entry_id == entry)
    source_ids = {clause.id for clause in declaration.clauses}
    previous = None
    for name in (filename, "供应商提交的原件" + source.suffix):
        execution = freeze_target_rule_execution(body, name)
        target = RoundInput("target", "file", "version", name, "ready", rule_version_id="frozen-rule", rule_body=body, rule_execution=execution)
        result = run_round_from_bytes(RoundWork("template-round", "tenant", [target]), {"version": source.read_bytes()})
        findings = result["findings"]
        assert len(findings) == len(source_ids)
        assert {row["item_key"].split(":rule:", 1)[1] for row in findings} == source_ids
        assert not result["coverage"]["unchecked"]
        assert not [row for row in findings if row["status"] == "ERR"], [(row["item_key"], row["suggestions"]) for row in findings]
        observed = {row["item_key"]: row["status"] for row in findings}
        if previous is not None:
            assert observed == previous, "Renaming a carrier cannot change customer clause decisions"
        previous = observed


def test_pgqp1_template_is_read_as_an_original_reference_without_a_42nd_rule():
    source = TEMPLATES / "UAES/F-PU-127_采购件质量计划_Purchased Goods Quality Plan PGQP1_V06 (1).xlsx"
    observed = snapshot(str(source))
    assert observed["tables"]
    assert "PGQP1" in str(observed)
    assert len(customer_presets()) == 41
    assert "E12" not in {item.source_type for item in customer_presets()}
