"""Rule-driven native/Paddle facts and dedicated OCR Responses observations."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
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


def _read_paddle_visual_facts(
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


_EXISTENCE_FIELDS = frozenset({"seal", "controlled_stamp", "authorization_table_present", "authorization_company_stamp_present", "before", "during", "after"})


_MAX_SUBMISSION_OPTIONS = 12
# "At least one reason" holds when any warrant page shows it; a blank template copy cannot contradict it.
_ANY_PAGE_FIELDS = frozenset({"submission_any_checked", "submission_other_present"})


def _pdf_section(page, starts, stops):
    anchors = [box for label in starts for box in page.search_for(label)]
    if not anchors:
        return page.rect
    # Translations on the same heading form one anchor. Distinct headings on
    # one page retain the whole page rather than dropping a business form.
    if max(box.y0 for box in anchors) - min(box.y0 for box in anchors) > max(box.height for box in anchors) * 2:
        return page.rect
    anchor = fitz.Rect(anchors[0])
    for box in anchors[1:]:
        anchor |= box
    ends = [box.y0 for label in stops for box in page.search_for(label) if box.y0 > anchor.y1]
    if not ends:
        return page.rect
    return fitz.Rect(max(0, anchor.x0 - 12), max(0, anchor.y0 - 8), page.rect.x1, min(ends) - 2)


def pdf_field_region(page, fields):
    requested = set(fields or ())
    primary = page.rect
    if requested and requested.issubset({"submission_any_checked", "submission_other_present"}):
        primary = _pdf_section(page, ("提交原因", "REASON FOR SUBMISSION"), ("提交结果", "SUBMISSION RESULTS", "供方声明", "DECLARATION"))
    elif requested and requested.issubset({"dimensional_checked", "appearance_checked", "material_functional_checked", "statistical_checked", "requirements_yes_checked"}):
        primary = _pdf_section(page, ("提交结果", "SUBMISSION RESULTS"), ("供方声明", "DECLARATION", "解释/说明", "EXPLANATION/COMMENTS", "经授权的供方代表签字"))
    elif requested and requested.issubset({"approval_date", "comments_present"}):
        anchors = page.search_for("认可情况")
        if len(anchors) == 1:
            anchor = anchors[0]
            primary = fitz.Rect(max(0, anchor.x0 - 12), max(0, anchor.y0 - 8), page.rect.x1, page.rect.y1)
    elif requested and requested.issubset({"authorized_signature_present"}):
        anchors = page.search_for("经授权的供方代表签字")
        if anchors:
            anchor = anchors[0]
            dates = [box for box in page.search_for("日期") if box.x0 > anchor.x1 and abs(box.y0 - anchor.y0) < anchor.height * 2]
            right = min((box.x0 for box in dates), default=page.rect.x1)
            primary = fitz.Rect(max(0, anchor.x0 - 4), max(0, anchor.y0 - anchor.height), right, min(page.rect.y1, anchor.y1 + anchor.height * 0.6))
    elif requested and requested.issubset(_DISPOSITION_FIELDS):
        anchors = page.search_for("认可情况")
        if anchors:
            anchor = anchors[0]
            primary = fitz.Rect(max(0, anchor.x0 - 8), max(0, anchor.y0 - anchor.height * 2), page.rect.x1, min(page.rect.y1, anchor.y1 + anchor.height * 2))
    elif requested == {"authorization_company_stamp_present"}:
        anchors = page.search_for("公司名称")
        if anchors:
            anchor = anchors[-1]
            statements = [box.y0 for box in page.search_for("特此声明") if box.y0 < anchor.y0]
            top = max(statements, default=anchor.y0 - anchor.height * 12)
            primary = fitz.Rect(max(0, anchor.x0 - 12), max(0, top), page.rect.x1, page.rect.y1)
    elif requested and requested.issubset({"psw_stamp_present", "psw_stamp_ending_text"}):
        anchors = page.search_for("公司名称")
        if anchors:
            anchor = anchors[-1]
            following = [box.y0 for label in ("认可情况", "仅供理想使用") for box in page.search_for(label) if box.y0 > anchor.y1]
            bottom = min(following, default=page.rect.y1) - 2
            primary = fitz.Rect(max(0, anchor.x0 - 8), max(0, anchor.y0 - 8), page.rect.x1, bottom)
    return primary


def _submission_option_regions(page, section):
    """Each reason's box with its label: forms exported from Excel keep these boxes as tiny images, unreadable in the whole section."""
    boxes: list[fitz.Rect] = []
    for item in page.get_image_info():
        mark = fitz.Rect(item["bbox"])
        if not (4 <= mark.width <= 16 and 4 <= mark.height <= 16 and mark.intersects(section)):
            continue
        if not any(abs(mark.x0 - box.x0) < 3 and abs(mark.y0 - box.y0) < 3 for box in boxes):
            boxes.append(mark)
    return [fitz.Rect(box.x0 - 3, box.y0 - 3, box.x1 + 70, box.y1 + 3) & section for box in sorted(boxes, key=lambda box: (box.x0, box.y0))]


