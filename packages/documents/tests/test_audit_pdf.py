"""PDF bytes are derived from one frozen machine result, not another audit."""

import re
from copy import deepcopy
from uuid import UUID

import pymupdf
import pytest
from documents.audit_pdf import render_audit_pdf


def report_source():
    return {
        "filename": "尺寸检验报告.xlsx",
        "generated_at": "2026-10-17T01:00:00+00:00",
        "originals": {"version-one": "尺寸检验报告.xlsx"},
        "audit": {
            "round_id": "round-one",
            "file_version_id": "version-one",
            "result_attempt": 1,
            "conclusion": "NG",
            "rule": {"id": "rule-one", "title": "客户尺寸要求", "body": {}},
            "counts": {"pass_count": 0, "ng_count": 1},
            "summary": None,
            "checks": [
                {
                    "rule_item": {"id": "1", "category": "零件号", "rule_to_check": "必须填写零件号"},
                    "finding": {"status": "NG", "suggestions": "已检查的零件号单元格为空。"},
                    "scope": {"total": 1, "checked": 1, "unknown": 0},
                    "evidence": [{"file_version_id": "version-one", "locator": {"kind": "xlsx_cell", "sheet": "检验", "cell": "A2"}, "excerpt": ""}],
                }
            ],
        },
    }


def test_a_pdf_contains_readable_chinese_rule_result_and_empty_cell_evidence():
    pdf = render_audit_pdf(report_source())
    assert pdf.startswith(b"%PDF-")
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
        assert "尺寸检验报告.xlsx" in text
        assert "客户尺寸要求" in text
        assert "必须填写零件号" in text
        assert "不符合" in text
        assert "该位置内容为空" in text
        assert "检验!A2" in text
        assert "深度" not in text
        assert "\ufffd" not in text
        assert document.metadata["subject"] == "round=round-one;file_version=version-one;result_attempt=1;rule_version=rule-one"


def test_optional_evidence_and_scope_can_be_absent_without_losing_the_report_or_verdict():
    source = report_source()
    check = source["audit"]["checks"][0]
    check["scope"] = None
    check["scope_notice"] = "未提供具体定位与摘录。"
    check["evidence"] = [{"file_version_id": "version-one", "locator": None, "excerpt": "", "location_gap": "原件定位信息不完整。"}]
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
        assert "不符合" in text and "必须填写零件号" in text
        assert "原件定位信息不完整" in text
        assert "该位置内容为空" not in text


def test_pdf_table_cell_report_keeps_the_original_pdf_page_and_coordinates():
    source = report_source()
    source["filename"] = source["originals"]["version-one"] = "尺寸检验报告.pdf"
    source["audit"]["checks"][0]["evidence"][0]["locator"] = {"kind": "pdf_cell", "page": 3, "row": 12, "column": 4}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
        assert "尺寸检验报告.pdf" in text
        assert "第 3 页 · 表格行 12 · 列 4" in text
        assert "不符合" in text
        assert "该位置内容为空" in text
        assert "Sheet" not in text
        assert "!A2" not in text


@pytest.mark.parametrize("field", ["page", "row", "column"])
@pytest.mark.parametrize("value", [None, True, "1", 1.5, 0, -1])
def test_pdf_table_cell_report_rejects_invalid_coordinates(field, value):
    source = report_source()
    source["audit"]["checks"][0]["evidence"][0]["locator"] = {"kind": "pdf_cell", "page": 1, "row": 1, "column": 1, field: value}
    with pytest.raises(ValueError, match="report_locator_unavailable"):
        render_audit_pdf(source)


def test_long_reports_keep_every_clause_and_treat_embedded_markup_as_source_text():
    source = report_source()
    base = source["audit"]["checks"][0]
    source["audit"]["checks"] = []
    for number in range(1, 31):
        check = deepcopy(base)
        check["rule_item"].update(id=str(number), rule_to_check=f"第{number:02d}条要求：" + "测量值必须满足图纸公差。" * 6)
        check["evidence"][0]["excerpt"] = "<b>原件文字，不是 HTML</b>"
        source["audit"]["checks"].append(check)
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        assert len(document) > 2
        text = "".join(page.get_text() for page in document)
        for number in range(1, 31):
            assert f"第{number:02d}条要求" in text
        assert "<b>原件文字，不是 HTML</b>" in text
        assert "不表示正式量产批准" in text


