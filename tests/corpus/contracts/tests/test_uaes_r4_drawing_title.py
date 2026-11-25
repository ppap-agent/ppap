"""R4: a same-round PDF drawing's number and designation are read from its own title block, at a cited place."""

import json

import fitz
import pytest
from corpus_contracts.uaes_r4_fixture import book, check
from documents.ocr.observation import current_ocr_observation

NUMBER = {"label": "图号 DWG No.", "value": "F 01R 10Y 776", "bbox": [690, 790, 860, 870]}
NAME = {"label": "名称 DESIGNATION", "value": "机加工壳体 MACHINED HOUSING", "bbox": [690, 620, 870, 760]}
PROJECT = {"label": "项目编号 Project No.", "value": "P-629", "bbox": [300, 380, 580, 430]}


def drawing(tmp_path, name="Z_part.pdf"):
    """An A1 sheet whose title block is strokes only: its text layer names no drawing number."""
    path = tmp_path / name
    with fitz.open() as document:
        page = document.new_page(width=2384, height=1684)
        page.insert_text((100, 100), "SECTION A-A")
        page.draw_rect(fitz.Rect(1700, 1500, 2300, 1660))
        document.save(path)
    return path


def parts_list(tmp_path):
    path = tmp_path / "ST_part.pdf"
    with fitz.open() as document:
        page = document.new_page()
        for y, text in ((80, "零件清单"), (95, "Parts list"), (180, "所属图纸"), (270, "序号")):
            page.insert_text((150, y), text, fontname="china-s")
        document.save(path)
    return path


@pytest.fixture
def title(monkeypatch):
    """The image model's two observations of the title block, by observation number; every request is recorded."""
    calls = []

    def answer(first, second=None):
        def request(**kwargs):
            calls.append(current_ocr_observation())
            return dict((second or first) if current_ocr_observation() == 2 else first)

        monkeypatch.setattr("audit.engine.checkers.ocr_visual_facts.request_ocr_visual_facts", request)

    return answer, calls


def reading(**fields):
    return {"kind": "drawing", "number": None, "name": None, "project": None, "status": None, **fields}


def materials(tmp_path, part="F01R10Y776", name="机加工壳体"):
    rows = [["UAES项目编号：\nUAES Project No.:", "P-629"], ["零件名称:\nPart Name:", name], ["零件号:\nPart No:", part], ["序号", "零件可视图", "材料名称和材料牌号"], [1, "", "AL"]]
    return book(tmp_path / "materials.xlsx", {"材料及分供方清单": rows})


def test_number_and_designation_from_the_title_block_with_their_places(tmp_path, title):
    answer, calls = title
    answer(reading(number=NUMBER, name=NAME, project=PROJECT))
    result = check(materials(tmp_path), "U04", "MSL:3", {"图纸": [drawing(tmp_path)]})
    assert result["status"] == "PASS" and "F 01R 10Y 776" in result["suggestions"], result
    assert calls == [1, 2]
    places = [json.loads(s["locator"]) for s in result["evidence"]["sources"] if json.loads(s["locator"]).get("fact") == "drawing_title_number"]
    assert len(places) == 1 and places[0]["page"] == 1 and places[0]["engine"] == "ocr_llm"
    x0, y0, x1, y1 = places[0]["bbox"]
    # The read corner is the bottom-right 300 x 120 mm of the sheet; the number lies inside it, not at the sheet origin.
    assert 2384 - 300 * 72 / 25.4 < x0 < x1 <= 2384 and 1684 - 120 * 72 / 25.4 < y0 < y1 <= 1684


def test_a_different_title_block_number_is_a_mismatched_source(tmp_path, title):
    answer, _calls = title
    answer(reading(number=NUMBER, name=NAME))
    result = check(materials(tmp_path, part="F01RD 0Y77601"), "U04", "MSL:3", {"图纸": [drawing(tmp_path)]})
    assert result["status"] == "NG" and "比对源错配" in result["suggestions"] and "F 01R 10Y 776" in result["suggestions"], result


@pytest.mark.parametrize(
    "first,second,why",
    [
        (reading(number=NUMBER), reading(number={**NUMBER, "value": "F 0IR 10Y 776"}), "两次读取的图号不一致"),
        (reading(number=NUMBER), reading(number={**NUMBER, "label": "首次用于1ST APPLICAT."}), "第二次读取未读出图号"),
        (reading(number=NUMBER), reading(), "第二次读取未读出图号"),
        (reading(number={**NUMBER, "label": "首次用于1ST APPLICAT."}), None, "标题栏未读出图号"),
        (reading(number={**NUMBER, "bbox": [0.69, 0.79, 0.86, 0.87]}), None, "标题栏未读出图号"),
    ],
)
def test_an_unconfirmed_number_leaves_the_drawing_open(tmp_path, title, first, second, why):
    answer, calls = title
    answer(first, second)
    result = check(materials(tmp_path), "U04", "MSL:3", {"图纸": [drawing(tmp_path)]})
    assert result["status"] == "UNABLE" and why in result["suggestions"], result
    assert len(calls) == (1 if second is None else 2)


def test_an_untyped_drawing_is_identified_by_its_title_block_and_a_parts_list_by_its_form(tmp_path, title):
    answer, calls = title
    answer(reading(number=NUMBER, name=NAME))
    study = book(tmp_path / "study.xlsx", {"目录": [["零件号码", "F01R10Y776"], ["特性名称", "过程", "规格"], ["孔径", "√", "尺寸"]]})
    only_list = check(study, "U06", "PCE:6", {None: [parts_list(tmp_path)]})
    assert only_list["status"] == "NA" and calls == [], only_list
    both = check(study, "U06", "PCE:6", {None: [parts_list(tmp_path), drawing(tmp_path)]})
    # The drawing is now known to be one; its marked characteristics are what stays unread.
    assert both["status"] == "UNABLE" and "未识别" not in both["suggestions"] and "图纸的全部标注" in both["suggestions"], both


def test_mixed_list_mismatch_is_judged_without_choosing_a_part(tmp_path, title):
    # Product owner, 2026-11-24: when no part can be determined, a cross-source mismatch whichever part is chosen fails
    # SCL:3, naming every file's own numbers; the drawing never chooses the part.
    answer, _calls = title
    rows = [["序号", "Code", "特性名称", "规格/公差", "分类"], [1, "F01R10Y776", "孔径", "9±0.01", "SC"], [2, "F01R10Y777", "直径", "8±0.1", "SC"]]
    target = book(tmp_path / "list.xlsx", {"特殊特性清单": rows})
    answer(reading(number={**NUMBER, "value": "F 01R 20Y 777"}))
    other = check(target, "U12", "SCL:3", {"图纸": [drawing(tmp_path)]})
    assert other["status"] == "NG" and "比对源错配" in other["suggestions"] and "图号F01R20Y777" in other["suggestions"] and "F01R10Y776、F01R10Y777" in other["suggestions"], other
    # A drawing number the list has rows of could still be this part: that stays open, and is not chosen.
    answer(reading(number=NUMBER))
    same = check(target, "U12", "SCL:3", {"图纸": [drawing(tmp_path)]})
    assert same["status"] == "UNABLE" and "不能确定本件号" in same["suggestions"], same
    assert "不能确定本件号" in check(target, "U12", "SCL:2", {"图纸": [drawing(tmp_path)]})["suggestions"]
