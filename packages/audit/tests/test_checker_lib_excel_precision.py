"""Display metadata is source evidence; Python float spelling is not."""

import hashlib
import io

import pytest
from audit.checker_lib.excel import displayed_decimal_places, load_frozen_workbook
from openpyxl import Workbook


@pytest.mark.parametrize(
    ("value", "number_format", "expected"),
    [
        (1.2, "0.0000", 4),
        (0, "#,##0.0000", 4),
        (1.234567, "0.0000", 4),
        (1.2, "0.000", 3),
        (1.2, "0.00000", 5),
        (1, "0", 0),
        (1.2345, "General", None),
        (1.2, "0.0000%", None),
        (1.2, "0.0000E+00", None),
        (1.2, "0.0000;[Red]-0.0000", None),
        ("1.2300", "General", 4),
        ("0.0000", "@", 4),
        ("-1.2300", "General", 4),
        ("1.23", "0.0000", 2),
        ("=1+1", "0.0000", None),
    ],
)
def test_decimal_places_comes_from_explicit_original_display(value, number_format, expected):
    cell = Workbook().active["A1"]
    cell.value, cell.number_format = value, number_format
    assert displayed_decimal_places(cell) == expected


@pytest.mark.parametrize("value", [None, True, False, "", "1.2300 kg", "1e-4", "NaN", float("nan"), float("inf")])
def test_non_numeric_or_non_decimal_literal_is_not_accepted(value):
    cell = Workbook().active["A1"]
    cell.value, cell.number_format = value, "0.0000"
    with pytest.raises(ValueError):
        displayed_decimal_places(cell)


def test_format_reader_uses_frozen_bytes_and_keeps_formula_metadata(tmp_path):
    book = Workbook()
    book.active["A1"] = "=1+1"
    book.active["A1"].number_format = "0.0000"
    output = io.BytesIO()
    book.save(output)
    book.close()
    content = output.getvalue()
    path = tmp_path / "original.xlsx"
    path.write_bytes(content)
    original = load_frozen_workbook(str(path), hashlib.sha256(content).hexdigest())
    try:
        assert original.active["A1"].data_type == "f"
        assert original.active["A1"].number_format == "0.0000"
    finally:
        original.close()
    with pytest.raises(ValueError, match="Frozen source identity changed"):
        load_frozen_workbook(str(path), "0" * 64)