def _pdf_images(audit, pages: Sequence[int], digest: str, fields=None):
    from audit.engine.checkers.ocr_visual_facts import OcrImage

    images = []
    context = {}
    with fitz.open(audit.file_url) as document:
        if document.needs_pass or len(pages) > 10 or any(type(page) is not int or not 1 <= page <= len(document) for page in pages):
            raise VisualFactsUnavailable("原页范围或访问权限尚不能确认。")
        for number in pages:
            page = document[number - 1]
            # Render in the original, unrotated PDF coordinate frame. Only this
            # private in-memory document is changed; source bytes stay frozen.
            page.set_rotation(0)
            requested = set(fields or ())
            primary = pdf_field_region(page, fields)
            context[f"pdf:{number}"] = page.get_text() if primary == page.rect else page.get_textbox(primary)
            regions = [(primary, True)]
            psw_fields = {"psw_stamp_present", "psw_stamp_ending_text", "authorized_signature_present"}
            labels = {}
            if requested.intersection(psw_fields):
                labels["经授权的供方代表签字"] = 70
            if requested.intersection(_DISPOSITION_FIELDS):
                labels["认可情况"] = 20
            if requested.intersection({"approval_date", "comments_present"}):
                labels["认可情况"] = 70
            if requested.intersection({"dimensional_checked", "appearance_checked", "material_functional_checked", "statistical_checked", "requirements_yes_checked"}):
                labels["提交结果"] = 70
            if requested.intersection({"submission_any_checked", "submission_other_present"}):
                labels["提交原因"] = 70
            if any(field.startswith("authorization_") for field in requested):
                labels["部门章授权声明"] = 200
            located_regions = []
            for label, height in labels.items():
                for found in page.search_for(label):
                    margin_above = 25 if label == "经授权的供方代表签字" else 8
                    clip = fitz.Rect(max(0, found.x0 - 12), max(0, found.y0 - margin_above), page.rect.x1, min(page.rect.y1, found.y1 + height))
                    if not clip.is_empty:
                        regions.append((clip, False))
                        located_regions.append(clip)
            for image in page.get_image_info():
                bounds = fitz.Rect(image["bbox"]) & page.rect
                if not 400 <= bounds.width * bounds.height <= page.rect.width * page.rect.height / 5:
                    continue
                # Local source-composite views only improve requested regions.
                # Shape chooses a magnification crop, never a seal verdict.
                if primary != page.rect and not bounds.intersects(primary):
                    continue
                if labels and primary == page.rect and not any(bounds.intersects(region) for region in located_regions):
                    continue
                if requested.intersection(psw_fields) and not 0.8 <= bounds.width / bounds.height <= 1.25:
                    continue
                if requested and not labels and not requested.issubset(_SEAL_FIELDS):
                    continue
                crop = bounds + (-2, -2, 2, 2) & page.rect
                if requested and requested.issubset(_SEAL_FIELDS):
                    # A clipped edge stamp may be only a narrow strip. Preserve
                    # adjacent page context in a square view instead of sending
                    # an extreme aspect ratio to the image reader.
                    side = max(crop.width, crop.height)
                    x = max(0, min((crop.x0 + crop.x1 - side) / 2, page.rect.width - side))
                    y = max(0, min((crop.y0 + crop.y1 - side) / 2, page.rect.height - side))
                    crop = fitz.Rect(x, y, min(page.rect.width, x + side), min(page.rect.height, y + side))
                regions.append((crop, False))
            # Every planned page keeps its full context; local views share a
            # finite total pixel allocation rather than multiplying it.
            page_regions = [(primary, True)] if primary != page.rect else regions[:7]
            if primary != page.rect and requested.issubset(_DISPOSITION_FIELDS):
                # Keep each option and its preceding checkbox together. A long
                # row makes the small marks unreadable after image scaling.
                conditional = page.search_for("条件批准")
                option_regions = []
                for label in ("批准", "条件批准", "拒绝"):
                    matches = [box for box in page.search_for(label) if box.intersects(primary)]
                    if label == "批准":
                        matches = [box for box in matches if not any(box.intersects(other) for other in conditional)]
                    for box in matches:
                        option_regions.append((fitz.Rect(max(primary.x0, box.x0 - box.height * 4), primary.y0, min(primary.x1, box.x1 + box.height), primary.y1), True))
                if len(option_regions) == 3:
                    page_regions = option_regions
            option_views = []
            if primary != page.rect and requested.issubset({"submission_any_checked", "submission_other_present"}):
                option_views = _submission_option_regions(page, primary)[:_MAX_SUBMISSION_OPTIONS]
                page_regions = [*page_regions, *((region, False) for region in option_views)]
            pixel_budget = min(12_000_000, 32_000_000 / len(pages) / len(page_regions))
            for bounds, complete in page_regions:
                area = bounds.width * bounds.height
                if not math.isfinite(area) or area <= 0:
                    raise VisualFactsUnavailable("原页区域尺寸无效。")
                local_scale = 8 if requested and requested.issubset(_SEAL_FIELDS) else 12 if bounds in option_views else 4
                base_scale = 1.5 if complete and primary == page.rect else local_scale
                scale = min(base_scale, math.sqrt(pixel_budget / area), 4096 / max(bounds.width, bounds.height))
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=bounds, alpha=False)
                images.append(
                    OcrImage(
                        pixmap.tobytes("png"),
                        {
                            "kind": "pdf_region",
                            "page": number,
                            "bbox": list(bounds),
                            "coordinate_system": "pdf_points_unrotated",
                            "source_sha256": digest,
                            "file_version_id": audit.audit_id,
                            "scope": f"pdf:{number}",
                            "context": complete,
                            "located_region": True,
                        },
                        pixmap.width,
                        pixmap.height,
                    )
                )
    return images, context


