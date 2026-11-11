"""UAES report leaves execute their own clauses over original file bytes."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from datetime import date, timedelta
from types import ModuleType, SimpleNamespace

import fitz
import pytest
from audit.checker_core import FrozenInput, FrozenTarget, RuleBinding, checker_digest, validate_results
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.standard_library import check_factory
from audit.standard_suites import load_predefined_checkers
from openpyxl import Workbook
from PIL import Image


def _workbook(sheets):
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def _execute(tmp_path, monkeypatch, entry, clause, sheets=None, *, content=None, suffix=".xlsx", peers=()):
    """Load real leaf files, use its factory adapter, then validate in Core."""
    suite = next(item for item in load_predefined_checkers() if item.source_path == f"UAES/{entry}")
    resources = ModuleType("resources")
    exec(compile(suite.files["resources.py"], f"{suite.source_path}/resources.py", "exec"), resources.__dict__)
    monkeypatch.setitem(sys.modules, "resources", resources)
    checker = ModuleType(f"uaes_report_test_{entry}")
    exec(compile(suite.files["checker.py"], f"{suite.source_path}/checker.py", "exec"), checker.__dict__)
    body = json.loads(suite.files["rules.json"])
    body["rules"] = [item for item in body["rules"] if item["id"] == clause]
    body["checker_binding"] = {clause: suite.entrypoints[clause]}
    assert len(body["rules"]) == 1
    content = _workbook(sheets) if content is None else content
    path = tmp_path / (entry + suffix)
    path.write_bytes(content)
    audit = AuditInput(audit_id="original", file_url=str(path), customer="UAES", project_id="project")
    rule = RuleInput(rule_id="frozen-rule", **{key: value for key, value in body.items() if key != "rule_id"})
    frozen = [FrozenInput(audit.audit_id, hashlib.sha256(content).hexdigest(), "target", path.name)]
    by_type = {}
    for identity, kind, peer_sheets in peers:
        peer_path = tmp_path / (identity + ".xlsx")
        peer_content = _workbook(peer_sheets)
        peer_path.write_bytes(peer_content)
        peer = AuditInput(audit_id=identity, file_url=str(peer_path), customer="UAES", project_id="project")
        by_type.setdefault(kind, []).append(peer)
        frozen.append(FrozenInput(identity, hashlib.sha256(peer_content).hexdigest(), "reference", peer_path.name))
    group = GroupContextInput(by_doc_type=by_type, by_audit_id={item.audit_id: item for values in by_type.values() for item in values})
    target = FrozenTarget("original", "revision", suite.suite_key, suite.revision_digest, body, RuleBinding(suite.suite_key, checker_digest(suite.program_files), body["checker_binding"]))
    opened = []
    context = SimpleNamespace(audit_file=audit, rule=rule, target=target, group_context=group, cache={}, on_close=opened.append, clause_id=clause)
    try:
        observed = check_factory(context, checker.create_audit).model_dump(mode="json")
        sources = (observed.get("evidence") or {}).get("sources", [])
        referenced = {"original"}
        referenced.update(json.loads(item["locator"])["file_version_id"] for item in sources)
        observed["evidence_file_version_ids"] = sorted(referenced)
        checked = validate_results([observed], target=target, inputs=frozen)
        assert checked["coverage"] == [{"file_version_id": "original", "clause_id": clause, "coverage": "mapped"}]
        result = checked["findings"][0]
        assert result["clause_id"] == clause
        if result["status"] == "PASS":
            assert result["evidence"]["sources"], result
        return result
    finally:
        for audit in reversed(opened):
            audit.close()


@pytest.mark.parametrize(("cpk", "expected"), [(1.34, "PASS"), (1.33, "NG"), ("=1+1", "UNABLE")])
def test_capability_uses_strict_numeric_source_and_keeps_unknown_formula(tmp_path, monkeypatch, cpk, expected):
    result = _execute(tmp_path, monkeypatch, "U06", "U06:1", {"工艺能力认证报告": [["读数次数", 125], ["Cpk", cpk], ["Ppk", 1.68]]})
    assert result["status"] == expected, result
    assert result["evidence"]["sources"]


@pytest.mark.parametrize(("ppk", "expected"), [(1.68, "PASS"), (1.67, "NG")])
def test_capability_ppk_is_strictly_above_threshold(tmp_path, monkeypatch, ppk, expected):
    result = _execute(tmp_path, monkeypatch, "U06", "U06:2", {"工艺能力认证报告": [["Ppk", ppk]]})
    assert result["status"] == expected, result


@pytest.mark.parametrize(("reaction", "expected"), [(None, "NG"), ("增加抽样并整改", "PASS")])
def test_capability_low_summary_value_requires_same_row_reaction(tmp_path, monkeypatch, reaction, expected):
    result = _execute(tmp_path, monkeypatch, "U06", "U06:3", {"能力值汇总": [["序号", "CPK", "PPK", "CPK目标", "PPK目标", "反应计划"], [1, 1.0, 1.7, 1.33, 1.67, reaction]]})
    assert result["status"] == expected, result


@pytest.mark.parametrize(
    ("entry", "clause", "title", "labels"), [("U04", "U04:1", "材料及分供方清单", ["序号", "材料", "分供方", "备注"]), ("U05", "U05:1-1", "MSA计划表", ["序号", "量具名称", "量具编号", "备注"])]
)
@pytest.mark.parametrize(("value", "expected"), [(None, "NG"), ("已填写", "PASS"), ("=1+1", "UNABLE")])
def test_non_remark_required_cells_read_actual_value(tmp_path, monkeypatch, entry, clause, title, labels, value, expected):
    result = _execute(tmp_path, monkeypatch, entry, clause, {title: [labels, [1, "对象", value, None]]})
    assert result["status"] == expected, result


def test_msa_plan_without_business_rows_is_unable(tmp_path, monkeypatch):
    result = _execute(tmp_path, monkeypatch, "U05", "U05:1-1", {"MSA计划表": [["序号", "量具名称", "量具编号", "备注"]]})
    assert result["status"] == "UNABLE", result


@pytest.mark.parametrize(("include_last", "expected"), [(True, "PASS"), (False, "NG")])
def test_packaging_requires_all_six_blocks(tmp_path, monkeypatch, include_last, expected):
    blocks = ["零件基础信息", "供应商基础信息", "包装设计的基本要求", "针对该零件包装的特殊要求", "包装方案信息", "包装材料清单"]
    result = _execute(tmp_path, monkeypatch, "U09", "U09:1", {"包装方案": [["包装方案"], *[[block] for block in blocks[: 6 if include_last else 5]]]})
    assert result["status"] == expected, result


@pytest.mark.parametrize(("time_header", "expected"), [("更改日期", "PASS"), ("尚待确认", "UNABLE")])
def test_engineering_history_accepts_explicit_alias_without_guessing(tmp_path, monkeypatch, time_header, expected):
    result = _execute(
        tmp_path, monkeypatch, "U11", "U11:1", {"工程更改一览表": [["工程更改一览表"], ["项目名称", "物料编号", "更改内容", "更改原因", time_header], ["P", "A123", "孔径", "改进", "2026-01-01"]]}
    )
    assert result["status"] == expected, result


@pytest.mark.parametrize(("value", "expected"), [("可追溯", "PASS"), (None, "UNABLE")])
def test_traceability_needs_value_beside_each_field_label(tmp_path, monkeypatch, value, expected):
    rows = [["供应商零件追溯"], ["基本信息", "P12345"], ["分供方追溯", "供应商A"], ["生产过程追溯", "批次A"], ["交付追溯", "订单A"], ["追溯状态总结", value]]
    result = _execute(tmp_path, monkeypatch, "U18", "U18:1", {"供应商零件追溯": rows})
    assert result["status"] == expected, result


def test_expired_qualification_fails_from_native_date_without_vision(tmp_path, monkeypatch):
    from audit.checker_lib.source import SourceAudit

    monkeypatch.setattr(SourceAudit, "visual_pdf", lambda *args: pytest.fail("Expired date needs no visual call"))
    rows = [["中国合格评定国家认可委员会", "CNAS"], ["截止日期", (date.today() - timedelta(days=1)).isoformat()]]
    result = _execute(tmp_path, monkeypatch, "U03", "U03:1", {"资质": rows})
    assert result["status"] == "NG", result


def test_future_qualification_text_does_not_prove_actual_cnas_mark(tmp_path, monkeypatch):
    from audit.checker_lib.source import SourceAudit

    monkeypatch.setattr(SourceAudit, "visual_pdf", lambda self, fields, **kwargs: dict.fromkeys(fields))
    rows = [["中国合格评定国家认可委员会", "CNAS"], ["截止日期", (date.today() + timedelta(days=365)).isoformat()]]
    result = _execute(tmp_path, monkeypatch, "U03", "U03:1", {"资质": rows})
    assert result["status"] == "UNABLE", result


@pytest.mark.parametrize(("mark", "expected"), [(True, "PASS"), (False, "NG")])
def test_qualification_uses_actual_title_and_cnas_observation(tmp_path, monkeypatch, mark, expected):
    from audit.checker_lib.source import SourceAudit

    def observe(owner, fields, **kwargs):
        assert set(fields) == {"cnas_mark"}
        assert kwargs["existential_fields"] == ("cnas_mark",)
        owner.witness({"kind": "xlsx_cell", "sheet": "资质", "cell": "A1"}, f"cnas_mark={mark}", visual=True)
        return {"cnas_mark": mark}

    monkeypatch.setattr(SourceAudit, "visual_pdf", observe)
    rows = [["中国合格评定国家认可委员会", "CNAS"], ["截止日期", (date.today() + timedelta(days=365)).isoformat()]]
    result = _execute(tmp_path, monkeypatch, "U03", "U03:1", {"资质": rows})
    assert result["status"] == expected, result


def test_qualification_body_reference_cannot_replace_commission_title(tmp_path, monkeypatch):
    from audit.checker_lib.source import SourceAudit

    monkeypatch.setattr(SourceAudit, "visual_pdf", lambda *args, **kwargs: pytest.fail("Unconfirmed title cannot reach CNAS acceptance"))
    rows = [["另一张文件"], ["截止日期", (date.today() + timedelta(days=365)).isoformat()], *[["普通正文"] for _ in range(5)], ["中国合格评定国家认可委员会"], ["CNAS"]]
    result = _execute(tmp_path, monkeypatch, "U03", "U03:1", {"无关文件": rows})
    assert result["status"] == "UNABLE", result


@pytest.mark.parametrize(("second_row", "expected"), [([None, "B", None, None], "NG"), ([None, "B", "供应商乙", None], "NG"), ([None, "=1+1", "供应商乙", None], "UNABLE")])
def test_material_rows_with_missing_serial_are_not_excluded(tmp_path, monkeypatch, second_row, expected):
    rows = [["序号", "材料", "分供方", "备注"], [1, "A", "供应商甲", None], second_row]
    result = _execute(tmp_path, monkeypatch, "U04", "U04:1", {"材料及分供方清单": rows})
    assert result["status"] == expected, result


@pytest.mark.parametrize(
    "footer",
    [
        ["编制人", "张三", None, None],
        [None, "编制人：张三", None, None],
        [None, "供应商负责工程师签字", "张三", None],
        [None, None, None, "只填写备注"],
        ["序号", "材料", "分供方", "备注"],
    ],
)
def test_material_footer_and_repeated_heading_are_not_business_rows(tmp_path, monkeypatch, footer):
    rows = [["序号", "材料", "分供方", "备注"], [1, "A", "供应商甲", None], footer]
    result = _execute(tmp_path, monkeypatch, "U04", "U04:1", {"材料及分供方清单": rows})
    assert result["status"] == "PASS", result


def test_material_footer_does_not_hide_later_incomplete_business_rows(tmp_path, monkeypatch):
    rows = [["序号", "材料", "分供方", "备注"], [1, "A", "供应商甲", None], ["编制人", "张三", None, None], [None, "B", "供应商乙", None]]
    result = _execute(tmp_path, monkeypatch, "U04", "U04:1", {"材料及分供方清单": rows})
    assert result["status"] == "NG", result


def test_photo_decodes_bytes_and_retains_manual_content_review(tmp_path, monkeypatch):
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(stream, format="PNG")
    result = _execute(tmp_path, monkeypatch, "U13", "U13:1", content=stream.getvalue(), suffix=".png")
    assert result["status"] == "PASS", result
    assert "人工" in result["suggestions"]
    invalid = _execute(tmp_path, monkeypatch, "U13", "U13:1", content=b"not an image", suffix=".png")
    assert invalid["status"] in {"ERR", "UNABLE"}, invalid


def _identity():
    return [["项目", "项目A"], ["零件号", "P12345"], ["零件名称", "支架"], ["版本", "A"]]


def test_u01_nonordinal_clause_binds_bubble_matching_and_peer_evidence(tmp_path, monkeypatch):
    sheets = {"尺寸报告": [["尺寸报告"], *_identity()]}
    peer = ("bubble", "气泡图", {"气泡图": [["气泡图"], *_identity()]})
    result = _execute(tmp_path, monkeypatch, "U01", "U01:1-2", sheets, peers=[peer])
    assert result["status"] == "PASS", result
    assert result["evidence_file_version_ids"] == ["bubble", "original"]
    mismatch = _identity()
    mismatch[1] = ["零件号", "P99999"]
    result = _execute(tmp_path, monkeypatch, "U01", "U01:1-2", sheets, peers=[("wrong", "气泡图", {"气泡图": [["气泡图"], *mismatch]})])
    assert result["status"] == "UNABLE", result


@pytest.mark.parametrize(("measurements", "expected"), [([5, 5, 5], "PASS"), ([5, 5, 11], "NG"), ([5, 5, "=1+1"], "UNABLE")])
def test_initial_report_checks_all_four_reports_and_measurements(tmp_path, monkeypatch, measurements, expected):
    rows = [["要求", "供应商实测值", "Sample2", "Sample3", "结果"], ["5±5", *measurements, "OK"]]
    sheets = {title: rows for title in ("尺寸报告", "材料报告", "功能报告", "可靠性报告")}
    result = _execute(tmp_path, monkeypatch, "U01", "U01:2", sheets)
    assert result["status"] == expected, result


def test_native_pdf_capability_table_preserves_original_page_after_excel_preparation(tmp_path, monkeypatch):
    from audit.checker_lib.source import NativeFacts

    original_location = NativeFacts.cell_location
    metadata = []

    def track(native, *args):
        metadata.append(native.data)
        return original_location(native, *args)

    monkeypatch.setattr(NativeFacts, "cell_location", track)
    with fitz.open() as document:
        page = document.new_page(width=460, height=320)
        for y in (20, 70, 120, 170, 220, 270):
            page.draw_line((20, y), (440, y))
        for x in (20, 440):
            page.draw_line((x, 20), (x, 270))
        page.draw_line((280, 120), (280, 270))
        for x, y, text in (
            (40, 50, "过程能力研究"),
            (40, 100, "工艺能力认证报告"),
            (40, 150, "读数次数"),
            (300, 150, "125"),
            (40, 200, "Cpk"),
            (300, 200, "1.34"),
            (40, 250, "Ppk"),
            (300, 250, "1.68"),
        ):
            page.insert_text((x, y), text, fontname="china-s", fontsize=11)
        content = document.tobytes()
    result = _execute(tmp_path, monkeypatch, "U06", "U06:1", content=content, suffix=".pdf")
    assert result["status"] == "PASS", result
    assert result["evidence"]["source_format"] == "pdf"
    assert result["evidence"]["prepared_format"] == "xlsx"
    for source in result["evidence"]["sources"]:
        locator = json.loads(source["locator"])
        assert locator["file_version_id"] == "original"
        assert locator["page"] == 1
        assert locator["kind"] == "pdf_page"
        assert locator["region"]
        assert locator["source_sha256"] == hashlib.sha256(content).hexdigest()
    assert metadata and all(data["prepared_format"] == "xlsx" and data["prepared_sha256"] for data in metadata)
    mapped = [item["source"] for table in metadata[0]["tables"] for item in table["cells"].values() if item.get("source")]
    assert mapped and all(item["bbox"] and item["row"] and item["column"] and item["page"] == 1 for item in mapped)


def test_report_resources_bind_every_clause_to_leaf_business_methods():
    entries = {"U01", "U03", "U04", "U05", "U06", "U09", "U11", "U13", "U18"}
    for suite in load_predefined_checkers():
        if suite.source_path not in {f"UAES/{entry}" for entry in entries}:
            continue
        resource = {}
        exec(suite.files["resources.py"], resource)
        clauses = resource["RESOURCE"]["clauses"]
        assert set(clauses) == set(suite.entrypoints)
        source = suite.files["checker.py"].decode()
        assert "audit.checkers" not in source
        assert "precheck" not in source
        for binding in clauses.values():
            assert f"def {binding['method']}(" in source
