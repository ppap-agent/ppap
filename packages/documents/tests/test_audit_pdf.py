"""PDF bytes are derived from one frozen machine result, not another audit."""

import re
from copy import deepcopy
from uuid import UUID

import pymupdf
import pytest
from documents import report_layout
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
        text = "".join("".join(page.get_text().split()) for page in document)
        assert "尺寸检验报告.xlsx" in text
        assert "客户尺寸要求" in text
        assert "必须填写零件号" in text
        assert "不符合" in text
        assert "该位置内容为空" in text
        assert "工作表「检验」A2" in text
        assert "深度" not in text
        assert "\ufffd" not in text
        assert document.metadata["title"] == "尺寸检验报告.xlsx-审核报告" and document.metadata["subject"] == "PPAP 审核报告"


def test_optional_evidence_and_scope_can_be_absent_without_losing_the_report_or_verdict():
    source = report_source()
    check = source["audit"]["checks"][0]
    check["scope"] = None
    check["scope_notice"] = "未提供具体定位与摘录。"
    check["evidence"] = [{"file_version_id": "version-one", "locator": None, "excerpt": "", "location_gap": "原件定位信息不完整。"}]
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join("".join(page.get_text().split()) for page in document)
        assert "不符合" in text and "必须填写零件号" in text
        assert "原件定位信息不完整" in text
        assert "该位置内容为空" not in text


def test_pdf_table_cell_report_keeps_the_original_pdf_page_and_coordinates():
    source = report_source()
    source["filename"] = source["originals"]["version-one"] = "尺寸检验报告.pdf"
    source["audit"]["checks"][0]["evidence"][0]["locator"] = {"kind": "pdf_cell", "page": 3, "row": 12, "column": 4}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join("".join(page.get_text().split()) for page in document)
        assert "尺寸检验报告.pdf" in text
        assert "第3页表格第12行第4列" in text
        assert "不符合" in text
        assert "该位置内容为空" in text
        assert "Sheet" not in text
        assert "工作表" not in text


def test_a_finding_located_on_a_pdf_page_names_that_page():
    # A PDF checker cites the page it read, often with a region; the report says which page, not the region.
    source = report_source()
    source["filename"] = source["originals"]["version-one"] = "控制计划.pdf"
    source["audit"]["checks"][0]["evidence"][0]["locator"] = {"kind": "pdf_page", "page": 2, "region": [10, 20, 300, 400]}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join("".join(page.get_text().split()) for page in document)
        assert "控制计划.pdf·第2页" in text and "300" not in text


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
        text = "".join("".join(page.get_text().split()) for page in document)
        for number in range(1, 31):
            assert f"第{number:02d}条要求" in text
        assert "<b>原件文字，不是HTML</b>" in text
        assert "不表示正式量产批准" in text
        # Chapter eight opens with what a machine result is (#357).
        assert _chapter(text, "八、结论作用域与局限性声明").removeprefix("八、结论作用域与局限性声明").startswith("·机器初审结果，以人工确认和签发为准。")


CHAPTERS = (
    "一、结论摘要",
    "二、提交范围与文件集",
    "三、问题总览",
    "四、A · B 类问题明细",
    "五、C 类问题明细",
    "六、状态登记",
    "七、整改建议汇总",
    "八、结论作用域与局限性声明",
    "九、附录 · 检查项全量结果",
    "十、附录 · 分则附加节",
)


def _pages(document) -> list[str]:
    return ["".join(page.get_text().split()) for page in document]


def _chapter_page(document, title: str) -> str:
    """The text of the first page of a chapter, found through the PDF outline."""
    page = next(page for _level, entry, page in document.get_toc() if entry == title)
    return "".join(document[page - 1].get_text().split())


def _chapter(text: str, title: str) -> str:
    """Body text of one chapter in the whitespace-free text of the whole report (after the contents page)."""
    index = CHAPTERS.index(title)
    flat = [chapter.replace(" ", "") for chapter in CHAPTERS]
    start = text.index(flat[index], text.index("目录") + 200)
    end = text.index(flat[index + 1], start) if index + 1 < len(flat) else len(text)
    return text[start:end]


