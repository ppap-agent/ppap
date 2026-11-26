"""What the auditor may put on record: quotations read back, searched places were looked at, status follows points."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from auditor import tools
from auditor.verdicts import Task
from openpyxl import Workbook


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = "流程"
    sheet["A1"] = "工序"
    sheet.merge_cells("A2:A3")
    sheet["A2"] = "气密"
    sheet["B2"] = 0
    sheet["B3"] = "检验"
    (tmp_path / "files").mkdir()
    book.save(tmp_path / "files" / "01.xlsx")
    (tmp_path / "files.json").write_text(json.dumps([{"name": "01.xlsx", "path": "files/01.xlsx", "role": "target"}]), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({"rules": [{"id": "PFD:1", "rule_to_check": "…"}]}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUDIT_PHASE", raising=False)
    return tmp_path


def verdict(status: str, *points: dict) -> dict:
    return {"clause": "PFD:1", "status": status, "reason": "依据", "points": list(points)}


def cite(where: str, quote: str) -> dict:
    return {"file": "01.xlsx", "where": where, "quote": quote}


def test_quotation_must_read_back_from_the_cited_place(work: Path) -> None:
    task = Task()
    accepted, problems = task.check(verdict("PASS", {"need": "有气密工序", "result": "met", "evidence": [cite("流程!A2", "气密")]}))
    assert accepted and not problems and accepted["points"][0]["evidence"][0]["verified"] == "text"
    refused, problems = task.check(verdict("PASS", {"need": "有气密工序", "result": "met", "evidence": [cite("流程!B3", "气密")]}))
    assert refused is None and "没有这段引文" in problems[0]


def test_merged_cell_reads_as_its_top_left_and_zero_is_a_value(work: Path) -> None:
    task = Task()
    assert task.original("01.xlsx").text_at("流程!A3") == "气密"
    assert task.original("01.xlsx").text_at("流程!B2") == "0"


def test_unknown_point_cannot_end_as_pass(work: Path) -> None:
    task = Task()
    points = ({"need": "有气密工序", "result": "met", "evidence": [cite("流程!A2", "气密")]}, {"need": "有图例", "result": "unknown", "why": "图例看不清"})
    assert task.check(verdict("PASS", *points))[0] is None
    assert task.check(verdict("NG", *points))[0] is None
    assert task.check(verdict("UNABLE", *points))[0] is not None


def test_absence_needs_a_searched_range_that_was_looked_at(work: Path) -> None:
    absent = verdict("NG", {"need": "有异常处理节点", "result": "unmet", "why": "全表没有", "searched": [{"file": "01.xlsx", "where": "流程!*"}]})
    refused, problems = Task().check(absent)
    assert refused is None and "没有读全" in problems[0]
    assert tools.main(["cells", "01.xlsx", "--sheet", "流程", "--range", "A1:B2"]) == 0
    assert Task().check(absent)[0] is None
    assert tools.main(["cells", "01.xlsx", "--sheet", "流程"]) == 0
    assert Task().check(absent)[0] is not None


def test_another_pass_of_the_work_answers_for_its_own_reading(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert tools.main(["cells", "01.xlsx", "--sheet", "流程"]) == 0
    monkeypatch.setenv("AUDIT_PHASE", "challenge")
    absent = verdict("NG", {"need": "有异常处理节点", "result": "unmet", "why": "全表没有", "searched": [{"file": "01.xlsx", "where": "流程!*"}]})
    assert Task().check(absent)[0] is None


def test_recorded_verdicts_are_read_again_and_the_last_one_stands(work: Path) -> None:
    task = Task()
    good = verdict("PASS", {"need": "有气密工序", "result": "met", "evidence": [cite("流程!A2", "气密")]})
    assert task.record(good) == [("PFD:1", [])]
    assert task.recorded()["PFD:1"]["status"] == "PASS"
    # A line written past the check is not believed.
    forged = {**good, "points": [{"need": "x", "result": "met", "why": "", "searched": [], "evidence": [{**cite("流程!A2", "不存在的字"), "verified": "text"}]}]}
    with (work / ".audit" / "verdicts.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(forged, ensure_ascii=False) + "\n")
    assert "PFD:1" not in task.recorded()
    assert task.refused() == 0


def test_seen_evidence_is_for_pages_and_pictures_only(work: Path) -> None:
    refused, problems = Task().check(verdict("PASS", {"need": "有受控章", "result": "met", "evidence": [{**cite("流程!A2", "受控"), "seen": True}]}))
    assert refused is None and "所见类证据" in problems[0]
