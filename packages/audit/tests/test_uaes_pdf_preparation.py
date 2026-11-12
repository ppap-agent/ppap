"""Native PDF continuation pages reach the XLSX reader with original provenance."""

import fitz
import pytest
from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent
from audit.checker_lib.uaes.native import UnknownFact
from audit.contracts import AuditInput, GroupContextInput, RuleInput


# None: the resource cell holds only a drawn circle, which converts to an empty cell.
@pytest.mark.parametrize("last_resource,expected", [("设备2", "PASS"), ("", "NG"), (None, "UNABLE")])
def test_uaes_reads_all_native_pdf_continuation_pages(tmp_path, last_resource, expected):
    path = tmp_path / "control-plan.pdf"
    with fitz.open() as document:
        for index, resource in enumerate(("设备1", last_resource)):
            page = document.new_page(width=500, height=220)
            for top in (20, 60, 100, 160):
                page.draw_line((20, top), (480, top))
            for left in (20, 480):
                page.draw_line((left, 20), (left, 160))
            for left in (170, 320):
                page.draw_line((left, 60), (left, 160))
            cells = (
                (40, 45, "Control Plan"),
                (30, 85, "工序名称/操作描述"),
                (180, 85, "设备/工装"),
                (330, 85, "评价/测量技术"),
                (30, 130, f"工序{index + 1}"),
                (180, 130, resource),
                (330, 130, "目视"),
            )
            for x, y, text in cells:
                if text:
                    page.insert_text((x, y), text, fontname="china-s", fontsize=11)
            if resource is None:
                page.draw_circle((240, 130), 10)
        document.save(path)
    original = path.read_bytes()
    agent = UaesControlPlanAgent(AuditInput(audit_id="frozen-pdf-version", file_url=str(path)), RuleInput(rule_id="uaes-cp", rule_profile="v4", preset_key="U08"))
    result = agent.check(0)
    assert result.status == expected, result.suggestions
    if expected == "NG":
        # The blank operation is on the continuation page, not the first.
        assert "工序2" in result.suggestions and "工序1" not in result.suggestions
    assert path.read_bytes() == original
    assert result.evidence.source_format == "pdf"
    assert result.evidence.prepared_format == "xlsx"
    assert {source.locator for source in result.evidence.sources} >= {"file:frozen-pdf-version;page:1", "file:frozen-pdf-version;page:2"}


def _related_source(tmp_path, name, *rows):
    from openpyxl import Workbook

    workbook = Workbook()
    for row in rows:
        workbook.active.append(list(row))
    path = tmp_path / name
    workbook.save(path)
    return AuditInput(audit_id=name, file_url=str(path), project_id="p")


def _control_plan(tmp_path, by_type, unclassified):
    target = _related_source(tmp_path, "cp.xlsx", ["控制计划"], ["零件号", "F01RD0Y776"], ["工序名称/操作描述", "评价/测量技术"], ["压铸", "目视"])
    agent = UaesControlPlanAgent(target, RuleInput(rule_id="uaes-cp", rule_profile="v4", preset_key="U08"))
    grouped = {kind: [_related_source(tmp_path, name, *rows) for name, rows in items] for kind, items in by_type.items()}
    others = [_related_source(tmp_path, name, *rows) for name, rows in unclassified]
    agent.group_context = GroupContextInput(
        task_group_id="group", by_doc_type=grouped, by_audit_id={item.audit_id: item for item in (target, *others, *(entry for items in grouped.values() for entry in items))}
    )
    return agent


@pytest.mark.parametrize(
    "by_type,unclassified,expected",
    [
        ({"潜在过程失效模式及后果分析": [("pfmea.xlsx", [["零件号", "F01RD0Y776"]])]}, [], "NG"),
        ({}, [], "NG"),
        ({}, [("drawing.xlsx", [["无关标题"]])], "UNABLE"),
        ({}, [("drawing.xlsx", [["图号", "F01R10Y776"]])], "NG"),
        ({}, [("drawing.xlsx", [["图号", "F01RD0Y776"]])], "UNABLE"),
    ],
    ids=["another-type", "nothing", "unclassified-cannot-be-excluded", "another-part", "same-part-unread"],
)
def test_only_a_proven_absence_of_the_drawing_is_a_failure(tmp_path, by_type, unclassified, expected):
    # No document type names a drawing: an untyped original is excluded only by the part number it states.
    result = _control_plan(tmp_path, by_type, unclassified).check(11)
    assert result.status == expected, result.suggestions