def test_report_has_an_own_cover_a_contents_page_and_the_ten_chapters_in_order():
    source = report_source()
    # The cover's customer is the one the file writes, as written; the chosen rule set never stands in for it.
    source["recognition"] = {"customer": "联合汽车电子有限公司", "project": "项目A", "model": "M1", "part_number": "F01RD0Y776", "part": None}
    source["audited_at"] = "2026-11-10T17:30:00+00:00"
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        pages = _pages(document)
        cover = pages[0]
        assert "联电科技" not in cover
        expected = ("客户联合汽车电子有限公司", "项目项目A", "车型M1", "零件号F01RD0Y776", "零件名称—", "检查日期2026-11-11", "供应商名称", "PPAP版本", "SQE工程师")
        positions = [cover.index(text) for text in expected]
        assert positions == sorted(positions)
        assert "请下载后自行补充" in cover
        # The cover is not a chapter and has no running header or page number.
        assert not re.search(r"第\d+/\d+页", cover) and "结论摘要" not in cover
        assert pages[1].startswith("目录封面1") and all(chapter.replace(" ", "") in pages[1] for chapter in CHAPTERS)
        toc = document.get_toc()
        assert [entry for _level, entry, _page in toc] == ["封面", *CHAPTERS]
        starts = [page for _level, _entry, page in toc]
        assert starts[0] == 1 and starts[1:] == sorted(starts[1:]) and starts[1] == 3
        for (_level, title, page), number in zip(toc[1:], starts[1:], strict=True):
            assert pages[page - 1].startswith(title.replace(" ", "")), title
            # The contents page names the same page number as the outline and links there.
            assert f"{title.replace(' ', '')}{number}" in pages[1]
        assert {link["page"] for link in document[1].get_links()} == {page - 1 for page in starts}
        for number, page in enumerate(pages[1:], 2):
            assert page.endswith(f"尺寸检验报告.xlsx-审核报告PPAP审核报告2026-11-11第{number}/{len(pages)}页"), page
        assert "本章无附加节。" in _chapter_page(document, "十、附录 · 分则附加节")


def test_chapter_four_has_the_eight_fixed_columns_and_unknown_values_read_as_a_dash():
    source = _graded_source([("A", None), (None, None)])
    source["audit"]["checks"][0]["rule_item"]["rule_to_check"] = "零件号必须与图纸一致"
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        chapter = _chapter_page(document, "四、A · B 类问题明细")
        assert "序号审核项结果严重程度问题性质判定依据说明位置" in chapter
        assert "1R1·类别1NGA—零件号必须与图纸一致" in chapter
        # An NG without a level stays in chapter four with an empty level, never a guessed one.
        assert "2R2·类别2NG——" in chapter and "严重程度为「—」的不符合项尚未定级" in chapter
        assert "尺寸检验报告.xlsx·工作表「检验」A1" in chapter
        assert "本次无C类问题。" in _chapter_page(document, "五、C 类问题明细")


def test_every_status_is_listed_in_chapter_nine_and_unknowns_are_registered_not_passed():
    source = report_source()
    base = source["audit"]["checks"][0]
    checks = []
    for number, status in enumerate(("PASS", "NA", "UNABLE", "ERR", "NG"), 1):
        check = deepcopy(base)
        check["rule_item"].update(id=f"R{number}", category=f"类别{number}")
        check["finding"] = {"status": status, "suggestions": f"第{number}项说明。"}
        checks.append(check)
    source["audit"]["checks"] = checks
    source["audit"]["counts"] = {"pass_count": 1, "ng_count": 1, "unknown_count": 2, "na_count": 1}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(_pages(document))
        appendix = _chapter(text, "九、附录 · 检查项全量结果")
        for number, label in enumerate(("PASS", "不适用", "待审核", "技术错误", "NG"), 1):
            assert f"{number}R{number}·类别{number}{label}" in appendix, label
        register = _chapter(text, "六、状态登记")
        assert "R2·类别2不适用第2项说明。" in register and "R3·类别3待审核第3项说明。" in register and "R4·类别4技术错误" in register
        assert "R1·类别1" not in register and "R5·类别5" not in register


