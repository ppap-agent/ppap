"""Source-grounded table topology for image-only PDF pages.

Raster lines, not a language model, define cell identities and merge spans.
No document text is inferred here. Transcription happens from separately
cropped, coordinate-bound physical cells after this grid passes validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)

MAX_IMAGE_PIXELS = 12_000_000
MAX_GRID_CELLS = 4096


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


def _boundaries(values: list[int], line_scores: np.ndarray) -> list[int]:
    groups: list[list[int]] = []
    for value in sorted(values):
        previous = groups[-1][-1] if groups else value
        same_border = value > previous and float(np.mean(line_scores[previous:value])) >= 0.5
        if not groups or value - previous > 5 and not same_border:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [int(np.median(group)) for group in groups]


def _line_fraction(mask: np.ndarray, left: int, top: int, right: int, bottom: int, *, vertical: bool) -> float:
    clipped = mask[max(0, top) : bottom, max(0, left) : right]
    if not clipped.size:
        return 0.0
    return float(np.mean(np.any(clipped != 0, axis=1 if vertical else 0)))


def _line_masks(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    height, width = gray.shape
    black = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    horizontal = cv2.morphologyEx(black, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(24, width // 65), 1)))
    vertical = cv2.morphologyEx(black, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(24, height // 80))))
    horizontal = cv2.dilate(horizontal, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    vertical = cv2.dilate(vertical, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    drawing = cv2.bitwise_or(horizontal, vertical)
    drawing = cv2.morphologyEx(drawing, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
    return horizontal, vertical, drawing


def detect_raster_grids(png: bytes) -> list[RasterGrid]:
    """Use closed physical cell faces, not missing global line segments.

    A long table can have different columns in its header and body. Every
    accepted face must have four supported borders, and its grid slots must
    form a complete, non-overlapping partition. Uncertain faces fail closed.
    """
    gray = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None or len(gray.shape) != 2:
        raise ValueError("Invalid scanned PDF page image")
    height, width = gray.shape
    if height * width > MAX_IMAGE_PIXELS or height < 120 or width < 120:
        return []
    horizontal, vertical, drawing = _line_masks(gray)
    contours, hierarchy = cv2.findContours(drawing, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []

    grids: list[RasterGrid] = []
    for parent_index, contour in enumerate(contours):
        if hierarchy[0][parent_index][3] != -1:
            continue
        left, top, span_x, span_y = cv2.boundingRect(contour)
        if span_x < max(120, width * 0.27) or span_y < max(60, height * 0.06):
            continue
        faces: list[tuple[int, int, int, int]] = []
        valid = True
        for index, face_contour in enumerate(contours):
            if hierarchy[0][index][3] != parent_index:
                continue
            x, y, sx, sy = cv2.boundingRect(face_contour)
            if sx < 8 or sy < 8:
                continue
            right, bottom = x + sx - 1, y + sy - 1
            borders = [_line_fraction(vertical, edge - 3, y + 3, edge + 4, bottom - 2, vertical=True) for edge in (x, right)] + [
                _line_fraction(horizontal, x + 3, edge - 3, right - 2, edge + 4, vertical=False) for edge in (y, bottom)
            ]
            if min(borders) < 0.73:
                valid = False
                break
            faces.append((x, y, right, bottom))
        if not valid or not faces or len(faces) > MAX_GRID_CELLS:
            continue
        xs = _boundaries([value for face in faces for value in (face[0], face[2])], np.mean(vertical[top : top + span_y] != 0, axis=0))
        ys = _boundaries([value for face in faces for value in (face[1], face[3])], np.mean(horizontal[:, left : left + span_x] != 0, axis=1))
        rows, cols = len(ys) - 1, len(xs) - 1
        if not 2 <= rows <= 250 or not 2 <= cols <= 70 or rows * cols > MAX_GRID_CELLS:
            continue
        occupied: set[tuple[int, int]] = set()
        candidate_cells: list[tuple[int, int, int, int, tuple[int, int, int, int]]] = []
        for face in faces:
            col0, row0, col1, row1 = [min(range(len(axis)), key=lambda i: abs(axis[i] - coordinate)) for coordinate, axis in zip(face, (xs, ys, xs, ys), strict=True)]
            slots = {(row, col) for row in range(row0, row1) for col in range(col0, col1)}
            if not slots or occupied.intersection(slots):
                valid = False
                break
            occupied.update(slots)
            candidate_cells.append((row0, col0, row1 - 1, col1 - 1, face))
        if not valid:
            continue
        expected = {(row, col) for row in range(rows) for col in range(cols)}
        if occupied != expected:
            continue

        candidate_cells.sort(key=lambda cell: (cell[0], cell[1]))
        cells = tuple(RasterCell(index, row0, col0, row1, col1, bounds) for index, (row0, col0, row1, col1, bounds) in enumerate(candidate_cells))
        grids.append(
            RasterGrid(
                row_count=rows,
                col_count=cols,
                cells=cells,
                merges=tuple((r0, c0, r1, c1) for r0, c0, r1, c1, _box in candidate_cells if r0 != r1 or c0 != c1),
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


def read_raster_page(png: bytes) -> tuple[bytes, list[RasterGrid]]:
    """Rectify a visibly skewed outer border only when direct topology fails.

    Return the same pixels used for both geometry and transcription. The
    full page is retained, including content outside the table rectangle.
    """
    grids = detect_raster_grids(png)
    if grids:
        return png, grids
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Invalid scanned PDF page image")
    height, width = image.shape[:2]
    if height * width > MAX_IMAGE_PIXELS or min(height, width) < 120:
        return png, []
    drawing = _line_masks(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))[2]
    contours, _ = cv2.findContours(drawing, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return png, []
    outer = max(contours, key=cv2.contourArea)
    if cv2.contourArea(outer) < width * height * 0.15:
        return png, []
    corners = cv2.approxPolyDP(outer, 0.005 * cv2.arcLength(outer, True), True)
    if len(corners) != 4 or not cv2.isContourConvex(corners):
        return png, []
    points = corners.reshape(4, 2).astype(np.float32)
    points = points[np.argsort(points[:, 1])]
    top = points[:2][np.argsort(points[:2, 0])]
    bottom = points[2:][np.argsort(points[2:, 0])]
    source = np.array([top[0], top[1], bottom[1], bottom[0]], dtype=np.float32)
    widths = [float(np.linalg.norm(source[1] - source[0])), float(np.linalg.norm(source[2] - source[3]))]
    heights = [float(np.linalg.norm(source[3] - source[0])), float(np.linalg.norm(source[2] - source[1]))]
    if min(*widths, *heights) < 60 or max(widths) / min(widths) > 1.15 or max(heights) / min(heights) > 1.15:
        return png, []
    if any(abs(float(source[b][1] - source[a][1])) > length * 0.1 for a, b, length in ((0, 1, widths[0]), (3, 2, widths[1]))):
        return png, []
    if any(abs(float(source[b][0] - source[a][0])) > length * 0.1 for a, b, length in ((0, 3, heights[0]), (1, 2, heights[1]))):
        return png, []
    x, y = source[0]
    sx, sy = max(widths), max(heights)
    target = np.array([[x, y], [x + sx, y], [x + sx, y + sy], [x, y + sy]], dtype=np.float32)
    transform = cv2.getPerspectiveTransform(source, target)
    page = np.array([[[0, 0], [width, 0], [width, height], [0, height]]], dtype=np.float32)
    mapped = cv2.perspectiveTransform(page, transform)[0]
    if not np.isfinite(mapped).all():
        return png, []
    low, high = np.floor(mapped.min(axis=0)), np.ceil(mapped.max(axis=0))
    output_width, output_height = (int(value) for value in high - low)
    if min(output_width, output_height) < 120 or output_width * output_height > MAX_IMAGE_PIXELS:
        return png, []
    translation = np.array([[1, 0, -low[0]], [0, 1, -low[1]], [0, 0, 1]])
    rectified = cv2.warpPerspective(image, translation @ transform, (output_width, output_height), borderValue=(255, 255, 255))
    encoded, result = cv2.imencode(".png", rectified)
    if not encoded:
        raise ValueError("Raster rectification encoding failed")
    normalized = result.tobytes()
    grids = detect_raster_grids(normalized)
    return (normalized, grids) if grids else (png, [])


def mask_raster_tables(png: bytes, grids: list[RasterGrid]) -> bytes:
    """Expose page remainder without asking a model to reconstruct known grids."""
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Invalid scanned PDF page image")
    height, width = image.shape[:2]
    for grid in grids:
        x, y, sx, sy = grid.bbox
        if x < 0 or y < 0 or sx <= 0 or sy <= 0 or x + sx > width or y + sy > height:
            raise ValueError("Raster source table lies outside its page")
        image[y : y + sy, x : x + sx] = 255
    encoded, masked = cv2.imencode(".png", image)
    if not encoded:
        raise ValueError("Raster page remainder encoding failed")
    return masked.tobytes()


def make_cell_panel(png: bytes, cells: tuple[RasterCell, ...]) -> bytes:
    """Label scanned cell crops *outside* their image evidence for OCR.

    Pack native cell crops on one compact sheet. Do not turn every small cell
    into a 1024-pixel-wide strip or multiply the request's white padding.
    Labels stay outside the source crop; IDs, not packing order, bind results.
    """
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Invalid scanned PDF page image")
    placed: list[tuple[np.ndarray, int, int]] = []
    x, y, row_height, width = 6, 6, 0, 0
    for cell in cells:
        x0, y0, x1, y1 = cell.bbox
        crop = image[y0 + 3 : y1 - 3, x0 + 3 : x1 - 3]
        if not crop.size:
            raise ValueError("Raster source cell is too small to transcribe")
        ratio = min(1.0, 1000 / crop.shape[1], 140 / crop.shape[0])
        resized = cv2.resize(
            crop,
            (max(1, int(crop.shape[1] * ratio)), max(1, int(crop.shape[0] * ratio))),
            interpolation=cv2.INTER_AREA,
        )
        label = f"CELL ID {cell.index}"
        label_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)[0][0]
        tile = np.full((resized.shape[0] + 30, max(resized.shape[1], label_width) + 12, 3), 255, dtype=np.uint8)
        cv2.putText(tile, label, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
        tile[25 : 25 + resized.shape[0], 6 : 6 + resized.shape[1]] = resized
        if x + tile.shape[1] + 6 > 1024 and x > 6:
            x, y, row_height = 6, y + row_height + 6, 0
        placed.append((tile, x, y))
        row_height = max(row_height, tile.shape[0])
        width = max(width, x + tile.shape[1] + 6)
        x += tile.shape[1] + 6
    if not placed:
        raise ValueError("Raster source panel has no cells")
    board = np.full((y + row_height + 6, width, 3), 255, dtype=np.uint8)
    for tile, left, top in placed:
        board[top : top + tile.shape[0], left : left + tile.shape[1]] = tile
    encoded, result = cv2.imencode(".png", board, [cv2.IMWRITE_PNG_COMPRESSION, 4])
    if not encoded:
        raise ValueError("Raster crop encoding failed")
    return result.tobytes()
