"""Bounded original visual facts. Business predicates belong to each checker.

These readers inspect graphics, not table contents. PDF text and spreadsheet
cells remain the native readers' responsibility. An unsupported carrier or an
incomplete source does not establish a document-wide fact.
"""

from __future__ import annotations

import hashlib
import math
import os
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import fitz


class VisualFactsUnavailable(ValueError):
    """The original has no supported, complete visual evidence scope."""


_MAX_SCOPES = 10
_MAX_IMAGE_PIXELS = 12_000_000
_MAX_TOTAL_PIXELS = 32_000_000
_SEAL_FIELDS = frozenset({"seal", "controlled_stamp"})


def _source_identity(audit, cache):
    with Path(audit.file_url).open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    metadata = audit.file_metadata or {}
    expected = metadata.get("source_sha256") or metadata.get("sha256")
    key = ("frozen_source", audit.audit_id)
    if (expected and expected != digest) or cache.get(key, digest) != digest:
        raise ValueError("Frozen original identity changed")
    cache[key] = digest
    return digest


def _policy_identity(audit, cache):
    from documents.ocr.env import ocr_policy_identity

    policy = ocr_policy_identity()
    key = ("ocr_policy", audit.audit_id)
    if cache.get(key, policy) != policy:
        raise ValueError("OCR configuration changed during the audit")
    cache[key] = policy
    return policy


def _pdf_images(audit, digest):
    from audit.engine.checkers.ocr_visual_facts import OcrImage

    images, context = [], {}
    with fitz.open(audit.file_url) as document:
        if document.needs_pass or not 1 <= len(document) <= _MAX_SCOPES:
            raise VisualFactsUnavailable("原件不能完整读取，或超过十页视觉读取范围。")
        pixel_budget = min(_MAX_IMAGE_PIXELS, _MAX_TOTAL_PIXELS / len(document))
        for number, page in enumerate(document, 1):
            # Mutate only this private in-memory page, never the frozen file.
            page.set_rotation(0)
            bounds = page.rect
            area = bounds.width * bounds.height
            if not math.isfinite(area) or area <= 0:
                raise VisualFactsUnavailable("原页尺寸无效。")
            scale = min(2, math.sqrt(pixel_budget / area), 4096 / max(bounds.width, bounds.height))
            pixels = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            scope = f"pdf:{number}"
            images.append(
                OcrImage(
                    pixels.tobytes("png"),
                    {
                        "kind": "pdf_region",
                        "page": number,
                        "bbox": list(bounds),
                        "coordinate_system": "pdf_points_unrotated",
                        "source_sha256": digest,
                        "file_version_id": str((audit.file_metadata or {}).get("file_version_id") or audit.audit_id),
                        "scope": scope,
                        "context": True,
                        "located_region": True,
                    },
                    pixels.width,
                    pixels.height,
                )
            )
            context[scope] = page.get_text()
    return images, context


def _raster_images(audit, digest):
    from PIL import Image

    from audit.engine.checkers.ocr_visual_facts import OcrImage

    with Image.open(audit.file_url) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise VisualFactsUnavailable("多帧图像没有受支持的完整范围。")
        width, height = image.size
        if width * height > _MAX_IMAGE_PIXELS:
            raise VisualFactsUnavailable("原图像超过有界视觉读取范围。")
        image.load()
        resized = image.convert("RGB")
        resized.thumbnail((4096, 4096))
        output = BytesIO()
        resized.save(output, format="PNG")
    return [
        OcrImage(
            output.getvalue(),
            {
                "kind": "image_region",
                "scope": "image:1",
                "bbox": [0, 0, width, height],
                "coordinate_system": "image_pixels",
                "source_sha256": digest,
                "file_version_id": str((audit.file_metadata or {}).get("file_version_id") or audit.audit_id),
                "context": True,
                "located_region": True,
            },
            resized.width,
            resized.height,
        )
    ], {}


