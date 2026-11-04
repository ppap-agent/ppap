"""Native-page selection and supported Paddle facts, without model fallback."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import fitz


class VisualFactsUnavailable(ValueError):
    """The original cannot establish all requested visual facts."""


_SEAL_FIELDS = frozenset({"seal", "psw_stamp_present", "controlled_stamp"})
_DISPOSITION_FIELDS = frozenset({"disposition_visible", "approved_checked", "conditional_checked", "rejected_checked"})
_NATIVE_DISPOSITION_TOKEN = re.compile(r"认可情况[：:]?|[□☐☑☒](?:条件批准|批准|拒绝)")


def _native_disposition_texts(page):
    """Use original character geometry; never attach an isolated nearby mark."""
    entries = []
    raw = page.get_text("rawdict", flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
    all_chars = [char for block in raw["blocks"] for line in block.get("lines", []) for span in line.get("spans", []) for char in span.get("chars", [])]
    overlays = []
    for drawing in page.get_drawings():
        rect = fitz.Rect(drawing["rect"])
        padding = max(0.5, float(drawing.get("width") or 0) / 2)
        overlays.append(rect + (-padding, -padding, padding, padding))
    overlays.extend(fitz.Rect(image["bbox"]) for image in page.get_image_info())
    overlays.extend(annotation.rect for annotation in (page.annots() or ()))
    overlays.extend(widget.rect for widget in (page.widgets() or ()))
    for block in raw["blocks"]:
        for line in block.get("lines", []):
            direction = line.get("dir", (1, 0))
            if abs(direction[0] - 1) > 0.01 or abs(direction[1]) > 0.01:
                continue
            chars = [char for span in line.get("spans", []) for char in span.get("chars", []) if not char["c"].isspace()]
            if any(len(char["c"]) != 1 for char in chars):
                continue
            text = "".join(char["c"] for char in chars)
            for match in _NATIVE_DISPOSITION_TOKEN.finditer(text):
                selected = chars[match.start() : match.end()]
                boxes = [char["bbox"] for char in selected]
                if match[0][0] in "□☐☑☒":
                    mark = fitz.Rect(boxes[0])
                    other_ink = (fitz.Rect(char["bbox"]) for char in all_chars if char is not selected[0] and char not in selected[1:] and char["c"].strip())
                    if any(not (mark & overlay).is_empty for overlay in overlays) or any(not (mark & box).is_empty for box in other_ink):
                        # A text-layer empty glyph can have a separate tick on
                        # top. Text alone cannot establish the rendered state.
                        continue
                # The attached glyph and its label must be physically adjacent.
                if any(b[0] < a[0] - 1 or b[0] - a[2] > 1.5 * max(a[3] - a[1], b[3] - b[1]) for a, b in zip(boxes, boxes[1:])):
                    continue
                entries.append(
                    {
                        "index": len(entries),
                        "text": match[0],
                        "source": "native_pdf",
                        "pdf_bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)],
                    }
                )
    return entries


def read_original_pdf_visual_facts(
    audit,
    fields: dict[str, str],
    witness,
    *,
    operation="v4_original_visual_facts",
    cache: dict[Any, Any] | None = None,
) -> dict[str, bool | None]:
    """Located seal detection proves presence only; other facts stay unknown.

    One-page sources select themselves. For longer PDFs, native labels must
    identify one relevant page. No label means no OCR, not a full-document scan.
    The authorized checker owns the cache and its frozen source.
    """
    from documents.ocr.paddle.reader import PaddleEvidenceUnavailable, read_pdf_page_evidence

    from audit.engine.checkers.psw_checkbox_evidence import read_disposition_facts
    from audit.engine.rule_engine import GuardStop
    from audit.engine.runtime import get_runtime
    from audit.pdf_tables import valid_pdf_bbox

    result: dict[str, bool | None] = dict.fromkeys(fields)
    needs_seal = bool(_SEAL_FIELDS.intersection(fields))
    needs_disposition = bool(_DISPOSITION_FIELDS.intersection(fields))
    if not needs_seal and not needs_disposition:
        return result
    path = Path(audit.file_url)
    if path.suffix.casefold() != ".pdf":
        raise VisualFactsUnavailable("此载体没有可用的Paddle原页证据，需人工核对原件。")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    cache = {} if cache is None else cache
    identity = ("frozen_source", audit.audit_id)
    if identity in cache and cache[identity] != digest:
        raise ValueError("Frozen PDF identity changed")
    cache[identity] = digest
    metadata = audit.file_metadata or {}
    expected = metadata.get("source_sha256") or metadata.get("sha256")
    if expected and expected != digest:
        raise ValueError("Frozen PDF identity changed")
    with fitz.open(path) as document:
        if document.needs_pass or not len(document):
            raise VisualFactsUnavailable("原件页或访问权限不可确认。")
        if len(document) == 1:
            selected = 1
        else:
            labels = ("认可情况",) if needs_disposition else ("零件提交保证书", "供方代表签字") if "psw_stamp_present" in fields else ("盖章", "印章", "零件提交保证书")
            candidates = [page.number + 1 for page in document if any(label in page.get_text() for label in labels)]
            if len(candidates) != 1:
                return result
            selected = candidates[0]
        if needs_disposition:
            native = read_disposition_facts(
                [item for item in _native_disposition_texts(document[selected - 1]) if valid_pdf_bbox(item["pdf_bbox"], document[selected - 1].rect.width, document[selected - 1].rect.height)],
                lambda locator, excerpt, **kwargs: witness({**locator, "file_version_id": audit.audit_id}, excerpt, **kwargs),
                page=selected,
                source_sha256=digest,
                visual=False,
            )
            result.update({field: native[field] for field in _DISPOSITION_FIELDS.intersection(fields)})
            if not needs_seal and all(native[field] is not None for field in _DISPOSITION_FIELDS):
                return result
    key = (digest, selected)
    if key not in cache:
        runtime = get_runtime()
        reader = getattr(runtime, "read_paddle_page", None)
        try:
            cache[key] = reader(str(path), selected) if callable(reader) else read_pdf_page_evidence(path, selected)
        except PaddleEvidenceUnavailable:
            cache[key] = None
        except GuardStop as exc:
            cache[key] = VisualFactsUnavailable(exc.message) if exc.status == "UNABLE" else exc
        except Exception as exc:
            # Retain failures: another rule must not replay this request.
            cache[key] = exc
    value = cache[key]
    if isinstance(value, Exception):
        raise value
    if value is None:
        return result
    if value.get("source_sha256") != digest:
        raise ValueError("Paddle source identity invalid")
    pages = value.get("pages", [])
    if len(pages) != 1 or pages[0].get("page") != selected:
        raise ValueError("Paddle page identity invalid")
    page = pages[0]
    frame = page.get("frame") or {}
    if needs_disposition:
        texts = [item for item in page.get("texts", []) if valid_pdf_bbox(item.get("pdf_bbox"), frame.get("pdf_width"), frame.get("pdf_height"))]
        observed = read_disposition_facts(
            texts,
            lambda locator, excerpt, **kwargs: witness({**locator, "file_version_id": audit.audit_id}, excerpt, **kwargs),
            page=selected,
            source_sha256=digest,
            visual=True,
        )
        for field in _DISPOSITION_FIELDS.intersection(fields):
            if result[field] is None:
                result[field] = observed[field]
    for index, seal in enumerate(page.get("seals", []) if needs_seal else []):
        bounds = seal.get("pdf_bbox")
        if seal.get("detected") is not True or not valid_pdf_bbox(bounds, frame.get("pdf_width"), frame.get("pdf_height")):
            continue
        witness(
            {
                "kind": "pdf_region",
                "page": selected,
                "bbox": bounds,
                "coordinate_system": "pdf_points_unrotated",
                "source_sha256": digest,
                "file_version_id": audit.audit_id,
                "element": "seal",
                "element_index": index,
                "engine": value.get("engine"),
            },
            "Paddle检测到此区域存在印章；未确认完整文字、真伪或签字。",
            visual=True,
        )
        for field in _SEAL_FIELDS.intersection(fields):
            if field != "controlled_stamp" or any("".join(str(text).split()) in {"受控", "受控章", "受控文件"} for text in seal.get("recognized_text", [])):
                result[field] = True
    if needs_seal and all(value is None for value in result.values()) and any(item.get("element") == "seal" and item.get("outcome", "error") != "unknown" for item in value.get("diagnostics", [])):
        raise ValueError("Paddle seal extraction failed")
    # Empty output proves neither absence, checkbox state nor authenticity.
    return result