def test_long_tables_repeat_their_header_on_every_page_and_mark_continuations():
    source = report_source()
    base = source["audit"]["checks"][0]
    source["audit"]["checks"] = []
    for number in range(1, 61):
        check = deepcopy(base)
        check["rule_item"].update(id=f"R{number}", category=f"类别{number}")
        check["finding"] = {"status": "PASS", "suggestions": "已核实。"}
        source["audit"]["checks"].append(check)
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        pages = _pages(document)
        appendix = [page for page in pages if "表9-1检查项全量结果" in page]
        assert len(appendix) > 1
        assert all("序号审核项结果严重程度判定依据说明来源证据" in page for page in appendix)
        assert sum("表9-1检查项全量结果（续表）" in page for page in appendix) == len(appendix) - 1
        text = "".join(pages)
        assert all(f"R{number}·类别{number}PASS" in text for number in range(1, 61))


def test_running_header_and_footer_share_one_embedded_face_however_many_pages():
    # Loading the CJK face for every header and footer line held a copy per line: a thirty-page report took
    # gigabytes and was killed in a 2 GiB worker. The faces embedded stay the same few as pages grow.
    source = report_source()
    base = source["audit"]["checks"][0]
    source["audit"]["checks"] = []
    for number in range(1, 121):
        check = deepcopy(base)
        check["rule_item"].update(id=f"R{number}", category=f"类别{number}")
        source["audit"]["checks"].append(check)
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        assert len(document) > 10
        faces = {font[0] for index in range(len(document)) for font in document.get_page_fonts(index)}
        assert len(faces) <= 3, faces
        last = document[-1].get_text()
        assert f"第 {len(document)} / {len(document)} 页" in last and "PPAP 审核报告" in last


def test_every_character_the_layout_writes_is_in_the_cut_faces(monkeypatch):
    # The faces are cut to the report's characters before layout. Text the layout adds by itself (contents,
    # 续表, running lines, an ellipsis) must end up in them too, never drawn from a fallback font.
    from documents import report_layout

    cuts = []
    original = report_layout._subset

    def recorded(face, characters):
        cuts.append(set(characters))
        return original(face, characters)

    monkeypatch.setattr(report_layout, "_subset", recorded)
    monkeypatch.setattr(report_layout, "LAYOUT_TEXT", "")  # as if the list of layout text had gaps
    source = report_source()
    source["filename"] = source["originals"]["version-one"] = "很长的文件名" * 12 + ".pdf"
    base = source["audit"]["checks"][0]
    source["audit"]["checks"] = []
    for number in range(1, 80):
        check = deepcopy(base)
        check["rule_item"].update(id=f"R{number}", category=f"类别{number}")
        source["audit"]["checks"].append(check)
    # A character missing from the cut faces is not an empty box: it is drawn, and read back, as another
    # character. So the layout's own words must come out exactly as written.
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        pages = ["".join(page.get_text().split()) for page in document]
        total = len(document)
    assert pages[1].startswith("目录封面1") and f"第{total}/{total}页" in pages[-1]
    assert any("（续表）" in page for page in pages)
    assert {ord(char) for char in "封面目录续表第页"} <= cuts[-1]


def test_new_pdfs_carry_no_internal_identity_or_checker_sample_verification():
    identity = "1ff467a2-fa00-4a10-8abc-ff12abcd3456"
    source = report_source()
    audit = source["audit"]
    audit.update(round_id="5d1b0c3e-7a51-4b4e-9d43-6c0e5c3f6a10", file_version_id="0a9a40f2-28a1-4a4b-9f1c-0f6b62a0e0b1", result_attempt=7)
    audit["rule"]["id"] = "8b7c5c1a-3f0e-4d55-9a2e-3b1d2f4e5a6c"
    source["originals"] = {audit["file_version_id"]: "尺寸检验报告.xlsx"}
    audit["checks"][0]["evidence"][0]["file_version_id"] = audit["file_version_id"]
    audit["checks"][0]["finding"] = {"status": "UNABLE", "suggestions": "签字区域未能读取，需人工复核。"}
    audit["counts"] = {"pass_count": 0, "ng_count": 0, "unknown_count": 1}
    audit["conclusion"] = "UNKNOWN"
    notice = f"本轮采用未经验证的检查版本。检查版本：第 3 版（{identity}）。"
    audit["checker_verification"] = {"suite_revision_id": identity, "suite_revision_number": 3, "status": "unverified", "samples_not_rerun": False, "notice": notice}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(_pages(document))
        properties = " ".join(str(value) for value in document.metadata.values()) + (document.get_xml_metadata() or "")
        outline = " ".join(entry for _level, entry, _page in document.get_toc())
        annotations = " ".join(str(annotation.info) for page in document for annotation in page.annots())
        for secret in (identity, audit["round_id"], audit["file_version_id"], audit["rule"]["id"], "检查版本", "样本验证", "未经验证的检查版本", "PyMuPDF", "MuPDF"):
            assert secret not in text and secret not in properties and secret not in outline and secret not in annotations, secret
        assert document.embfile_count() == 0
        assert set(document.metadata.get(key) for key in ("author", "keywords", "creator", "producer")) == {""}
        # Customer-facing risk stays: the unknown clause is named, located and not counted as met.
        assert "待审核1项" in text and "签字区域未能读取，需人工复核。" in text and "「待审核」项未计为PASS" in text