def _paddle_seals(audit, fields, digest, cache, witness):
    """A located seal proves presence only. It never proves a blank region."""
    from documents.ocr.paddle.reader import PaddleEvidenceUnavailable, read_pdf_page_evidence

    from audit.engine.runtime import get_runtime
    from audit.pdf_tables import valid_pdf_bbox

    result = dict.fromkeys(fields)
    runtime = get_runtime()
    hook = getattr(runtime, "read_paddle_page", None)
    if not callable(hook):
        if runtime is not None or not (os.environ.get("PPAP_PADDLE_BRIDGE_URL", "").strip() or os.environ.get("PADDLE_OCR_URL", "").strip()):
            return result
    with fitz.open(audit.file_url) as document:
        # No guessed page role and no full-document Paddle sweep.
        if document.needs_pass or len(document) != 1:
            return result
        original_bounds = document[0].rect * document[0].derotation_matrix
    key = ("directory_paddle_page", audit.audit_id, digest, 1)
    if key not in cache:
        try:
            cache[key] = hook(str(audit.file_url), 1) if callable(hook) else read_pdf_page_evidence(Path(audit.file_url), 1)
        except PaddleEvidenceUnavailable:
            cache[key] = None
        except Exception as error:
            cache[key] = error
    value = cache[key]
    if isinstance(value, Exception):
        raise value
    if value is None:
        return result
    if value.get("source_sha256") != digest:
        raise ValueError("Paddle source identity invalid")
    pages = value.get("pages", [])
    if len(pages) != 1 or pages[0].get("page") != 1:
        raise ValueError("Paddle page identity invalid")
    page = pages[0]
    frame = page.get("frame") or {}
    if any(
        type(frame.get(name)) not in (int, float) or not math.isclose(frame[name], expected, abs_tol=0.01)
        for name, expected in (("pdf_width", original_bounds.width), ("pdf_height", original_bounds.height))
    ):
        raise ValueError("Paddle original frame invalid")
    for index, seal in enumerate(page.get("seals", [])):
        bounds = seal.get("pdf_bbox")
        if seal.get("detected") is not True or not valid_pdf_bbox(bounds, frame.get("pdf_width"), frame.get("pdf_height")):
            continue
        for field in fields:
            # A substring also matches “非受控”. Only an unambiguous stamp
            # designation can establish this named presence capability.
            if field == "controlled_stamp" and "".join(str(seal.get("recognized_text", "")).split()) not in {"受控", "受控章", "受控文件"}:
                continue
            result[field] = True
            witness(
                {
                    "kind": "pdf_region",
                    "page": 1,
                    "bbox": bounds,
                    "coordinate_system": "pdf_points_unrotated",
                    "source_sha256": digest,
                    "file_version_id": str((audit.file_metadata or {}).get("file_version_id") or audit.audit_id),
                    "scope": "pdf:1",
                    "element": "seal",
                    "element_index": index,
                    "fact": field,
                    "engine": "paddle",
                },
                str(seal.get("recognized_text") or "已定位原件印章"),
                visual=True,
            )
    return result


def _excel_facts(audit, fields, cache, witness, existential_fields):
    from documents.excel.visual import ExcelRegionRenderError, has_region_graphics
    from openpyxl.utils.cell import range_boundaries

    from audit.checker_lib.excel import load_workbook
    from audit.checker_lib.visual import observe_excel_region

    key = ("directory_visual_workbook", audit.audit_id)
    if key not in cache:
        cache[key] = load_workbook(str(audit.file_url))
    workbook = cache[key]
    # The neutral region reader pins one original per cache. Keep that cache
    # local to this file version, even if the caller shares its parent cache.
    region_cache = cache.setdefault(("directory_excel_regions", audit.audit_id, str((audit.file_metadata or {}).get("file_version_id") or audit.audit_id)), {})
    sheets = workbook.worksheets
    if not 1 <= len(sheets) <= _MAX_SCOPES:
        raise VisualFactsUnavailable("工作簿超出十个工作表的视觉读取范围。")
    selected = fields if len(sheets) == 1 else {field: description for field, description in fields.items() if field in existential_fields}
    if not selected:
        return dict.fromkeys(fields)
    readings = []
    original = Path(audit.file_url).read_bytes()
    for sheet in sheets:
        region = sheet.calculate_dimension()
        left, top, right, bottom = range_boundaries(region)
        if (right - left + 1) * (bottom - top + 1) > 50_000:
            raise VisualFactsUnavailable("工作表区域超过有界视觉读取范围。")
        # Native labels bind the rendered image to the actual selected sheet.
        # They are context, not a claim that an image/checkbox is present.
        labels = tuple(dict.fromkeys(str(cell.value).strip()[:160] for row in sheet[region] for cell in row if isinstance(cell.value, str) and cell.value.strip()))[:32]
        if not labels:
            readings.append(dict.fromkeys(selected))
            continue
        # Cell dimensions do not include pictures, drawing shapes or old XLS
        # controls. Clear the print-area crop when their full extent is unknown.
        # The existing renderer prints the selected complete worksheet to one
        # bounded image; its original source and native range remain distinct.
        rendered_region = None if not original.startswith(b"PK") or has_region_graphics(original, sheet_name=sheet.title, cell_range=None) else region
        try:
            readings.append(observe_excel_region(audit, sheet, rendered_region, selected, labels, cache=region_cache, witness=witness, whole_field=True))
        except ExcelRegionRenderError as error:
            if error.code in {"region_coverage_unconfirmed", "field_text_unconfirmed", "sheet_binding_failed", "output_too_large"}:
                raise VisualFactsUnavailable("无法确认工作表的完整原件图像范围。") from error
            raise
        except ValueError as error:
            if str(error) == "Excel field did not render to one page":
                raise VisualFactsUnavailable("工作表未形成受支持的完整单页图像。") from error
            raise
    # Cross-sheet relations and universal predicates need a joint observation
    # plan, which this bounded reader does not implement. Only caller-declared
    # existential facts can combine separate worksheet observations.
    result = dict.fromkeys(fields)
    for field in selected:
        values = [reading.get(field) for reading in readings]
        if field in existential_fields and any(value is True for value in values):
            result[field] = True
        elif values and all(value is not None and value == values[0] for value in values):
            result[field] = values[0]
    return result


