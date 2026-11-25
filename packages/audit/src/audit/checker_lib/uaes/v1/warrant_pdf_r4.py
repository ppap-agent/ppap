"""Read a PSW as a paginated document. No PDF tables, guessed page order, or annex-to-body field matching."""

from __future__ import annotations

import math
import re
from collections import Counter
from io import BytesIO
from pathlib import Path

import fitz

from audit.checker_lib.uaes.v1.reader import compact, unable
from audit.checker_lib.uaes.v1.warrant import (
    _BOX,
    _STAMP_FIND,
    _STAMP_LOCAL,
    _STAMP_READ,
    _STAMP_SEARCH,
    _TEXT,
    IDENTITY,
    ORDER,
    PARTS,
    SEAL,
    SIGNATURE,
    TOOLING,
    WarrantR3,
    _local_image,
    _object,
    _ScanFacts,
    _subbox,
    part_request,
)
from audit.engine.rule_engine import GuardStop

# Candidate imprints read locally on one page: a bound on model reads, not an assumed number of marks on a warrant.
_STAMP_CROPS = 8
# What a candidate's local reading may show instead of an imprint; anything else ("uncertain") leaves the page open.
_NOT_IMPRINT = frozenset({"printed_logo", "handwriting", "digital_signature", "printed_text", "checkbox"})

# Only a value explicitly printed alongside its own identity label is read natively.
# Generic "date", "number", stacked fields, graphics and other layouts use the visual field reader.
_NATIVE: dict[str, str] = {}
for field, label in (*[(k, v) for k, v in IDENTITY.items() if not k.endswith("_date")], ORDER, TOOLING):
    zh, en = label.rstrip("）").split("（")
    for label in (zh, en, zh + " / " + en):
        _NATIVE[compact(label)] = field

for field, labels in {
    "customer_part_number": ("Customer Part Number",),
    "organization_part_number": ("Organization Part Number",),
    "drawing_number": ("Drawing Number",),
    "engineering_change_level_date": ("编制日期", "日期（编制）", "Date(编制)", "Engineering Change Level Date"),
    "additional_engineering_changes_date": ("修订日期", "其他工程更改日期", "日期（修订）", "Date(修订)", "Additional Engineering Changes Date"),
}.items():
    _NATIVE.update({compact(label): field for label in labels})

_WARRANT = r"(?:零件提交保证书|零件送样保证书|partsubmissionwarrant)(?:[（(](?:psw|partsubmissionwarrant)[）)])?"
_CONTINUATION = r"\s*[-/（(]?\s*(?:续页|续表|continued|continuation)\s*[）)]?"
_ANNEX = r"(?:部门章授权声明|department(?:al)?\s+seal\s+authorization\s+statement|(?:附件|附页|附录|appendix|annex|attachment)(?:\s*[a-z0-9一二三四五]+)?(?:[:：\s-]+.+)?)"
_TITLE_SCHEMA = _object(heading=_TEXT, bbox={"anyOf": [_BOX, {"type": "null"}]}, pagination=_TEXT)


def page_sequence(text):
    match = re.fullmatch(r"(?:page\s*(\d+)\s*(?:of|/)\s*(\d+)|第(\d+)页[，,/]?共(\d+)页)", text.strip(), re.I)
    if match:
        n, total = (int(v) for v in match.groups() if v is not None)
        if 1 <= n <= total <= 10:
            return (n, total)
    return None


def page_kind(heading: str) -> str | None:
    folded = compact(heading)
    if re.fullmatch(_WARRANT, folded):
        return "body"
    if re.fullmatch(_WARRANT + _CONTINUATION, folded):
        return "continuation"
    if re.fullmatch(_ANNEX, heading.strip(), re.I):
        return "annex"
    return None


def page_image(path, number):
    with fitz.open(path) as doc:
        page = doc[number - 1]
        page.set_rotation(0)
        rect = page.rect
        if rect.is_empty or not math.isfinite(rect.get_area()) or rect.get_area() <= 0:
            raise unable("保证书原页尺寸无效。")
        scale = min(200 / 72, 4096 / max(rect.width, rect.height), math.sqrt(10_000_000 / rect.get_area()))
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