def _source_identity(audit, cache):
    path = Path(audit.file_url)
    if path.suffix.casefold() not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
        raise VisualFactsUnavailable("当前载体尚无已定位的原页图像证据。")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    expected = (audit.file_metadata or {}).get("source_sha256") or (audit.file_metadata or {}).get("sha256")
    key = ("frozen_source", audit.audit_id)
    if (expected and expected != digest) or (key in cache and cache[key] != digest):
        raise ValueError("Frozen PDF identity changed")
    cache[key] = digest
    return digest


def _ocr_pdf_facts(audit, fields, witness, *, pages, cache, field_scopes=None, native_by_scope=None):
    from documents.ocr.env import ocr_policy_identity

    from audit.engine.checkers.ocr_visual_facts import observe_ocr_images
    from audit.engine.rule_engine import GuardStop

    digest = _source_identity(audit, cache)
    policy = ocr_policy_identity()
    identity_key = ("ocr_policy", audit.audit_id)
    if identity_key in cache and cache[identity_key] != policy:
        raise ValueError("OCR configuration changed during the audit")
    cache[identity_key] = policy
    key = ("ocr_visual", digest, tuple(pages), tuple(sorted(fields.items())), tuple((field, tuple(scopes)) for field, scopes in sorted((field_scopes or {}).items())))
    if key not in cache:
        observations = []
        try:
            images, context = _pdf_images(audit, pages, digest, fields)
            facts = observe_ocr_images(
                images,
                fields,
                lambda *args, **kwargs: observations.append((args, kwargs)),
                context_by_scope=context,
                field_scopes=field_scopes,
                any_fields=_EXISTENCE_FIELDS | _ANY_PAGE_FIELDS,
                native_by_scope=native_by_scope,
                request_cache=cache,
            )
            _source_identity(audit, cache)
            cache[key] = (facts, observations)
        except GuardStop as exc:
            cache[key] = VisualFactsUnavailable(exc.message) if exc.status == "UNABLE" else exc
        except Exception as exc:
            cache[key] = exc
    value = cache[key]
    if isinstance(value, Exception):
        raise value
    facts, observations = value
    for args, kwargs in observations:
        witness(*args, **kwargs)
    return dict(facts)


