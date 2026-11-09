"""Ambiguous labels cannot establish a complete seal inspection region."""

import hashlib
from pathlib import Path

import fitz
import pytest
from audit.engine.checkers.pdf_field_region import pdf_field_region


@pytest.mark.parametrize("reverse", [False, True])
def test_multiple_company_labels_do_not_select_one_by_text_stream_order(reverse):
    class Page:
        rect = fitz.Rect(0, 0, 600, 850)

        def search_for(self, label):
            if label == "公司名称":
                boxes = [fitz.Rect(370, 180, 410, 190), fitz.Rect(450, 180, 490, 190)]
                return boxes[::-1] if reverse else boxes
            return []

    page = Page()
    assert pdf_field_region(page, {"psw_stamp_present", "psw_stamp_ending_text"}) == page.rect


def test_single_company_label_still_keeps_its_complete_focused_region():
    class Page:
        rect = fitz.Rect(0, 0, 600, 850)

        def search_for(self, label):
            return {"公司名称": [fitz.Rect(400, 600, 450, 610)], "认可情况": [fitz.Rect(50, 700, 100, 710)]}.get(label, [])

    region = pdf_field_region(Page(), {"psw_stamp_present", "psw_stamp_ending_text"})
    assert region.contains(fitz.Rect(400, 610, 570, 690))
    assert region.y0 > 500 and region.y1 < 700


def test_shanghai_yada_instruction_labels_do_not_crop_away_the_warrant():
    source = Path(__file__).resolve().parents[3] / "assets/V2.0测试/模拟测试文件V2-01/16-零件提交保证书/16-零件提交保证书-上海亚大.pdf"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == "24133e74c1dac80339a694863930121b9525d4b0d887f7e020b9ed82f3014c0b"
    with fitz.open(source) as document:
        page = document[4]
        assert len(page.search_for("公司名称")) == 2
        assert pdf_field_region(page, {"psw_stamp_present", "psw_stamp_ending_text"}) == page.rect
