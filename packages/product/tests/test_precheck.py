import re

from product.precheck import precheck_task


def file(id_, name, *, validation="valid", reasons=(), state="matched", gaps=(), doc_type="控制计划", part_number="P1", part="壳体", selection="selected", **extra):
    return {
        "file": {"id": id_, "original_filename": name},
        "selection": selection,
        "validation": {"state": validation, "reasons": list(reasons)},
        "match": {"state": state, "gaps": list(gaps), "document_type": doc_type, "metadata": {"customer": "UAES", "part_number": part_number, "part": part}},
        **extra,
    }


def test_each_file_gets_one_level_and_blocks_fail_the_batch():
    task = {
        "files": [
            file("ok", "PSW.xlsx", doc_type="零件提交保证书"),
            file("locked", "加密.xlsx", validation="invalid", reasons=["文件加密或异常，无法识别"], state=None),
            file("who", "未知.pdf", state="unmatched", gaps=["matching_customer_required"]),
            file("norule", "8D.pdf", state="unmatched", gaps=["matching_document_type_rules_missing"]),
            file("scan", "流程图.pdf", doc_type="过程流程图", gaps=["empty_document_text"], part_number=""),
            file("ref", "图纸.pdf", state="reference", reference_only=True),
            file("off", "旧.xlsx", selection="removed"),
        ]
    }
    result = precheck_task(task)
    levels = {row["file_version_id"]: row["level"] for row in result["files"]}
    assert levels == {"ok": "ok", "locked": "block", "who": "block", "norule": "unmatched", "scan": "warn", "ref": "reference"}
    assert [item["code"] for item in result["blocks"]] == ["B01", "B03"]
    assert sorted(item["code"] for item in result["warnings"]) == ["W02", "W05"]
    assert [row["code"] for row in result["statuses"]] == ["S02"]
    assert result["conclusion"] == "fail"
    assert result["counts"] == {"ok": 1, "warn": 1, "block": 2, "unmatched": 1, "reference": 1, "pending": 0}


def test_two_drafts_of_one_document_have_no_single_valid_version_unless_one_replaces_the_other():
    drafts = [file("a", "控制计划-底护板-V1.6.xlsx"), file("b", "控制计划-底护板-V1.7.xlsx"), file("c", "MSA-1-卡尺.xls", doc_type="MSA"), file("d", "MSA-2-CMM.xls", doc_type="MSA")]
    result = precheck_task({"files": drafts})
    assert {item["file_version_id"] for item in result["blocks"]} == {"a", "b"}
    assert all(item["code"] == "B02" for item in result["blocks"])
    replaced = [file("a", "控制计划-底护板-V1.6.xlsx"), file("b", "控制计划-底护板-V1.7.xlsx", replacement_of_file_version_id="a")]
    assert precheck_task({"files": replaced})["conclusion"] == "pass"


def test_unfinished_recognition_is_not_run_rather_than_passed():
    result = precheck_task({"files": [file("a", "PFMEA.xlsx", state="matching")]})
    assert result["conclusion"] == "not_run"
    assert result["statuses"][0]["code"] == "未执行"
    assert precheck_task({"files": []})["conclusion"] == "not_run"


def _status(result, code):
    return [item for item in result["statuses"] if item["code"] == code]


def test_a_type_outside_the_customer_set_is_out_of_scope_and_only_a_type_inside_without_a_rule_is_not_adapted():
    result = precheck_task(
        {
            "files": [
                file("gauge", "检具验收报告.xlsx", state="unmatched", gaps=["matching_document_type_rules_missing"], doc_type="检具验收报告"),
                file("pgqp", "PGQP1.xlsx", state="reference", doc_type="E12"),
                file("cp", "控制计划.xlsx", state="unmatched", gaps=["matching_customer_rules_missing"], doc_type="控制计划"),
            ]
        }
    )
    assert {item["file_version_id"] for item in _status(result, "S01")} == {"gauge", "pgqp"}
    assert [item["file_version_id"] for item in _status(result, "S02")] == ["cp"]
    levels = {row["file_version_id"]: row["level"] for row in result["files"]}
    assert levels == {"gauge": "unmatched", "pgqp": "reference", "cp": "unmatched"}


