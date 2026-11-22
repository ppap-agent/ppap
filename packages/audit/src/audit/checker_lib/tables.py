"""Header, window, and merge helpers shared by directory checkers."""

from __future__ import annotations

from collections.abc import Mapping

from audit.checker_lib.excel import column_letter


def is_blank(value: object) -> bool:
    return value is None or not str(value).strip()


def normalize_label(value: object) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch not in " \t\r\n/／、，,;；:：()（）")


def label_matches(value: object, aliases: tuple[str, ...]) -> bool:
    text = normalize_label(value)
    if not text:
        return False
    return any(text == normalize_label(alias) or text.startswith(normalize_label(alias)) for alias in aliases)


def find_headers(sheet, aliases: Mapping[str, tuple[str, ...]], *, max_rows: int, max_cols: int):
    """Best header row inside the scan window. ``None`` when no alias matches."""
    best = None
    limit_row = min(int(sheet.max_row or 1), max_rows)
    limit_col = min(int(sheet.max_column or 1), max_cols)
    for row in range(1, limit_row + 1):
        found: dict[str, int] = {}
        for col in range(1, limit_col + 1):
            for role, names in aliases.items():
                if role not in found and label_matches(sheet.cell(row, col).value, names):
                    found[role] = col
        if found and (best is None or len(found) > len(best[1])):
            best = (row, found)
            if len(found) == len(aliases):
                break
    return best


def cell_span(row: int, last_row: int, col: int, last_col: int) -> tuple[str, str]:
    return f"{column_letter(col)}{row}", f"{column_letter(last_col)}{max(last_row, row)}"