def read_original_visual_facts(
    audit,
    fields: dict[str, str],
    witness: Callable[..., Any],
    *,
    cache: dict,
    operation="directory_original_visual_facts",
    existential_fields: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Read original graphics once, retaining failures and their frozen inputs.

    A single source scope supports the declared visual predicates. Across
    multiple pages/sheets, only explicit existential facts can be established;
    per-page agreement cannot establish cross-page relationships or universal
    predicates. Other fields remain unknown without model requests. DOCX image
    objects have no reliable field location, so that carrier is UNABLE.
    """
    from audit.engine.checkers.ocr_visual_facts import observe_ocr_images
    from audit.engine.rule_engine import GuardStop

    if not fields or any(not isinstance(field, str) or not field.strip() or not isinstance(description, str) or not description.strip() for field, description in fields.items()):
        raise VisualFactsUnavailable("没有明确的视觉事实需求，不能按空范围通过。")
    if not existential_fields.issubset(fields):
        raise ValueError("Existential fields must belong to the requested visual facts")
    suffix = Path(audit.file_url).suffix.casefold()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".xlsx", ".xlsm", ".xls"}:
        raise VisualFactsUnavailable("当前载体没有已定位的原件图像证据，需人工核对。")
    digest = _source_identity(audit, cache)
    policy = _policy_identity(audit, cache)
    key = (
        "directory_original_visual",
        audit.audit_id,
        str((audit.file_metadata or {}).get("file_version_id") or audit.audit_id),
        digest,
        policy,
        tuple(sorted(fields.items())),
        tuple(sorted(existential_fields)),
    )
    if key not in cache:
        sources = []

        def collect(*args, **kwargs):
            sources.append((args, kwargs))

        try:
            if suffix in {".xlsx", ".xlsm", ".xls"}:
                facts = _excel_facts(audit, fields, cache, collect, existential_fields)
            else:
                facts = dict.fromkeys(fields)
                selected = fields
                if suffix == ".pdf":
                    with fitz.open(audit.file_url) as document:
                        if document.needs_pass or not 1 <= len(document) <= _MAX_SCOPES:
                            raise VisualFactsUnavailable("原件不能完整读取，或超过十页视觉读取范围。")
                        if len(document) > 1:
                            selected = {field: description for field, description in fields.items() if field in existential_fields}
                    seal_fields = {field: description for field, description in selected.items() if field in _SEAL_FIELDS and field in existential_fields}
                    if seal_fields:
                        facts.update(_paddle_seals(audit, seal_fields, digest, cache, collect))
                missing = {field: description for field, description in selected.items() if facts[field] is None}
                if missing:
                    images, context = _pdf_images(audit, digest) if suffix == ".pdf" else _raster_images(audit, digest)
                    facts.update(observe_ocr_images(images, missing, collect, context_by_scope=context, request_cache=cache, any_fields=existential_fields))
            _source_identity(audit, cache)
            _policy_identity(audit, cache)
            cache[key] = facts, sources
        except GuardStop as error:
            cache[key] = VisualFactsUnavailable(error.message) if error.status == "UNABLE" else error
        except Exception as error:
            cache[key] = error
    value = cache[key]
    if isinstance(value, Exception):
        raise value
    facts, sources = value
    for args, kwargs in sources:
        witness(*args, **kwargs)
    return dict(facts)
