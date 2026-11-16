from urllib.parse import parse_qs, urlsplit

import pytest
from product.meego import WorkItemRef
from product.meego_writeback import marker, summary, task_link, writeback_state


def audited(name: str, conclusion: str | None, execution: str = "completed", **counts) -> dict:
    full = {"pass_count": 0, "ng_count": 0, "unknown_count": 0, "not_covered_count": 0, "error_count": 0, **counts}
    return {
        "file": {"original_filename": name},
        "selection": "selected",
        "match": {"state": "matched"},
        "audit": {"execution": execution, "conclusion": conclusion, "counts": full},
    }


def task(*files: dict, **extra) -> dict:
    return {"id": "t1", "round_id": "r1", "state": "completed", "execution": "completed", "has_execution_error": False, "round_started_at": "2026-11-16T07:30:00Z", "files": list(files), **extra}


REFERENCE = {"file": {"original_filename": "图纸.pdf"}, "selection": "selected", "match": {"state": "reference"}, "reference_only": True, "audit": None}
REMOVED = {"file": {"original_filename": "旧版.xlsx"}, "selection": "removed", "match": None, "audit": None}


def test_comment_states_the_web_conclusions_and_counts():
    lines = summary(task(audited("PFMEA.xlsx", "NG", ng_count=2, unknown_count=1), audited("流程图.xlsx", "PASS", pass_count=1044), REFERENCE, REMOVED), "https://ppap.example")
    assert lines[0] == "PPAP 审核结果（PPAP 审核轮次 r1）"
    assert lines[1] == "审核时间：2026 年 11 月 16 日 07:30"
    assert lines[2:5] == ["PFMEA.xlsx：不符合", "流程图.xlsx：符合", "图纸.pdf：作为参考资料，未单独审核"]
    assert lines[5] == "总体：不符合；不符合 2 项，尚未确定 1 项"
    assert all("旧版" not in line for line in lines)


def test_unknown_is_never_reported_as_passed():
    lines = summary(task(audited("A.xlsx", "PASS", pass_count=3), audited("B.xlsx", "UNKNOWN", unknown_count=5)), "https://ppap.example")
    assert "B.xlsx：尚有未确定项" in lines
    assert "总体：尚有未确定项；不符合 0 项，尚未确定 5 项" in lines
    assert summary(task(audited("A.xlsx", "PASS", pass_count=3)), "https://ppap.example")[-2] == "总体：符合；不符合 0 项，尚未确定 0 项"


@pytest.mark.parametrize(
    ("body", "state"),
    [
        (task(audited("A.xlsx", None, "running")), "round_incomplete"),
        (task(audited("A.xlsx", "PASS"), state="reviewing", execution="running"), "round_incomplete"),
        (task(audited("A.xlsx", "PASS"), round_id=None), "round_incomplete"),
        (task(audited("A.xlsx", None)), "round_incomplete"),
        (task(audited("A.xlsx", "SOMETHING_NEW")), "round_incomplete"),
        (task(REFERENCE), "round_incomplete"),
        (task({**REFERENCE, "reference_only": False, "match": {"state": "matched"}}), "round_incomplete"),
        (task(audited("A.xlsx", None, "review_failed")), "round_has_errors"),
        (task(audited("A.xlsx", "PASS"), execution="failed"), "round_has_errors"),
        (task(audited("A.xlsx", "PASS"), has_execution_error=True), "round_has_errors"),
    ],
)
def test_unfinished_failed_or_unknown_tasks_are_not_summarized(body, state):
    assert writeback_state(body) == state
    with pytest.raises(ValueError, match=state):
        summary(body, "https://ppap.example")


def test_links_enter_the_current_edition_and_markers_name_the_round():
    parts = urlsplit(task_link("https://ppap.example/", "t1"))
    assert parts.path == "/enter-current"
    assert parse_qs(parts.query)["return_to"] == ["https://ppap.example/tasks/t1"]
    assert "r1" in marker("r1")


def test_work_item_references_reject_paths():
    assert WorkItemRef.parse("proj_1/story/42").work_item_id == "42"
    for bad in ("proj/story", "proj/story/abc", "../x/story/1", "proj/sto ry/1"):
        with pytest.raises(ValueError):
            WorkItemRef.parse(bad)