class DocumentPage(_ScanFacts):
    def __init__(self, agent, path, page):
        self.agent, self.path, self.number = agent, path, page.number + 1
        page.set_rotation(0)
        self.page_size = page.rect.width, page.rect.height
        super().__init__(agent, b"", self.page_size)
        self.bounds: dict[str, list[float]] = {}
        self.native: dict[str, list[tuple[str, list[float]]]] = {}
        titles: list[tuple[str, str, list[float]]] = []
        folios = set()
        self.role_visual = False
        inline = []
        labels = []
        incomplete = set()
        lines = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]
        # Text behind a scanned image is an OCR layer, not independent native evidence.
        pictures = [fitz.Rect(image["bbox"]) for image in page.get_image_info()]
        for block in lines:
            for line in block.get("lines", []):
                spans = line["spans"]
                box = fitz.Rect(line["bbox"])
                if any(box.intersects(pic) for pic in pictures) or any(not span.get("alpha", 255) for span in spans):
                    continue
                text = "".join(s["text"] for s in spans).strip()
                if kind := page_kind(text):
                    titles.append((kind, text, list(box)))
                if sequence := page_sequence(text):
                    folios.add(sequence)
                if name := _NATIVE.get(compact(text)):
                    if text.endswith((":", "：")):
                        labels.append((name, box))
                    else:
                        incomplete.add(name)
                else:
                    inline.append((text, box))
                pair = re.fullmatch(r"([^：:]+)[：:]\s*([^：:]+)", text)
                if pair and (name := _NATIVE.get(compact(pair[1]))) and pair[2].strip():
                    self.native.setdefault(name, []).append((pair[2].strip(), list(box)))
        # Some word processors position the label and its value in two text runs instead.
        # Accept only a single adjacent run on the same baseline; do not walk into another column.
        for name, box in labels:
            peers = [(t, r) for t, r in inline if t and ":" not in t and "：" not in t and 0 <= r.x0 - box.x1 <= box.height * 1.5 and abs(r.y1 - box.y1) <= min(r.height, box.height) / 5]
            if len(peers) == 1 and not any(c.x0 >= box.x1 and c.x0 < peers[0][1].x0 and abs(c.y1 - box.y1) <= box.height / 5 for _, c in labels):
                value, bound = peers[0]
                self.native.setdefault(name, []).append((value, list(box | bound)))
            else:
                incomplete.add(name)
        for name in incomplete:
            self.native.pop(name, None)
        if not titles:
            prompt = (
                "逐字转写本页自己的文档标题（含“续页”、附件编号等），不是正文中引用的标题。"
                "bbox 仅圈出该标题（坐标范围0–1000）；pagination 只逐字抄如 Page 1 of 2 的页码。无法辨识填 null；不要推测文件类型。"
            )
            observation = agent.transcribe(self.render(), prompt, _TITLE_SCHEMA, f"第{self.number}页标题")
            title = observation.get("heading")
            box_n = self.normalized_box(observation.get("bbox"))
            if box_n and isinstance(title, str) and (kind := page_kind(title)):
                titles.append((kind, title, box_n))
                self.role_visual = True
            if sequence := page_sequence(observation.get("pagination") or ""):
                folios.add(sequence)
        self.folio = next(iter(folios)) if len(folios) == 1 else None
        self.kind = titles[0][0] if titles and len({t[0] for t in titles}) == 1 else None
        if self.kind:
            _, heading, self.bounds["page_role"] = titles[0]
            self.cite("page_role", heading)

    def normalized_box(self, proposal):
        box = _subbox(proposal, (0, 0, 1000, 1000))
        return [round(v * side / 1000, 3) for v, side in zip(box, self.page_size * 2, strict=True)] if box else None

    def render(self) -> bytes:
        if not self.image:
            self.image = page_image(self.path, self.number)
        return self.image

    def get(self, name, default=None):
        if name in self.native:
            found = self.native[name]
            if len(found) != 1:
                return {"present": None}
            value, self.bounds[name] = found[0]
            return {"present": True, "value": value}
        idx = next((i for i, part in enumerate(PARTS) if name in part), None)
        if idx is None:
            return default
        if idx not in self.parts:
            names = tuple(n for n in PARTS[idx] if n not in self.native)
            prompt, schema = part_request(names)
            # Each field has a source region, not a synthetic cell or a presumed first page.
            schema = _object(**{n: _object(**prop["properties"], bbox={"anyOf": [_BOX, {"type": "null"}]}) for n, prop in schema["properties"].items()})
            prompt += (
                "\n净重照抄数值和该栏自己明确声明的单位，不从其他栏目推断。"
                "每个存在的字段用 bbox 圈出它在本页的标签和对应值或整组选项（0–1000坐标），无法定位写null。没有字段时 present=false 仅表示在本页完整核实没有。"
            )
            try:
                self.parts[idx] = self.agent.transcribe(self.render(), prompt, schema, f"保证书正文第{self.number}页")
            except GuardStop as exc:
                self.parts[idx] = exc
        if isinstance(self.parts[idx], GuardStop):
            raise self.parts[idx]
        field = self.parts[idx].get(name) or {}
        if field.get("present") is True:
            # The signature's own line box locates it as well as the box around its label and value.
            box = self.normalized_box(field.get("bbox")) or self.normalized_box(field.get("field_box"))
            if box is None:
                return {"present": None}
            self.bounds[name] = box
        return field

    def cite(self, name, excerpt):
        native = (name == "page_role" and not self.role_visual) or name in self.native
        bbox = self.bounds.get(name)
        loc = {"page": self.number, "source_sha256": self.agent._digest, "engine": "native_pdf" if native else "ocr_llm", "fact": name}
        if bbox:
            loc.update(kind="pdf_region", bbox=bbox, coordinate_system="pdf_points_unrotated")
        else:
            loc.update(kind="pdf_page")
        self.agent.witness(loc, excerpt, visual=not native)


