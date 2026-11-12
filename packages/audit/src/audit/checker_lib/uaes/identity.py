"""Whether a written part number, revision or project is actual source data."""

from __future__ import annotations

import unicodedata

_PLACEHOLDERS = {"", "/", "-", "--", "—", "–", "na", "n/a", "n.a.", "none", "null", "tbd", "xx", "xxx", "xxxx", "无", "待填", "待定", "待确认", "未填写"}


def _identity(value) -> str:
    # Preserve punctuation: P12-3 is not P123, and step 10.1 is not 101.
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold()


_LABELS = {
    _identity(label)
    for label in (
        "零件号",
        "零件编号",
        "零件名称",
        "物料编号",
        "物料名称",
        "理想汽车零件号",
        "客户零件号",
        "Part number",
        "Part No",
        "Part No.",
        "版本",
        "版本号",
        "工程更改版本",
        "Revision",
        "版次",
        "修订版次",
        "项目",
        "项目名称",
        "车型项目",
        "项目代号",
        "UAES项目编号",
        "客户",
        "客户名称",
        "Customer",
        "Customer Name",
        "供应商名称",
        "供应商代码",
        "日期",
    )
}


def pair_identity(value) -> str | None:
    """A pairing key must be actual source data, not a placeholder or a label."""
    normalized = _identity(value)
    return None if normalized in _PLACEHOLDERS or normalized.rstrip(":：") in _LABELS else normalized
