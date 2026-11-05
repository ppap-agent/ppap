"""Option boxes come from the XLS drawing layer, so rendering and fonts cannot change them."""

from pathlib import Path

from documents.excel.legacy_shapes import option_boxes

CETUS_XLS = Path(__file__).resolve().parents[3] / "assets" / "corpus" / "documents" / "v3" / "Cetus Spoiler-有特殊特性清单" / "控制计划 -CETUS.xls"


def test_overpainted_box_shows_its_top_shape_and_only_the_last_option_is_filled():
    content = CETUS_XLS.read_bytes()
    boxes = option_boxes(content, "扰流板", 4)
    assert [box.filled for box in boxes] == [False, False, True]
    assert [box.column for box in boxes] == sorted(box.column for box in boxes)


def test_row_without_shapes_has_no_boxes():
    content = CETUS_XLS.read_bytes()
    assert option_boxes(content, "扰流板", 40) == []
