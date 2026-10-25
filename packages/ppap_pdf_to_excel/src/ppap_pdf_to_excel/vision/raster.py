"""Source-grounded table topology for image-only PDF pages.

Raster lines, not a language model, define cell identities and merge spans.
No document text is inferred here. Transcription happens from separately
cropped, coordinate-bound physical cells after this grid passes validation.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)

MAX_IMAGE_PIXELS = 12_000_000
MAX_GRID_CELLS = 400


@dataclass(frozen=True)
class RasterCell:
    index: int
    row: int
    col: int
    end_row: int
    end_col: int
    bbox: tuple[int, int, int, int]


@dataclass(frozen=True)
class RasterGrid:
    row_count: int
    col_count: int
    cells: tuple[RasterCell, ...]
    merges: tuple[tuple[int, int, int, int], ...]
    bbox: tuple[int, int, int, int]


def _clusters(scores: np.ndarray, minimum: float) -> list[int]:
    indices = np.flatnonzero(scores >= minimum)
    if not indices.size:
        return []
    groups: list[tuple[int, int]] = []
    first = last = int(indices[0])
    for index in indices[1:]:
        position = int(index)
        if position - last > 4:
            groups.append((first, last))
            first = position
        last = position
    groups.append((first, last))
    return [(low + high) // 2 for low, high in groups]


def _line_fraction(mask: np.ndarray, left: int, top: int, right: int, bottom: int, *, vertical: bool) -> float:
    clipped = mask[max(0, top) : bottom, max(0, left) : right]
    if not clipped.size:
        return 0.0
    return float(np.mean(np.any(clipped != 0, axis=1 if vertical else 0)))


def detect_raster_grids(png: bytes) -> list[RasterGrid]:
    """Detect only unambiguous rectangular scan grids with physical borders.

    Intermediate boundaries with uncertain line continuity reject the entire
    grid, rather than silently manufacturing merged cells from noisy pixels.
    """
    raw = np.frombuffer(png, dtype=np.uint8)
    gray = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
    if gray is None or len(gray.shape) != 2:
        raise ValueError("Invalid scanned PDF page image")
    height, width = gray.shape
    if height * width > MAX_IMAGE_PIXELS:
        logger.warning("Raster grid unavailable: reason=image_size_limit")
        return []
    if height < 120 or width < 120:
        return []

    black = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12
    )
    horizontal = cv2.morphologyEx(
        black, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(24, width // 65), 1))
    )
    vertical = cv2.morphologyEx(
        black, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(24, height // 80)))
    )
    horizontal = cv2.dilate(horizontal, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    vertical = cv2.dilate(vertical, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    drawing = cv2.bitwise_or(horizontal, vertical)
    drawing = cv2.morphologyEx(drawing, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
    contours, _hierarchy = cv2.findContours(drawing, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    grids: list[RasterGrid] = []
    for contour in contours:
        left, top, span_x, span_y = cv2.boundingRect(contour)
        if span_x < max(120, width * 0.27) or span_y < max(60, height * 0.06):
            continue
        hscore = np.count_nonzero(horizontal[top : top + span_y, left : left + span_x], axis=1)
        vscore = np.count_nonzero(vertical[top : top + span_y, left : left + span_x], axis=0)
        ys = [top + coordinate for coordinate in _clusters(hscore, max(16, span_x * 0.16))]
        xs = [left + coordinate for coordinate in _clusters(vscore, max(16, span_y * 0.035))]
        rows, cols = len(ys) - 1, len(xs) - 1
        if not 2 <= rows <= 250 or not 2 <= cols <= 70 or rows * cols > MAX_GRID_CELLS:
            continue
        parent = list(range(rows * cols))

        def root(value: int) -> int:
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def join(one: int, two: int) -> None:
            parent[root(one)] = root(two)

        valid = True
        for row in range(rows):
            for col in range(cols):
                if col + 1 < cols:
                    x = xs[col + 1]
                    fraction = _line_fraction(
                        vertical, x - 2, ys[row] + 3, x + 3, ys[row + 1] - 3, vertical=True
                    )
                    if fraction <= 0.20:
                        join(row * cols + col, row * cols + col + 1)
                    elif fraction < 0.73:
                        valid = False
                if row + 1 < rows:
                    y = ys[row + 1]
                    fraction = _line_fraction(
                        horizontal, xs[col] + 3, y - 2, xs[col + 1] - 3, y + 3, vertical=False
                    )
                    if fraction <= 0.20:
                        join(row * cols + col, (row + 1) * cols + col)
                    elif fraction < 0.73:
                        valid = False
        if not valid:
            continue

        grouped: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for row in range(rows):
            for col in range(cols):
                grouped[root(row * cols + col)].append((row, col))
        candidate_cells = []
        merges = []
        for coordinates in grouped.values():
            row0 = min(row for row, _col in coordinates)
            row1 = max(row for row, _col in coordinates)
            col0 = min(col for _row, col in coordinates)
            col1 = max(col for _row, col in coordinates)
            if len(coordinates) != (row1 - row0 + 1) * (col1 - col0 + 1):
                valid = False
                break
            candidate_cells.append(
                (row0, col0, row1, col1, (xs[col0], ys[row0], xs[col1 + 1], ys[row1 + 1]))
            )
            if len(coordinates) > 1:
                merges.append((row0, col0, row1, col1))
        if not valid:
            continue
        candidate_cells.sort(key=lambda cell: (cell[0], cell[1]))
        cells = tuple(
            RasterCell(index, row0, col0, row1, col1, bounds)
            for index, (row0, col0, row1, col1, bounds) in enumerate(candidate_cells)
        )
        grids.append(
            RasterGrid(
                row_count=rows,
                col_count=cols,
                cells=cells,
                merges=tuple(sorted(merges)),
                bbox=(left, top, span_x, span_y),
            )
        )

    grids.sort(key=lambda grid: (grid.bbox[1], grid.bbox[0]))
    if not grids:
        logger.warning(
            "Raster source grid unavailable: image_width=%d image_height=%d connected_regions=%d reason=no_reliable_line_grid",
            width,
            height,
            len(contours),
        )
    else:
        logger.info(
            "Raster source grid scan: image_width=%d image_height=%d connected_regions=%d verified_grids=%d",
            width,
            height,
            len(contours),
            len(grids),
        )
    return grids


def make_cell_panels(png: bytes, cells: tuple[RasterCell, ...]) -> list[bytes]:
    """Label scanned cell crops *outside* their image evidence for OCR.

    Up to eight full-width crops per image; each label is a physical cell ID.
    A vision model may transcribe these crops but cannot change their layout.
    """
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Invalid scanned PDF page image")
    panels = []
    for start in range(0, len(cells), 8):
        tiles: list[np.ndarray] = []
        for cell in cells[start : start + 8]:
            x0, y0, x1, y1 = cell.bbox
            crop = image[y0 + 3 : y1 - 3, x0 + 3 : x1 - 3]
            if not crop.size:
                raise ValueError("Raster source cell is too small to transcribe")
            ratio = min(1000 / crop.shape[1], 140 / crop.shape[0])
            resized = cv2.resize(
                crop,
                (max(1, int(crop.shape[1] * ratio)), max(1, int(crop.shape[0] * ratio))),
                interpolation=cv2.INTER_CUBIC if ratio > 1 else cv2.INTER_AREA,
            )
            tile = np.full((resized.shape[0] + 30, 1024, 3), 255, dtype=np.uint8)
            cv2.putText(
                tile, f"CELL ID {cell.index}", (12, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 1, cv2.LINE_AA
            )
            tile[30 : 30 + resized.shape[0], : resized.shape[1]] = resized
            tiles.append(tile)
        board = np.full((sum(tile.shape[0] + 5 for tile in tiles), 1024, 3), 255, dtype=np.uint8)
        y = 0
        for tile in tiles:
            board[y : y + tile.shape[0]] = tile
            y += tile.shape[0] + 5
        encoded, result = cv2.imencode(".png", board, [cv2.IMWRITE_PNG_COMPRESSION, 4])
        if not encoded:
            raise ValueError("Raster crop encoding failed")
        panels.append(result.tobytes())
    return panels
