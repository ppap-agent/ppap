"""Shared check helpers for SubSupplierListExcelAgent."""

import re
from typing import Any


def is_decimal_with_four_places(cell: Any) -> bool:
    """检查值是否为保留四位小数的数字。"""
    if cell.value is None:
        return False
    if isinstance(cell.value, str):
        s_value = cell.value.strip()
        if not s_value:
            return False
        try:
            float(s_value)
        except ValueError:
            return False

    num_format = cell.number_format
    if num_format:
        if ".0000" in num_format and ".00000" not in num_format:
            if "%" not in num_format:
                return True

    if isinstance(cell.value, str):
        s_value = cell.value.strip()
        if "." in s_value:
            decimal_part = s_value.rsplit(".", 1)[1]
            if decimal_part.isdigit() and len(decimal_part) == 4:
                return True

    return False


def has_content(value: Any) -> bool:
    if value is None or (str(value).strip().lower() == "none"):
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def is_valid_part_number(value: Any) -> bool:
    pattern = r"[a-zA-Z0-9_-]+"
    if value is None:
        return False
    normalized = str(value).strip()
    if not normalized:
        return False
    return re.fullmatch(pattern, normalized) is not None


def is_valid_serial_number(value: Any) -> bool:
    if value is None or (str(value).strip().lower() == "none"):
        return False

    if isinstance(value, int):
        return True

    if isinstance(value, float):
        return value.is_integer()

    if isinstance(value, str):
        value = value.strip()
        try:
            int(value)
            return True
        except ValueError:
            return False

    return False


def clean_str_value(value: Any) -> str:
    return "".join(str(value).split()).lower()


def is_ignorable_part_no(value: Any) -> bool:
    text = str(value).strip()
    return text in {"", "/", "-", "—"}
