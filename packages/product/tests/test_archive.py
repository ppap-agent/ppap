"""Archive result filter over already-authorized rows; no database."""

from product.store.archive import archive_page, archive_result_states

QUERY = {"search": "", "category_id": None, "document_type": None, "customer": None, "initiator": None, "from_date": None, "through_date": None, "result": None, "page": 1, "page_size": 10}


def entry(document_id, execution="completed", conclusion="PASS"):
    return {"document_id": document_id, "initiator": "Alice", "audited_at": "2026-11-01T00:00:00+00:00", "execution": execution, "conclusion": conclusion}


def document(document_id, completed, activity, updated_at="2026-11-01T00:00:00+00:00"):
    metadata = {"customer": "客户", "model": None, "part": None}
    return {
        "document_id": document_id,
        "title": f"{document_id}.xlsx",
        "metadata": metadata,
        "document_type": None,
        "updated_at": updated_at,
        "latest_completed": completed,
        "latest_activity": activity,
    }


def test_result_states_follow_what_the_row_shows():
    assert archive_result_states(document("a", entry("a"), entry("a"))) == {"PASS"}
    assert archive_result_states(document("b", entry("b", conclusion="NG"), entry("b", "running", None))) == {"NG", "active"}
    # A completed audit with undecided clauses shows as such; a failed one leaves no completed result. Neither becomes PASS.
    assert archive_result_states(document("u", entry("u", conclusion="UNKNOWN"), entry("u"))) == {"UNKNOWN"}
    assert archive_result_states(document("c", None, entry("c", "review_failed", "UNKNOWN"))) == {"none"}
    assert archive_result_states(document("d", None, entry("d", "queued", None))) == {"none", "active"}


def test_result_filter_pages_over_all_matches_and_keeps_facets():
    documents = [document(f"pass-{index:02d}", None, None, f"2026-11-01T00:{index:02d}:00+00:00") for index in range(12)] + [document("ng", None, None)]
    for row in documents:
        row["latest_activity"] = {"initiator": "Alice", "audited_at": row["updated_at"]}
    results = {row["document_id"]: {"NG"} if row["document_id"] == "ng" else {"PASS"} for row in documents}
    first = archive_page(documents, {**QUERY, "result": "PASS"}, results)
    assert (first["total"], len(first["documents"]), first["document_count"]) == (12, 10, 13)
    second = archive_page(documents, {**QUERY, "result": "PASS", "page": 2}, results)
    assert [row["document_id"] for row in second["documents"]] == ["pass-01", "pass-00"]
    assert [row["document_id"] for row in archive_page(documents, {**QUERY, "result": "NG"}, results)["documents"]] == ["ng"]
    # A document whose result could not be projected is not counted as any result.
    assert archive_page(documents, {**QUERY, "result": "PASS"}, {})["total"] == 0
    assert archive_page(documents, QUERY)["total"] == 13