def test_one_cjk_font_family_is_embedded_as_a_subset():
    with pymupdf.open(stream=render_audit_pdf(report_source()), filetype="pdf") as document:
        fonts = {(font[3], font[1]) for page in document for font in page.get_fonts(full=True)}
    names = {name.split("+", 1)[-1] for name, _ext in fonts}
    assert len({name.removesuffix(" Bold").removesuffix(" Regular").removesuffix("-Bold").removesuffix("-Regular") for name in names}) == 1, names
    assert all(ext != "n/a" for _name, ext in fonts), fonts
    report_layout.report_fonts.cache_clear()
    regular, _bold = report_layout.report_fonts()
    family = pymupdf.Font(fontbuffer=regular).name
    assert any(family.split()[0] in name for name in names)
    assert family.startswith("Sarasa UI SC")


def test_the_report_font_is_the_pinned_sarasa_ui_sc_release():
    # The files are the v1.0.43 unhinted release; a change of font is a deliberate change of these digests.
    import hashlib

    digests = {name: hashlib.sha256((report_layout.FONT_DIR / name).read_bytes()).hexdigest() for name in report_layout.FONT_FILES}
    assert digests == {
        "SarasaUiSC-Regular.ttf": "f5d4abd5a4b01084598590f01c084cc86794603674a6899b190c9225b754a213",
        "SarasaUiSC-Bold.ttf": "00199abc2b5e839e6ba68f26338532d60415a5f2407fa4218f71c4217b27c200",
    }


def _on_page(document, needle: str) -> bool:
    found = False
    for page in document:
        for hit in page.search_for(needle):
            assert page.rect.contains(hit)
            found = True
    return found


def _spans(document) -> list[str]:
    return [span["text"] for page in document for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) for span in line["spans"]]


