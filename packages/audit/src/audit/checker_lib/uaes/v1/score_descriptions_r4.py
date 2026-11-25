"""PFMEA:2 reads the actual effect and controls from their own cells; the program owns every score boundary."""

from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from audit.engine.rule_engine import GuardStop

from .fmea import placeholder
from .scoped_text_r4 import read_facts

EFFECT, PREVENT, DETECT = "失效后果", "预防控制", "探测控制"
_LABELS = {
    EFFECT: {"安全": "safety", "法规": "safety", "轻微外观": "minor_only"},
    PREVENT: {"物理防错": "physical", "全自动锁死": "auto_lock", "作业指导书": "instruction_only", "无预防措施": "absent"},
    DETECT: {"人工抽检": "manual_spot", "自动防错": "automatic_poka", "无法探测": "absent", "无探测措施": "absent"},
}
_ALLOWED = {role: set(terms.values()) | {"other", "unknown"} for role, terms in _LABELS.items()}


class CellReading(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    kind: Literal["safety", "minor_only", "physical", "auto_lock", "instruction_only", "absent", "manual_spot", "automatic_poka", "other", "unknown"]
    quote: str


class Readings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cells: list[CellReading]


def _applicable(s, o, d) -> Iterator[tuple[str, set[str], str]]:
    if s < 9:
        yield EFFECT, {"safety"}, f"安全或法规后果，S={s}低于9"
    if not 2 <= s <= 4:
        yield EFFECT, {"minor_only"}, f"仅轻微外观后果，S={s}不在2～4"
    if o <= 3:
        yield PREVENT, {"absent", "instruction_only"}, f"无预防控制或仅人工指导书，O={o}≤3"
    if o >= 8:
        yield PREVENT, {"physical"}, f"有物理防错，O={o}≥8"
    if s >= 9 and o == 1:
        yield PREVENT, {"absent", "instruction_only", "other"}, f"S={s}≥9，未设物理防错或全自动锁死，O=1"
    if d < 10:
        yield DETECT, {"absent"}, f"无探测措施，D={d}<10"
    if d <= 3:
        yield DETECT, {"manual_spot"}, f"人工抽检，D={d}≤3"
    if d >= 8:
        yield DETECT, {"automatic_poka"}, f"自动防错，D={d}≥8"


def _sources(reader, item, s, o, d):
    for field in dict.fromkeys(f for f, _, _ in _applicable(s, o, d)):
        if field not in item.columns:
            continue
        col, row = item.columns[field], item.row
        merge = item.sheet.ctx.ops.get_detailed_merge_info(item.sheet.ws, row, col) if item.sheet.ctx.ops else None
        row, col = (merge.min_row, merge.min_col) if merge else (row, col)
        value = reader.raw(item.sheet, row, col)
        if not value and (reader.uncomputed(item.sheet, row, col) or item.values.get(field)):
            # A carried description isn't evidence at this cell; only an actual merge binds its value to this row.
            continue
        page_cell = None
        if str(reader.audit_file.file_url).lower().endswith(".pdf"):
            # A converted cell is not a PDF location, nor proof that a blank cell has no drawing.
            if reader.drawn(item.sheet, row, col) is not False:
                continue
            page_cell = reader._cell_sources.get((reader._workbook_cache.sheetnames.index(item.sheet.title), row, col))
            if page_cell is None:
                continue
        yield field, ("" if value is None else str(value).strip()), (row, col, page_cell)


def _read_descriptions(texts):
    pending = [k for k, v in texts.items() if v is None]
    # Batch identical field roles/texts once; never send the numeric ratings or adjacent rows to the model.
    # One bounded request per file. Never claim coverage of unrequested or unread fields.
    if not pending:
        return
    chunk = pending[:32]
    keys = {f"{field}:{i}": (field, text) for i, (field, text) in enumerate(chunk)}
    try:
        reading = Readings.model_validate(
            read_facts(
                "pfmea_score_cell_facts",
                sources={k: v[1] for k, v in keys.items()},
                instructions=(
                    "每个id独立读取该格明确写出的本行事实。quote逐字保留整格。失效后果只区分实际安全/法规后果safety、仅轻微外观minor_only、其他。"
                    "预防控制只区分已实施的物理防错physical、仅写全自动锁死auto_lock、只用人工指导书instruction_only、明确无措施absent、其他。"
                    "探测控制只区分实际人工抽检manual_spot、自动防错automatic_poka、明确无措施absent、其他。"
                    "否定、假设、曾用的方法不能充当实际方法。明确是另一类才用other，混有多种方法且不能唯一归类、含混或相互矛盾则unknown。"
                ),
                schema=Readings.model_json_schema(),
            )
        )
    except GuardStop as exc:
        if exc.status != "UNABLE":
            raise
        return
    except ValidationError:
        return
    if len(reading.cells) != len(keys) or {v.id for v in reading.cells} != keys.keys():
        return
    for cell in reading.cells:
        field, source = keys[cell.id]
        if cell.quote == source and cell.kind in _ALLOWED[field]:
            texts[field, source] = cell.kind


def score_reader(reader, items):
    texts = {}
    positions = {}
    for item in items:
        scores = [reader.integer(item.values.get(k, "")) for k in ("严重度", "频度", "探测度")]
        if not all(n is not None and 1 <= n <= 10 for n in scores):
            continue
        for field, source, pos in _sources(reader, item, *scores):
            key = (field, source)
            positions[id(item), field] = (key, pos)
            if key not in texts:
                if not source or source.casefold() in {"无", "none"}:
                    texts[key] = "absent" if field != EFFECT else "unknown"
                elif placeholder(source):
                    texts[key] = "unknown"  # A reference such as "see attachment" doesn't establish absence.
                else:
                    texts[key] = _LABELS[field].get(source.rstrip("。"))
    _read_descriptions(texts)

    def check(item, s, o, d, fail, gaps):
        for field in dict.fromkeys(f for f, _, _ in _applicable(s, o, d)):
            entry = positions.get((id(item), field))
            kind = texts[entry[0]] if entry else None
            if kind is None or kind == "unknown":
                gaps.append(f"{item.where()}的{field}未读清")
                continue
            key, (row, col, page_cell) = entry
            if page_cell:
                page, bbox = page_cell
                reader.legacy().witness({"kind": "pdf_region", "page": page, "bbox": bbox}, key[1])
            else:
                reader.cite(item.sheet, row, col)
            for role, forbidden, explanation in _applicable(s, o, d):
                if role == field and kind in forbidden:
                    reader.at(item, {EFFECT: "严重度", PREVENT: "频度", DETECT: "探测度"}[field])
                    fail({EFFECT: "2", PREVENT: "3", DETECT: "4"}[field], f"{item.where()}{field}“{key[1]}”：{explanation}")

    return check
