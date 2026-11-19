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


def test_copies_with_one_name_of_one_part_are_each_b02_and_drafts_with_different_names_are_not():
    """附件 R-03 (用户裁定): same part + the very same file name + two or more copies → every copy B02."""
    twins = [file("a", "A40-粗糙度仪-Cgk.xls", doc_type="MSA"), file("b", "A40-粗糙度仪-Cgk.xls", doc_type="MSA")]
    result = precheck_task({"files": twins})
    assert [(item["file_version_id"], item["code"]) for item in result["blocks"]] == [("a", "B02"), ("b", "B02")] and result["conclusion"] == "fail"
    # Another part's file of the same name, differently named drafts and an unreadable part number do not group.
    other = [file("a", "MSA.xls", doc_type="MSA"), file("b", "MSA.xls", doc_type="MSA", part_number="P2")]
    drafts = [file("a", "控制计划-V1.6.xlsx"), file("b", "控制计划-V1.7.xlsx")]
    unknown = [file("a", "MSA.xls", doc_type="MSA", part_number=""), file("b", "MSA.xls", doc_type="MSA", part_number="")]
    for batch in (other, drafts, unknown):
        assert not [item for item in precheck_task({"files": batch})["blocks"] if item["code"] == "B02"]
    replaced = [file("a", "控制计划.xlsx"), file("b", "控制计划.xlsx", replacement_of_file_version_id="a")]
    assert precheck_task({"files": replaced})["conclusion"] == "pass"


def test_the_b02_evidence_says_whether_the_copies_agree():
    import io

    from openpyxl import Workbook

    def book(value, *, title="Cgk"):
        workbook = Workbook()
        workbook.active.title = title
        workbook.active.append(["校准值", value])
        out = io.BytesIO()
        workbook.save(out)
        return out.getvalue()

    def evidence(first, second, digests=("x", "y")):
        rows = [file(id_, "Cgk.xlsx", doc_type="MSA") for id_ in ("a", "b")]
        for row, digest in zip(rows, digests):
            row["file"]["sha256"] = digest
        content = {"a": first, "b": second}
        result = precheck_task({"files": rows}, read=lambda row: content[row["file"]["id"]])
        assert {item["evidence"] for item in result["blocks"]} == {result["blocks"][0]["evidence"]}
        return result["blocks"][0]["evidence"]

    assert evidence(b"", b"", ("same", "same")) == "2 份同名文件「Cgk.xlsx」逐字节相同"
    assert evidence(book("1.6"), book("1.6")) == "2 份同名文件「Cgk.xlsx」为另存／重存副本：各工作表单元格取值逐格相同"
    assert evidence(book("1.6"), book("3.2")) == "2 份同名文件「Cgk.xlsx」为同一对象的两个版本，第 1 份与第 2 份共 1 处不同：Cgk!B1：1.6 → 3.2"
    assert "工作表「Cgk」只在第 1 份中" in evidence(book("1.6"), book("1.6", title="其他"))
    rows = [file(id_, "Cgk.xlsx", doc_type="MSA") for id_ in ("a", "b")]
    assert precheck_task({"files": rows})["blocks"][0]["evidence"] == "2 份同名文件「Cgk.xlsx」字节不同，内容未逐格比对"


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


def test_the_inspection_report_type_is_t01_for_li_and_t01a_for_nio_so_neither_is_out_of_scope():
    from product.precheck import type_code

    # PRD §1.5: one recognized type, two dictionary members by customer; UAES has neither in its M.
    assert (type_code("全功能检验报告", "理想"), type_code("全功能检验报告", "蔚来"), type_code("T01a")) == ("T01", "T01a", "T01a")
    rows = []
    for customer in ("理想", "蔚来", "UAES"):
        row = file(customer, f"{customer}-尺寸报告.xlsx", state="unmatched", gaps=["matching_document_type_rules_missing"], doc_type="全功能检验报告")
        row["match"]["metadata"]["customer"] = customer
        rows.append(row)
    result = precheck_task({"files": rows})
    assert [item["file_version_id"] for item in _status(result, "S01") if "scope" not in item] == ["UAES"]
    assert {item["file_version_id"] for item in _status(result, "S02") if "scope" not in item} == {"理想", "蔚来"}


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

    text = (Path(__file__).resolve().parents[3] / "assets/uaes/prechecks/PPAP-00-A_客户与文件类型覆盖矩阵_路由表_v1.13.md").read_text(encoding="utf-8")
    section = text.split("### 4.1 同批关联图", 1)[1].split("### 4.2", 1)[0]
    for (customer, target), peers in PEERS.items():
        row = next(line for line in section.splitlines() if line.startswith(f"| {customer} |") and f"`{target}`" in line.split("|")[2])
        assert set(peers) == set(re.findall(r"`([TE]\d{2})`", row.split("|")[3])), (customer, target)


