"""N01 source requirements must not inherit V3's relaxed numeric or empty rules."""

import io

import pytest
from audit.audits import RoundInput, RoundWork
from audit.presets import preset_rule_body
from corpus_contracts.core_round import customer_suite, run_suite_round
from openpyxl import Workbook


def execute(
    *,
    nominal=None,
    point_type="CC",
    part1=0.1,
    part2=0.2,
    centering=0.075,
    range_value=0.05,
    marker="P01",
    cover_part="P12345",
    formula_cache=None,
    drawing=False,
    drawing_outside_rows=False,
    cover_layout="pairs",
    separate_diagram=False,
):
    book = Workbook()
    cover = book.active
    cover.title = "CoverSheet"
    for label, value in [("零件号", cover_part), ("零件名", "支架"), ("项目", "项目A"), ("项目阶段", "P"), ("日期", "2026-01-01")]:
        cover.append([label, value])
    if cover_layout == "columns":
        cover.delete_rows(1, cover.max_row)
        cover.append(["零件号", "零件名", "项目", "项目阶段", "日期"])
        cover.append([cover_part, "支架", "项目A", "P", "2026-01-01"])
    elif cover_layout == "note-under-empty-part":
        cover.insert_rows(2)
        cover["A2"] = "备注：仅供内部使用"
    sheet = book.create_sheet("Surface")
    headers = ["测点号", "类型", "坐标X", "坐标Y", "坐标Z", "名义值", "上公差", "下公差", "主要控制方向", "测量偏差值", None, "Centering", "Range", "测点图/截面"]
    sheet.append(headers)
    sheet.append([None] * 9 + ["Part1", "Part2"])
    sheet.merge_cells("J1:K1")
    for column in [*range(1, 10), 12, 13, 14]:
        sheet.merge_cells(start_row=1, end_row=2, start_column=column, end_column=column)
    sheet.append(["P01", point_type, 1, 2, 3, nominal, 1, -1, "X", part1, part2, centering, range_value, marker])
    if drawing:
        from openpyxl.drawing.image import Image
        from PIL import Image as Pixels

        bitmap = io.BytesIO()
        Pixels.new("RGB", (8, 8), "white").save(bitmap, format="PNG")
        image = Image(bitmap)
        if drawing_outside_rows:
            from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor

            image.anchor = TwoCellAnchor(_from=AnchorMarker(col=13, row=4), to=AnchorMarker(col=14, row=8))
            sheet.add_image(image)
        else:
            sheet.add_image(image, "N3")
    if separate_diagram:
        book.create_sheet("独立测点图")["A1"] = "P99"
    content = io.BytesIO()
    book.save(content)
    book.close()
    if formula_cache:
        from xml.etree import ElementTree as ET
        from zipfile import ZIP_DEFLATED, ZipFile

        with ZipFile(io.BytesIO(content.getvalue())) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        xml = ET.fromstring(entries["xl/worksheets/sheet2.xml"])
        for cell in xml.iter(ns + "c"):
            if cell.get("r") in formula_cache:
                cell.find(ns + "v").text = str(formula_cache[cell.get("r")])
        entries["xl/worksheets/sheet2.xml"] = ET.tostring(xml)
        content = io.BytesIO()
        with ZipFile(content, "w", ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
    body = preset_rule_body("蔚来", "N01")
    body["rules"].reverse()
    target = RoundInput("target", "file", "version", "测量报告.xlsx", "ready", rule_version_id="nio-n01", rule_body=body)
    result = run_suite_round(RoundWork("round", "tenant", [target]), {"version": content.getvalue()}, suites={"version": customer_suite("蔚来", "N01")})
    # Source order is intentionally reversed; assertions use original clause IDs.
    return {finding["item_key"].split(":rule:", 1)[1].split(":")[1]: finding for finding in result["findings"]}


def test_source_values_have_expected_conditions_and_arithmetic():
    findings = execute()
    assert [findings[str(index)]["status"] for index in range(1, 12)] == ["PASS", "PASS", "NA", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS"]


@pytest.mark.parametrize(
    ("values", "clause", "expected"),
    [
        ({"cover_part": None}, "1", "NG"),
        ({"nominal": 0}, "4", "NG"),
        ({"point_type": None}, "5", "NG"),
        ({"marker": "P99"}, "6", "NG"),
        ({"marker": None}, "6", "UNABLE"),
        ({"marker": "P01 未识别的图示内容"}, "6", "UNABLE"),
        ({"part1": 1.1}, "7", "NG"),
        ({"centering": 0.085}, "8", "NG"),
        ({"centering": 0.67}, "9", "NG"),
        ({"centering": "unknown"}, "9", "UNABLE"),
        ({"range_value": 0.06}, "10", "NG"),
        ({"range_value": 0.67}, "11", "NG"),
        ({"range_value": "unknown"}, "11", "UNABLE"),
        ({"part1": "not numeric"}, "7", "UNABLE"),
    ],
)
def test_customer_source_is_not_relaxed_to_a_false_pass(values, clause, expected):
    findings = execute(**values)
    assert findings[clause]["status"] == expected, findings[clause]


@pytest.mark.parametrize(("values", "clause"), [({"part1": "=999"}, "7"), ({"part1": "=999"}, "8"), ({"nominal": "=1-1"}, "4")])
def test_a_formula_without_a_cached_value_is_unknown_rather_than_an_empty_cell(values, clause):
    finding = execute(**values)[clause]
    assert finding["status"] == "UNABLE", finding


def test_known_cached_formula_values_still_use_the_actual_source_arithmetic():
    findings = execute(part1="=0.1", formula_cache={"J3": 0.1})
    assert findings["7"]["status"] == findings["8"]["status"] == "PASS"


def test_text_markers_do_not_certify_unread_visual_markers():
    findings = execute(drawing=True)
    assert findings["6"]["status"] == "UNABLE"
    assert findings["7"]["status"] == "PASS"


def test_cover_values_can_follow_a_real_column_header_row():
    assert execute(cover_layout="columns")["1"]["status"] == "PASS"


def test_an_unrelated_note_cannot_fill_a_missing_cover_part_number():
    assert execute(cover_part=None, cover_layout="note-under-empty-part")["1"]["status"] != "PASS"


def test_unread_drawing_below_the_point_rows_still_needs_confirmation():
    assert execute(drawing=True, drawing_outside_rows=True)["6"]["status"] == "UNABLE"


def test_separate_diagram_is_not_ignored_when_a_main_sheet_marker_matches():
    assert execute(separate_diagram=True)["6"]["status"] == "UNABLE"
