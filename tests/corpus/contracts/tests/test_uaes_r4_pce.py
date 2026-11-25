"""R4 capability clauses exercised via the frozen candidate, not its reading helpers."""

import json

import pytest
from corpus_contracts.uaes_r4_fixture import UAES, book, check


def report(index=1.67, spec=(10, 0.2, -0.1)):
    rows = [["名义值", spec[0], "上公差", spec[1], "下公差", spec[2]], ["DESCRIPTIVE STATISTICS", "VALUES"], ["Number of readings", 125], ["Process Capability (Cpk)", 1.33]]
    if index is not None:
        rows.append(["Performance index (Ppk)", index])
    return rows


def directory(name, proc="√", family="KIT-01", part="P-0007"):
    return [["零件号码", part], ["总报告编号", family], ["特性名称", "过程", "规格"], [name, proc, "尺寸"]]


def plan(path, rows):
    return book(path, {"PGQP1": [["Part No/零件号:", "P-0007"], *rows]})


@pytest.mark.parametrize("ppk,status,reason", [(None, "NG", "没有给出Ppk"), (1.66, "NG", "低于1.67"), (1.67, "PASS", "不低于1.67"), ("=NA()", "UNABLE", "公式"), ("不清", "UNABLE", "不是数值")])
def test_per_report_missing_vs_uncertain_or_low(tmp_path, ppk, status, reason):
    p = book(tmp_path / "study.xlsx", {"甲": report(1.9), "乙": report(ppk)})
    out = check(p, "U06", "PCE:2")
    assert out["status"] == status and reason in out["suggestions"], out


@pytest.mark.parametrize("pg,comment,status", [(None, None, "PASS"), (2.0, None, "NG"), (2.0, "增加巡检", "PASS")])
def test_unnamed_summary_targets_and_action(tmp_path, pg, comment, status):
    path = book(tmp_path / "c.xlsx", {"质量数据": [["零件号码", "P-0007"], ["特性名称", "目标(CPK)", "结果(CPK)", "目标(PPK)", "结果(PPK)", "备注"], ["同轴度", 1.33, 1.33, 1.67, 1.67, comment]]})
    peers = {} if pg is None else {"E12": [plan(tmp_path / "q.xlsx", [["特性名称", "目标(PPK)"], ["同轴度", pg]])]}
    out = check(path, "U06", "PCE:3", peers)
    assert out["status"] == status, out


@pytest.mark.parametrize("part,family,status", [("P-0007", "KIT-01", "PASS"), ("P-0007", "KIT-02", "NG"), ("P-0008", "KIT-01", "NG"), ("P-0007", None, "UNABLE")])
def test_only_identified_sibling_directories_combine(tmp_path, part, family, status):
    p = book(tmp_path / "a.xlsx", {"目次": directory("气压")})
    b = book(tmp_path / "b.xlsx", {"CONTENTS": directory("深度", family=family, part=part)})
    q = plan(tmp_path / "q.xlsx", [["特性名称", "特殊特性"], ["气压", "SC"], ["深度", "√"]])
    out = check(p, "U06", "PCE:4", {"E12": [q], "过程能力研究": [b]})
    assert out["status"] == status, out


@pytest.mark.parametrize("real_study,status", [(True, "PASS"), (False, "NG")])
def test_study_required_for_quantitative_not_visual(tmp_path, real_study, status):
    entries = directory("压力") + [["印痕", "√", "目视"]]
    path = book(tmp_path / "c.xlsx", {"目录": entries, **({"压力": report()} if real_study else {})})
    q = plan(tmp_path / "q.xlsx", [["过程特性", "特性类型"], ["压力", "定量"], ["印痕", "定性"]])
    out = check(path, "U06", "PCE:5", {"E12": [q]})
    assert out["status"] == status, out


def test_directory_pooling_does_not_merge_research_pages(tmp_path):
    p = book(tmp_path / "a.xlsx", {"目录": directory("温度"), "温度": report()})
    b = book(tmp_path / "b.xlsx", {"目次": directory("深度"), "深度": report()})
    q = plan(tmp_path / "q.xlsx", [["过程特性"], ["温度"], ["深度"]])
    out = check(p, "U06", "PCE:5", {"E12": [q], "过程能力研究": [b]})
    assert out["status"] == "UNABLE" and "跨册" in out["suggestions"], out


