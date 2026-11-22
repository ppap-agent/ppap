"""The one wording of an original location that people read: on the web, in both plugins and in the reports."""


def location_label(locator: dict) -> str:
    """A public original location as a sentence, e.g. 工作表「控制计划」B12, 第 2 页第 4 段, 第 1 个表格第 3 行第 2 列."""
    kind = locator.get("kind")
    if kind == "xlsx_cell":
        return f"工作表「{locator['sheet']}」{locator['cell']}"
    if kind == "xlsx_range":
        return f"工作表「{locator['sheet']}」{locator['start']}:{locator['end']}"
    if kind == "pdf_page":
        return f"第 {locator['page']} 页"
    if kind == "pdf_block":
        return f"第 {locator['page']} 页第 {locator['block']} 段"
    if kind == "pdf_cell":
        return f"第 {locator['page']} 页表格第 {locator['row']} 行第 {locator['column']} 列"
    if kind == "docx_paragraph":
        return f"第 {locator['paragraph']} 段"
    if kind == "docx_cell":
        return f"第 {locator['table']} 个表格第 {locator['row']} 行第 {locator['column']} 列"
    raise ValueError("location_unavailable")
