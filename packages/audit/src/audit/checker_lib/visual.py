"""Excel-region observation. Delegates to the existing reader; no new service."""

from __future__ import annotations

from typing import Any


def observe_excel_region(*args: Any, **kwargs: Any) -> dict[str, object]:
    from audit.engine.checkers.excel_field_evidence import observe_excel_region as read_region

    return read_region(*args, **kwargs)
