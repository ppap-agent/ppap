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
            file("ok", "PFMEA.xlsx", doc_type="PFMEA"),
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
