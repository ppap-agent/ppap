"""Read-only archive projections over authorized, frozen task results."""

import json


def category_key(model, part=None):
    return json.dumps([model or "其他"] + ([part or "其他"] if part is not None else []), ensure_ascii=False)


# What the archive row shows: the latest decided conclusion, else none yet; an audit in flight is shown beside either.
ARCHIVE_RESULTS = frozenset({"PASS", "NG", "UNKNOWN", "none", "active"})


def archive_result_states(document: dict) -> set[str]:
    completed, activity = document["latest_completed"], document["latest_activity"]
    states = {completed["conclusion"]} if completed else {"none"}
    if activity and activity["execution"] in {"queued", "running"}:
        states.add("active")
    return states


def archive_matches(document: dict, query: dict) -> bool:
    """Every filter except the result, which needs the frozen round projection."""
    metadata, entry = document["metadata"], document["latest_completed"] or document["latest_activity"]
    day = entry["audited_at"][:10]
    return (
        query["search"].casefold() in document["title"].casefold()
        and (not query["category_id"] or query["category_id"] in {category_key(metadata.get("model")), category_key(metadata.get("model"), metadata.get("part") or "其他")})
        and (not query["document_type"] or query["document_type"] == document["document_type"])
        and (not query["customer"] or query["customer"] == metadata.get("customer"))
        and (not query["initiator"] or query["initiator"] == entry["initiator"])
        and (not query["from_date"] or day >= query["from_date"])
        and (not query["through_date"] or day <= query["through_date"])
    )


def archive_page(documents: list[dict], query: dict, results: dict[str, set[str]] | None = None) -> dict:
    """`results` maps document ids to their result states and is required when the query filters by result."""
    categories: dict[str, dict] = {}
    for document in documents:
        metadata = document["metadata"]
        model, part = metadata.get("model") or "其他", metadata.get("part") or "其他"
        category = categories.setdefault(model, {"id": category_key(model), "label": model, "count": 0, "parts": {}})
        category["count"] += 1
        child = category["parts"].setdefault(part, {"id": category_key(model, part), "label": part, "count": 0})
        child["count"] += 1

    def matches(document):
        return archive_matches(document, query) and (not query.get("result") or query["result"] in (results or {}).get(document["document_id"], set()))

    filtered = sorted(filter(matches, documents), key=lambda row: (row["updated_at"], row["document_id"]), reverse=True)
    start = (query["page"] - 1) * 10
    return {
        "query": query,
        "documents": filtered[start : start + 10],
        "total": len(filtered),
        "document_count": len(documents),
        "categories": [{**category, "parts": sorted(category["parts"].values(), key=lambda row: row["label"])} for _, category in sorted(categories.items())],
        "document_types": sorted({row["document_type"] for row in documents if row["document_type"]}),
        "customers": sorted({row["metadata"]["customer"] for row in documents if row["metadata"].get("customer")}),
        "initiators": sorted({row["latest_activity"]["initiator"] for row in documents}),
    }
