"""Shared layout of the delivered PDF reports: an own cover, a contents page and ten chapters.

Both the audit and the precheck report go through here, so the page format is one
definition: A4 portrait, one column, 20 mm top/bottom and 18 mm left/right margins,
one embedded and subset CJK font family, no running header or footer on the cover,
a header with the report name and type and a footer with the date and page number
on every other page, and long tables that repeat their header on each page.

The PDF outline holds the cover and the ten chapters; the web reader builds its
chapter list from that outline, so chapter names exist only in the renderers.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from html import escape
from io import BytesIO
from pathlib import Path

import pymupdf

PAGE = pymupdf.Rect(0, 0, 595, 842)
# A4 portrait, single column: 20 mm top/bottom and 18 mm left/right margins.
BODY = pymupdf.Rect(51, 57, 544, 785)
GAP = 6
MAX_PAGES = 500
MAX_HTML = 4_000_000
FAMILY = "ReportSans"
# Noto Sans CJK (思源黑体) from the runtime image (fonts-noto-cjk); the SC face of a collection.
NOTO_PATHS = (
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc"),
)
CSS = (
    f"body {{font-family:{FAMILY}; font-size:10pt; line-height:1.55}} h1 {{font-size:16pt; margin:0 0 8pt 0}} h2 {{font-size:12pt; margin:8pt 0 4pt 0}} "
    "h3 {font-size:10.5pt; margin:6pt 0 3pt 0} p {white-space:pre-wrap; margin:2pt 0} .meta {font-size:8.5pt; color:#444} "
    "table {border-collapse:collapse; width:100%} th, td {border:0.5pt solid #777; padding:2pt 3pt; font-size:8.5pt; line-height:1.4; text-align:left; vertical-align:top} "
    "th {background-color:#e6e6e6; font-weight:bold} .strong {font-weight:bold} .nowrap {white-space:nowrap} .caption {font-size:8.5pt; font-weight:bold; margin:4pt 0 2pt 0} "
    ".card {border:0.5pt solid #777; padding:3pt 6pt} .card p {margin:1pt 0} "
    ".cover h1 {font-size:24pt; margin-top:120pt; text-align:center} .cover .kind {font-size:13pt; text-align:center; margin-bottom:36pt} "
    # MuPDF sizes columns by content, so padding gives the cover values and the contents lines their width.
    ".cover th {white-space:nowrap; font-size:10.5pt; padding:5pt 8pt} .cover td {font-size:10.5pt; padding:5pt 8pt} "
    ".toc td {font-size:10.5pt; border:none; border-bottom:0.5pt dotted #999; padding:5pt 260pt 5pt 2pt} .toc .page {text-align:right; white-space:nowrap; padding:5pt 2pt}"
)
CHINESE_NUMBERS = "一二三四五六七八九十"


def text(value) -> str:
    """Escape source text. Each "f" is its own text run so no font folds ASCII ff/fi/fl into
    a ligature: copied or extracted frozen values keep their original code points."""
    return escape(str(value if value is not None else "")).replace("f", "<span>f</span>")


def dash(value) -> str:
    return text(value) if value not in (None, "") else "—"


@dataclass(frozen=True)
class Table:
    """A table that may run over pages; every page repeats the header, later pages say 续表."""

    caption: str
    head: tuple[str, ...]
    rows: Sequence[tuple[str, ...]]  # cell HTML, already escaped
    empty: str = "无。"
    # Short columns (number, result, level) keep their words whole; MuPDF ignores CSS widths.
    nowrap: tuple[int, ...] = ()


@dataclass(frozen=True)
class Chapter:
    title: str  # e.g. 一、结论摘要
    blocks: list  # HTML strings (kept together when they fit a page) and Tables


def chapter_title(number: int, name: str) -> str:
    return f"{CHINESE_NUMBERS[number - 1] if number <= 10 else number}、{name}"


def _extract_collection_face(collection: bytes, wanted: str) -> bytes | None:
    """One face of a TrueType/OpenType collection as a standalone font file."""
    if collection[:4] != b"ttcf":
        return collection
    count = struct.unpack(">I", collection[8:12])[0]
    for index in range(count):
        offset = struct.unpack(">I", collection[12 + 4 * index : 16 + 4 * index])[0]
        version, tables = struct.unpack(">IH", collection[offset : offset + 6])
        records = [struct.unpack(">4sIII", collection[offset + 12 + 16 * n : offset + 28 + 16 * n]) for n in range(tables)]
        header = bytearray(collection[offset : offset + 12])
        body = bytearray()
        start = 12 + 16 * tables
        directory = bytearray()
        for tag, checksum, table_offset, length in records:
            directory += struct.pack(">4sIII", tag, checksum, start + len(body), length)
            body += collection[table_offset : table_offset + length]
            body += b"\0" * (-len(body) % 4)
        face = bytes(header + directory + body)
        try:
            name = pymupdf.Font(fontbuffer=face).name
        except Exception:  # noqa: BLE001 - an unreadable face is skipped
            continue
        if wanted in name:
            return face
    return None


@cache
def report_fonts() -> tuple[bytes, bytes]:
    """Regular and bold faces of the one report font family."""
    for regular_path, bold_path in NOTO_PATHS:
        regular_file = Path(regular_path)
        if not regular_file.is_file():
            continue
        regular = _extract_collection_face(regular_file.read_bytes(), "CJK SC")
        if regular is None:
            continue
        bold_file = Path(bold_path)
        bold = _extract_collection_face(bold_file.read_bytes(), "CJK SC") if bold_file.is_file() else None
        return regular, bold or regular
    # MuPDF's own embedded CJK font covers Chinese and ASCII; still one family for every glyph.
    fallback = pymupdf.Font("cjk").buffer
    return fallback, fallback


@cache
def _canonical_codepoints() -> dict[int, int]:
    """Each code point of the report faces mapped to the ordinary code point drawn with the same glyph.

    Noto Sans CJK draws U+0020/U+00A0, U+002D/U+2011 and U+00B7/U+30FB with one glyph each; the PDF text
    layer can name one code point per glyph, and MuPDF picks the higher one, so copied text would read
    no-break spaces, no-break hyphens, katakana dots and radicals for 一/人/日. The ordinary one is what the report wrote.
    """
    from fontTools.ttLib import TTFont

    canonical: dict[int, int] = {}
    for face in dict.fromkeys(report_fonts()):
        cmap = TTFont(BytesIO(face), fontNumber=0, lazy=True).getBestCmap() or {}
        preferred: dict[str, int] = {}
        for code, glyph in cmap.items():
            preferred[glyph] = min(code, preferred.get(glyph, code), key=_preference)
        for code, glyph in cmap.items():
            if preferred[glyph] != code:
                canonical.setdefault(code, preferred[glyph])
    return canonical


# Code points that only share another character's glyph: no-break space and hyphen, the katakana middle dot,
# CJK radicals and Kangxi radicals, private-use code points and compatibility ideographs. The ordinary character wins.
_VARIANTS = ((0x00A0, 0x00A0), (0x2011, 0x2011), (0x30FB, 0x30FB), (0x2E80, 0x2FDF), (0xE000, 0xF8FF), (0xF900, 0xFAFF), (0x2F800, 0x2FA1F), (0xF0000, 0x10FFFF))


def _preference(code: int) -> tuple[bool, int]:
    return any(low <= code <= high for low, high in _VARIANTS), code


_HEX = re.compile(r"<([0-9a-fA-F]+)>")


def _canonical_to_unicode(document: pymupdf.Document) -> None:
    """Rewrite each font's ToUnicode map so a shared glyph copies as the code point the report wrote."""
    canonical = _canonical_codepoints()
    for xref in range(1, document.xref_length()):
        kind, value = document.xref_get_key(xref, "ToUnicode")
        if kind != "xref":
            continue
        target = int(value.split()[0])
        cmap = document.xref_stream(target).decode("latin-1")
        mapping: dict[int, str] = {}
        for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
            for source, destination in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
                mapping[int(source, 16)] = destination
        for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
            for line in block.strip().splitlines():
                codes = _HEX.findall(line)
                if len(codes) != 3 or "[" in line:
                    return  # an array range is not produced by MuPDF; leave the map untouched rather than guess
                first, last, start = int(codes[0], 16), int(codes[1], 16), int(codes[2], 16)
                for offset in range(last - first + 1):
                    mapping[first + offset] = f"{start + offset:04x}"
        changed = False
        for source, destination in mapping.items():
            if len(destination) == 4 and int(destination, 16) in canonical:
                mapping[source] = f"{canonical[int(destination, 16)]:04x}"
                changed = True
        if not changed:
            continue
        entries = sorted(mapping.items())
        blocks = "".join(
            f"{len(chunk)} beginbfchar\n" + "".join(f"<{source:04x}> <{destination}>\n" for source, destination in chunk) + "endbfchar\n"
            for chunk in (entries[index : index + 100] for index in range(0, len(entries), 100))
        )
        head = cmap[: cmap.index("endcodespacerange") + len("endcodespacerange\n")]
        tail = cmap[cmap.index("endcmap") :]
        document.update_stream(target, (head + blocks + tail).encode("latin-1"))


