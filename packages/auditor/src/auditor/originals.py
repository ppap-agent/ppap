"""Read an original by location. Observation only: nothing here judges a clause.

Every read names where it looked, so a citation can be read again from the same original.
Locations are written the way an auditor would write them:

    工作表名!B7        one cell (a merged cell reads as its top-left cell)
    工作表名!A5:F9     a range
    page:3             a PDF page (text layer, or what is seen when the page is a scan)
    paragraph:12       a Word paragraph, from 1
    table:1:3:2        a Word table cell: table, row, column, from 1
    image:xl/media/image2.emf   a picture embedded in the original
"""

from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

SPREADSHEETS = {".xlsx", ".xlsm", ".xls"}
WORD = {".docx", ".doc"}
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
# The longest side of an image handed to the model.
MAX_PIXELS = 2200

_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})$")
_XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class Unreadable(Exception):
    """The original or the location cannot be read. The message is written for the auditor."""


def column_letter(number: int) -> str:
    letters = ""
    while number:
        number, rest = divmod(number - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def parse_cell(ref: str) -> tuple[int, int]:
    match = _CELL.match(ref.strip())
    if not match:
        raise Unreadable(f"单元格写法不对：{ref}（应如 B7）")
    column = 0
    for letter in match.group(1).upper():
        column = column * 26 + ord(letter) - 64
    return int(match.group(2)), column


def parse_range(ref: str) -> tuple[int, int, int, int]:
    start, _, end = ref.partition(":")
    r1, c1 = parse_cell(start)
    r2, c2 = parse_cell(end or start)
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


def cell_name(row: int, column: int) -> str:
    return f"{column_letter(column)}{row}"


def shown(value: Any) -> str:
    """A cell value as the auditor reads it. 0 and FALSE are values, not blanks."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    return str(value)


def squeeze(text: str) -> str:
    """Text with all whitespace removed: how a quotation is compared with its source."""
    return re.sub(r"\s+", "", text)


@dataclass
class Sheet:
    name: str
    values: dict[tuple[int, int], Any]
    merged: list[tuple[int, int, int, int]]
    # Formula cells that carry no cached value: their value is unknown, not blank.
    formulas: dict[tuple[int, int], str] = field(default_factory=dict)
    hidden: bool = False

    @cached_property
    def rows(self) -> int:
        return max((row for row, _ in [*self.values, *self.formulas]), default=0)

    @cached_property
    def columns(self) -> int:
        return max((column for _, column in [*self.values, *self.formulas]), default=0)

    def anchor(self, row: int, column: int) -> tuple[int, int]:
        for r1, c1, r2, c2 in self.merged:
            if r1 <= row <= r2 and c1 <= column <= c2:
                return r1, c1
        return row, column

    def text(self, row: int, column: int) -> str:
        return shown(self.values.get(self.anchor(row, column)))


def _xlsx_sheets(path: Path) -> list[Sheet]:
    from openpyxl import load_workbook

    cached = load_workbook(path, data_only=True)
    raw = load_workbook(path, data_only=False)
    sheets = []
    for sheet in cached.worksheets:
        # _cells holds only the cells the file stores, so a sheet formatted to a million rows stays cheap.
        values = {key: cell.value for key, cell in sheet._cells.items() if cell.value not in (None, "")}
        formulas = {key: str(getattr(cell.value, "text", cell.value)) for key, cell in raw[sheet.title]._cells.items() if cell.data_type == "f" and key not in values}
        merged = [(area.min_row, area.min_col, area.max_row, area.max_col) for area in sheet.merged_cells.ranges]
        sheets.append(Sheet(sheet.title, values, merged, formulas, sheet.sheet_state != "visible"))
    return sheets


def _xls_sheets(path: Path) -> list[Sheet]:
    import xlrd

    try:
        book = xlrd.open_workbook(str(path), formatting_info=True)
    except Exception:
        # Some files only open without formatting; merged areas are then unknown.
        book = xlrd.open_workbook(str(path))
    sheets = []
    for sheet in book.sheets():
        values: dict[tuple[int, int], Any] = {}
        for row in range(sheet.nrows):
            for column in range(sheet.ncols):
                cell = sheet.cell(row, column)
                value: Any = cell.value
                if cell.ctype in (0, 6) or value == "":
                    continue
                if cell.ctype == 3:
                    try:
                        value = xlrd.xldate_as_datetime(value, book.datemode)
                    except Exception:
                        pass
                elif cell.ctype == 4:
                    value = bool(value)
                elif cell.ctype == 5:
                    value = f"#错误({value})"
                values[(row + 1, column + 1)] = value
        merged = [(r1 + 1, c1 + 1, r2, c2) for r1, r2, c1, c2 in getattr(sheet, "merged_cells", [])]
        sheets.append(Sheet(sheet.name, values, merged, hidden=bool(getattr(sheet, "visibility", 0))))
    return sheets


def convert(source: Path, target: str, out: Path) -> Path:
    """Convert with LibreOffice. Used to see what a format cannot show directly."""
    out.mkdir(parents=True, exist_ok=True)
    result = out / f"{source.stem}.{target}"
    if result.is_file():
        return result
    with tempfile.TemporaryDirectory() as profile:
        try:
            subprocess.run(
                ["soffice", "--headless", f"-env:UserInstallation=file://{profile}", "--convert-to", target, "--outdir", str(out), str(source)],
                capture_output=True,
                timeout=240,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Unreadable(f"转换失败：{source.name} → {target}（{type(error).__name__}）") from None
    if not result.is_file():
        raise Unreadable(f"转换失败：{source.name} → {target}")
    return result


def _relationships(archive: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """A part's relationships: id → (type, target part)."""
    folder, _, name = part.rpartition("/")
    path = f"{folder}/_rels/{name}.rels"
    if path not in archive.namelist():
        return {}
    found = {}
    for item in ET.fromstring(archive.read(path)).iter(f"{_REL}Relationship"):
        target = item.get("Target", "")
        resolved = target.lstrip("/") if target.startswith("/") else os.path.normpath(str(PurePosixPath(folder) / target))
        found[item.get("Id", "")] = (item.get("Type", "").rsplit("/", 1)[-1], resolved)
    return found


def _workbook_drawings(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pictures and drawn shapes of a workbook, with the sheet and cell each is anchored to."""
    pictures: list[dict[str, Any]] = []
    shapes: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if "xl/workbook.xml" not in names:
            return pictures, shapes
        parts = _relationships(archive, "xl/workbook.xml")
        seen = set()
        for sheet in ET.fromstring(archive.read("xl/workbook.xml")).iter(f"{_MAIN}sheet"):
            _kind, part = parts.get(sheet.get(f"{_R}id", ""), ("", ""))
            if part not in names:
                continue
            for kind, drawing in _relationships(archive, part).values():
                if kind != "drawing" or drawing not in names:
                    continue
                media = _relationships(archive, drawing)
                for anchor in ET.fromstring(archive.read(drawing)):
                    start = anchor.find(f"{_XDR}from")
                    at = cell_name(int(start.findtext(f"{_XDR}row", "0")) + 1, int(start.findtext(f"{_XDR}col", "0")) + 1) if start is not None else None
                    for blip in anchor.iter(f"{_A}blip"):
                        _type, target = media.get(blip.get(f"{_R}embed", ""), ("", ""))
                        if target:
                            seen.add(target)
                            pictures.append({"name": target, "sheet": sheet.get("name"), "cell": at})
                    text = "".join(node.text or "" for node in anchor.iter(f"{_A}t")).strip()
                    if anchor.find(f"{_XDR}cxnSp") is not None:
                        shapes.append({"sheet": sheet.get("name"), "cell": at, "kind": "连线"})
                    elif anchor.find(f"{_XDR}sp") is not None or anchor.find(f"{_XDR}grpSp") is not None:
                        preset = next((geometry.get("prst") for geometry in anchor.iter(f"{_A}prstGeom")), None)
                        shapes.append({"sheet": sheet.get("name"), "cell": at, "kind": "图形", "shape": preset, "text": text})
        # Pictures no drawing anchors (headers, cell pictures, objects): present, place unknown.
        pictures += [{"name": name, "sheet": None, "cell": None} for name in names if "/media/" in name and not name.endswith("/") and name not in seen]
    return pictures, shapes


class Original:
    """One frozen original. ``scratch`` holds what is derived from it; the original is never written."""

    def __init__(self, path: Path | str, scratch: Path | str = "scratch") -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise Unreadable(f"没有这份文件：{path}")
        self.suffix = self.path.suffix.lower()
        self.scratch = Path(scratch) / ".derived" / self.path.name

    # Spreadsheets

    @cached_property
    def sheets(self) -> list[Sheet]:
        if self.suffix not in SPREADSHEETS:
            raise Unreadable(f"{self.path.name} 不是表格文件")
        try:
            return _xls_sheets(self.path) if self.suffix == ".xls" else _xlsx_sheets(self.path)
        except Unreadable:
            raise
        except Exception as error:
            raise Unreadable(f"表格打不开：{self.path.name}（{type(error).__name__}）") from None

    def sheet(self, name: str) -> Sheet:
        for sheet in self.sheets:
            if sheet.name == name:
                return sheet
        raise Unreadable(f"{self.path.name} 没有工作表「{name}」；有：{'、'.join(sheet.name for sheet in self.sheets)}")

    # Word

    @cached_property
    def _document(self) -> Any:
        import docx

        if self.suffix not in WORD:
            raise Unreadable(f"{self.path.name} 不是 Word 文件")
        return docx.Document(str(convert(self.path, "docx", self.scratch) if self.suffix == ".doc" else self.path))

    @cached_property
    def paragraphs(self) -> list[str]:
        return [paragraph.text for paragraph in self._document.paragraphs]

    @cached_property
    def tables(self) -> list[list[list[str]]]:
        return [[[cell.text for cell in row.cells] for row in table.rows] for table in self._document.tables]

    # Pages

    @cached_property
    def _pdf(self) -> Any:
        import fitz

        if self.suffix == ".pdf":
            return fitz.open(self.path)
        if self.suffix in IMAGES:
            raise Unreadable(f"{self.path.name} 是图片，没有页")
        return fitz.open(convert(self.path, "pdf", self.scratch))

    @property
    def pages(self) -> int:
        return 1 if self.suffix in IMAGES else len(self._pdf)

    def page_text(self, page: int) -> str:
        if not 1 <= page <= self.pages:
            raise Unreadable(f"{self.path.name} 没有第 {page} 页（共 {self.pages} 页）")
        return "" if self.suffix in IMAGES else self._pdf[page - 1].get_text()

    def render(self, page: int = 1, dpi: int = 110, clip: tuple[float, float, float, float] | None = None, grid: bool = False) -> Path:
        """A page as a picture. ``clip`` is left, top, right, bottom as fractions of the page, to look closer.

        ``grid`` draws ten columns A–J and ten rows 1–10 over the picture, so a place can be named and clipped.
        """
        import fitz
        from PIL import Image, ImageDraw, ImageFont

        if not 1 <= page <= self.pages:
            raise Unreadable(f"{self.path.name} 没有第 {page} 页（共 {self.pages} 页）")
        if clip and not (0 <= clip[0] < clip[2] <= 1 and 0 <= clip[1] < clip[3] <= 1):
            raise Unreadable("clip 是 左,上,右,下 四个 0 到 1 之间的比例，例如 0,0,0.5,0.3")
        self.scratch.mkdir(parents=True, exist_ok=True)
        label = f"page-{page}-{dpi}" + ("-" + "-".join(f"{value:.3f}" for value in clip) if clip else "") + ("-grid" if grid else "")
        out = self.scratch / f"{label}.png"
        if self.suffix in IMAGES:
            image = Image.open(self.path).convert("RGB")
            if clip:
                image = image.crop((int(clip[0] * image.width), int(clip[1] * image.height), int(clip[2] * image.width), int(clip[3] * image.height)))
        else:
            sheet = self._pdf[page - 1]
            area = sheet.rect
            if clip:
                area = fitz.Rect(area.x0 + clip[0] * area.width, area.y0 + clip[1] * area.height, area.x0 + clip[2] * area.width, area.y0 + clip[3] * area.height)
            pixels = sheet.get_pixmap(dpi=dpi, clip=area)
            image = Image.open(io.BytesIO(pixels.tobytes("png"))).convert("RGB")
        image.thumbnail((MAX_PIXELS, MAX_PIXELS))
        if grid:
            pen = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=max(14, image.width // 70))
            for step in range(1, 10):
                pen.line([(image.width * step // 10, 0), (image.width * step // 10, image.height)], fill=(220, 0, 0), width=1)
                pen.line([(0, image.height * step // 10), (image.width, image.height * step // 10)], fill=(220, 0, 0), width=1)
            for step in range(10):
                pen.text((image.width * step // 10 + 4, 2), chr(65 + step), fill=(220, 0, 0), font=font)
                pen.text((2, image.height * step // 10 + 4), str(step + 1), fill=(220, 0, 0), font=font)
        image.save(out)
        return out

    # Embedded pictures and drawn shapes

    @cached_property
    def _package(self) -> Path:
        """The zip package that holds this original's embedded pictures."""
        if self.suffix == ".xls":
            return convert(self.path, "xlsx", self.scratch)
        if self.suffix == ".doc":
            return convert(self.path, "docx", self.scratch)
        if self.suffix in {".xlsx", ".xlsm", ".docx"}:
            return self.path
        raise Unreadable(f"{self.path.name} 没有可列出的内嵌图片；整页请用 render")

    @cached_property
    def drawings(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self._package.suffix.lower() == ".docx":
            with zipfile.ZipFile(self._package) as archive:
                return [{"name": name, "sheet": None, "cell": None} for name in archive.namelist() if "/media/" in name and not name.endswith("/")], []
        return _workbook_drawings(self._package)

    def picture(self, name: str) -> Path:
        """An embedded picture as PNG; EMF and WMF are drawn by LibreOffice first."""
        from PIL import Image

        if name not in {item["name"] for item in self.drawings[0]}:
            raise Unreadable(f"{self.path.name} 没有内嵌图片 {name}")
        self.scratch.mkdir(parents=True, exist_ok=True)
        out = self.scratch / (name.replace("/", "_").rsplit(".", 1)[0] + ".png")
        if out.is_file():
            return out
        with zipfile.ZipFile(self._package) as archive:
            content = archive.read(name)
        if name.lower().endswith((".emf", ".wmf")):
            raw = self.scratch / name.replace("/", "_")
            raw.write_bytes(content)
            return convert(raw, "png", self.scratch)
        image = Image.open(io.BytesIO(content)).convert("RGB")
        image.thumbnail((MAX_PIXELS, MAX_PIXELS))
        image.save(out)
        return out

    # Citations

    def text_at(self, where: str) -> str | None:
        """The text at a location, or None when the location exists but carries no text (a scan, a picture).

        Raises Unreadable when the location does not exist in this original.
        """
        where = where.strip()
        kind, _, rest = where.partition(":")
        if kind == "page" and rest.isdigit():
            return self.page_text(int(rest)).strip() or None
        if kind == "paragraph" and rest.isdigit():
            if not 1 <= int(rest) <= len(self.paragraphs):
                raise Unreadable(f"{self.path.name} 没有第 {rest} 段（共 {len(self.paragraphs)} 段）")
            return self.paragraphs[int(rest) - 1]
        if kind == "table" and re.fullmatch(r"\d+:\d+:\d+", rest):
            table, row, column = (int(part) for part in rest.split(":"))
            try:
                if min(table, row, column) < 1:
                    raise IndexError
                return self.tables[table - 1][row - 1][column - 1]
            except IndexError:
                raise Unreadable(f"{self.path.name} 没有表 {table} 第 {row} 行第 {column} 列") from None
        if kind == "image":
            if self.suffix in IMAGES and not rest:
                return None
            self.picture(rest)
            return None
        if "!" in where:
            name, _, area = where.rpartition("!")
            sheet = self.sheet(name)
            r1, c1, r2, c2 = parse_range(area)
            if (r2 - r1 + 1) * (c2 - c1 + 1) > 20000:
                raise Unreadable("引用的区域太大；请引用具体的单元格或较小的区域")
            cells = dict.fromkeys(sheet.anchor(row, column) for row in range(r1, r2 + 1) for column in range(c1, c2 + 1))
            return "\n".join(shown(sheet.values.get(cell)) for cell in cells)
        raise Unreadable(f"位置写法不对：{where}（应如 工作表名!B7、page:3、paragraph:12、table:1:3:2、image:名称）")


def transcribe(picture: Path, ask: str = "") -> str:
    """Ask the vision model to transcribe a picture. A transcription is a reading aid, not the original's text."""
    import httpx

    base, key, model = (os.environ.get(f"PPAP_MODEL_VISION_{name}", "") for name in ("BASE_URL", "API_KEY", "MODEL"))
    if not (base and key and model):
        raise Unreadable("没有配置视觉模型，不能转写；请直接查看图片")
    prompt = ask or "逐字转写图中能看清的全部文字，保持原有行序和表格的行列关系。看不清的字写成□，不要补全、纠正或猜测。"
    encoded = base64.b64encode(picture.read_bytes()).decode("ascii")
    request: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}, {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}", "detail": "high"}]}],
        "max_output_tokens": 8000,
    }
    if effort := os.environ.get("PPAP_MODEL_VISION_REASONING_EFFORT"):
        request["reasoning"] = {"effort": effort}
    try:
        response = httpx.post(f"{base.rstrip('/')}/responses", json=request, headers={"Authorization": f"Bearer {key}"}, timeout=180)
        response.raise_for_status()
        output = response.json().get("output", [])
    except (httpx.HTTPError, ValueError) as error:
        raise Unreadable(f"转写失败（{type(error).__name__}）；请直接查看图片") from None
    return "".join(part.get("text", "") for item in output if isinstance(item, dict) for part in item.get("content") or [] if isinstance(part, dict)).strip()