def test_a_missing_source_decides_the_clause_before_unknowns_about_others(tmp_path):
    unknown_flow = ("flow.xlsx", [["过程流程图"]])
    other_plan = ("pgqp1.xlsx", [["零件号", "F01R10Y776"]])
    same_pfmea = ("pfmea.xlsx", [["零件号", "F01RD0Y776"]])
    agent = _control_plan(tmp_path, {"过程流程图": [unknown_flow], "潜在过程失效模式及后果分析": [same_pfmea], "E12": [other_plan]}, [])
    result = agent.check(6)
    assert result.status == "NG", result.suggestions
    assert "采购件质量计划" in result.suggestions


def test_a_row_proving_the_fact_decides_even_with_unknown_rows():
    from audit.checker_lib.uaes.native import any_row

    def unknown(_row):
        raise UnknownFact("unknown")

    rows = ["unknown", "proof"]
    predicate = lambda row: unknown(row) if row == "unknown" else True  # noqa: E731
    assert any_row(rows, predicate) is True
    with pytest.raises(UnknownFact):
        any_row(["unknown", "nothing"], lambda row: unknown(row) if row == "unknown" else False)
    assert any_row(["nothing"], lambda row: False) is False


def test_pages_repeating_one_header_row_continue_one_table_after_a_different_form(tmp_path):
    from audit.checker_lib.uaes.native import NativeFacts

    path = tmp_path / "cover-and-plan.pdf"
    pages = ((("封面", ""), ("零件号", "F01")), (("工序名称", "生产资源"), ("工序1", "设备1")), (("工序名称", "生产资源"), ("工序2", "设备2")))
    with fitz.open() as document:
        for rows in pages:
            page = document.new_page(width=400, height=220)
            for top in (20, 60, 100):
                page.draw_line((20, top), (380, top))
            for left in (20, 200, 380):
                page.draw_line((left, 20), (left, 100))
            for row, values in enumerate(rows):
                for column, text in enumerate(values):
                    if text:
                        page.insert_text((40 + 180 * column, 45 + 40 * row), text, fontname="china-s", fontsize=11)
        document.save(path)
    cover, plan = NativeFacts(str(path), "v", lambda *args, **kwargs: None).data["tables"]
    assert {address: cell["source"]["page"] for address, cell in cover["cells"].items()} == dict.fromkeys(("A1", "A2", "B1", "B2"), 1)
    assert [(plan["cells"][f"A{row}"]["value"], plan["cells"][f"B{row}"]["value"], plan["cells"][f"B{row}"]["source"]["page"]) for row in (2, 3)] == [("工序1", "设备1", 2), ("工序2", "设备2", 3)]


@pytest.mark.parametrize("outlined_page,expected", [(False, "NA"), (True, "UNABLE")])
def test_a_page_without_text_cannot_prove_the_msa_plan_is_absent(tmp_path, outlined_page, expected):
    from audit.checker_lib.uaes.msa import UaesMsaAgent

    path = tmp_path / "msa.pdf"
    with fitz.open() as document:
        if outlined_page:
            # Text drawn as vector outlines leaves no text layer to read a title from.
            page = document.new_page(width=400, height=220)
            page.draw_rect((40, 40, 120, 60))
        page = document.new_page(width=400, height=220)
        page.insert_text((40, 45), "重复性及再现性分析报告", fontname="china-s", fontsize=11)
        document.save(path)
    agent = UaesMsaAgent(AuditInput(audit_id="msa", file_url=str(path)), RuleInput(rule_id="uaes-msa", rule_profile="v4", preset_key="U05"))
    assert agent.check(0).status == expected


def test_a_page_without_a_reconstructed_grid_cannot_prove_the_capability_summary_is_absent(tmp_path):
    from audit.checker_lib.uaes.engineering import UaesCapabilityAgent

    path = tmp_path / "ppk.pdf"
    with fitz.open() as document:
        # The summary's text is readable, but no table grid is rebuilt for it.
        document.new_page(width=400, height=220).insert_text((40, 45), "Ppk能力值汇总  PPK结果值  PPK目标值  反应计划", fontname="china-s", fontsize=11)
        document.save(path)
    agent = UaesCapabilityAgent(AuditInput(audit_id="ppk", file_url=str(path)), RuleInput(rule_id="uaes-ppk", rule_profile="v4", preset_key="U06"))
    assert agent.check(2).status == "UNABLE"