def test_a_file_without_a_recognized_customer_routes_by_the_task_customer():
    row = file("cp", "控制计划-776.xlsx")
    row["match"]["metadata"]["customer"] = None
    assert "S02" not in [item["code"] for item in precheck_task({"customer_preference": "UAES", "files": [row]})["statuses"]]
    assert [item["code"] for item in precheck_task({"customer_preference": None, "files": [row]})["statuses"]] == ["S02"]


def _criterion(id_, result, reason=""):
    return {"criterion_id": id_, "criterion": f"判据 {id_}", "result": result, "code": "W02" if result == "不满足" else None, "location": "", "evidence": "", "reason": reason, "source": "10"}


def test_a_criterion_handed_to_the_formal_audit_is_s03_and_an_entry_not_fully_decided_is_s06_with_its_coverage():
    criteria = [
        *(_criterion(f"M-{i:02d}", "满足") for i in range(1, 8)),
        _criterion("M-08", "不满足", "未填"),
        _criterion("P-05", "未执行", "需与同批其他文件比对"),
        _criterion("X-05", "未执行", "需要图像识别"),
        _criterion("S-01", "S03", "超差判断移交正式审核"),
        _criterion("R-02", "S02", "该客户无此要求"),
    ]
    result = precheck_task({"files": [file("psw", "PSW.xlsx", doc_type="零件提交保证书")]}, {"psw": criteria})
    s03, s06 = _status(result, "S03"), _status(result, "S06")
    assert [item["note"] for item in s03] == ["S-01 判据 S-01：超差判断移交正式审核"]
    # S02 and S03 are not the precheck's to decide: 8 of the 10 due criteria were judged.
    assert s06[0]["coverage"] == {"judged": 8, "total": 10}
    assert s06[0]["note"].startswith("已判 8/10 条判据，覆盖率 80%；未判：P-05（需与同批其他文件比对）")
    assert result["unreviewed"] == 1 + 2
    # Statuses never change the file level or the conclusion.
    assert [row["level"] for row in result["files"]] == ["warn"] and result["conclusion"] == "pass"

    decided = precheck_task({"files": [file("psw", "PSW.xlsx", doc_type="零件提交保证书")]}, {"psw": [_criterion("M-01", "满足"), _criterion("R-02", "S02")]})
    assert decided["statuses"] == [] and decided["unreviewed"] == 0
    # Without content results the caller did not run the content level: no coverage is claimed either way.
    assert not _status(precheck_task({"files": [file("psw", "PSW.xlsx", doc_type="零件提交保证书")]}), "S06")


def test_an_unreadable_original_is_not_s06():
    """总纲 v0.21 §3.1: S06 is only 「该判而没判完」; 「读不出」 is W05, not S06."""
    result = precheck_task({"files": [file("cp", "控制计划.xlsx")]}, {})
    assert _status(result, "S06") == []
    criteria = [
        {"criterion_id": "A", "criterion": "a", "result": "满足"},
        {"criterion_id": "B", "criterion": "b", "result": "未执行", "reason": "文件无可读文字", "unread": True},
    ]
    result = precheck_task({"files": [file("psw", "PSW.xlsx", doc_type="零件提交保证书")]}, {"psw": criteria})
    # 总纲 §3.1 硬约束 4 注: what the precheck had no means to read is no S06 and no level, but it is 未审.
    assert _status(result, "S06") == [] and result["unreviewed"] == 1 and result["blocks"] == []


