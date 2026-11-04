"""Keep seal detection distinct from seal OCR and signature certification."""

from __future__ import annotations

from .client import PaddleOCRFormatError
from .layout import blocks, box, diagnostic, iou


def _recognition_bbox(recognition: dict) -> list[float] | None:
    for key in ("seal_bbox", "bbox", "coordinate"):
        if key in recognition:
            return box(recognition[key])
    return None


def seal_evidence(pruned: dict, regions: list[dict], page_no: int, *, diagnostics: list[dict] | None = None) -> list[dict]:
    detected = [region for region in regions if region["label"] == "seal"]
    recognized: list[dict] = []
    try:
        records = blocks(pruned, "seal_res_list")
    except PaddleOCRFormatError as exc:
        diagnostic(diagnostics, element="seal", page=page_no, index=None, message=str(exc))
        records = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            diagnostic(diagnostics, element="seal", page=page_no, index=index, message="PaddleOCR seal block is invalid")
            continue
        texts = item.get("rec_texts", [])
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            diagnostic(diagnostics, element="seal", page=page_no, index=index, message="PaddleOCR seal text is invalid")
            continue
        geometry_invalid = False
        try:
            bounds = _recognition_bbox(item)
        except PaddleOCRFormatError as exc:
            diagnostic(diagnostics, element="seal", page=page_no, index=index, message=str(exc))
            bounds = None
            geometry_invalid = True
        recognized.append({"index": index, "texts": [text for text in texts if text.strip()], "bbox": bounds, "geometry_invalid": geometry_invalid})

    linked: dict[int, int] = {}
    used_recognitions: set[int] = set()
    # Only match when geometry gives a unique, positive-overlap correspondence.
    for region in detected:
        if region["bbox"] is None:
            continue
        matches = [(rec["index"], iou(region["bbox"], rec["bbox"])) for rec in recognized if rec["bbox"] is not None]
        positive = [(index, score) for index, score in matches if score > 0]
        if len(positive) == 1:
            index = positive[0][0]
            rec = next(item for item in recognized if item["index"] == index)
            overlapping_regions = [item for item in detected if item["bbox"] is not None and iou(item["bbox"], rec["bbox"]) > 0]
            if len(overlapping_regions) == 1 and index not in used_recognitions:
                linked[region["index"]] = index
                used_recognitions.add(index)
    # Historical results often had one seal OCR record without a bbox.
    if len(detected) == 1 and len(records) == 1 and len(recognized) == 1 and not linked and recognized[0]["bbox"] is None and not recognized[0]["geometry_invalid"]:
        linked[detected[0]["index"]] = recognized[0]["index"]
        used_recognitions.add(recognized[0]["index"])

    result: list[dict] = []
    for region in detected:
        recognition_index = linked.get(region["index"])
        recognition = next((item for item in recognized if item["index"] == recognition_index), None)
        text = recognition["texts"] if recognition else []
        result.append(
            {
                "page": page_no,
                "detected": True,
                "bbox": region["bbox"],
                "text_status": "recognized" if text else "unreadable",
                "recognized_text": text,
                "source": "paddle_layout",
                "region_index": region["index"],
                "recognition_index": recognition_index,
            }
        )
    for item in recognized:
        if item["index"] in used_recognitions:
            continue
        unresolved = bool(detected)
        if unresolved:
            diagnostic(diagnostics, element="seal", page=page_no, index=item["index"], message="PaddleOCR seal text cannot be uniquely bound to a detected region", outcome="unknown")
        result.append(
            {
                "page": page_no,
                "detected": None if unresolved else True,
                "bbox": item["bbox"],
                "text_status": "recognized" if item["texts"] else "unreadable",
                "recognized_text": item["texts"],
                "source": "paddle_seal_recognition",
                "region_index": None,
                "recognition_index": item["index"],
                "association": "unresolved" if unresolved else "recognition_only",
            }
        )
    return result


def link_region_words(seals: list[dict], texts: list[dict], page_no: int, diagnostics: list[dict]) -> None:
    """Link located partial words to a unique region, never certify stamp ink."""
    for seal in seals:
        seal["supplemental_text_indices"] = []
    located = [seal for seal in seals if seal.get("detected") is True and seal.get("source") == "paddle_layout" and seal.get("pdf_bbox")]
    for word in texts:
        if word.get("source") != "paddle_overall_ocr" or not word.get("pdf_bbox"):
            continue
        bounds = word["pdf_bbox"]
        owners = [seal for seal in located if seal["pdf_bbox"][0] <= bounds[0] and seal["pdf_bbox"][1] <= bounds[1] and bounds[2] <= seal["pdf_bbox"][2] and bounds[3] <= seal["pdf_bbox"][3]]
        if len(owners) == 1:
            owners[0]["supplemental_text_indices"].append(word["index"])
        elif len(owners) > 1:
            diagnostic(diagnostics, element="seal", page=page_no, index=word["index"], message="PaddleOCR overall word is contained in multiple seal regions", outcome="unknown")
