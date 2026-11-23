from urllib.parse import parse_qs, urlsplit

import pytest
from product.meego import WorkItemRef
from product.meego_writeback import marker, report_names, summary, task_link, writeback_state
from product.store.task_view import overall


def audited(name: str, conclusion: str | None, execution: str = "completed", *, confirmed: str | None = None, **counts) -> dict:
    """One audited file as the task view states it: machine counts, clause outcomes, and the confirmed conclusion."""
    full = {"pass_count": 0, "ng_count": 0, "unknown_count": 0, "not_covered_count": 0, "error_count": 0, **counts}
    outcomes = ["PASS"] * full["pass_count"] + ["NG"] * full["ng_count"] + ["UNABLE"] * full["unknown_count"]
    if confirmed == "PASS":
        # Confirmed original facts settled every clause the machine could not decide.
        outcomes = ["PASS" if outcome == "UNABLE" else outcome for outcome in outcomes]
    return {
        "file": {"original_filename": name},
        "selection": "selected",
        "match": {"state": "matched"},
        "audit": {"execution": execution, "conclusion": conclusion, "confirmed_conclusion": confirmed or conclusion, "counts": full, "checks": [{"outcome": outcome} for outcome in outcomes]},
    }


def task(*files: dict, **extra) -> dict:
    audits = [row["audit"] for row in files if row.get("audit")]
    standing = {"conclusion": overall([audit["confirmed_conclusion"] for audit in audits]), "machine_conclusion": overall([audit["conclusion"] for audit in audits]), "objection": False}
    return {
        "id": "t1",
        "round_id": "r1",
        "state": "completed",
        "execution": "completed",
        "has_execution_error": False,
        "round_started_at": "2026-11-16T07:30:00Z",
        "files": list(files),
        **standing,
        **extra,
    }


REFERENCE = {"file": {"original_filename": "图纸.pdf"}, "selection": "selected", "match": {"state": "reference"}, "reference_only": True, "audit": None}
REMOVED = {"file": {"original_filename": "旧版.xlsx"}, "selection": "removed", "match": None, "audit": None}


def test_comment_states_the_web_conclusions_and_counts():
    lines = summary(task(audited("PFMEA.xlsx", "NG", ng_count=2, unknown_count=1), audited("流程图.xlsx", "PASS", pass_count=1044), REFERENCE, REMOVED), "https://ppap.example")
    assert lines[0] == "PPAP 审核结果（PPAP 审核轮次 r1）"
    # 07:30 UTC is read in China as Beijing time.
    assert lines[1] == "审核时间：2026 年 11 月 16 日 15:30（北京时间）"
    assert lines[2:5] == ["PFMEA.xlsx：不符合（另 1 项待审核）", "流程图.xlsx：符合", "图纸.pdf：作为参考资料，未单独审核"]
    assert lines[5] == "总体：不符合；不符合 2 项，尚未确定 1 项"
    assert all("旧版" not in line for line in lines)


def test_unknown_is_never_reported_as_passed():
    lines = summary(task(audited("A.xlsx", "PASS", pass_count=3), audited("B.xlsx", "UNKNOWN", unknown_count=5)), "https://ppap.example")
    assert "B.xlsx：待审核" in lines
    # A file label aggregated over its reviewed clauses names the 待审核 ones beside it.
    assert "C.xlsx：符合（另 2 项待审核）" in summary(task(audited("C.xlsx", "PASS", pass_count=3, unknown_count=2)), "https://ppap.example")
    assert "总体：待审核；不符合 0 项，尚未确定 5 项" in lines
    assert summary(task(audited("A.xlsx", "PASS", pass_count=3)), "https://ppap.example")[-2] == "总体：符合；不符合 0 项，尚未确定 0 项"


def test_confirmed_original_facts_carry_the_comment_and_say_so():
    lines = summary(task(audited("PFMEA.xlsx", "UNKNOWN", confirmed="PASS", pass_count=3, unknown_count=2)), "https://ppap.example")
    assert "PFMEA.xlsx：符合（依据人工确认的原件事实）" in lines
    assert "总体：符合；不符合 0 项，尚未确定 0 项" in lines
    # Without confirmation the same file stays open.
    assert "PFMEA.xlsx：待审核" in summary(task(audited("PFMEA.xlsx", "UNKNOWN", pass_count=3, unknown_count=2)), "https://ppap.example")


def test_a_round_conclusion_never_turns_missing_or_unknown_into_pass():
    assert overall(["PASS", None]) == "UNKNOWN"
    assert overall([]) == "UNKNOWN"
    assert overall(["PASS", "UNKNOWN"]) == "UNKNOWN"
    assert overall(["UNKNOWN", "NG"]) == "NG"
    assert overall(["PASS", "PASS"]) == "PASS"


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


def test_report_names_tell_same_named_files_of_one_round_apart_and_never_drift():
    names = report_names(["PFMEA.xlsx", "控制计划.xlsx", "pfmea.xls", "PFMEA.xlsx"], "2026-11-16T07:30:00Z")
    # The first keeps the plain name, so a report already attached under it is still recognised.
    assert names == [
        "PPAP 审核报告 · PFMEA · 2026-11-16 1530.pdf",
        "PPAP 审核报告 · 控制计划 · 2026-11-16 1530.pdf",
        "PPAP 审核报告 · pfmea · 第 2 份 · 2026-11-16 1530.pdf",
        "PPAP 审核报告 · PFMEA · 第 3 份 · 2026-11-16 1530.pdf",
    ]
    assert len(set(names)) == len(names)
    # A name decides whether a report was already attached, so a round without its start time has none.
    for start in (None, ""):
        with pytest.raises(ValueError):
            report_names(["PFMEA.xlsx"], start)


def test_a_work_item_opens_at_its_feishu_project_address_by_space_key():
    from types import SimpleNamespace

    from product.meego import work_item, work_item_url

    settings = SimpleNamespace(meego_base_url="https://project.feishu.cn/")
    assert work_item_url(settings, work_item("6afa8c695111c30e59b3bd22", "story", "7170181153")) == "https://project.feishu.cn/6afa8c695111c30e59b3bd22/story/detail/7170181153"


def test_an_unknown_conclusion_finds_the_new_option_or_a_space_s_older_option_name():
    from product.meego_writeback import conclusion_option

    assert conclusion_option({"符合": "a", "不符合": "b", "待审核": "c"}, "UNKNOWN") == ("待审核", "c")
    assert conclusion_option({"符合": "a", "不符合": "b", "尚有未确定项": "old"}, "UNKNOWN") == ("尚有未确定项", "old")
    assert conclusion_option({"尚有未确定项": "old"}, "NG") is None
