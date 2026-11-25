"""UAES v1.0 revision 4: a same-round PDF drawing's own identity, read from the original.

Each page is a parts list (by its printed form title and column headings), a drawing whose title block names its own
drawing number, or unknown. A UAES part drawing (Z_F01R10Y776.pdf, an A0 sheet) letters its title block with vector
strokes outside the text layer, so the number, designation, project and status are read from an image of the title
block. ISO 7200 and GB/T 10609.1 put the title block in the bottom-right corner of the frame at a fixed physical size
(about 180 mm wide) whatever the sheet size; the image is that corner at its physical size, not a share of the sheet,
which on an A0 sheet shrinks the lettering below what the image model reads.

The image model only transcribes; code decides. A field is kept only when two separate observations of the same image
agree: in both its printed label is that field's label and its value is the same (a drawing number up to its layout
spaces). A drawing number is compared without its layout spaces only (layout_id). Every kept field is cited at the place
both observations gave for it on the page, with the corner that was read.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import fitz

from audit.engine.rule_engine import Decision, GuardStop

from .materials import DRAWING_TITLE, PARTS_TITLE, TITLE, compact, layout_id, original_name, pdf_field, title_key

MM = 72 / 25.4
# The bottom-right corner read: a 180 mm title block with its revision rows, and margin.
CORNER = (300 * MM, 120 * MM)
# Pages read per original; each costs at most two image requests (one when the first sees no drawing number).
PAGES = 6

_BOX = {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 1000}, "minItems": 4, "maxItems": 4}
_FIELD = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["label", "value", "bbox"],
    "properties": {"label": {"type": "string"}, "value": {"type": "string"}, "bbox": _BOX},
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", *TITLE],
    "properties": {"kind": {"type": "string", "enum": ["drawing", "unknown"]}, **dict.fromkeys(TITLE, _FIELD)},
}
PROMPT = (
    "这是一份原件页面右下角的局部图像。只转写，不判断。"
    "图中有工程图标题栏（印有图号或 DWG No. 等栏目的表格）时 kind 填 drawing，否则填 unknown。"
    "逐栏查看这个标题栏，分别转写本图的图号（number）、名称（name）、项目或项目编号（project）、图纸状态（status）四栏："
    "label 照印刷原样抄该栏的标签文字；value 照原样抄该栏填写的内容，保留大小写、字符间的空格分组、后缀和中英文完整名称，不补字、不改字；"
    "bbox 是该栏（标签连同内容）在本图像中的位置，按从左上到右下的[x0,y0,x1,y1]千分坐标。"
    "不转写首次用于、相关图号、所属图纸、零件清单行、材料、订货规范等其他栏。标题栏里确实没有某一栏，或该栏看不清，才填 null，不推断。"
)


def corner(page: Any) -> fitz.Rect:
    rect = page.rect
    return fitz.Rect(max(rect.x0, rect.x1 - CORNER[0]), max(rect.y0, rect.y1 - CORNER[1]), rect.x1, rect.y1)


def _box(raw: Any) -> tuple[int, int, int, int] | None:
    """A place in whole per-mille coordinates of the image: neither a point nor the whole image."""
    if not isinstance(raw, list) or len(raw) != 4 or any(type(v) is not int for v in raw):
        return None
    x0, y0, x1, y1 = raw
    if not (0 <= x0 and x0 + 5 <= x1 <= 1000 and 0 <= y0 and y0 + 5 <= y1 <= 1000) or (x1 - x0) * (y1 - y0) >= 900_000:
        return None
    return (x0, y0, x1, y1)


def _key(field: str, value: str) -> str:
    return layout_id(value) if field == "number" else " ".join(value.split())


def _observed(raw: Any) -> tuple[bool, dict[str, tuple[str, str, tuple[int, int, int, int]]]]:
    """One observation: whether it saw a drawing title block, and each field printed under that field's own label."""
    if not isinstance(raw, dict):
        return False, {}
    found = {}
    for field in TITLE:
        record = raw.get(field)
        if not isinstance(record, dict) or title_key(record.get("label")) != field:
            continue
        value, box = record.get("value"), _box(record.get("bbox"))
        if not isinstance(value, str) or not value.strip() or box is None:
            continue
        # A drawing number is letters and digits in layout groups; a label or a sentence in its place is not one.
        if field == "number" and not (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ./\-]*", value.strip()) and re.search(r"\d", value)):
            continue
        found[field] = (str(record["label"]).strip(), value.strip(), box)
    return raw.get("kind") == "drawing", found


def _version(item: Any) -> str:
    return str((getattr(item, "file_metadata", None) or {}).get("file_version_id") or item.audit_id)


