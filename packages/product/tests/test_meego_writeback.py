from urllib.parse import parse_qs, urlsplit

import pytest
from audit.audits import NA, NG, PASS, UNABLE
from product.meego import WorkItemRef
from product.meego_writeback import file_conclusion, marker, summary, task_link


def test_unknown_or_empty_results_are_never_reported_as_passed():
    assert file_conclusion([{"status": PASS}, {"status": NA}]) == "符合"
    assert file_conclusion([{"status": PASS}, {"status": UNABLE}]) == "尚有未确定项"
    assert file_conclusion([{"status": PASS}, {"status": NG}, {"status": UNABLE}]) == "不符合"
    assert file_conclusion([]) == "尚有未确定项"
    assert file_conclusion([{"status": NA}]) == "不适用"
    assert file_conclusion([{"status": "SOMETHING_NEW"}]) == "尚有未确定项"


def test_unfinished_rounds_are_not_summarized():
    with pytest.raises(ValueError, match="round_incomplete"):
        summary({"id": "r1", "execution": "running"}, "https://ppap.example")


def test_links_enter_the_current_edition_and_markers_name_the_round():
    link = task_link("https://ppap.example/", {"id": "r1", "source_draft_id": "t1"})
    parts = urlsplit(link)
    assert parts.path == "/enter-current"
    assert parse_qs(parts.query)["return_to"] == ["https://ppap.example/tasks/t1"]
    assert "r1" in marker("r1")


def test_work_item_references_reject_paths():
    assert WorkItemRef.parse("proj_1/story/42").work_item_id == "42"
    for bad in ("proj/story", "proj/story/abc", "../x/story/1", "proj/sto ry/1"):
        with pytest.raises(ValueError):
            WorkItemRef.parse(bad)