def _archive() -> tuple[pymupdf.Archive, str]:
    regular, bold = report_fonts()
    archive = pymupdf.Archive()
    archive.add(regular, "report-regular.otf")
    archive.add(bold, "report-bold.otf")
    faces = f"@font-face {{font-family:{FAMILY}; src:url(report-regular.otf)}} @font-face {{font-family:{FAMILY}; font-weight:bold; src:url(report-bold.otf)}} "
    return archive, faces


def table_html(table: Table, rows: Sequence[tuple[str, ...]], continued: bool) -> str:
    caption = f"<p class='caption'>{escape(table.caption)}{'（续表）' if continued else ''}</p>" if table.caption else ""

    def cell(tag: str, column: int, value: str) -> str:
        return f"<{tag} class='nowrap'>{value}</{tag}>" if column in table.nowrap else f"<{tag}>{value}</{tag}>"

    head = "".join(cell("th", column, escape(value)) for column, value in enumerate(table.head))
    body = "".join("<tr>" + "".join(cell("td", column, value) for column, value in enumerate(row)) + "</tr>" for row in rows)
    return f"{caption}<table><tr>{head}</tr>{body}</table>"


class _Pages:
    def __init__(self, writer: pymupdf.DocumentWriter, archive: pymupdf.Archive, css: str):
        self.writer, self.archive, self.css = writer, archive, css
        self.device = None
        self.count = 0
        self.y = BODY.y0

    def new_page(self) -> None:
        if self.device is not None:
            self.writer.end_page()
        if self.count >= MAX_PAGES:
            raise ValueError("report_page_limit")
        self.device = self.writer.begin_page(PAGE)
        self.count += 1
        self.y = BODY.y0

    def close(self) -> None:
        if self.device is not None:
            self.writer.end_page()
            self.device = None

    def _story(self, html: str) -> pymupdf.Story:
        return pymupdf.Story(html=f'<html lang="zh"><body>{html}</body></html>', user_css=self.css, archive=self.archive)

    def _remaining(self) -> pymupdf.Rect:
        return pymupdf.Rect(BODY.x0, self.y, BODY.x1, BODY.y1)

    def _fits(self, html: str, rect: pymupdf.Rect) -> bool:
        more, _filled = self._story(html).place(rect)
        return not more

    def flow(self, html: str) -> None:
        """Place HTML from the cursor on, continuing over as many pages as it needs."""
        story = self._story(html)
        while True:
            more, filled = story.place(self._remaining())
            story.draw(self.device)
            if not more:
                self.y = pymupdf.Rect(filled).y1 + GAP
                return
            self.new_page()

    def block(self, html: str) -> None:
        """Keep a block on one page when it fits on a page at all."""
        if self.y > BODY.y0 and not self._fits(html, self._remaining()) and self._fits(html, BODY):
            self.new_page()
        self.flow(html)

    def table(self, table: Table) -> None:
        if not table.rows:
            caption = f"<p class='caption'>{escape(table.caption)}</p>" if table.caption else ""
            self.block(f"{caption}<p>{escape(table.empty)}</p>")
            return
        index, continued = 0, False
        while index < len(table.rows):
            rest = table.rows[index:]
            low, high = 0, len(rest)
            # Largest number of rows that fit below the cursor together with the header.
            while low < high:
                middle = (low + high + 1) // 2
                if self._fits(table_html(table, rest[:middle], continued), self._remaining()):
                    low = middle
                else:
                    high = middle - 1
            if low == 0:
                if self.y > BODY.y0:
                    self.new_page()
                    continue
                # One row taller than a page: it runs on over pages; nothing is cut.
                self.flow(table_html(table, rest[:1], continued))
                index += 1
            else:
                story = self._story(table_html(table, rest[:low], continued))
                _more, filled = story.place(self._remaining())
                story.draw(self.device)
                self.y = pymupdf.Rect(filled).y1 + GAP
                index += low
            continued = True
            if index < len(table.rows):
                self.new_page()