def test_named_by_user_is_only_a_rule_chosen_by_hand_that_the_file_is_matched_to():
    from product.precheck import named_by_user

    chosen = file("p", "照片.xlsx", preferred_rule_version_id="photo")
    chosen["match"]["rule"] = {"id": "photo"}
    assert named_by_user(chosen)
    recognized = file("p", "照片.xlsx")
    recognized["match"]["rule"] = {"id": "photo"}
    assert not named_by_user(recognized)
    stale = file("p", "照片.xlsx", preferred_rule_version_id="other")
    stale["match"]["rule"] = {"id": "photo"}
    assert not named_by_user(stale)
    # A re-audit inherits the previous Round's rule (the first of its options): keeping it is not chosen by hand.
    options = [{"id": "photo", "title": "照片 · 原审核版本"}, {"id": "photo-2", "title": "照片 · 较新版本"}]
    inherited = file("p", "照片.xlsx", preferred_rule_version_id="photo", rule_options=options)
    inherited["match"]["rule"] = {"id": "photo"}
    assert not named_by_user(inherited)
    # Another rule picked on P3 of the re-audit is.
    picked = file("p", "照片.xlsx", preferred_rule_version_id="photo-2", rule_options=options)
    picked["match"]["rule"] = {"id": "photo-2"}
    assert named_by_user(picked)
    # A file new to the re-audit has no original rule; a rule chosen for it is chosen by hand.
    added = file("p", "照片.xlsx", preferred_rule_version_id="photo", rule_options=[])
    added["match"]["rule"] = {"id": "photo"}
    assert named_by_user(added)


def test_image_type_files_are_one_batch_note_that_changes_no_count_level_or_conclusion():
    """总纲 §5.2: 同批存在图像型文件 is a batch-level W01 that does not affect any single file's conclusion."""
    from product.precheck_content import NO_TEXT

    batch = [
        file("psw", "PSW.xlsx", doc_type="零件提交保证书"),
        file("photo", "样品照片.JPG", doc_type="零件提交保证书"),
        file("scan", "扫描.pdf", doc_type="零件提交保证书"),
        file("drop", "旧照片.png", selection="removed"),
    ]
    content = {"scan": [{"criterion_id": "A", "criterion": "a", "result": "未执行", "reason": NO_TEXT, "unread": True}]}
    plain = precheck_task({"files": batch[:1] + [file("x", "扫描.pdf", doc_type="零件提交保证书")]}, {"x": [{"criterion_id": "A", "criterion": "a", "result": "满足"}]})
    result = precheck_task({"files": batch}, content)
    assert result["image_files"] == [{"file_version_id": "photo", "filename": "样品照片.JPG"}, {"file_version_id": "scan", "filename": "扫描.pdf"}]
    assert not [item for item in result["warnings"] if item["code"] == "W01"]
    assert [row["level"] for row in result["files"]] == ["ok", "ok", "ok"] and result["conclusion"] == "pass"
    assert plain["image_files"] == []


def test_unreviewed_counts_s03_to_s07_and_never_s01_or_s02():
    batch = [
        file("gauge", "检具验收报告.xlsx", state="unmatched", gaps=["matching_document_type_rules_missing"], doc_type="检具验收报告"),
        file("cp", "控制计划.xlsx", state="unmatched", gaps=["matching_customer_rules_missing"]),
        file("cpk", "过程能力.xlsx", doc_type="过程能力研究", part_number="P1"),
    ]
    result = precheck_task({"files": batch})
    assert sorted(item["code"] for item in result["statuses"]) == ["S01", "S02", "S04"]
    assert result["unreviewed"] == 1


def test_an_unrouted_pdf_without_a_text_layer_is_an_image_type_file():
    import pymupdf
    from product.precheck import image_files

    blank = pymupdf.open()
    blank.new_page()
    scan, text = blank.tobytes(), None
    with_text = pymupdf.open()
    with_text.new_page().insert_text((72, 72), "Control Plan")
    text = with_text.tobytes()
    rows = [file("scan", "扫描件.pdf", state="unmatched"), file("doc", "说明.pdf", state="unmatched")]
    originals = {"scan": scan, "doc": text}
    found = image_files(rows, {}, read=lambda row: originals[row["file"]["id"]])
    assert [item["file_version_id"] for item in found] == ["scan"]
    assert image_files(rows, {}) == []
