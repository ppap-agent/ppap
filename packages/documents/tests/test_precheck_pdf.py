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


def test_report_has_the_ten_fixed_chapters_and_every_finding():
    pdf = render_precheck_pdf({"task_number": "PPAP-1", "generated_at": "2026-11-12 10:00", "precheck": RESULT})
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        text = "".join(page.get_text() for page in document).replace(" ", "").replace("\n", "")
    chapters = (
        "一、结论摘要",
        "二、提交范围与文件集",
        "三、问题总览",
        "四、阻断项明细",
        "五、警告项明细",
        "六、状态登记",
        "七、整改建议汇总",
        "八、结论作用域与局限性声明",
        "九、附录·检查项全量结果",
        "十、附录·分则附加节",
    )
    positions = [text.index(chapter) for chapter in chapters]
    assert positions == sorted(positions)
    assert "U09:S1" in text and "已判定0项" in text and "文件无可读文字" in text
    assert "结论：不通过" in text and "B-01" in text and "W-01" in text and "「通过」只表示「能审」" in text
