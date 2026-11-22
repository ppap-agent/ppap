import pytest
from documents.location import location_label


@pytest.mark.parametrize(
    ("locator", "label"),
    [
        ({"kind": "xlsx_cell", "sheet": "控制计划", "cell": "B12"}, "工作表「控制计划」B12"),
        ({"kind": "xlsx_range", "sheet": "控制计划", "start": "A1", "end": "C3"}, "工作表「控制计划」A1:C3"),
        ({"kind": "pdf_block", "page": 2, "block": 4}, "第 2 页第 4 段"),
        ({"kind": "pdf_cell", "page": 3, "row": 12, "column": 4}, "第 3 页表格第 12 行第 4 列"),
        ({"kind": "pdf_page", "page": 4, "region": [None, 10.5, None, 80.25]}, "第 4 页"),
        ({"kind": "docx_paragraph", "paragraph": 7}, "第 7 段"),
        ({"kind": "docx_cell", "table": 1, "row": 3, "column": 2}, "第 1 个表格第 3 行第 2 列"),
    ],
)
def test_every_public_location_reads_as_one_sentence(locator, label):
    assert location_label(locator) == label


def test_an_unknown_location_has_no_wording():
    with pytest.raises(ValueError, match="location_unavailable"):
        location_label({"kind": "published_clause"})
