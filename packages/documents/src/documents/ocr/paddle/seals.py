"""Keep seal detection distinct from seal OCR and signature certification."""

from __future__ import annotations

from .client import PaddleOCRFormatError
from .layout import blocks, box, iou


def _recognition_bbox(recognition: dict) -> list[float] | None:
    for key in ("seal_bbox", "bbox", "coordinate"):
        if key in recognition:
            return box(recognition[key])
    return None


def seal_evidence(pruned: dict, regions: list[dict], page_no: int) -> list[dict]:
    detected = [region for region in regions if region["label"] == "seal"]
    recognized: list[dict] = []
    for index, item in enumerate(blocks(pruned, "seal_res_list")):
        if not isinstance(item, dict):
            raise PaddleOCRFormatError("PaddleOCR seal block is invalid")
        texts = item.get("rec_texts", [])
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise PaddleOCRFormatError("PaddleOCR seal text is invalid")
        recognized.append({"index": index, "texts": [text for text in texts if text.strip()], "bbox": _recognition_bbox(item)})

    linked: dict[int, int] = {}
    used_recognitions: set[int] = set()
    # Only match when geometry gives a unique, positive-overlap correspondence.
    for region in detected:
        if region["bbox"] is None:
            continue
        matches = [(rec["index"], iou(region["bbox"], rec["bbox"])) for rec in recognized if rec["bbox"] is not None]
        positive = sorted([(index, score) for index, score in matches if score > 0], key=lambda pair: pair[1], reverse=True)
        if len(positive) == 1 or (len(positive) > 1 and positive[0][1] > positive[1][1] * 1.2):
            index = positive[0][0]
            if index not in used_recognitions:
                linked[region["index"]] = index
                used_recognitions.add(index)
    # Historical results often had one seal OCR record without a bbox.
    if len(detected) == 1 and len(recognized) == 1 and not linked and recognized[0]["bbox"] is None:
        linked[detected[0]["index"]] = recognized[0]["index"]
        used_recognitions.add(recognized[0]["index"])

    result: list[dict] = []
    for region in detected:
        index = linked.get(region["index"])
        recognition = next((item for item in recognized if item["index"] == index), None)
        text = recognition["texts"] if recognition else []
        result.append({
            "page": page_no,
            "detected": True,
            "bbox": region["bbox"],
            "text_status": "recognized" if text else "unreadable",
            "recognized_text": text,
            "source": "paddle_layout",
            "region_index": region["index"],
            "recognition_index": index,
        })
    for item in recognized:
        if item["index"] in used_recognitions:
            continue
        result.append({
            "page": page_no,
            "detected": True,
            "bbox": item["bbox"],
            "text_status": "recognized" if item["texts"] else "unreadable",
            "recognized_text": item["texts"],
            "source": "paddle_seal_recognition",
            "region_index": None,
            "recognition_index": item["index"],
        })
    return result
