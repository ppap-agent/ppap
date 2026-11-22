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


def test_customer_groups_nest_the_vehicle_tree_and_search_reads_part_numbers():
    documents = [document(f"d{index}", None, None) for index in range(4)]
    for row, (customer, model, part, number) in zip(documents, [("理想汽车", "L7", "尾门", "P01"), ("理想汽车", "L7", None, None), ("蔚来汽车", "ES6", "侧围", "P02"), (None, "L7", "尾门", None)]):
        row["metadata"] = {"customer": customer, "model": model, "part": part, "part_number": number}
        row["latest_activity"] = {"initiator": "Alice", "audited_at": row["updated_at"]}
    page = archive_page(documents, QUERY)
    groups = {group["name"]: group for group in page["customer_groups"]}
    # A document without a customer stays in the whole tree only.
    assert [(group["name"], group["count"]) for group in page["customer_groups"]] == [("理想汽车", 2), ("蔚来汽车", 1)]
    assert [(row["label"], row["count"], [(part["label"], part["count"]) for part in row["parts"]]) for row in groups["理想汽车"]["categories"]] == [("L7", 2, [("其他", 1), ("尾门", 1)])]
    assert page["categories"][1]["count"] == 3
    # A customer plus one of its vehicle ids narrows to that customer's documents.
    l7 = groups["理想汽车"]["categories"][0]["id"]
    assert [row["document_id"] for row in archive_page(documents, {**QUERY, "customer": "理想汽车", "category_id": l7})["documents"]] == ["d1", "d0"]
    assert [row["document_id"] for row in archive_page(documents, {**QUERY, "search": "p02"})["documents"]] == ["d2"]
    assert [row["document_id"] for row in archive_page(documents, {**QUERY, "search": "尾门"})["documents"]] == ["d3", "d0"]


def test_customer_tree_has_one_node_per_customer_a_file_writes_as_written_never_a_rule_set():
    names = ["UAES", "联合汽车电子有限公司", " UAES ", "UAES（联电）", "理想", "长城汽车"]
    documents = [document(f"d{index}", None, None) for index in range(len(names))]
    for row, name in zip(documents, names, strict=True):
        row["metadata"] = {"customer": name, "model": None, "part": None}
        row["latest_activity"] = {"initiator": "Alice", "audited_at": row["updated_at"]}
    page = archive_page(documents, QUERY)
    # The rule set names (联电科技/UAES, 理想/Li Auto) never stand in for what a file writes; nothing is merged but surrounding spaces.
    assert [(group["name"], group["count"]) for group in page["customer_groups"]] == [("UAES", 2), ("UAES（联电）", 1), ("理想", 1), ("联合汽车电子有限公司", 1), ("长城汽车", 1)]
    assert [row["metadata"]["customer"] for row in documents] == names
    assert {row["document_id"] for row in archive_page(documents, {**QUERY, "customer": "UAES"})["documents"]} == {"d0", "d2"}
    assert [row["document_id"] for row in archive_page(documents, {**QUERY, "customer": "联合汽车电子有限公司"})["documents"]] == ["d1"]
