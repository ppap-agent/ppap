"""Vector marks on a PDF page become pictures on the converted sheet; table rulings do not."""

from __future__ import annotations

from dataclasses import replace

import pymupdf
import pytest
from documents.pdf import DEFAULT_SPEC, OutputSpec, PdfSpec, convert_pdf_to_excel, validate_pdf_spec
from openpyxl import load_workbook

# A 3 x 3 ruled table: columns at x = 50/150/250/350, rows at y = 50/90/130/170.
_XS = (50, 150, 250, 350)
_YS = (50, 90, 130, 170)


def _ruled_table_pdf(*, circle: bool) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=400, height=300)
    for x in _XS:
        page.draw_line((x, _YS[0]), (x, _YS[-1]))
    for y in _YS:
        page.draw_line((_XS[0], y), (_XS[-1], y))
    # A shaded cell is a ruling as well (an axis-aligned rectangle).
    page.draw_rect(pymupdf.Rect(_XS[2] + 2, _YS[2] + 2, _XS[3] - 2, _YS[3] - 2), color=None, fill=(0.9, 0.9, 0.9))
    for row, y in enumerate(_YS[:-1]):
        for col, x in enumerate(_XS[:-1]):
            page.insert_text((x + 5, y + 15), f"R{row}C{col}")
    if circle:
        # Drawn in the middle cell (row 2, column 2 on the sheet).
        page.draw_circle((200, 115), 10, color=(1, 0, 0), width=1.5)
    data = document.tobytes()
    document.close()
    return data


def _convert(tmp_path, pdf: bytes, **output) -> list:
    spec = replace(DEFAULT_SPEC, output=OutputSpec(**output))
    target = tmp_path / "converted.xlsx"
    convert_pdf_to_excel(pdf, str(target), spec=spec)
    workbook = load_workbook(target)
    assert workbook.worksheets[0]["A1"].value == "R0C0"
    return workbook.worksheets[0]._images


def test_a_drawn_circle_becomes_one_picture_anchored_at_its_cell(tmp_path):
    images = _convert(tmp_path, _ruled_table_pdf(circle=True), rasterize_vector_graphics=True)

    assert len(images) == 1
    anchor = images[0].anchor._from
    assert (anchor.row + 1, anchor.col + 1) == (2, 2)


def test_table_rulings_and_shading_produce_no_picture(tmp_path):
    assert _convert(tmp_path, _ruled_table_pdf(circle=False), rasterize_vector_graphics=True) == []


def test_vector_marks_stay_off_by_default(tmp_path):
    assert _convert(tmp_path, _ruled_table_pdf(circle=True)) == []
    assert _convert(tmp_path, _ruled_table_pdf(circle=True), preserve_pdf_embedded_images=True) == []


@pytest.mark.parametrize("dpi", [0, 1201])
def test_vector_graphic_dpi_is_validated(dpi):
    with pytest.raises(ValueError, match="vector_graphic_dpi"):
        validate_pdf_spec(PdfSpec(output=OutputSpec(rasterize_vector_graphics=True, vector_graphic_dpi=dpi)))