@pytest.mark.parametrize(("status", "notice"), [("unverified", "本轮采用未经验证的检查版本；即使全部为 PASS，也不表示检查版本已通过样本验证。"), ("verified", "受理本轮时平台已更新，样本未重跑。")])
def test_all_pass_pdf_prominently_preserves_frozen_checker_qualification(status, notice):
    source = report_source()
    audit = source["audit"]
    audit["conclusion"] = "PASS"
    audit["counts"] = {"pass_count": 1, "ng_count": 0}
    audit["checks"][0]["finding"]["status"] = "PASS"
    audit["checker_verification"] = {
        "suite_revision_id": "frozen-suite-3",
        "suite_revision_number": 3,
        "status": status,
        "samples_not_rerun": status == "verified",
        "notice": notice + "检查版本：第 3 版（frozen-suite-3）。",
    }
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        first_page = "".join(document[0].get_text().split())
        assert "审核结论：符合" in first_page
        assert "".join(notice.split()) in first_page
        assert "frozen-suite-3" in first_page
        assert "共1项：符合1项，不符合0项" in first_page


def _on_page(document, needle: str) -> bool:
    found = False
    for page in document:
        for hit in page.search_for(needle):
            assert page.rect.contains(hit)
            found = True
    return found


def test_copied_pdf_text_preserves_ascii_ff_fi_fl_identities_and_a_different_identifier():
    source = report_source()
    # Legal UUID: hex only, with ASCII ff. fi/fl/ffi/ffl cannot appear in a real suite id.
    identity = "1ff467a2-fa00-4a10-8abc-ff12abcd3456"
    distinct = "9aa467a2-ab00-4b10-8def-aa12abcd7890"
    assert str(UUID(identity)) == identity
    assert str(UUID(distinct)) == distinct
    assert identity != distinct
    excerpt = "evidence excerpt keeps original ascii [fi] [fl] [ffi] [ffl]"
    suggestions = "suggestion keeps original ascii (fi) (fl) (ffi) (ffl)"
    preserved_ligatures = "9\ufb00aaaa-9\ufb01bbbb-9\ufb02cccc-9\ufb03dddd-9\ufb04eeee"
    audit = source["audit"]
    audit["conclusion"] = "PASS"
    audit["counts"] = {"pass_count": 1, "ng_count": 0}
    audit["rule"]["id"] = identity
    audit["rule"]["title"] = preserved_ligatures
    check = audit["checks"][0]
    check["finding"]["status"] = "PASS"
    check["finding"]["suggestions"] = suggestions
    check["evidence"][0]["excerpt"] = excerpt
    # Keep the version clause on its own line so wrapping cannot split the UUID.
    # Default get_text(), span text, and search_for then see the original code points.
    notice = "本轮采用未经验证的检查版本。\n" + f"检查版本：第 1 版（{identity}）。"
    audit["checker_verification"] = {
        "suite_revision_id": identity,
        "suite_revision_number": 1,
        "status": "unverified",
        "samples_not_rerun": False,
        "notice": notice,
    }
    folded_identity = identity.replace("ff", "\ufb00")
    expanded_ligatures = preserved_ligatures.replace("\ufb03", "ffi").replace("\ufb04", "ffl").replace("\ufb00", "ff").replace("\ufb01", "fi").replace("\ufb02", "fl")
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        copied = "".join(page.get_text() for page in document)
        visible = "".join(span["text"] for page in document for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) for span in line["spans"])
        assert audit["checker_verification"]["suite_revision_id"] == identity
        assert identity in copied
        assert identity in visible
        assert "".join(notice.split()) in "".join(copied.split())
        assert document.metadata["subject"] == f"round=round-one;file_version=version-one;result_attempt=1;rule_version={identity}"
        assert _on_page(document, identity)
        assert excerpt in copied and excerpt in visible and _on_page(document, excerpt)
        assert suggestions in copied and suggestions in visible and _on_page(document, suggestions)
        for token, ligature in (("fi", "\ufb01"), ("fl", "\ufb02"), ("ffi", "\ufb03"), ("ffl", "\ufb04")):
            assert f"[{token}]" in copied and f"[{token}]" in visible and _on_page(document, f"[{token}]")
            assert f"({token})" in copied and f"({token})" in visible and _on_page(document, f"({token})")
            assert f"[{ligature}]" not in copied and f"[{ligature}]" not in visible and not _on_page(document, f"[{ligature}]")
            assert f"({ligature})" not in copied and f"({ligature})" not in visible and not _on_page(document, f"({ligature})")
        assert distinct not in copied and distinct not in visible and not _on_page(document, distinct)
        assert folded_identity not in copied and folded_identity not in visible and not _on_page(document, folded_identity)
        assert preserved_ligatures in copied and preserved_ligatures in visible and _on_page(document, preserved_ligatures)
        assert expanded_ligatures not in copied and expanded_ligatures not in visible and not _on_page(document, expanded_ligatures)