def test_copied_pdf_text_preserves_ascii_ff_fi_fl_identities_and_a_different_identifier():
    source = report_source()
    # Legal UUID-shaped part identity: hex only, with ASCII ff. fi/fl/ffi/ffl cannot appear in it.
    identity = "1ff467a2-fa00-4a10-8abc-ff12abcd3456"
    distinct = "9aa467a2-ab00-4b10-8def-aa12abcd7890"
    assert str(UUID(identity)) == identity
    assert str(UUID(distinct)) == distinct
    assert identity != distinct
    excerpt = "evidence excerpt keeps original ascii [fi] [fl] [ffi] [ffl]"
    suggestions = "suggestion keeps original ascii (fi) (fl) (ffi) (ffl)"
    preserved_ligatures = "9ﬀaaaa-9ﬁbbbb-9ﬂcccc-9ﬃdddd-9ﬄeeee"
    source["recognition"] = {"customer": None, "project": None, "model": None, "part_number": identity, "part": None}
    audit = source["audit"]
    audit["conclusion"] = "PASS"
    audit["counts"] = {"pass_count": 1, "ng_count": 0}
    audit["rule"]["title"] = preserved_ligatures
    check = audit["checks"][0]
    check["finding"]["status"] = "PASS"
    check["finding"]["suggestions"] = suggestions
    check["evidence"][0]["excerpt"] = excerpt
    folded_identity = identity.replace("ff", "ﬀ")
    expanded_ligatures = preserved_ligatures.replace("ﬃ", "ffi").replace("ﬄ", "ffl").replace("ﬀ", "ff").replace("ﬁ", "fi").replace("ﬂ", "fl")
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        # Table cells wrap, so source text is compared without the line breaks the layout adds.
        copied = "".join("".join(page.get_text().split()) for page in document)
        visible = "".join("".join(span.split()) for span in _spans(document))

        def flat(value: str) -> str:
            return "".join(value.split())

        # Cover values and the rule title are not wrapped: they are found on the page as one piece.
        assert identity in copied and identity in visible and _on_page(document, identity)
        assert preserved_ligatures in copied and preserved_ligatures in visible and _on_page(document, preserved_ligatures)
        assert flat(excerpt) in copied and flat(excerpt) in visible
        assert flat(suggestions) in copied and flat(suggestions) in visible
        for token, ligature in (("fi", "\ufb01"), ("fl", "\ufb02"), ("ffi", "\ufb03"), ("ffl", "\ufb04")):
            assert f"[{token}]" in copied and f"[{token}]" in visible
            assert f"({token})" in copied and f"({token})" in visible
            assert f"[{ligature}]" not in copied and f"[{ligature}]" not in visible
            assert f"({ligature})" not in copied and f"({ligature})" not in visible
        assert "\ufb00" not in copied.replace(preserved_ligatures, "") and "\ufb01" not in copied.replace(preserved_ligatures, "")
        assert distinct not in copied and distinct not in visible and not _on_page(document, distinct)
        assert folded_identity not in copied and folded_identity not in visible and not _on_page(document, folded_identity)
        assert expanded_ligatures not in copied and expanded_ligatures not in visible and not _on_page(document, expanded_ligatures)


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
    mismatch = deepcopy(missing)
    mismatch["rule_item"].update(id="R47", category="材料证明")
    supply = {"kind": "source_mismatch", "document": "材料证明", "part_number": "P1", "found_part_number": "P2"}
    mismatch["finding"] = {"status": "NG", "suggestions": "比对源零件号不一致。", "locator": {"semantic": {"supply": supply}}}
    unknown = deepcopy(missing)
    unknown["rule_item"].update(id="R48", category="检具")
    unknown["finding"] = {"status": "UNABLE", "suggestions": "未能确认。", "locator": {"semantic": {"supply": {"kind": "missing", "document": "检具清单"}}}}
    source["audit"]["checks"] = [*checks, missing, mismatch, unknown]
    source["audit"]["counts"] = {"pass_count": 43, "ng_count": 4, "unknown_count": 1}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(_pages(document))
        appendix = _chapter(text, "九、附录 · 检查项全量结果")
        for number in range(1, 49):
            assert f"R{number}·" in appendix
        remediation = _chapter(text, "七、整改建议汇总")
        # Two problems at one location merge into one point; with no correct value in the result, the action is 增补 at the place.
        assert "①增补：在「尺寸检验报告.xlsx·工作表「检验」A2」补齐审核项「类别3」要求的内容（现状：第3项缺值）" in remediation
        assert "②增补：在「尺寸检验报告.xlsx·工作表「检验」A2」补齐审核项「类别7」要求的内容（现状：第7项缺值）" in remediation
        assert "改值：使" not in remediation
        assert "已核实" not in remediation
        assert "增补零件号P1的《图纸》" in remediation and "增补零件号P1的《材料证明》，删除本批零件号不一致的《材料证明》" in remediation
        assert "建议截止日" in remediation and "整改益处" in remediation and "修订后自查要点" in remediation
        register = _chapter(text, "六、状态登记")
        assert "需补交文件清单补交：零件号P1的《图纸》换交：《材料证明》（零件号P1）" in register
        # An undecided clause is not a file to supply.
        assert "检具清单" not in remediation and "检具清单" not in register
        assert "需补交或换交的关联文件：2份" in _chapter(text, "三、问题总览")


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
        assert "本次无A·B类问题。" in text and "本次无C类问题。" in text
        assert "客户—" in text


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
        assert f"总评：{rating}" in _chapter_page(document, "一、结论摘要")


