"""The title block of a UAES part drawing (图号 / DWG No., 名称 / DESIGNATION, 版本 / VERSION), as one image transcription returns it.

UAES drawings (Z_F01R10Y776.pdf) letter the title block at the bottom right with vector strokes, outside the text layer.
"""

_TEXT = {"type": ["string", "null"]}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["drawing_number", "designation", "version"],
    "properties": {"drawing_number": _TEXT, "designation": _TEXT, "version": _TEXT},
}

PROMPT = (
    "这是一份零件图纸右下角标题栏区域的图像。请按 JSON Schema 转写，只转写，不判断。\n"
    "drawing_number：“图号 / DWG No.”栏中的编号；designation：“名称 / DESIGNATION”栏的内容；version：“版本 / VERSION”栏的内容。\n"
    "照原样转写，保留空格；栏位空白写空字符串；图中没有该栏或看不清写 null。不要转写“首次用于 / FIRST APPLICAT.”等其他栏的编号。"
)