@pytest.mark.parametrize("dim,expected", [(("⌀10", 0.2, -0.1), "PASS"), ((10.1, 0.1, -0.2), "NG"), ((None, 10.2, None), "UNABLE")])
def test_dimension_components_not_derived_limits(tmp_path, dim, expected):
    p = book(tmp_path / "s.xlsx", {"目录": directory("孔径"), "孔径": report()})
    d = book(tmp_path / "d.xlsx", {"图纸全部特殊特性": [["零件号码", "P-0007"], ["特性名称", "类型", "名义值", "上公差", "下公差"], ["孔径", "定量", *dim], ["外观", "定性"]]})
    out = check(p, "U06", "PCE:6", {"图纸": [d]})
    assert out["status"] == expected, out


@pytest.mark.parametrize(
    "part,pattern,clause,status",
    [
        ("*776*", "776-机加CPK.xlsx", "PCE:3", "PASS"),
        ("*A40*", "*test*.xlsx", "PCE:3", "NG"),
        ("*V040*", "*.xlsx", "PCE:2", "NG"),
        ("*V040*", "*.xlsx", "PCE:6", "NA"),
    ],
)
def test_original_workbook_blind_checks(part, pattern, clause, status):
    root = next((UAES / "samples").glob(part))
    p = next(root.glob(f"*/07 */{pattern}"))
    out = check(p, "U06", clause)
    assert out["status"] == status, out


def test_one_named_report_must_not_hide_sibling_statistics(tmp_path):
    p = book(tmp_path / "c.xlsx", {"能力认证报告": report(), "另一工序": report(1.1)})
    out = check(p, "U06", "PCE:2")
    assert out["status"] != "PASS", out


def test_visual_directory_cannot_exempt_quantified_plan(tmp_path):
    p = book(tmp_path / "s.xlsx", {"目录": directory("压力")[:3] + [["压力", "√", "目视"]]})
    q = plan(tmp_path / "q.xlsx", [["过程特性", "特性类型"], ["压力", "定量"]])
    out = check(p, "U06", "PCE:5", {"E12": [q]})
    assert out["status"] == "NG", out


def test_explicit_positive_and_negative_deviations_match_plus_minus(tmp_path):
    p = book(tmp_path / "s.xlsx", {"目录": directory("孔径"), "孔径": [["尺寸规格", "10+0.1/-0.1"], *report()[1:]]})
    d = book(tmp_path / "d.xlsx", {"图纸全部特殊特性": [["零件号码", "P-0007"], ["特性名称", "类型", "图纸规格"], ["孔径", "定量", "⌀10±0.1"]]})
    out = check(p, "U06", "PCE:6", {"图纸": [d]})
    assert out["status"] == "PASS", out


def test_real_776_scope_requires_pgqp1_mark_or_parameter_evidence():
    sources = UAES / "samples/F01R10Y776_机加壳体"
    target = next(sources.glob("测试文件/07 */776-机加CPK.xlsx"))
    sibling = next(sources.glob("测试文件/07 */776-毛坯CPK.xlsx"))
    plan_file = next(sources.glob("比对文件/采购件质量计划/*.xlsx"))
    refs = {"过程能力研究": [sibling], "E12": [plan_file]}
    special = check(target, "U06", "PCE:4", refs)
    process = check(target, "U06", "PCE:5", refs)
    # The yellow fill in the PGQP1 SC column marks the special characteristics (product owner, 2026-11-24): the marked
    # set is read and compared with the catalogues, never left open over the fill.
    assert special["status"] in {"PASS", "NG"} and "填色" not in special["suggestions"], special
    assert process["status"] == "NG" and "D27" in process["suggestions"] and "铸造压力" in process["suggestions"]


def test_known_low_ppk_in_named_summary_not_hidden_by_absent_cpk(tmp_path):
    target = book(tmp_path / "p.xlsx", {"能力值汇总": [["特性名称", "目标(PPK)", "结果(PPK)", "备注"], ["直径", 1.67, 1.2]]})
    finding = check(target, "U06", "PCE:3")
    assert finding["status"] == "NG" and "低于目标" in finding["suggestions"]


def test_study_characteristic_and_combined_spec_are_bound_to_the_cited_cells(tmp_path):
    p = book(tmp_path / "delivery.xlsx", {"目次": directory("衬套外径"), "供应商页签047": [[None]] * 34 + [["特性名称", "衬套外径"], [None, None, "规格", "15±0.04"], *report()[1:]]})
    d = book(tmp_path / "drawing.xlsx", {"图纸全部特殊特性": [["零件号码", "P-0007"], ["特性名称", "类型", "图纸规格"], ["衬套外径", "定量", "⌀15+0.04/-0.04"]]})
    result = check(p, "U06", "PCE:6", {"图纸": [d]})
    assert result["status"] == "PASS", result
    locations = {json.loads(s["locator"]).get("cell") for s in result["evidence"]["sources"]}
    assert "D36" in locations
