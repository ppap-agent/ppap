"""PDF bytes are derived from one frozen machine result, not another audit."""

from copy import deepcopy

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
