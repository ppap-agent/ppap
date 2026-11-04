"""Independent PFMEA preparation inputs exercise source rules, not formal NG copies."""

from __future__ import annotations

import pytest
from audit.contracts import AuditInput
from audit.precheck_facts import UnknownFact
from audit.precheck_pfmea import control_scores, pfmea_precheck
from openpyxl import Workbook


def original(tmp_path, *, detection=2, prevention="预防措施（2）", control="目视检查（6）", severity=7, characteristic="CC", formula=False, fourth=False):
    book = Workbook()
    sheet = book.active
    sheet.title = "PFMEA"
    for row in [
        ["公司名称", "实际供方"],
        ["车型项目", "M1"],
        ["零件名称", "零件A"],
        ["零件号", "P-1"],
        ["制造地址", "实际工厂"],
        ["PFMEA开始日期", "2025-01-01"],
        ["PFMEA修订日期", "2025-02-01"],
        ["跨职能团队", "制造、质量"],
    ]:
        sheet.append(row)
    headers = ["过程项", "过程步骤", "过程作业要素", "过程项功能", "失效影响", "严重度", "失效模式", "发生度", "失效起因", "现行预防控制", "现行探测控制", "探测度", "AP", "特殊特性"]
    if fourth:
        headers[3] = "功能要求"
        headers[7] = "频度"
        headers[12] = "RPN"
    sheet.append(headers)
    for _ in range(12):
        sheet.append(
            [
                "过程项A",
                "OP10 加工",
                "设备A",
                "S:完成加工；O:供主机厂装配；C:完成使用",
                "S:设备停机（3）；O:不能装配（7）；C:影响使用（5）",
                severity,
                "尺寸超差",
                3,
                "刀具磨损",
                prevention,
                control,
                "=1+1" if formula else detection,
                7 * 3 * detection if fourth else "H",
                characteristic,
            ]
        )
    history = book.create_sheet("变更履历")
    history.append(["版本", "修订日期", "变更说明"])
    history.append(["A", "2025-01-01", "初版"])
    history.append(["B", "2025-02-01", "修订"])
    path = tmp_path / "source.xlsx"
    book.save(path)
    book.close()
    return AuditInput(audit_id="frozen-file-version", file_url=str(path))


def by_id(audit, customer="N02"):
    return {fact["id"].split(":", 1)[1]: fact for fact in pfmea_precheck(audit, customer)}


def test_precheck_r09_uses_prevention_and_detection_while_r08_stays_disabled(tmp_path):
    observed = by_id(original(tmp_path, detection=2))
    assert observed["R09"]["status"] == "satisfied"
    assert observed["R08"]["status"] == "not_checked" and observed["R08"]["code"] == "S02"
    assert observed["R06"]["status"] == "satisfied"
    assert observed["R12"]["status"] == "satisfied"
    assert "R13" not in observed and "R14" not in observed
    assert observed["R10"]["status"] == "not_checked" and observed["R10"]["code"] is None
    assert observed["R09"]["evidence"]["sources"]
    assert all("frozen-file-version" in source["locator"] for source in observed["R09"]["evidence"]["sources"])
    bad = by_id(original(tmp_path, detection=6))
    assert bad["R09"]["status"] == "defect" and bad["R09"]["code"] == "B06"
    assert bad["R09"]["certified"] is False


def test_customer_overlay_does_not_impose_li_auto_fields_or_minimum_on_uaes(tmp_path):
    observed = by_id(original(tmp_path, detection=6), "U07")
    for identifier in ("R02", "R04", "R05", "R06", "R09"):
        assert observed[identifier]["status"] == "not_checked" and observed[identifier]["code"] == "S02"
    assert observed["R12"]["status"] == "satisfied"
    assert observed["R17"]["code"] == "S02"


def test_uncached_formula_and_empty_scope_cannot_become_pass(tmp_path):
    observed = by_id(original(tmp_path, formula=True))
    assert observed["R09"]["status"] == "unknown"
    assert observed["R12"]["status"] == "unknown"
    path = tmp_path / "blank.xlsx"
    book = Workbook()
    book.active.append(["PFMEA", "严重度", "发生度", "探测度"])
    book.save(path)
    book.close()
    blank = by_id(AuditInput(audit_id="blank-version", file_url=str(path)))
    assert blank["R08"]["code"] == "S02"
    assert blank["R12"]["status"] == "unknown"


