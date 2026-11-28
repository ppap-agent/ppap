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


def test_what_was_seen_on_a_page_with_text_is_still_on_record_when_read_again(work: Path) -> None:
    import fitz

    document = fitz.open()
    document.new_page().insert_text((72, 72), "Control Plan")
    document.save(work / "files" / "02.pdf")
    (work / "files.json").write_text(json.dumps([{"name": "01.xlsx", "path": "files/01.xlsx"}, {"name": "02.pdf", "path": "files/02.pdf"}]), encoding="utf-8")
    task = Task()
    stamp = {"file": "02.pdf", "where": "page:1", "quote": "右上角红色受控章", "seen": True}
    assert task.record(verdict("PASS", {"need": "有受控章", "result": "met", "evidence": [stamp]})) == [("PFD:1", [])]
    assert task.recorded()["PFD:1"]["points"][0]["evidence"][0]["verified"] == "seen"


def test_each_word_of_one_search_counts_as_searched(work: Path) -> None:
    assert tools.main(["find", "01.xlsx", "返工", "报废"]) == 0
    absent = verdict("NG", {"need": "有异常处理节点", "result": "unmet", "why": "没有", "searched": [{"file": "01.xlsx", "where": "find:返工"}, {"file": "01.xlsx", "where": "find:报废"}]})
    assert Task().check(absent)[0] is not None


def test_a_search_of_one_sheet_is_not_a_search_of_the_file(work: Path) -> None:
    assert tools.main(["find", "01.xlsx", "返工", "--sheet", "流程"]) == 0
    absent = verdict("NG", {"need": "有异常处理节点", "result": "unmet", "why": "没有", "searched": [{"file": "01.xlsx", "where": "find:返工"}]})
    assert Task().check(absent)[0] is None


def test_a_picture_a_few_pixels_across_is_enlarged_before_the_model_sees_it(tmp_path: Path) -> None:
    from auditor.originals import SMALLEST, _save
    from PIL import Image

    _save(Image.new("RGB", (13, 37), "white"), tmp_path / "tick.png")
    with Image.open(tmp_path / "tick.png") as made:
        assert min(made.size) >= SMALLEST and made.height > made.width


def test_the_same_block_of_every_sheet_is_read_at_once_and_counts_as_read(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from openpyxl import load_workbook

    book = load_workbook(work / "files" / "01.xlsx")
    for name, value in (("甲", 1.7), ("乙", 1.2)):
        sheet = book.create_sheet(name)
        sheet["T72"] = value
    book.save(work / "files" / "01.xlsx")
    assert tools.main(["cells", "01.xlsx", "--sheet", "*", "--range", "T72", "A1"]) == 0
    read = capsys.readouterr().out
    assert "「甲」T72:T72　T72=1.7" in read and "「乙」T72:T72　T72=1.2" in read and "「流程」A1:A1　A1=工序" in read
    low = verdict("NG", {"need": "各表能力指数达标", "result": "unmet", "evidence": [{"file": "01.xlsx", "where": "乙!T72", "quote": "1.2"}]})
    assert Task().check(low)[0] is not None


def test_parsed_cells_are_kept_for_the_next_look_and_dropped_when_the_original_changes(work: Path) -> None:
    from auditor.originals import Original
    from openpyxl import load_workbook

    assert Original(work / "files" / "01.xlsx", work / "cache").sheet("流程").text(2, 1) == "气密"
    assert list((work / "cache").rglob("cells-*.pickle"))
    assert Original(work / "files" / "01.xlsx", work / "cache").sheet("流程").text(2, 1) == "气密"
    book = load_workbook(work / "files" / "01.xlsx")
    book["流程"]["A2"] = "清洗"
    book.save(work / "files" / "01.xlsx")
    assert Original(work / "files" / "01.xlsx", work / "cache").sheet("流程").text(2, 1) == "清洗"


def test_a_picture_is_shown_in_the_cell_it_sits_on(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from openpyxl import load_workbook
    from openpyxl.drawing.image import Image as Placed
    from PIL import Image

    Image.new("RGB", (120, 120), "white").save(work / "mark.png")
    book = load_workbook(work / "files" / "01.xlsx")
    book["流程"].add_image(Placed(work / "mark.png"), "C3")
    book.save(work / "files" / "01.xlsx")
    assert tools.main(["cells", "01.xlsx", "--sheet", "流程", "--range", "A3:C3"]) == 0
    assert "C3=〔图 " in capsys.readouterr().out


def test_in_the_closing_turn_nothing_more_is_read_but_a_verdict_is_taken(work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert tools.main(["cells", "01.xlsx", "--sheet", "流程"]) == 0
    monkeypatch.setenv("AUDIT_CLOSING", "1")
    assert tools.main(["cells", "01.xlsx", "--sheet", "流程"]) == 1
    assert "时间到了" in capsys.readouterr().out
    (work / "v.json").write_text(json.dumps(verdict("PASS", {"need": "有气密工序", "result": "met", "evidence": [cite("流程!A2", "气密")]}), ensure_ascii=False), encoding="utf-8")
    assert tools.main(["submit", "v.json"]) == 0
    assert Task().recorded()["PFD:1"]["status"] == "PASS"
