from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

_EXCEL_SERIAL_MIN = 20_000
_EXCEL_SERIAL_MAX = 80_000
_EXCEL_SERIAL_EPOCH = datetime(1899, 12, 30)


def normalize_excel_date(value: Any) -> date | None:
    """Normalize common Excel date representations to a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _date_from_excel_serial(float(value))

    text = str(value).strip()
    if not text or "xxxx" in text.lower():
        return None

    numeric_match = re.fullmatch(r"\d+(?:\.0+)?", text)
    if numeric_match:
        parsed = _date_from_excel_serial(float(text))
        if parsed is not None:
            return parsed

    compact = text.replace("年", "-").replace("月", "-").replace("日", "")
    compact = re.sub(r"\s+", " ", compact).strip()
    compact = re.sub(r"^(\d{4}[./-]\d{1,2}[./-]\d{1,2})\s+.*$", r"\1", compact)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(compact, fmt).date()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(compact).date()
    except ValueError:
        return None


def _date_from_excel_serial(serial: float) -> date | None:
    if not (_EXCEL_SERIAL_MIN <= serial <= _EXCEL_SERIAL_MAX):
        return None
    try:
        return (_EXCEL_SERIAL_EPOCH + timedelta(days=serial)).date()
    except Exception:
        return None
