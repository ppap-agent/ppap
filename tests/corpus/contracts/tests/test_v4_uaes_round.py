"""UAES source-specific decisions through frozen, real Round execution."""

import io

import pytest
from audit.audits import RoundInput, RoundWork
from audit.presets import preset_rule_body
from corpus_contracts.core_round import customer_suite, run_suite_round
from openpyxl import Workbook


def workbook(sheets):
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    content = io.BytesIO()
    book.save(content)
    book.close()
    return content.getvalue()


def execute(entry, sheets):
    body = preset_rule_body("UAES", entry)
    target = RoundInput("target", "file", "version", "原件.xlsx", "ready", rule_version_id="uaes-" + entry, rule_body=body)
    result = run_suite_round(RoundWork("round", "tenant", [target]), {"version": workbook(sheets)}, suites={"version": customer_suite("UAES", entry)})
    assert all(item["coverage"] == "executed" for item in result["coverage"]["rule_items"]), result
    return result["findings"]


@pytest.mark.parametrize(("cpk", "expected"), [(1.34, "PASS"), (1.33, "NG"), ("=1+1", "UNABLE")])
def test_capability_strict_threshold_and_uncalculated_formula(cpk, expected):
    findings = execute("U06", {"工艺能力认证报告": [["读数次数", 125], ["Cpk", cpk], ["Ppk", 1.68]]})
    assert findings[0]["status"] == expected, findings
    assert findings[1]["status"] == "PASS"
    assert findings[0]["locator"]["semantic"]["sources"]


@pytest.mark.parametrize(("ppk", "expected"), [(1.68, "PASS"), (1.67, "NG")])
def test_capability_ppk_strict_threshold(ppk, expected):
    findings = execute("U06", {"工艺能力认证报告": [["读数次数", 125], ["Cpk", 1.5], ["Ppk", ppk]]})
    assert findings[1]["status"] == expected


@pytest.mark.parametrize(("reaction", "expected"), [(None, "NG"), ("增加抽样并整改", "PASS")])
def test_capability_summary_requires_reaction_for_low_result(reaction, expected):
    findings = execute("U06", {"能力值汇总": [["CPK", "PPK", "CPK目标", "PPK目标", "反应计划"], [1.0, 1.7, 1.33, 1.67, reaction]]})
    assert findings[2]["status"] == expected, findings


def test_cp_missing_resource_is_ng_but_unknown_fifo_never_passes():
    findings = execute("U08", {"控制计划": [["工序名称", "生产资源", "特殊特性"], ["成型", None, "CC"]]})
    assert findings[0]["status"] == "NG"
    assert findings[1]["status"] == "UNABLE"
    assert findings[3]["status"] == "PASS"


def test_pfmea_integer_failure_does_not_require_visual_guess():
    rows = [["失效模式", "失效原因", "失效后果", "预防控制", "探测控制", "S", "O", "D"], ["断裂", "材料", "安全风险", "材料验证", "无", 3, 2, 5]]
    findings = execute("U07", {"PFMEA": rows})
    assert findings[1]["status"] == "NG", findings


def test_msa_plan_checks_all_non_remark_fields():
    findings = execute("U05", {"MSA计划表": [["序号", "量具名称", "量具编号", "备注"], [1, "卡尺", None, None]]})
    assert findings[0]["status"] == "NG", findings


def test_material_list_checks_non_remark_fields():
    findings = execute("U04", {"材料及分供方清单": [["序号", "材料", "分供方", "备注"], [1, "钢材", None, None]]})
    assert findings[0]["status"] == "NG", findings


def test_source_bindings_cover_every_uaes_source_clause():
    from audit.checkers.v4.uaes import CLASS_BY_ENTRY
    from audit.checkers.v4.uaes.bindings import BINDINGS
    from audit.engine.checkers.rule_declarations import discover_declared_rules

    assert set(CLASS_BY_ENTRY) == {f"U{index:02d}" for index in range(1, 20)}
    for entry, cls in CLASS_BY_ENTRY.items():
        names = {name for name, _ in discover_declared_rules(cls)}
        for clause in preset_rule_body("UAES", entry)["rules"]:
            assert BINDINGS["v4"][clause["id"]] in names
        assert cls.SUPPORTED_EXTENSIONS


def test_u12_uncalculated_formula_and_merged_number_never_hide_physical_row():
    book = Workbook()
    sheet = book.active
    sheet.title = "特殊特性清单"
    sheet.append(["物料名称", "支架"])
    sheet.append(["物料编号", "P12345"])
    sheet.append(["编号", "产品特性", "特性及公差", "特性类型"])
    sheet.append([1, "孔径", "10±0.1", "CC"])
    sheet.append([None, "高度", None, "SC"])
    sheet.merge_cells("A4:A5")
    content = io.BytesIO()
    book.save(content)
    body = preset_rule_body("UAES", "U12")
    target = RoundInput("target", "file", "version", "特性.xlsx", "ready", rule_version_id="rules", rule_body=body)
    result = run_suite_round(RoundWork("round", "tenant", [target]), {"version": content.getvalue()}, suites={"version": customer_suite("UAES", "U12")})
    assert result["findings"][1]["status"] == "NG"
    sheet["C5"] = "=1+1"
    content = io.BytesIO()
    book.save(content)
    book.close()
    result = run_suite_round(RoundWork("round", "tenant", [target]), {"version": content.getvalue()}, suites={"version": customer_suite("UAES", "U12")})
    assert result["findings"][1]["status"] == "UNABLE"


@pytest.mark.parametrize("resources", [None, "另一压装机"])
def test_cp_unknown_process_anchor_is_never_skipped_as_a_repeated_header(resources):
    findings = execute("U08", {"控制计划": [["工序名称", "生产资源"], ["压装", "压装机"], ["=A2", resources]]})
    assert findings[0]["status"] == "UNABLE", findings[0]
