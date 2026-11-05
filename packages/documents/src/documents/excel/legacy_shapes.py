"""Filled option boxes read from a binary XLS drawing layer, independent of how text renders."""

import struct
from dataclasses import dataclass

import xlrd
from xlrd import compdoc

_BOUNDSHEET, _DRAWING, _CONTINUE, _EOF = 0x85, 0xEC, 0x3C, 0x0A
_SHAPE, _PROPERTIES, _ANCHOR = 0xF00A, 0xF00B, 0xF010
_RECTANGLE, _FILL_COLOR, _FILL_FLAGS = 1, 0x181, 0x1BF


@dataclass(frozen=True)
class OptionBox:
    row: int
    column: int
    offset: int
    filled: bool | None


def _stream(content: bytes) -> bytes:
    document = compdoc.CompDoc(content)
    stream = document.get_named_stream("Workbook") or document.get_named_stream("Book")
    if not stream:
        raise ValueError("XLS workbook stream is missing")
    return stream


def _records(stream: bytes, start: int = 0):
    position = start
    while position + 4 <= len(stream):
        kind, size = struct.unpack("<HH", stream[position : position + 4])
        yield kind, stream[position + 4 : position + 4 + size]
        position += 4 + size


def _sheet_drawing(stream: bytes, index: int) -> bytes:
    offsets = [struct.unpack("<I", body[:4])[0] for kind, body in _records(stream) if kind == _BOUNDSHEET]
    drawing, previous = bytearray(), None
    for kind, body in _records(stream, offsets[index]):
        if kind == _EOF:
            break
        if kind == _DRAWING or (kind == _CONTINUE and previous == _DRAWING):
            drawing += body
            previous = _DRAWING
        else:
            previous = kind
    return bytes(drawing)


def _art_records(drawing: bytes, start: int = 0, end: int | None = None):
    position, end = start, len(drawing) if end is None else end
    while position + 8 <= end:
        head, kind, size = struct.unpack("<HHI", drawing[position : position + 8])
        if head & 0xF == 0xF:
            yield from _art_records(drawing, position + 8, position + 8 + size)
        else:
            yield kind, head >> 4, drawing[position + 8 : position + 8 + size]
        position += 8 + size


def _filled(properties: dict[int, int]) -> bool | None:
    color = properties.get(_FILL_COLOR, 0xFFFFFF)
    if color >> 24:  # Indexed and scheme colors need a palette this reader does not resolve.
        return None
    if not properties.get(_FILL_FLAGS, 0x10) & 0x10:
        return False
    red, green, blue = color & 0xFF, color >> 8 & 0xFF, color >> 16 & 0xFF
    return (red * 299 + green * 587 + blue * 114) / 1000 < 128


def option_boxes(content: bytes, sheet_name: str, row: int) -> list[OptionBox] | None:
    """Visible rectangles on a 1-based row, left to right; None when other shapes may carry the mark."""
    book = xlrd.open_workbook(file_contents=content, on_demand=True)
    try:
        index = book.sheet_names().index(sheet_name)
    finally:
        book.release_resources()
    shapes: list[dict] = []
    for kind, instance, body in _art_records(_sheet_drawing(_stream(content), index)):
        if kind == _SHAPE:
            shapes.append({"type": instance, "properties": {}})
        elif kind == _PROPERTIES and shapes:
            for start in range(0, instance * 6, 6):
                identifier, value = struct.unpack("<HI", body[start : start + 6])
                shapes[-1]["properties"][identifier & 0x3FFF] = value
        elif kind == _ANCHOR and shapes and len(body) >= 18:
            _, column, offset, top, _, _, _, bottom, _ = struct.unpack("<9H", body[:18])
            shapes[-1].update(column=column, offset=offset, top=top + 1, bottom=bottom + 1)
    # Later shapes paint over earlier ones, so one anchor shows only its last shape.
    visible: dict[tuple[int, int, int], OptionBox] = {}
    for shape in shapes:
        if "column" not in shape or not shape["top"] <= row <= shape["bottom"]:
            continue
        if shape["type"] != _RECTANGLE:
            return None
        visible[(shape["column"], shape["offset"], shape["top"])] = OptionBox(row, shape["column"], shape["offset"], _filled(shape["properties"]))
    return sorted(visible.values(), key=lambda box: (box.column, box.offset))