def test_a_missing_counterpart_is_s04_with_its_w01_and_a_mismatched_one_is_s07_with_its_facts():
    result = precheck_task(
        {
            "files": [
                file("pfmea", "PFMEA.xlsx", doc_type="潜在过程失效模式及后果分析", part_number="P1"),
                file("list", "特殊特性清单.xlsx", doc_type="过程特殊特性清单", part_number="P1"),
                file("cpk", "过程能力.xlsx", doc_type="过程能力研究", part_number="P1"),
                file("pgqp", "PGQP1.xlsx", state="reference", reference_only=True, doc_type="E12", part_number="P2"),
            ]
        }
    )
    # PFMEA has its 特殊特性清单; its only PGQP1 belongs to no submitted part but names another one.
    assert [(item["file_version_id"], item["code"]) for item in result["statuses"]] == [("pfmea", "S07"), ("cpk", "S07")]
    assert _status(result, "S07")[1]["source_mismatch"] == {
        "document": "采购件质量计划（PGQP1）",
        "document_type": "E12",
        "identity": "零件号",
        "value": "P1",
        "found_value": "P2",
        "found_file_version_id": "pgqp",
        "found_filename": "PGQP1.xlsx",
    }
    # S07 never blocks or warns the file it belongs to.
    assert result["warnings"] == [] and result["conclusion"] == "pass"

    alone = precheck_task({"files": [file("cpk", "过程能力.xlsx", doc_type="过程能力研究", part_number="P1")]})
    assert [item["code"] for item in alone["statuses"]] == ["S04"]
    assert alone["statuses"][0]["missing"] == {"document": "采购件质量计划（PGQP1）", "document_type": "E12", "part_number": "P1"}
    assert [item["code"] for item in alone["warnings"]] == ["W01"]
    assert not _status(alone, "S07")


def test_a_counterpart_of_another_submitted_part_is_not_associated_and_an_unreadable_one_decides_nothing():
    batch = [
        file("a", "过程能力-A.xlsx", doc_type="过程能力研究", part_number="P1"),
        file("b", "过程能力-B.xlsx", doc_type="过程能力研究", part_number="P2"),
        file("pgqp", "PGQP1-B.xlsx", state="reference", reference_only=True, doc_type="E12", part_number="P2"),
    ]
    result = precheck_task({"files": batch})
    assert [(item["file_version_id"], item["code"]) for item in result["statuses"]] == [("a", "S04")]

    def pgqp(part):
        return file("pgqp", "PGQP1.xlsx", state="reference", reference_only=True, doc_type="E12", part_number=part)

    unreadable = precheck_task({"files": [file("a", "过程能力.xlsx", doc_type="过程能力研究", part_number="P1"), pgqp("待填")]})
    assert unreadable["statuses"] == [] and unreadable["warnings"] == []
    no_part = precheck_task({"files": [file("a", "过程能力.xlsx", doc_type="过程能力研究", part_number=""), pgqp("P2")]})
    assert not _status(no_part, "S04") and not _status(no_part, "S07")
    # An untyped file could be the counterpart: the missing counterpart is not established.
    untyped = precheck_task({"files": [file("a", "过程能力.xlsx", doc_type="过程能力研究"), file("x", "附件.pdf", state="failed", gaps=["document_classification_failed"], doc_type=None)]})
    assert not _status(untyped, "S04")


def test_the_counterpart_graph_is_the_one_ppap_00_a_registers():
    from pathlib import Path

    from product.precheck import PEERS

    text = (Path(__file__).resolve().parents[3] / "assets/uaes/prechecks/PPAP-00-A_客户与文件类型覆盖矩阵_路由表_v1.7.md").read_text(encoding="utf-8")
    section = text.split("### 4.1 同批关联图", 1)[1].split("### 4.2", 1)[0]
    for (customer, target), peers in PEERS.items():
        row = next(line for line in section.splitlines() if line.startswith(f"| {customer} |") and f"`{target}`" in line.split("|")[2])
        assert set(peers) == set(re.findall(r"`([TE]\d{2})`", row.split("|")[3])), (customer, target)


def test_a_file_without_a_recognized_customer_routes_by_the_task_customer():
    row = file("cp", "控制计划-776.xlsx")
    row["match"]["metadata"]["customer"] = None
    assert "S02" not in [item["code"] for item in precheck_task({"customer_preference": "UAES", "files": [row]})["statuses"]]
    assert [item["code"] for item in precheck_task({"customer_preference": None, "files": [row]})["statuses"]] == ["S02"]
