"""Native PDF continuation pages reach the XLSX reader with original provenance."""

import hashlib
import json

import fitz
import pytest
from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent
from audit.checker_lib.uaes.native import MissingFact, UnknownFact
from audit.contracts import AuditInput, GroupContextInput, RuleInput


@pytest.mark.parametrize("last_resource,expected", [("设备2", "PASS"), ("", "NG"), (None, "UNABLE")])
def test_uaes_reads_all_native_pdf_continuation_pages_and_keeps_original_unknowns(tmp_path, last_resource, expected):
    path = tmp_path / "control-plan.pdf"
    with fitz.open() as document:
        for index, resource in enumerate(("设备1", last_resource)):
            page = document.new_page(width=400, height=220)
            for top in (20, 60, 100, 160):
                page.draw_line((20, top), (380, top))
            for left in (20, 380):
                page.draw_line((left, 20), (left, 160))
            page.draw_line((200, 60), (200, 160))
            for x, y, text in ((40, 45, "Control Plan"), (40, 85, "工序名称"), (220, 85, "生产资源"), (40, 130, f"工序{index + 1}"), (220, 130, resource or "")):
                if text:
                    page.insert_text((x, y), text, fontname="china-s", fontsize=11)
            if resource is None:
                page.draw_circle((260, 130), 10)
        document.save(path)
    original = path.read_bytes()
    agent = UaesControlPlanAgent(AuditInput(audit_id="frozen-pdf-version", file_url=str(path)), RuleInput(rule_id="uaes-cp", rule_profile="v4", preset_key="U08"))
    result = agent.check(0)
    assert result.status == expected
    assert path.read_bytes() == original
    assert result.evidence.source_format == "pdf"
    assert result.evidence.prepared_format == "xlsx"
    assert agent.native().has_unread_content() is (last_resource is None)
    sources = [json.loads(source.locator) for source in result.evidence.sources]
    assert {source["page"] for source in sources} == {1, 2}
    assert all(source["kind"] == "pdf_cell" and source["file_version_id"] == "frozen-pdf-version" and source["source_sha256"] == hashlib.sha256(original).hexdigest() for source in sources)
    assert all(source["bbox"] and source["prepared_sheet"] and source["prepared_cell"] and source["prepared_sha256"] for source in sources)


def _related_source(tmp_path, name, title):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active["A1"] = title
    path = tmp_path / name
    workbook.save(path)
    return AuditInput(audit_id=name, file_url=str(path), project_id="p")


@pytest.mark.parametrize(
    "by_type,unclassified,expected",
    [
        ({"PFMEA": ["pfmea.xlsx"]}, [], MissingFact),
        ({}, [], MissingFact),
        ({}, ["unknown.xlsx"], UnknownFact),
        ({"图纸": ["a.xlsx", "b.xlsx"]}, [], UnknownFact),
        ({"测量系统分析": ["msa.xlsx"]}, [], UnknownFact),
    ],
    ids=["another-type", "nothing", "unclassified-cannot-be-excluded", "several-candidates", "broad-type-stays-open"],
)
def test_only_a_proven_absence_of_the_related_source_is_a_failure(tmp_path, by_type, unclassified, expected):
    grouped = {kind: [_related_source(tmp_path, name, "无关标题") for name in names] for kind, names in by_type.items()}
    others = [_related_source(tmp_path, name, "无关标题") for name in unclassified]
    target = _related_source(tmp_path, "cp.xlsx", "控制计划")
    agent = UaesControlPlanAgent(target, RuleInput(rule_id="uaes-cp", rules=[], rule_profile="v4", type="控制计划"))
    agent.group_context = GroupContextInput(
        task_group_id="group", by_doc_type=grouped, by_audit_id={item.audit_id: item for item in (target, *others, *(entry for items in grouped.values() for entry in items))}
    )
    with pytest.raises(expected):
        agent.related(("图纸", "drawing") if "测量系统分析" not in by_type else ("kappa",))


def test_a_missing_source_decides_the_clause_before_unknowns_about_others(tmp_path, monkeypatch):
    target = _related_source(tmp_path, "cp.xlsx", "控制计划")
    agent = UaesControlPlanAgent(target, RuleInput(rule_id="uaes-cp", rules=[], rule_profile="v4", type="控制计划"))

    def related(terms, **_):
        if terms == ("flow",):
            raise UnknownFact("identity unknown")
        raise MissingFact("absent")

    monkeypatch.setattr(agent, "related", related)
    with pytest.raises(MissingFact):
        agent.related_all(("flow",), ("plan",))


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