def read_psw_pdf_visual_facts(audit, fields: dict[str, str], witness, *, cache: dict[Any, Any], batch_fields: dict[str, str] | None = None, rule_no: int | None = None) -> dict[str, object]:
    """Plan every applicable PSW/authorization page, then request them once.

    Multiple warrant pages remain separate scopes. No page is selected by
    order, missing identity or a presumed instruction role.
    """
    from audit.engine.checkers.psw_checkbox_evidence import read_disposition_facts
    from audit.pdf_tables import valid_pdf_bbox

    all_fields = batch_fields or fields
    result: dict[str, object] = dict.fromkeys(all_fields)
    digest = _source_identity(audit, cache)
    native_by_scope: dict[str, dict[str, object]] = {}
    with fitz.open(audit.file_url) as document:
        if document.needs_pass:
            raise VisualFactsUnavailable("原件访问权限尚不能确认。")
        warrant = [page.number + 1 for page in document if page.search_for("经授权的供方代表签字") or page.search_for("认可情况")]
        authorization = [page.number + 1 for page in document if page.search_for("部门章授权声明")]
        if not warrant and len(document) == 1:
            warrant = [1]
        if not warrant:
            return {field: None for field in fields}
        # With a located authorization title, inspect those pages. If the
        # rule needs to establish its absence, every page in a small source is
        # an explicit scope; a missing native title cannot prove absence.
        authorization_scopes = authorization
        if not authorization and any(field.startswith("authorization_") for field in all_fields) and len(document) <= 10:
            authorization_scopes = list(range(1, len(document) + 1))
        pages = sorted(set(warrant + authorization_scopes))
        if len(pages) > 10:
            raise VisualFactsUnavailable("本次所需原页超出有界视觉读取范围。")
        scopes = {}
        selected_fields = {}
        for field, description in all_fields.items():
            relevant = authorization_scopes if field.startswith("authorization_") else warrant
            if relevant:
                scopes[field] = [f"pdf:{number}" for number in relevant]
                selected_fields[field] = description
        for number in warrant:
            page = document[number - 1]
            native = read_disposition_facts(
                [item for item in _native_disposition_texts(page) if valid_pdf_bbox(item["pdf_bbox"], page.rect.width, page.rect.height)],
                lambda locator, excerpt, **kwargs: witness({**locator, "file_version_id": audit.audit_id}, excerpt, **kwargs),
                page=number,
                source_sha256=digest,
                visual=False,
            )
            for field in _DISPOSITION_FIELDS.intersection(all_fields):
                native_by_scope.setdefault(field, {})[f"pdf:{number}"] = native[field]
        for field, observations in native_by_scope.items():
            values = list(observations.values())
            if all(value is not None for value in values) and len(set(values)) == 1:
                result[field] = values[0]
                selected_fields.pop(field, None)
                scopes.pop(field, None)
        if authorization and "authorization_table_present" in all_fields:
            result["authorization_table_present"] = True
            for number in authorization:
                for bounds in document[number - 1].search_for("部门章授权声明"):
                    witness(
                        {
                            "kind": "pdf_region",
                            "page": number,
                            "bbox": list(bounds),
                            "coordinate_system": "pdf_points_unrotated",
                            "source_sha256": digest,
                            "file_version_id": audit.audit_id,
                            "element": "authorization_title",
                            "fact": "authorization_table_present",
                        },
                        "部门章授权声明",
                        visual=False,
                    )
            selected_fields.pop("authorization_table_present", None)
            scopes.pop("authorization_table_present", None)
    from audit.engine.checkers.psw_visual_evidence import evaluate_psw_observation

    if all(result[field] is not None for field in fields) or (rule_no is not None and evaluate_psw_observation(rule_no, result).status in {"PASS", "NG"}):
        return {field: result[field] for field in fields}

    def request_stage(stage):
        stage_scopes = {field: scopes[field] for field in stage}
        stage_pages = sorted({int(scope.split(":")[1]) for names in stage_scopes.values() for scope in names})
        return _ocr_pdf_facts(audit, stage, witness, pages=stage_pages, cache=cache, field_scopes=stage_scopes, native_by_scope=native_by_scope)

    result = _psw_stages(result, selected_fields, rule_no, request_stage)
    return {field: result[field] for field in fields}