def _spans(document) -> list[str]:
    return [span["text"] for page in document for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) for span in line["spans"]]


def test_long_one_line_frozen_notice_keeps_a_legal_ff_uuid_contiguous():
    """Production verified notice is one line. Wrapping must not split the suite id."""
    source = report_source()
    identity = "1ff467a2-fa00-4a10-8abc-ff12abcd3456"
    distinct = "9aa467a2-ab00-4b10-8def-aa12abcd7890"
    assert str(UUID(identity)) == identity and str(UUID(distinct)) == distinct and identity != distinct
    # Same concatenation as frozen_checker_verification for a verified revision. No inserted newline.
    notice = "本轮检查版本已通过样本验证；样本验证不保证本次审核结论正确。" + f"检查版本：第 1 版（{identity}）。"
    assert "\n" not in notice and notice.count(identity) == 1
    identity_bytes = identity.encode()
    audit = source["audit"]
    audit["conclusion"] = "PASS"
    audit["counts"] = {"pass_count": 1, "ng_count": 0}
    audit["checks"][0]["finding"]["status"] = "PASS"
    audit["checker_verification"] = {
        "suite_revision_id": identity,
        "suite_revision_number": 1,
        "status": "verified",
        "samples_not_rerun": False,
        "notice": notice,
    }
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        copied = "".join(page.get_text() for page in document)
        spans = _spans(document)
        visible = "".join(spans)
        assert audit["checker_verification"]["suite_revision_id"].encode() == identity_bytes
        assert audit["checker_verification"]["notice"].encode() == notice.encode()
        assert identity_bytes in audit["checker_verification"]["notice"].encode()
        assert identity in copied and identity in visible and any(identity in span for span in spans)
        assert _on_page(document, identity)
        assert distinct not in copied and distinct not in visible and not _on_page(document, distinct)
        assert "本轮检查版本已通过样本验证" in copied and "样本验证不保证本次审核结论正确" in copied
        assert "尺寸检验报告.xlsx" in copied and "审核结论：符合" in copied and "必须填写零件号" in copied
        assert "不表示正式量产批准" in copied and "\ufffd" not in copied
        assert "".join(notice.split()) in "".join(copied.split())
        assert identity.replace("ff", "\ufb00") not in copied and identity.replace("ff", "\ufb00") not in visible


def test_absent_or_nonmatching_suite_revision_id_is_not_rewritten():
    source = report_source()
    identity = "1ff467a2-fa00-4a10-8abc-ff12abcd3456"
    literal = "原文 <b>不是 HTML</b> & 保持。\n下一行仍在。"
    audit = source["audit"]
    audit["checker_verification"] = {"notice": literal}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        copied = "".join(page.get_text() for page in document)
        assert "原文 <b>不是 HTML</b> & 保持。" in copied and "下一行仍在。" in copied
        assert identity not in copied
    audit["checker_verification"] = {"suite_revision_id": identity, "notice": literal}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        copied = "".join(page.get_text() for page in document)
        assert "原文 <b>不是 HTML</b> & 保持。" in copied and identity not in copied
    audit["checker_verification"] = {"suite_revision_id": "", "notice": "空标识不拆开正文。"}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        assert "空标识不拆开正文。" in "".join(page.get_text() for page in document)
    # "amp" occurs only inside the escaped ampersand, not in the notice text.
    audit["checker_verification"] = {"suite_revision_id": "amp", "notice": "A & B"}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        copied = "".join(page.get_text() for page in document)
        assert "A & B" in copied and "amp" not in copied


def _pages(document) -> list[str]:
    return ["".join(page.get_text().split()) for page in document]


def test_report_has_cover_and_four_sections_each_starting_a_page_with_frozen_recognition():
    source = report_source()
    source["recognition"] = {"customer": "联合汽车电子", "project": "项目A", "model": "M1", "part_number": "F01RD0Y776", "part": None}
    source["audited_at"] = "2026-11-10T17:30:00+00:00"
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        pages = _pages(document)
        cover = pages[0]
        for text in ("客户名称联合汽车电子", "项目名称项目A", "车型M1", "零件号F01RD0Y776", "零件名称—", "检查日期2026-11-11", "供应商名称", "PPAP版本", "SQE工程师"):
            assert text in cover, text
        assert "请下载后自行补充" in cover
        # The cover has no running header or page number.
        assert cover.count("PPAP审核报告") == 1 and not re.search(r"第\d+/\d+页", cover)
        starts = [next(index for index, page in enumerate(pages) if page.startswith(heading)) for heading in ("1检查概览", "2问题详情", "3整改建议", "4附录")]
        assert starts == sorted(starts) and starts[0] == 1
        for number, page in enumerate(pages[1:], 1):
            assert page.endswith(f"尺寸检验报告.xlsx-审核报告PPAP审核报告2026-11-11第{number}/{len(pages) - 1}页"), page


