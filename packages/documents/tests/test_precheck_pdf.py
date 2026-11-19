import pymupdf
from documents.precheck_pdf import render_precheck_pdf

FINDING = {"file_version_id": "locked", "filename": "加密.xlsx", "location": "加密.xlsx", "responsibility": "材料侧"}
RESULT = {
    "outline_version": "PPAP-00 v0.14",
    "conclusion": "fail",
    "files": [
        {"file_version_id": "locked", "filename": "加密.xlsx", "level": "block", "document_type": None, "customer": None},
        {
            "file_version_id": "scan",
            "filename": "流程图.pdf",
            "level": "warn",
            "document_type": "过程流程图",
            "customer": "UAES",
            "entry_key": "U09",
            "criteria": [
                {"criterion_id": "U09:S1", "criterion": "文件标题含「包装方案」", "result": "未执行", "code": None, "location": "", "evidence": "", "reason": "文件无可读文字", "source": "l2"}
            ],
        },
    ],
    "blocks": [{**FINDING, "number": "B-01", "code": "B01", "title": "文件无法解析", "level": "block", "evidence": "文件加密", "problem": "文件无法读取。", "action": "解除密码后重新上传。"}],
    "warnings": [
        {
            **FINDING,
            "file_version_id": "scan",
            "filename": "流程图.pdf",
            "location": "流程图.pdf",
            "responsibility": "解析侧",
            "number": "W-01",
            "code": "W05",
            "title": "局部不可解析",
            "level": "warn",
            "evidence": "未读取到文件文字",
            "problem": "未读取到文件文字。",
            "action": "提供可编辑原件。",
        }
    ],
    "statuses": [],
    "counts": {"ok": 0, "warn": 1, "block": 1, "unmatched": 0, "reference": 0, "pending": 0},
}


CHAPTERS = (
    "一、结论摘要",
    "二、提交范围与文件集",
    "三、问题总览",
    "四、阻断项明细",
    "五、警告项明细",
    "六、状态登记",
    "七、整改建议汇总",
    "八、结论作用域与局限性声明",
    "九、附录 · 检查项全量结果",
    "十、附录 · 分则附加节",
)
SOURCE = {
    "task_number": "PPAP-1",
    "generated_at": "2026-11-12 10:00",
    "recognition": {"customer": "联合汽车电子有限公司", "project": "项目A", "model": None, "part_number": "F01RD0Y776", "part": "支架"},
    "precheck": RESULT,
}


def _flat(document) -> list[str]:
    return ["".join(page.get_text().split()) for page in document]


def test_report_has_a_cover_a_contents_page_and_the_ten_fixed_chapters_with_every_finding():
    with pymupdf.open(stream=render_precheck_pdf(SOURCE), filetype="pdf") as document:
        pages = _flat(document)
        toc = document.get_toc()
        text = "".join(pages)
    assert [entry for _level, entry, _page in toc] == ["封面", *CHAPTERS]
    for _level, title, page in toc[1:]:
        assert pages[page - 1].startswith(title.replace(" ", "")), title
    cover = pages[0]
    positions = [cover.index(item) for item in ("客户全称联合汽车电子有限公司", "项目项目A", "车型—", "零件号F01RD0Y776", "零件名称支架", "检查日期2026-11-12", "供应商名称", "PPAP版本", "SQE工程师")]
    assert positions == sorted(positions) and "结论摘要" not in cover and "第1/" not in cover
    assert pages[1].startswith("目录封面1")
    assert pages[2].endswith(f"PPAP-1-预检报告PPAP预检报告2026-11-12第3/{len(pages)}页")
    assert "U09:S1" in text and "已判定0项" in text and "文件无可读文字" in text
    assert "结论：不通过" in text and "「通过」只表示「能审」" in text
    assert document_metadata_is_clean(render_precheck_pdf(SOURCE))


def document_metadata_is_clean(pdf: bytes) -> bool:
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        values = " ".join(str(value) for value in document.metadata.values())
        return document.metadata["title"] == "PPAP-1-预检报告" and document.metadata["subject"] == "PPAP 预检报告" and "locked" not in values and "MuPDF" not in values


def test_findings_are_cards_with_the_outline_fields_and_unknown_fields_read_as_a_dash():
    with pymupdf.open(stream=render_precheck_pdf(SOURCE), filetype="pdf") as document:
        pages = _flat(document)
        toc = {entry: page for _level, entry, page in document.get_toc()}
    blocks = pages[toc["四、阻断项明细"] - 1]
    assert "B-01｜文件无法读取。" in blocks
    fields = ("位置", "证据", "判定依据", "原因码", "影响", "整改建议", "责任侧", "置信度", "关联", "出处")
    positions = [blocks.index(f"{field}：") for field in fields]
    assert positions == sorted(positions)
    assert "判定依据：—" in blocks and "原因码：B01文件无法解析" in blocks and "整改建议：解除密码后重新上传。" in blocks
    warnings = pages[toc["五、警告项明细"] - 1]
    assert warnings.index("警告是移交清单，不是免检证明。") < warnings.index("W-01｜未读取到文件文字。")


def test_status_register_lists_every_category_and_unknown_codes():
    statuses = [
        {"code": "S07", "scope": "content", "file_version_id": "scan", "filename": "流程图.pdf", "note": "比对源零件号不一致，该项无法比对。"},
        {"code": "S09", "file_version_id": "scan", "filename": "流程图.pdf", "note": "新状态。"},
    ]
    result = {**RESULT, "statuses": statuses}
    with pymupdf.open(stream=render_precheck_pdf({**SOURCE, "precheck": result}), filetype="pdf") as document:
        pages = _flat(document)
        toc = {entry: page for _level, entry, page in document.get_toc()}
    register = pages[toc["六、状态登记"] - 1]
    for code in ("S01", "S02", "S03", "S04", "S05", "S07", "未执行", "S09"):
        assert code in register, code
    assert "比对源零件号不一致S07流程图.pdf比对源零件号不一致，该项无法比对。" in register
    assert "范围外S01—本批无" in register


def test_a_task_without_a_number_is_named_without_any_identifier():
    with pymupdf.open(stream=render_precheck_pdf({**SOURCE, "task_number": None}), filetype="pdf") as document:
        assert document.metadata["title"] == "预检报告"
        assert "审核任务" not in "".join(_flat(document))