def _psw_stages(result, selected_fields, rule_no, request_stage):
    from audit.engine.checkers.psw_visual_evidence import PSW_RULE_FIELDS, evaluate_psw_observation, normalize_psw_stamp_facts

    required = PSW_RULE_FIELDS.get(rule_no, selected_fields)
    groups = ({"psw_stamp_present", "psw_stamp_ending_text"}, {"authorized_signature_present"}, _DISPOSITION_FIELDS)
    for group in groups:
        if not set(required).intersection(group):
            continue
        core_fields = {field: description for field, description in selected_fields.items() if field in group}
        if core_fields:
            result.update(request_stage(core_fields))
        result = normalize_psw_stamp_facts(result)
        if rule_no is not None and evaluate_psw_observation(rule_no, result).status in {"PASS", "NG"}:
            return result
    if rule_no is not None and evaluate_psw_observation(rule_no, result).status in {"PASS", "NG"}:
        return result
    needed = set()
    if rule_no == 3 and (result.get("psw_stamp_ends_with_company") is False or (result.get("authorization_table_present") is True and result.get("psw_stamp_present") is True)):
        needed = {"authorization_table_present", "authorization_company_stamp_present"}
    elif rule_no == 7:
        needed = {"submission_any_checked", "submission_other_present"}
    elif rule_no == 8 and result.get("approved_checked") is True and result.get("conditional_checked") is False and result.get("rejected_checked") is False:
        needed = {"dimensional_checked", "appearance_checked", "material_functional_checked", "statistical_checked", "requirements_yes_checked"}
    elif rule_no in {9, 10} and result.get("conditional_checked") is True and result.get("approved_checked") is False and result.get("rejected_checked") is False:
        needed = {"approval_date", "comments_present"}
    branch = {field: selected_fields[field] for field in needed.intersection(selected_fields) if result.get(field) is None}
    if branch:
        result.update(request_stage(branch))
    return result


def _raster_visual_facts(audit, fields, witness, cache):
    from io import BytesIO

    from PIL import Image

    from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images

    digest = _source_identity(audit, cache)
    key = ("ocr_raster", digest, tuple(sorted(fields.items())))
    if key not in cache:
        observations = []
        try:
            with Image.open(audit.file_url) as image:
                if getattr(image, "n_frames", 1) != 1:
                    raise VisualFactsUnavailable("多帧图像尚无明确逐帧范围。")
                width, height = image.size
                if width * height > 12_000_000:
                    raise VisualFactsUnavailable("原始图像像素超出本次有界读取范围。")
                image.load()
                resized = image.convert("RGB")
                resized.thumbnail((4096, 4096))
                if resized.width * resized.height > 12_000_000:
                    factor = math.sqrt(12_000_000 / (resized.width * resized.height))
                    resized = resized.resize((max(1, int(resized.width * factor)), max(1, int(resized.height * factor))))
                output = BytesIO()
                resized.save(output, format="PNG")
            frames = [
                OcrImage(
                    output.getvalue(),
                    {
                        "kind": "image_region",
                        "scope": "image:1",
                        "context": True,
                        "bbox": [0, 0, width, height],
                        "coordinate_system": "image_pixels",
                        "file_version_id": audit.audit_id,
                        "source_sha256": digest,
                    },
                    resized.width,
                    resized.height,
                )
            ]
            facts = observe_ocr_images(frames, fields, lambda *args, **kwargs: observations.append((args, kwargs)), any_fields=_EXISTENCE_FIELDS)
            _source_identity(audit, cache)
            cache[key] = (facts, observations)
        except Exception as exc:
            cache[key] = exc
    value = cache[key]
    if isinstance(value, Exception):
        raise value
    facts, observations = value
    for args, kwargs in observations:
        witness(*args, **kwargs)
    return dict(facts)