def test_levels_order_remediation_and_overrides_are_explained():
    override = {"default": "C", "level": "A", "reason": "孔径超差会导致总装干涉"}
    source = _graded_source([("C", None), ("A", override), (None, None), ("B", None)])
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(_pages(document))
        assert "A类B类C类未定级1111" in text
        assert "规则默认C→判定A｜理由：孔径超差会导致总装干涉" in text
        assert "严重程度C（规则默认）" in text
        remediation = _chapter(text, "七、整改建议汇总")
        order = [remediation.index(f"第{number}项缺值") for number in (2, 4, 1, 3)]
        assert order == sorted(order)
        sections = [remediation.index(title) for title in ("P1立即整改（A类）", "P2限期整改（B类）", "P3建议整改（C类）", "未定级的不符合项")]
        assert sections == sorted(sections)
        assert "3个工作日" in remediation and "7个工作日" in remediation and "15个工作日" in remediation


def test_an_undecided_clause_is_counted_beside_the_rating_and_never_as_a_problem():
    source = _graded_source([("C", None)])
    undecided = deepcopy(source["audit"]["checks"][0])
    undecided["rule_item"] = {"id": "2", "category": "签字", "rule_to_check": "必须有签字"}
    undecided["finding"] = {"status": "UNABLE", "suggestions": "签字区域未能读取，需人工复核。"}
    source["audit"]["checks"].append(undecided)
    source["audit"]["counts"] = {"pass_count": 0, "ng_count": 1, "unknown_count": 1}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        pages = _pages(document)
        summary = _chapter_page(document, "一、结论摘要")
    assert "待审核1项" in summary
    # The stat boxes repeat the same four values; 待审核 stays its own box, not PASS.
    assert "PASS0NG1待审核1不适用0" in summary
    # Owner's ruling on PRD §2.11 四: no A / B and a C passes with conditions; the pending clause is named beside it.
    assert "总评：有条件通过另1项待审核" in summary and "问题总数1项" in summary
    assert "签字区域未能读取，需人工复核。" in "".join(pages)


def test_every_embedded_report_font_is_a_subset():
    """A whole CJK face is ~17 MB; the report embeds only the glyphs it uses, whichever font the runtime has."""
    pdf = render_audit_pdf(report_source())
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        names = {font[3] for page in document for font in page.get_fonts(full=True)}
    assert names and all(re.match(r"^[A-Z]{6}\+", name) for name in names), names
    assert len(pdf) < 2_000_000


def test_copied_report_text_is_the_text_the_report_wrote():
    """Glyphs the CJK font shares (space/no-break space, -/no-break hyphen, ·/・, 一/⼀) copy as the ordinary character.

    Known gap: in the running header Noto may draw a digit with an alternate glyph that only a private-use
    code point names, so that digit copies as private use; this test does not cover it.
    """
    source = report_source()
    source["filename"] = "F01-R10 1号 一人日 控制计划.xlsx"
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
    assert "F01-R10 1号 一人日 控制计划" in text
    assert "四、A · B 类问题明细" in text
    variants = {char for char in text if char in "\u00a0\u2011\u30fb" or 0x2E80 <= ord(char) <= 0x2FDF or 0xF900 <= ord(char) <= 0xFAFF}
    assert not variants, variants


def _warning(code: str, problem: str, location: str = "尺寸检验报告.xlsx", scope: str = "file") -> dict:
    titles = {"W01": "缺项", "W02": "字段为空或未识别", "W03": "写法不规范"}
    return {
        "code": code,
        "title": titles[code],
        "level": "warn",
        "file_version_id": "version-one",
        "filename": "尺寸检验报告.xlsx",
        "location": location,
        "evidence": "(空)",
        "problem": problem,
        "action": "",
        "responsibility": "材料侧",
        "scope": scope,
    }


def test_a_pass_with_pending_clauses_passes_and_names_them_and_an_uncovered_clause_is_pending():
    source = report_source()
    audit = source["audit"]
    audit["conclusion"] = "PASS"
    audit["checks"][0]["finding"] = {"status": "PASS", "suggestions": "已核实。"}
    uncovered = deepcopy(audit["checks"][0])
    uncovered.update(finding=None, evidence=[], scope=None, scope_notice="本条要求未完成自动检查。")
    uncovered["rule_item"] = {"id": "2", "category": "签字", "rule_to_check": "必须有供应商签字"}
    audit["checks"].append(uncovered)
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(_pages(document))
        summary = _chapter_page(document, "一、结论摘要")
    assert "总评：通过另1项待审核文件结论：PASS" in summary and "问题总数0项" in summary
    assert "2·签字待审核本条要求未完成自动检查。" in _chapter(text, "六、状态登记")
    assert "2·签字待审核—必须有供应商签字" in _chapter(text, "九、附录 · 检查项全量结果")
    assert "本次未发现问题项，无需整改。" in _chapter(text, "七、整改建议汇总")


