"""A blank cell stays blank under grid rules drawn as one filled path and under a neighbour's rule ending at its edge."""

import fitz
from audit.pdf_tables import graphic_inside


def _page(tmp_path, *, mark: bool):
    path = tmp_path / f"grid-{mark}.pdf"
    with fitz.open() as document:
        page = document.new_page(width=300, height=200)
        shape = page.new_shape()
        # The grid's rules as thin rectangles, each closed back to its start, in one filled path (V040 control plan).
        for x0, y0, x1, y1 in ((20, 20, 280, 20.5), (20, 20, 20.5, 180), (120, 20, 120.5, 180), (20, 179.5, 280, 180)):
            shape.draw_polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])
        shape.finish(color=None, fill=(0, 0, 0))
        shape.commit()
        # A neighbour's row rule that starts 0.3 pt inside this cell's right edge, and a white background beside it.
        page.draw_rect(fitz.Rect(119.7, 90, 280, 90.5), color=None, fill=(0, 0, 0))
        page.draw_rect(fitz.Rect(5, 20, 20.2, 180), color=None, fill=(1, 1, 1))
        if mark:
            page.draw_circle((70, 100), 6, fill=(0, 0, 0))
        document.save(path)
    return str(path)


def test_rules_and_spill_are_not_cell_content(tmp_path):
    assert graphic_inside(_page(tmp_path, mark=False), 1, [20, 20, 120, 180], 1.0) is False


def test_a_mark_inside_the_cell_is_content(tmp_path):
    assert graphic_inside(_page(tmp_path, mark=True), 1, [20, 20, 120, 180], 1.0) is True