def read_original_pdf_visual_facts(
    audit,
    fields: dict[str, str],
    witness,
    *,
    operation="v4_original_visual_facts",
    cache: dict[Any, Any] | None = None,
    source_pages: Sequence[int] | None = None,
    batch_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Native facts first; supported Paddle facts or dedicated OCR observations.

    Callers may supply explicitly planned original page numbers. Otherwise the
    small source is covered as separate page scopes, with no guessed page role.
    """
    from documents.ocr.env import ocr_model_name, ocr_openai_api_key, ocr_openai_base_url

    cache = {} if cache is None else cache
    if Path(audit.file_url).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}:
        if source_pages is not None and list(source_pages) != [1]:
            raise VisualFactsUnavailable("单张图像没有该原页范围。")
        all_fields = batch_fields or fields
        if operation.startswith("psw_rule_"):
            initial = dict.fromkeys(all_fields)
            observed = _psw_stages(initial, all_fields, int(operation.rsplit("_", 1)[-1]), lambda stage: _raster_visual_facts(audit, stage, witness, cache))
        else:
            observed = _raster_visual_facts(audit, all_fields, witness, cache)
        return {field: observed.get(field) for field in fields}
    if operation.startswith("psw_rule_"):
        return read_psw_pdf_visual_facts(audit, fields, witness, cache=cache, batch_fields=batch_fields, rule_no=int(operation.rsplit("_", 1)[-1]))
    digest = _source_identity(audit, cache)
    with fitz.open(audit.file_url) as document:
        if source_pages is not None:
            pages = sorted(set(source_pages))
        elif len(document) <= 10:
            pages = list(range(1, len(document) + 1))
        else:
            return dict.fromkeys(fields)
        if not pages or len(pages) > 10 or any(type(number) is not int or not 1 <= number <= len(document) for number in pages):
            raise VisualFactsUnavailable("原页计划无效或超出有界读取范围。")
        result: dict[str, bool | None] = dict.fromkeys(fields)
        if _DISPOSITION_FIELDS.intersection(fields):
            from audit.engine.checkers.psw_checkbox_evidence import read_disposition_facts
            from audit.pdf_tables import valid_pdf_bbox

            by_page = []
            for number in pages:
                page = document[number - 1]
                native = read_disposition_facts(
                    [item for item in _native_disposition_texts(page) if valid_pdf_bbox(item["pdf_bbox"], page.rect.width, page.rect.height)],
                    lambda locator, excerpt, **kwargs: witness({**locator, "file_version_id": audit.audit_id}, excerpt, **kwargs),
                    page=number,
                    source_sha256=digest,
                    visual=False,
                )
                by_page.append(native)
            for field in _DISPOSITION_FIELDS.intersection(fields):
                values = [value[field] for value in by_page]
                if all(value is not None for value in values) and len(set(values)) == 1:
                    result[field] = values[0]
    unresolved = {field: description for field, description in fields.items() if result[field] is None}
    if not unresolved:
        return result
    # Signatures, checkboxes, photos and graphic semantics go directly to the
    # dedicated OCR model. A seal-only request may benefit from Paddle first.
    if set(unresolved).issubset(_SEAL_FIELDS):
        try:
            paddle = _read_paddle_visual_facts(audit, unresolved, witness, operation=operation, cache=cache)
            result.update({field: value for field, value in paddle.items() if value is not None})
        except Exception:
            # This is a configured alternative evidence capability, not a
            # replay of an uncertain Paddle request. Its failure stays cached.
            try:
                configured = bool(ocr_model_name() and ocr_openai_api_key() and ocr_openai_base_url())
            except ValueError:
                configured = False
            if not configured:
                raise
    unresolved = {field: description for field, description in fields.items() if result[field] is None}
    if unresolved:
        observed = _ocr_pdf_facts(audit, unresolved, witness, pages=pages, cache=cache)
        result.update({field: value if type(value) is bool else None for field, value in observed.items()})
    return result