def _size(blocks) -> int:
    return sum(len(block) if isinstance(block, str) else sum(len(cell) for row in block.rows for cell in row) for block in blocks)


def render_report(*, cover: str, chapters: list[Chapter], name: str, kind: str, date: str) -> bytes:
    """``cover`` is the cover HTML; ``name`` and ``kind`` go into the running header, ``date`` into the footer."""
    if len(cover) + sum(_size(chapter.blocks) for chapter in chapters) > MAX_HTML:
        raise ValueError("report_content_too_large")
    archive, faces = _archive()
    css = faces + CSS
    output = BytesIO()
    starts: list[int] = []
    with pymupdf.DocumentWriter(output) as writer:
        pages = _Pages(writer, archive, css)
        pages.new_page()
        pages.flow(f"<div class='cover'>{cover}</div>")
        for chapter in chapters:
            # Every chapter starts a page, so the outline and the reader land on its first line.
            pages.new_page()
            starts.append(pages.count)
            pages.block(f"<h1>{escape(chapter.title)}</h1>")
            for block in chapter.blocks:
                if isinstance(block, Table):
                    pages.table(block)
                else:
                    pages.block(block)
        pages.close()
    # The contents page is the second page; every chapter start moves one page on.
    starts = [start + 1 for start in starts]
    entries = [("封面", 1), *((chapter.title, start) for chapter, start in zip(chapters, starts, strict=True))]
    rows = "".join(f"<tr><td>{escape(title)}</td><td class='page'>{page}</td></tr>" for title, page in entries)
    contents = BytesIO()
    with pymupdf.DocumentWriter(contents) as writer:
        story = pymupdf.Story(html=f'<html lang="zh"><body><h1>目录</h1><table class="toc">{rows}</table></body></html>', user_css=css, archive=archive)
        device = writer.begin_page(PAGE)
        more, _filled = story.place(BODY)
        if more:
            raise ValueError("report_contents_overflow")
        story.draw(device)
        writer.end_page()
    with pymupdf.open(stream=output.getvalue(), filetype="pdf") as document, pymupdf.open(stream=contents.getvalue(), filetype="pdf") as toc:
        document.insert_pdf(toc, start_at=1)
        total = len(document)
        margin = f"body {{font-family:{FAMILY}; font-size:8pt; color:#444}} p {{margin:0}}"
        header = text(name if len(name) <= 50 else name[:49] + "…")
        for index, page in enumerate(document):
            if index == 0:
                continue  # the cover has no running header or footer
            page.insert_htmlbox(pymupdf.Rect(51, 30, 420, 50), f"<p>{header}</p>", css=faces + margin, archive=archive)
            page.insert_htmlbox(pymupdf.Rect(420, 30, 544, 50), f"<p style='text-align:right'>{escape(kind)}</p>", css=faces + margin, archive=archive)
            page.draw_line((51, 48), (544, 48), color=(0.6, 0.6, 0.6), width=0.5)
            page.insert_htmlbox(pymupdf.Rect(51, 800, 300, 820), f"<p>{escape(date)}</p>", css=faces + margin, archive=archive)
            page.insert_htmlbox(pymupdf.Rect(300, 800, 544, 820), f"<p style='text-align:right'>第 {index + 1} / {total} 页</p>", css=faces + margin, archive=archive)
        # fontTools subsets the Noto CJK faces; MuPDF's own subsetter rejects their CFF and leaves them whole.
        # The text layer is made exact before the contents lines are found by their titles.
        document.subset_fonts(fallback=True)
        _canonical_to_unicode(document)
        # Each contents line links to its page.
        contents_page = document[1]
        for title, page_number in entries:
            hits = contents_page.search_for(title)
            if hits:
                line = pymupdf.Rect(BODY.x0, hits[0].y0, BODY.x1, hits[0].y1)
                contents_page.insert_link({"kind": pymupdf.LINK_GOTO, "from": line, "page": page_number - 1, "to": pymupdf.Point(0, 0), "zoom": 0})
        document.set_toc([[1, title, page_number] for title, page_number in entries])
        # Properties carry the report name and type only: no internal identifier, build or tool version.
        document.set_metadata({"title": name, "subject": kind, "author": "", "keywords": "", "creator": "", "producer": "", "creationDate": "", "modDate": "", "trapped": ""})
        document.del_xml_metadata()
        return document.tobytes(garbage=4, deflate=True)
