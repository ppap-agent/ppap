"""Read-time suggestions from confirmed reason categories and original fields."""

from product.feedback_groups import group_feedback, source_field


def feedback(id, *, suite="cp", clause="R1", status="PASS", column="B", category="missing_value"):
    return {
        "id": id,
        "suite_key": suite,
        "rule_clause_id": clause,
        "original_status": status,
        "reason_category": category,
        "file_version_id": id,
        "expected_status": "NG",
        "evidence": [{"file_version_id": id, "locator": {"kind": "xlsx_cell", "sheet": "Sheet", "cell": column + id[-1]}}],
    }


def test_same_confirmed_column_category_forms_read_time_suggestions():
    rows = [feedback("file1"), feedback("file2"), feedback("file3", clause="R2"), feedback("file4", suite="pfmea"), feedback("file5", column="C"), feedback("file6", category="incorrect_reading")]
    groups = group_feedback(rows, tenant_id="tenant-a")
    assert len(groups) == 1
    assert groups[0]["feedback_ids"] == ["file1", "file2"]
    assert groups[0]["source_field"] == "Sheet · B"
    assert groups[0]["id"] != group_feedback(rows, tenant_id="tenant-b")[0]["id"]
    assert rows[0]["expected_status"] == rows[1]["expected_status"] == "NG"
    assert source_field({"kind": "docx_cell", "table": 1, "row": 4, "column": 2}) == ("docx_cell", 1, 2)