def test_precheck_warnings_are_c_class_problems_with_concrete_remediation():
    source = report_source()
    audit = source["audit"]
    audit["conclusion"] = "PASS"
    audit["checks"][0]["finding"] = {"status": "PASS", "suggestions": "已核实。"}
    source["precheck_warnings"] = [
        _warning("W02", "文件中未识别到零件号。"),
        _warning("W03", "C3 日期写法：应为 yyyy-mm-dd，请核实。", "尺寸检验报告.xlsx › 封面!B4", "content"),
    ]
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(_pages(document))
        summary = _chapter_page(document, "一、结论摘要")
    # No audit NG: the file stays PASS; the warnings make the report pass with conditions (C only).
    assert "总评：有条件通过文件结论：PASS" in summary and "C类2项" in summary and "C类含预检警告2项" in summary
    chapter_five = _chapter(text, "五、C 类问题明细")
    assert "W-01预检W02·字段为空或未识别W02C—" in chapter_five and "W-02预检W03·写法不规范W03C—" in chapter_five
    assert "A类B类C类未定级002" in _chapter(text, "三、问题总览")
    remediation = _chapter(text, "七、整改建议汇总")
    assert "本次无P1项" in remediation and "本次无P2项" in remediation
    assert "P3增补：在《尺寸检验报告.xlsx》表头填写零件号尺寸检验报告.xlsx15个工作日" in remediation
    assert "改值：尺寸检验报告.xlsx›封面!B4——C3日期写法：应为yyyy-mm-dd" in remediation
    assert "请核实" not in remediation and "同上" not in remediation
    # Warnings are not audit items: the four result values count only the clauses.
    assert "PASS1NG0待审核0不适用0" in summary


def test_a_row_taller_than_a_page_runs_on_over_pages_without_losing_evidence():
    source = report_source()
    check = source["audit"]["checks"][0]
    # A deep-review correction can cite many places; one such row is taller than a page.
    check["evidence"] = [{**check["evidence"][0], "excerpt": f"证据片段{number}：" + "很长的原文摘录" * 12} for number in range(1, 41)] if check.get("evidence") else []
    check["finding"] = {**(check.get("finding") or {}), "status": "NG", "suggestions": "逐条核对的说明。" * 80}
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        text = "".join(_pages(document))
    assert "逐条核对的说明。" in text
    if check["evidence"]:
        assert all(f"证据片段{number}" in text for number in (1, 40))


def test_one_action_is_one_point_and_a_precheck_request_joins_the_ng_that_asks_for_the_same_file():
    source = report_source()
    base = source["audit"]["checks"][0]
    checks = []
    for number, (level, document) in enumerate((("B", "图纸"), ("A", "图纸"), ("A", "过程流程图")), 1):
        check = deepcopy(base)
        check["rule_item"].update(id=f"R{number}", category=f"类别{number}")
        check["evidence"] = []
        check["finding"] = {"status": "NG", "suggestions": f"缺少{document}。", "locator": {"semantic": {"severity": level, "supply": {"kind": "missing", "document": document}}}}
        checks.append(check)
    source["audit"]["checks"] = checks
    source["audit"]["counts"] = {"pass_count": 0, "ng_count": 3, "unknown_count": 0}
    source["precheck_warnings"] = [{**_warning("W01", "缺少同零件的《过程流程图》。"), "location": "尺寸检验报告.xlsx"}]
    with pymupdf.open(stream=render_audit_pdf(source), filetype="pdf") as document:
        remediation = _chapter("".join(_pages(document)), "七、整改建议汇总")
    overview = remediation.split("P1立即整改")[0]
    # Two clauses asking for the drawing are one point at the more urgent level; each keeps its basis.
    assert overview.count("增补《图纸》") == 1 and overview.count("增补《过程流程图》") == 1
    assert "依据：第1项·审核项R1（类别1）" in remediation and "依据：第2项·审核项R2（类别2）" in remediation
    # The precheck asks for the same flow chart: no second point, its basis joins the NG.
    assert "增补同零件的《过程流程图》" not in remediation and "依据：预检W01" in remediation
    assert "本次无P2项" in remediation
