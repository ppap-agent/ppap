from typing import Any


def build_layout_structure(raw_data: dict[str, Any]) -> list[dict[str, Any] | list[dict[str, Any]]]:
    """Collect table/seal boxes and merge cross-page leading tables."""
    results = raw_data.get("layoutParsingResults", [])
    pages: list[list[dict[str, Any]]] = []

    for page_idx, page in enumerate(results):
        boxes = page.get("prunedResult", {}).get("layout_det_res", {}).get("boxes", [])
        filtered: list[dict[str, Any]] = []
        for box in boxes:
            label = box.get("label")
            if label not in {"table", "seal"}:
                continue
            copied = dict(box)
            copied["page_idx"] = page_idx
            copied["children"] = []
            filtered.append(copied)

        filtered.sort(key=lambda item: item["coordinate"][1])
        pages.append(filtered)

    for idx in range(len(pages) - 1, 0, -1):
        cur = pages[idx]
        prev = pages[idx - 1]
        if not cur or not prev:
            continue
        if cur[0].get("label") != "table" or prev[-1].get("label") != "table":
            continue

        popped = cur.pop(0)
        prev[-1]["children"].append(popped)
        if popped["children"]:
            prev[-1]["children"].extend(popped["children"])
            popped["children"] = []

    items: list[dict[str, Any] | list[dict[str, Any]]] = []
    for page in pages:
        for box in page:
            if box["children"]:
                items.append([box, *box["children"]])
            else:
                items.append(box)
    return items
