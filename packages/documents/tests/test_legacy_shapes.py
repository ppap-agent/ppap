"""Option boxes come from the XLS drawing layer, so rendering and fonts cannot change them."""

from pathlib import Path

from documents.excel.legacy_shapes import option_boxes

# 控制计划 -CETUS.xls, content-addressed in the corpus.
CETUS_XLS = Path(__file__).resolve().parents[3] / "assets" / "corpus" / "files" / "09ea288088c0028eb96f422b04b62d867dca37c6778ff9518766e4df46809620.xls"


def test_overpainted_box_shows_its_top_shape_and_only_the_last_option_is_filled():
    content = CETUS_XLS.read_bytes()
    boxes = option_boxes(content, "扰流板", 4)
    assert [box.filled for box in boxes] == [False, False, True]
    assert [box.column for box in boxes] == sorted(box.column for box in boxes)


def test_row_without_shapes_has_no_boxes():
    content = CETUS_XLS.read_bytes()
    assert option_boxes(content, "扰流板", 40) == []