def title_block(agent: Any, page: Any, where: str, cites: list) -> dict[str, Any]:
    """The drawing number, designation, project and status two observations of the title block agree on; each kept
    field's place goes to `cites`."""
    from documents.ocr.observation import ocr_observation_scope

    area = corner(page)
    zoom = min(4.0, 2400 / max(area.width, area.height))
    image = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=area).tobytes("png")
    seen = []
    for number in (1, 2):
        with ocr_observation_scope(number):
            seen.append(_observed(agent.transcribe(image, PROMPT, SCHEMA, f"{where}的标题栏")))
        if "number" not in seen[0][1]:
            return {"kind": "unknown", "where": f"{where}（标题栏未读出图号）"}
    (drawing_1, first), (drawing_2, second) = seen
    record: dict[str, Any] = {"kind": "unknown", "where": where}
    for field in TITLE:
        if field not in first or field not in second:
            continue
        (label, value, a), (_label, other, b) = first[field], second[field]
        if _key(field, value) != _key(field, other):
            if field == "number":
                return {"kind": "unknown", "where": f"{where}（两次读取的图号不一致：{value}、{other}）"}
            continue
        # The model's boxes are coarse and vary between observations; the place cited covers both, inside the read corner.
        x0, y0, x1, y1 = min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])
        bbox = [round(area.x0 + x0 * area.width / 1000, 2), round(area.y0 + y0 * area.height / 1000, 2), round(area.x0 + x1 * area.width / 1000, 2), round(area.y0 + y1 * area.height / 1000, 2)]
        record[field] = value
        cites.append(
            (
                {
                    "kind": "pdf_region",
                    "page": page.number + 1,
                    "bbox": bbox,
                    "coordinate_system": "pdf_points_unrotated",
                    "read_region": [round(v, 2) for v in area],
                    "engine": "ocr_llm",
                    "observations": 2,
                    "fact": f"drawing_title_{field}",
                },
                f"{label}：{value}",
                True,
            )
        )
    if "number" not in record:
        record["where"] = f"{where}（第二次读取未读出图号）"
    elif drawing_1 and drawing_2:
        record["kind"] = "drawing"
    return record


def _page(agent: Any, item: Any, page: Any, cites: list) -> dict[str, Any]:
    where = f"{original_name(item)} 第{page.number + 1}页"
    # Only upright visible strings, not oblique watermarks crossing the form's cells.
    lines = [
        (fitz.Rect(span["bbox"]), span["text"].strip())
        for block in page.get_text("dict", flags=0)["blocks"]
        for line in block.get("lines", [])
        if line["dir"][0] > 0.99
        for span in line["spans"]
        if span["text"].strip() and span.get("alpha", 255)
    ]

    def cite(box: Any, label: str, value: str) -> None:
        cites.append(({"kind": "pdf_region", "page": page.number + 1, "bbox": [round(v, 2) for v in box], "fact": label}, value, False))

    # An expressly titled parts-list form is not a drawing, even when it names one in its header.
    top = [(box, text) for box, text in lines if box.y0 < page.rect.height * 0.3]
    heading = next(((box, text) for box, text in top if compact(text) in PARTS_TITLE), None)
    if heading and any(compact(text) in {"所属图纸", "related-drawing", "序号", "index/po"} for _, text in lines):
        cite(heading[0], "单据标题", heading[1])
        return {"kind": "parts_list", "where": where}
    # A page without a text layer is a scan, not read from an image; a rotated page's places are not verified.
    if not lines or page.rotation or any(fitz.Rect(image["bbox"]).get_area() >= 0.9 * page.rect.get_area() for image in page.get_image_info()):
        return {"kind": "unknown", "where": f"{where}（无可用文字层或页面已旋转，未读）"}
    # A title block in the text layer: an explicit drawing heading or a scale and sheet/version grid, with labelled fields.
    values = {field: pdf_field(lines, labels, numbers=field == "number") for field, labels in TITLE.items()}
    headings = {compact(text) for _, text in lines}
    layout = bool(headings & DRAWING_TITLE or headings & {"scale", "比例"} and headings & {"版本", "version", "sheet", "页次"})
    if layout and len(values["number"]) == 1 and len(values["name"]) == 1:
        record: dict[str, Any] = {"kind": "drawing", "where": where}
        for field, found in values.items():
            if len(found) == 1:
                box, value = found[0]
                record[field] = value
                cite(box, field, value)
        return record
    return title_block(agent, page, where, cites)


def drawing_pages(agent: Any, item: Any, *, limit: int = PAGES, until_drawing: bool = False) -> list[dict[str, Any]]:
    """Each page of a same-round original, up to `limit`: {"kind": "drawing" | "parts_list" | "unknown", "where", number, name, ...}.

    Only a PDF is read here; any other original is unknown. Pages past the limit are unknown, not read. A page is read
    once per agent; every call cites what was read on it, since each clause carries its own evidence."""
    path = Path(str(item.file_url))
    if path.suffix.lower() != ".pdf":
        return [{"kind": "unknown", "where": original_name(item)}]
    metadata = getattr(item, "file_metadata", None) or {}
    expected = metadata.get("source_sha256") or metadata.get("sha256")
    if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise GuardStop(Decision.ERR, "冻结的关联资料已变化，尚未取得业务结论。")
    cache = agent.__dict__.setdefault("_r4_drawing_pages", {})
    found = []
    with fitz.open(str(path)) as document:
        for page in document:
            if page.number >= min(limit, PAGES):
                found.append({"kind": "unknown", "where": f"{original_name(item)} 第{page.number + 1}页（超出读取页数上限，未读）"})
                continue
            key = (_version(item), page.number)
            if key not in cache:
                cites: list = []
                cache[key] = (_page(agent, item, page, cites), cites)
            record, cites = cache[key]
            for locator, value, visual in cites:
                agent.legacy().witness(locator, value, _version(item), visual=visual)
            found.append(record)
            if until_drawing and record["kind"] == "drawing":
                break
    return found or [{"kind": "unknown", "where": original_name(item)}]
