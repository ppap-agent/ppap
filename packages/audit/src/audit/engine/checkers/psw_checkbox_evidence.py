"""Read explicit, source-located PSW disposition checkbox symbols.

A nearby option name or an isolated mark does not establish its state. This
adapter accepts only one labelled row and an attached checkbox glyph. It does
not infer missing symbols or use marks from submission/material sections.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

_FIELDS = ("approved_checked", "conditional_checked", "rejected_checked")
_OPTION_FIELDS = {"批准": "approved_checked", "条件批准": "conditional_checked", "拒绝": "rejected_checked"}
_ATTACHED_OPTION = re.compile(r"([□☐☑☒])(批准|条件批准|拒绝)(?:[（(]需提供整改计划[）)])?")


def _bounds(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        return None
    x0, y0, x1, y1 = (float(v) for v in value)
    return (x0, y0, x1, y1) if 0 <= x0 < x1 and 0 <= y0 < y1 else None


def read_disposition_facts(
    texts: Sequence[Mapping[str, Any]],
    witness: Callable[..., None],
    *,
    page: int,
    source_sha256: str,
) -> dict[str, bool | None]:
    """Return known states only for exact symbols of the native text layer in one disposition row.

    A caller must validate the original-page identity and coordinate mapping.
    """
    facts: dict[str, bool | None] = dict.fromkeys(("disposition_visible", *_FIELDS))
    if type(page) is not int or page < 1 or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None:
        return facts
    if len(texts) > 10000:
        return facts
    entries = []
    for item in texts:
        if not isinstance(item.get("text"), str) or type(item.get("index")) is not int or item["index"] < 0:
            continue
        bbox = _bounds(item.get("pdf_bbox"))
        if bbox is None:
            continue
        text = re.sub(r"\s+", "", item["text"])
        entries.append((item, text, bbox))
    # Duplicated IDs cannot provide distinct, trustworthy source locations.
    counts = Counter(item["index"] for item, _, _ in entries)
    if any(count != 1 for count in counts.values()):
        return facts
    anchors = [(item, box) for item, text, box in entries if text.rstrip(":：") == "认可情况"]
    if len(anchors) != 1:
        return facts
    anchor, anchor_box = anchors[0]

    def note(item, bbox, fact):
        witness(
            {
                "kind": "pdf_region",
                "page": page,
                "bbox": list(bbox),
                "source_sha256": source_sha256,
                "coordinate_system": "pdf_points_unrotated",
                "source": item.get("source", "native_pdf"),
                "element": "checkbox_text",
                "element_index": item["index"],
                "fact": fact,
            },
            item["text"],
            visual=False,
        )

    facts["disposition_visible"] = True
    note(anchor, anchor_box, "disposition_visible")
    options: dict[str, list[tuple[bool, Mapping[str, Any], tuple[float, float, float, float]]]] = {field: [] for field in _FIELDS}
    for item, text, box in entries:
        match = _ATTACHED_OPTION.fullmatch(text)
        if match is None or box[0] <= anchor_box[2]:
            continue
        overlap = min(box[3], anchor_box[3]) - max(box[1], anchor_box[1])
        if overlap < 0.5 * min(box[3] - box[1], anchor_box[3] - anchor_box[1]):
            continue
        field = _OPTION_FIELDS[match[2]]
        options[field].append((match[1] in {"☑", "☒"}, item, box))
    for field, candidates in options.items():
        if len(candidates) != 1:
            continue
        state, item, box = candidates[0]
        facts[field] = state
        note(item, box, field)
    return facts