def test_fourth_edition_disables_score_suffix_and_checks_rpn(tmp_path):
    observed = by_id(original(tmp_path, fourth=True, prevention="预防措施，无独立分值"))
    assert observed["R09"]["status"] == "not_checked" and observed["R09"]["code"] == "S02"
    assert observed["R06"]["code"] == "S02"
    assert observed["R12"]["status"] == "satisfied"


@pytest.mark.parametrize("value", ["OP20", "目视检查100%", "检查2025-01-01", "第3步", "目视(6) 人工(5)", "100", "检查4M", "检查5件"])
def test_measure_scores_never_use_identifiers_dates_quantities_or_partial_measures(value):
    with pytest.raises(UnknownFact):
        control_scores(value)


def test_all_explicit_measure_suffixes_are_used():
    assert control_scores("预防措施（2）\n防错措施[4]") == [2, 4]
    assert control_scores("人工检查6；自动检查(3)") == [6, 3]


def test_original_definite_math_conflict_and_symbol_warning_are_not_technical_errors(tmp_path):
    observed = by_id(original(tmp_path, severity=6, characteristic="3"))
    assert observed["R06"]["status"] == "defect" and observed["R06"]["code"] == "B06"
    assert observed["R11"]["status"] == "defect" and observed["R11"]["code"] == "W03"
    assert observed["R11"]["critical"] is False


def peer_original(tmp_path, *, part="P-1", symbol="CC", operation="加工"):
    path = tmp_path / "pfd.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "过程流程图"
    sheet.append(["零件号", part])
    sheet.append(["项目", "M1"])
    sheet.append(["工序号", "工序名称", "产品特性类别", "过程特性类别"])
    sheet.append(["OP10", operation, symbol, ""])
    book.save(path)
    book.close()
    return AuditInput(audit_id="independent-pfd-version", file_url=str(path))


def with_peer(audit, peer, customer="N02"):
    from audit.contracts import GroupContextInput
    from audit.kinds import PpapDocumentType

    group = GroupContextInput(by_doc_type={PpapDocumentType.PROCESS_FLOW_CHART.get_type_name(): [peer]}, by_audit_id={peer.audit_id: peer})
    return {fact["id"].split(":", 1)[1]: fact for fact in pfmea_precheck(audit, customer, group)}


def test_real_dual_original_process_and_characteristic_check(tmp_path):
    audit = original(tmp_path)
    result = with_peer(audit, peer_original(tmp_path))
    assert result["R17"]["status"] == result["R18"]["status"] == "satisfied"
    for identifier in ("R17", "R18"):
        versions = {__import__("json").loads(source["locator"])["file_version_id"] for source in result[identifier]["evidence"]["sources"]}
        assert versions == {"frozen-file-version", "independent-pfd-version"}
    conflict = with_peer(audit, peer_original(tmp_path, operation="装配"))
    assert conflict["R17"]["status"] == "defect" and conflict["R17"]["code"] == "B06"
    mark_conflict = with_peer(audit, peer_original(tmp_path, symbol="SC"))
    assert mark_conflict["R18"]["status"] == "defect" and mark_conflict["R18"]["code"] == "B06"


def test_pair_does_not_merge_distinct_part_punctuation_and_missing_is_not_defect(tmp_path):
    audit = original(tmp_path)
    result = with_peer(audit, peer_original(tmp_path, part="P1"))
    assert result["R17"]["status"] == result["R18"]["status"] == "unknown"
    missing = by_id(audit)
    assert missing["R17"]["code"] == "S04" and missing["R17"]["status"] == "not_checked"


def test_li_auto_g_equals_cc_only_after_actual_pair_binding(tmp_path):
    result = with_peer(original(tmp_path, characteristic="G"), peer_original(tmp_path, symbol="CC"), "L07")
    assert result["R18"]["status"] == "satisfied"


def test_explicit_version_or_customer_conflict_prevents_cross_file_ng(tmp_path):
    from openpyxl import load_workbook

    audit = original(tmp_path)
    peer = peer_original(tmp_path, operation="与本体不同的工序")
    book = load_workbook(audit.file_url)
    book.active.insert_rows(9)
    book.active.cell(9, 1, "版本")
    book.active.cell(9, 2, "B")
    book.save(audit.file_url)
    book.close()
    book = load_workbook(peer.file_url)
    book.active.insert_rows(2)
    book.active.cell(2, 1, "版本")
    book.active.cell(2, 2, "A")
    book.save(peer.file_url)
    book.close()
    result = with_peer(audit, peer)
    assert result["R17"]["status"] == "unknown" and result["R17"]["code"] is None
