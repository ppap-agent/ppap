"""Offline original carriers prove execution coverage, never customer conformity."""

from pathlib import Path

import pytest
from audit.audits import RoundInput, RoundWork
from audit.presets import preset_rule, preset_rule_body
from corpus_contracts.core_round import customer_suite, run_suite_round

ROOT = Path(__file__).resolve().parents[4]
TEMPLATES = ROOT / "assets/理想/模板"
CASES = [
    ("L01", "APQP-尺寸检验报告.xlsx"),
    ("L02", "APQP-零部件设计验证计划.xlsx"),
    ("L04", "APQP-材料、产品及分供方清单.xlsx"),
    ("L05", "APQP-测量系统分析报告.xlsx"),
    ("L06", "APQP-过程能力分析报告.xlsx"),
    ("L06", "APQP-过程特殊特性清单.xlsx"),
    ("L07", "APQP-潜在过程失效模式及后果分析（PFMEA）.xlsx"),
    ("L08", "APQP-生产控制计划.xlsx"),
    ("L09", "APQP-检具认可报告.xlsx"),
    ("L10", "APQP-模具验收报告.xls"),
    ("L11", "APQP-过程流程图.xlsx"),
    ("L12", "APQP-产品过程CCSC特性管理清单.xlsx"),
    ("L13", "APQP-设备开发验收清单.xlsx"),
    ("L14", "APQP-产能评估表.xlsx"),
    ("L15", "APQP-项目问题清单.xlsx"),
    ("L16", "APQP-零部件年度试验要求.xlsx"),
    ("L17", "APQP-零件提交保证书（PSW签署）.xlsx"),
]


def test_all_original_carriers_are_explicit_and_do_not_invent_an_l03_template():
    assert len(CASES) == 17
    assert {name for _, name in CASES} == {path.name for path in TEMPLATES.iterdir() if path.suffix in {".xlsx", ".xls"}}
    assert "L03" not in {entry for entry, _ in CASES}
    # 00-A maps L03 to T03; the legacy source filename 02-2 is not its V4 type.
    assert preset_rule("理想", "L03").source_type == "T03"


@pytest.mark.parametrize(("entry", "name"), CASES, ids=[entry + ":" + name for entry, name in CASES])
def test_each_original_carrier_executes_the_frozen_customer_source_without_a_whole_group_error(entry, name):
    body = preset_rule_body("理想", entry)
    target = RoundInput("target", "file", "original-version", name, "ready", rule_version_id="li-" + entry, rule_body=body)
    result = run_suite_round(RoundWork("offline-round", "offline-tenant", [target]), {"original-version": (TEMPLATES / name).read_bytes()}, suites={"original-version": customer_suite("理想", entry)})
    findings = result["findings"]
    expected = {clause.id for clause in preset_rule("理想", entry).clauses}
    assert {row["item_key"].split(":rule:", 1)[1] for row in findings} == expected
    assert len(findings) == len(expected)
    assert all(row["coverage"] == "executed" for row in result["coverage"]["rule_items"])
    assert not [(row["status"], row["suggestions"]) for row in findings if row["status"] == "ERR"]
    assert all(row["locator"]["semantic"]["sources"] for row in findings if row["status"] == "PASS")
