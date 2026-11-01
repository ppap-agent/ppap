"""Bounded, read-only content excerpts. No macros, LLM calls or office processes."""

from io import BytesIO
from itertools import islice
from pathlib import Path
from typing import Any
from zipfile import ZipFile

MAX_PREVIEW_BYTES = 25 * 1024 * 1024
MAX_TEXT = 50000


def document_preview(content: bytes, filename: str, sheet: str = "", start_row: int = 1) -> dict:
    unsupported = {"available": False, "notice": "此格式或大小不支持内容预览，请下载原件。"}
    if len(content) > MAX_PREVIEW_BYTES:
        return unsupported
    extension = Path(filename).suffix.lower()
    lines = []
    metadata: dict[str, Any] = {}
    if extension in {".md", ".txt", ".csv", ".json"}:
        text = content[: MAX_TEXT * 4].decode("utf-8-sig", errors="replace")
    elif extension in {".docx", ".xlsx", ".xlsm"}:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 2048 or sum(e.file_size for e in entries) > 16 * 1024 * 1024:
                return unsupported
        if extension == ".docx":
            from docx import Document

            doc = Document(BytesIO(content))
            # Preserve paragraph/table order in the main document body.
            from docx.table import Table
            from docx.text.paragraph import Paragraph

            for block in islice(doc.element.body, 300):
                if block.tag.endswith("}p"):
                    lines.append(Paragraph(block, doc).text)
                elif block.tag.endswith("}tbl"):
                    lines.append("[表格]")
                    for row in islice(Table(block, doc).rows, 100):
                        lines.append(" | ".join(cell.text[:1000] for cell in row.cells[:40]))
                if sum(map(len, lines)) >= MAX_TEXT:
                    break
        else:
            from openpyxl import load_workbook

            book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
            try:
                metadata["sheets"] = book.sheetnames
                if sheet and sheet not in book.sheetnames:
                    return {"available": False, "sheets": book.sheetnames, "notice": "未找到该工作表，请重新选择。"}
                if sheet:
                    start_row = max(1, min(start_row, 1048576))
                    metadata.update(sheet=sheet, start_row=start_row, rows=[], next_row=None)
                    worksheets: list[Any] = [book[sheet]]
                else:
                    worksheets = book.worksheets[:5]
                for worksheet in worksheets:
                    lines.append("[工作表] " + worksheet.title)
                    first = start_row if sheet else 1
                    last = min(first + 199, worksheet.max_row or first + 199)
                    for number, row in enumerate(worksheet.iter_rows(min_row=first, max_row=last, max_col=40), first):
                        cells = [f"{cell.coordinate}: {str(cell.value)[:1000]}" for cell in row if cell.value is not None]
                        if cells:
                            line = " | ".join(cells)
                            lines.append(line)
                            if sheet:
                                metadata["rows"].append({"number": number, "text": line})
                        if sheet:
                            metadata["end_row"] = number
                            metadata["next_row"] = number + 1 if number < (worksheet.max_row or last) else None
                        if sum(map(len, lines)) >= MAX_TEXT:
                            break
                    if sum(map(len, lines)) >= MAX_TEXT:
                        break
            finally:
                book.close()
        text = "\n".join(lines)
    else:
        return unsupported
    notice = "简化内容摘录，不代表完整原件或审核覆盖；Office 默认前 5 张表、每表 200 行、前 40 列 / Word 前 300 个内容块。公式不执行，排版与图片请查看原件。"
    if sheet and metadata.get("sheet"):
        notice = "工作表摘录：每次最多 200 行、前 40 列，内容有长度限制。空行省略；公式不执行，排版、图片和完整内容请核对原件。"
    return {"available": True, "text": text[:MAX_TEXT], "notice": notice, **metadata}
