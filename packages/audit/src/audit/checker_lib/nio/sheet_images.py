"""Where worksheet pictures sit, so a rule can ask whether a picture is in a given column or cell range."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ImageCells:
    """Cell boxes (1-based, inclusive) covered by the sheet's pictures, plus pictures without a cell position."""

    boxes: tuple[tuple[int, int, int, int], ...]
    unplaced: int

    def in_area(self, *, row_min: int, row_max: int, col_min: int, col_max: int) -> bool:
        return any(top <= row_max and bottom >= row_min and left <= col_max and right >= col_min for top, left, bottom, right in self.boxes)


def image_cells(ws: Any) -> ImageCells:
    boxes: list[tuple[int, int, int, int]] = []
    unplaced = 0
    for image in getattr(ws, "_images", None) or ():
        anchor = getattr(image, "anchor", None)
        start = getattr(anchor, "_from", None)
        if start is None:
            unplaced += 1
            continue
        end = getattr(anchor, "to", None) or start
        boxes.append((start.row + 1, start.col + 1, max(start.row, end.row) + 1, max(start.col, end.col) + 1))
    return ImageCells(tuple(boxes), unplaced)
