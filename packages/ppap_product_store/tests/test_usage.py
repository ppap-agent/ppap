"""Known ledger examples for the P8 read projection; no provider calls."""

from datetime import datetime, timezone

from ppap_product_store.usage import usage_page

AS_OF = datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc)
FREE = {"free_total": {"value": 700, "reason": None}, "free_remaining": {"value": 0, "reason": None}}
ORG = {"id": "organization-a", "name": "测试企业"}
QUERY = {"range": "7d", "search": "", "page": 1, "page_size": 10}


def fee(round_id="round-a", **changes):
    return {
        "task_id": "task-a",
        "round_id": round_id,
        "file_version_id": "version-a",
        "filename": "Control Plan.xlsx",
        "initiator": "Alice",
        "created_at": "2026-10-31T16:00:00+00:00",
        "consumed_points": 500,
        "is_free_trial": True,
        "ai_item_id": "item-a",
        "external_status": "none",
        "result_attempt": 2,
        **changes,
    }


def test_month_summary_is_independent_of_filtered_page_and_local_midnight():
    rows = [fee(), fee("round-old", created_at="2026-10-31T15:59:59+00:00"), fee("round-earlier", created_at="2026-10-25T15:59:59+00:00")]
    page = usage_page(rows, QUERY, ORG, FREE, {}, AS_OF)
    assert (page["from_date"], page["through_date"], page["total"]) == ("2026-10-26", "2026-11-01", 2)
    assert page["summary"]["month"] == "2026-11"
    assert page["summary"]["consumption_count"]["value"] == 1
    empty = usage_page(rows, {**QUERY, "search": "missing", "page": 2}, ORG, FREE, {}, AS_OF)
    assert empty["entries"] == []
    assert empty["summary"] == page["summary"]
    assert page["summary"]["charged_points"]["value"] is None
    assert page["summary"]["pending_points"]["value"] is None


def test_paid_receipt_is_not_an_order_or_proof_of_debit_and_obligation_survives_retry():
    free = fee(consumed_points=200)
    paid = fee(consumed_points=300, is_free_trial=False, external_status="unknown", external_record_id="receipt-only")
    page = usage_page([free, paid], QUERY, ORG, FREE, {}, AS_OF)
    entry = page["entries"][0]
    assert (page["total"], entry["points"]) == (1, 500)
    assert entry["allocation"] == {"free_points": 200, "paid_points": 300, "reason": None}
    assert entry["orders"] == []
    assert entry["settlement"]["state"] == "unknown"
    assert entry["audit_sequence"] is None
    updated = usage_page([free, {**paid, "result_attempt": 3, "external_status": "confirmed"}], QUERY, ORG, FREE, {}, AS_OF)["entries"][0]
    assert updated["usage_id"] == entry["usage_id"]
    assert updated["orders"] == []
    assert updated["settlement"]["state"] == "unknown"


def test_historical_aggregate_and_incomplete_file_fee_remain_visible_as_unattributed():
    aggregate = fee(file_version_id=None, consumed_points=1200)
    partial = fee("round-incomplete", consumed_points=300)
    page = usage_page([fee(), aggregate, partial], QUERY, ORG, FREE, {}, AS_OF)
    assert page["total"] == 1
    assert page["unattributed"]["slice_count"] == 2
    assert page["unattributed"]["points"] == 1500
    assert page["unattributed"]["reason"]
    assert page["summary"]["consumption_count"]["value"] is None


def test_pagination_and_initiator_search_keep_the_exact_authorized_history_reference():
    rows = [fee(f"round-{index}", file_version_id=f"version-{index}", task_id=f"task-{index}") for index in range(12)]
    history = {("task-1", "version-1"): {"sequence": 3, "document_id": "feishu-document"}}
    page = usage_page(rows, {**QUERY, "search": "aLiCe"}, ORG, FREE, history, AS_OF)
    second = usage_page(rows, {**QUERY, "search": "aLiCe", "page": 2}, ORG, FREE, history, AS_OF)
    assert (page["total"], len(page["entries"]), len(second["entries"])) == (12, 10, 2)
    combined = page["entries"] + second["entries"]
    assert len({row["usage_id"] for row in combined}) == 12
    selected = next(row for row in combined if row["document_id"])
    assert selected["reference"] == {"task_id": "task-1", "round_id": "round-1", "file_version_id": "version-1", "result_attempt": 2}
    assert selected["audit_sequence"] == 3
    assert selected["audit_access"]["state"] == "allowed"
    assert all(row["audit_access"]["state"] == "denied" for row in combined if row != selected)