def test_every_check_is_listed_and_remediation_holds_only_failed_items_merged_by_location():
    source = report_source()
    base = source["audit"]["checks"][0]
    checks = []
    for number in range(1, 46):
        check = deepcopy(base)
        check["rule_item"].update(id=f"R{number}", category=f"类别{number}")
        check["finding"] = {"status": "NG", "suggestions": f"第{number}项缺值。"} if number in {3, 7} else {"status": "PASS", "suggestions": "已核实。"}
        checks.append(check)
    missing = deepcopy(base)
    missing["rule_item"].update(id="R46", category="图纸")
    missing["evidence"] = []
    missing["finding"] = {"status": "NG", "suggestions": "缺少图纸。", "locator": {"semantic": {"supply": {"kind": "missing", "document": "图纸", "part_number": "P1"}}}}
    source["audit"]["checks"] = [*checks, missing]
    source["audit"]["counts"] = {"pass_count": 43, "ng_count": 3}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        pages = _pages(document)
        text = "".join(pages)
        for number in range(1, 47):
            assert f"R{number}·" in text
        assert sum("检查结果一览" in page for page in pages) == 3
        assert sum("（续表）" in page for page in pages) == 2
        remediation = text[text.index("3整改建议") : text.index("4附录")]
        assert "①【类别3】第3项缺值。" in remediation and "②【类别7】第7项缺值。" in remediation
        assert "已核实" not in remediation
        assert "补交：图纸（零件号P1）" in remediation
        assert "需补交或换交的关联文件：1份" in text


def test_all_pass_report_says_no_remediation_and_no_supply():
    source = report_source()
    audit = source["audit"]
    audit["conclusion"] = "PASS"
    audit["counts"] = {"pass_count": 1, "ng_count": 0}
    audit["checks"][0]["finding"] = {"status": "PASS", "suggestions": "已核实。"}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(_pages(document))
        assert "本次未发现问题项，无需整改。" in text
        assert "本次没有需补交或换交的关联文件。" in text
        assert "客户名称—" in text


def _graded_source(levels):
    source = report_source()
    base = source["audit"]["checks"][0]
    checks = []
    for number, (level, override) in enumerate(levels, 1):
        check = deepcopy(base)
        check["rule_item"].update(id=f"R{number}", category=f"类别{number}")
        semantic = {"severity": level} if level else {}
        if override:
            semantic["severity_override"] = override
        check["finding"] = {"status": "NG", "suggestions": f"第{number}项缺值。", "locator": {"semantic": semantic}}
        check["evidence"][0]["locator"] = {"kind": "xlsx_cell", "sheet": "检验", "cell": f"A{number}"}
        checks.append(check)
    source["audit"]["checks"] = checks
    source["audit"]["counts"] = {"pass_count": 0, "ng_count": len(checks)}
    return source


@pytest.mark.parametrize(
    ("levels", "rating"),
    [
        ([("C", None), ("C", None)], "有条件通过"),
        ([("C", None), ("B", None)], "不通过"),
        ([("C", None), (None, None)], "不通过"),
    ],
)
def test_rating_comes_only_from_final_levels_and_an_unleveled_ng_fails(levels, rating):
    with pymupdf.open(stream=render_audit_pdf(_graded_source(levels)), filetype="pdf") as document:
        pages = _pages(document)
        assert f"总评：{rating}" in pages[0] and f"总评：{rating}" in pages[1]


def test_levels_order_remediation_and_overrides_are_explained():
    override = {"default": "C", "level": "A", "reason": "孔径超差会导致总装干涉"}
    source = _graded_source([("C", None), ("A", override), (None, None), ("B", None)])
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(_pages(document))
        assert "A类B类C类未定级1111" in text
        assert "规则默认C→判定A｜理由：孔径超差会导致总装干涉" in text
        assert "严重程度C（规则默认）" in text
        remediation = text[text.index("3整改建议") : text.index("4附录")]
        order = [remediation.index(f"第{number}项缺值") for number in (2, 4, 1, 3)]
        assert order == sorted(order)
        groups = remediation[remediation.index("合并为一条。") :]
        assert groups.index("A类") < groups.index("B类") < groups.index("C类") < groups.index("未定级")