class WarrantDocument(_ScanFacts):
    """Combine only explicitly scoped pages. Two independent PSWs must never make one passing warrant."""

    def __init__(self, agent: WarrantR3, path: Path):
        self.agent = agent
        self.selected: dict[str, DocumentPage] = {}
        with fitz.open(path) as doc:
            if doc.needs_pass or not 1 <= len(doc) <= 10:
                raise unable("保证书原件不能打开或超出本次核对的页数范围。")
            pages = [DocumentPage(agent, path, p) for p in doc]
        self.pages = pages
        self.body = [p for p in pages if p.kind in {"body", "continuation"}]
        # Repeated warrant titles are one body only with a unique and complete printed folio sequence.
        folios = [p.folio for p in self.body]
        numbered = bool(folios) and Counter(folios) == Counter((i, len(folios)) for i in range(1, len(folios) + 1))
        if (sum(p.kind == "body" for p in pages) != 1 and not numbered) or any(p.kind is None for p in pages) or not any(p.kind == "body" for p in pages):
            raise unable("未能将原件各页唯一归属到一份保证书正文、其续页或附件，不能跨页拼接字段。")
        if len(self.body) > 1:
            ids = [p.get("customer_part_number") or {} for p in self.body]
            written = {v["value"] for v in ids if v.get("present") is True and v.get("value")}
            id_bound = len(written) == 1 and all(v.get("present") is True and v.get("value") for v in ids)
            unread_id = any(v.get("present") is None or (v.get("present") is True and v.get("value") is None) for v in ids)
            if unread_id or len(written) > 1 or not (numbered or id_bound):
                raise unable("正文页之间件号不同，或没有可核对的完整分页或一致件号，不能跨页拼接字段。")
        self.annex = {p.number for p in pages if p.kind == "annex"}

    def get(self, name, default=None):
        observations = [(p, p.get(name) or {}) for p in self.body]
        if any(f.get("present") is None for _, f in observations):
            return default
        present = [(p, f) for p, f in observations if f.get("present") is True]
        if len(present) > 1 and name in IDENTITY:
            if all(isinstance(f.get("value"), str) and f["value"] == present[0][1].get("value") for _, f in present):
                for p, f in present[1:]:
                    p.cite(name, f["value"])
                present = present[:1]
        if len(present) == 1:
            self.selected[name], field = present[0]
            if set(field) <= {"present", "bbox"}:
                self.selected[name].cite(name, "原件设有该字段／区域")
            return field
        if not present:
            for page, _ in observations:
                page.cite(name, "本页未见该字段")
            return {"present": False}
        return default

    def __getitem__(self, name):
        return self.get(name) or {}

    def signature(self):
        field = self.get(SIGNATURE[0])
        page = self.selected.get(SIGNATURE[0])
        if not (page and field):
            return {}
        page.render()
        read = self.agent._scan_signature(page, dict(field))
        boxes = [b for b in (field.get("field_box"), field.get("bbox")) if isinstance(b, list) and len(b) == 4 and all(type(v) is int for v in b)]
        if read.get("handwritten") is None and boxes:
            # A signature line's box can miss its line by a row. Its boxes are read once more, joined and grown by one row
            # above and below; the local reading must still name the supplier's signature line, not a neighbouring row.
            row = max(10, boxes[0][3] - boxes[0][1])
            wider = [min(b[0] for b in boxes), max(0, min(b[1] for b in boxes) - row), max(b[2] for b in boxes), min(1000, max(b[3] for b in boxes) + row)]
            read = self.agent._scan_signature(page, {**field, "field_box": wider})
        return read

    def seal(self):
        """An ink seal anywhere in the warrant file: one located imprint is enough. Absence needs every page of the file
        looked at whole, in two overlapping halves, and every candidate located there read on its own crop as something other
        than an imprint; the product owner decided (2026-11-24) that a completely read original without a seal fails. A page
        with an unconfirmed candidate, or more candidates than the read budget, keeps the fact open."""
        unknown = False
        # The warrant's own pages first, then its annexes: a seal on any page of the file counts.
        for page in [*self.body, *(p for p in self.pages if p not in self.body)]:
            result = self.page_seal(page)
            if result is True:
                return True
            unknown |= result is None
        return None if unknown else False

    def page_seal(self, page: DocumentPage) -> bool | None:
        from PIL import Image

        agent = self.agent
        with Image.open(BytesIO(page.render())) as image:
            w, h = image.size
            crops: dict[tuple[int, int, int, int], dict] = {}
            cleared = True
            for panel in ((0, 0, w, (h * 55 + 99) // 100), (0, h * 45 // 100, w, h)):
                result = agent.transcribe(_local_image(image, panel), _STAMP_FIND, _STAMP_SEARCH, "印章全页分区定位")
                boxes = result.get("candidates")
                if not isinstance(boxes, list):
                    cleared = False
                    continue
                looked = True
                for proposal in boxes:
                    box = _subbox(proposal, panel)
                    if box is None:
                        looked = False
                        continue
                    # The mark with its surroundings, so it is read whole and in place (a header logo beside the form title,
                    # an imprint over a signing line), not as a clipped fragment.
                    pad_x, pad_y = max(16, (box[2] - box[0]) // 2), max(16, (box[3] - box[1]) // 2)
                    box = (max(0, box[0] - pad_x), max(0, box[1] - pad_y), min(w, box[2] + pad_x), min(h, box[3] + pad_y))
                    if len(set(image.crop(box).convert("L").getextrema())) == 1:
                        continue  # Blank paper: nothing is drawn there, so no imprint either.
                    if box not in crops:
                        if len(crops) >= _STAMP_CROPS:
                            looked = False
                            continue
                        crops[box] = agent.transcribe(_local_image(image, box), _STAMP_READ, _STAMP_LOCAL, "疑似印迹的原件局部")
                    local = crops[box]
                    kind, detail = local.get("kind"), local.get("detail")
                    if not isinstance(detail, str) or not detail.strip():
                        looked = False
                        continue
                    agent._scan_note(page, image, box, SEAL[0], f"{kind}：{detail}")
                    if kind == "imprint":
                        return True
                    if kind not in _NOT_IMPRINT:
                        looked = False
                if looked:
                    agent._scan_note(page, image, panel, SEAL[0], "本图区域已看过，所定位的图形均不是盖章印迹")
                cleared = cleared and looked
        return False if cleared else None
