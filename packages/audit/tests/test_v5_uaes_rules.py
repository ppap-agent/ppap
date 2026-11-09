"""Native UAES PFMEA, control plan, characteristics and flow clauses in Core."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest
from audit.checker_core import FrozenInput, FrozenTarget, RuleBinding, checker_digest, validate_results
from audit.checker_lib.source import SourceAudit
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.standard_library import check_factory
from audit.standard_suites import load_predefined_checkers
from openpyxl import Workbook


def _xlsx(sheets):
    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def _run(tmp_path, monkeypatch, entry, clause=None, sheets=None, *, peers=(), original=None, real_text=False):
    """The real leaf owns business results; only external visual facts are unknown."""
    suite = next(item for item in load_predefined_checkers() if item.source_path == f"v5/UAES/{entry}")
    resources = ModuleType("resources")
    exec(suite.files["resources.py"], resources.__dict__)
    monkeypatch.setitem(sys.modules, "resources", resources)
    name = f"uaes_native_rules_{entry}"
    checker = ModuleType(name)
    monkeypatch.setitem(sys.modules, name, checker)
    exec(suite.files["checker.py"], checker.__dict__)
    body = json.loads(suite.files["rules.json"])
    if clause:
        body["rules"] = [row for row in body["rules"] if row["id"] == clause]
        body["checker_binding"] = {clause: suite.entrypoints[clause]}
    content = original.read_bytes() if original else _xlsx(sheets)
    filename = original.name if original else entry + ".xlsx"
    path = tmp_path / filename
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    audit = AuditInput(audit_id="original", file_url=str(path), customer="UAES", project_id="project", file_metadata={"source_sha256": digest, "original_filename": filename})
    rule = RuleInput(rule_id="frozen-rule", **body)
    frozen = [FrozenInput(audit.audit_id, digest, "target", path.name)]
    grouped = {}
    for identity, kind, peer_sheets in peers:
        peer_path = tmp_path / (identity + ".xlsx")
        peer_content = _xlsx(peer_sheets)
        peer_path.write_bytes(peer_content)
        peer_digest = hashlib.sha256(peer_content).hexdigest()
        peer = AuditInput(audit_id=identity, file_url=str(peer_path), customer="UAES", project_id="project", file_metadata={"source_sha256": peer_digest})
        grouped.setdefault(kind, []).append(peer)
        frozen.append(FrozenInput(identity, peer_digest, "reference", peer_path.name))
    group = GroupContextInput(by_doc_type=grouped, by_audit_id={peer.audit_id: peer for values in grouped.values() for peer in values})
    target = FrozenTarget(audit.audit_id, "revision", suite.suite_key, suite.revision_digest, body, RuleBinding(suite.suite_key, checker_digest(suite.program_files), body["checker_binding"]))
    visual_requests = []

    def unknown_visual(owner, fields, **_options):
        visual_requests.append(dict(fields))
        return dict.fromkeys(fields)

    monkeypatch.setattr(SourceAudit, "visual_pdf", unknown_visual)
    original_text_result = SourceAudit.text_result

    def text_result(owner, *args, **kwargs):
        visual_requests.append({"__text__": True})
        if real_text:
            return original_text_result(owner, *args, **kwargs)
        return "UNABLE", "本测试未提供文本语义事实。"

    monkeypatch.setattr(SourceAudit, "text_result", text_result)
    opened = []
    context = SimpleNamespace(audit_file=audit, rule=rule, target=target, group_context=group, cache={}, on_close=opened.append, clause_id=None)
    try:
        observed = []
        for cid in target.binding.entrypoints:
            context.clause_id = cid
            row = check_factory(context, checker.create_audit).model_dump(mode="json")
            sources = (row.get("evidence") or {}).get("sources", [])
            row["evidence_file_version_ids"] = sorted({audit.audit_id, *(json.loads(source["locator"])["file_version_id"] for source in sources)})
            observed.append(row)
        checked = validate_results(observed, target=target, inputs=frozen)
        assert len(checked["findings"]) == len(body["rules"])
        assert all(row["coverage"] == "mapped" for row in checked["coverage"])
        findings = {row["clause_id"]: row for row in checked["findings"]}
        for row in findings.values():
            if row["status"] == "PASS":
                assert row["evidence"]["sources"], row
        return (findings[clause] if clause else findings), visual_requests
    finally:
        for value in reversed(opened):
            value.close()


def _identity(*, part="P12345", revision="A"):
    return [["项目", "项目A"], ["零件号", part], ["零件名称", "支架"], ["版本", revision]]


@pytest.mark.parametrize(("number", "name", "expected"), [("P12345", "支架", "PASS"), (None, "支架", "NG"), ("P12345", None, "NG"), ("=1+1", "支架", "UNABLE")])
def test_special_characteristics_requires_actual_material_identity(tmp_path, monkeypatch, number, name, expected):
    result, requests = _run(tmp_path, monkeypatch, "U12", "U12:1", {"特殊特性清单": [["物料名称", name], ["物料编号", number]]})
    assert result["status"] == expected, result
    assert not requests


@pytest.mark.parametrize(
    ("extra_row", "expected"),
    [([2, "高度", "20±0.2", "SC"], "PASS"), ([None, "高度", "20±0.2", "SC"], "NG"), ([2, "高度", None, "SC"], "NG"), (["=1+1", "高度", "20±0.2", "SC"], "UNABLE")],
)
def test_special_characteristics_checks_second_physical_row(tmp_path, monkeypatch, extra_row, expected):
    rows = [["编号", "产品特性", "特性及公差", "特性类别"], [1, "孔径", "10±0.1", "CC"], extra_row]
    result, _ = _run(tmp_path, monkeypatch, "U12", "U12:2", {"特殊特性清单": rows})
    assert result["status"] == expected, result


def test_special_characteristics_missing_column_is_ng_not_silent_pass(tmp_path, monkeypatch):
    rows = [["编号", "产品特性", "特性及公差"], [1, "孔径", "10±0.1"]]
    result, _ = _run(tmp_path, monkeypatch, "U12", "U12:2", {"特殊特性清单": rows})
    assert result["status"] == "NG", result


@pytest.mark.parametrize(
    ("text", "request_count"),
    [("历史问题：2025年孔径偏小造成总装返工", 1), ("历史问题", 0), ("例如历史问题：2025年孔径偏小造成总装返工", 0), ("历史问题：未发现相关历史问题或客户抱怨", 1)],
)
def test_pfmea_needs_actual_lesson_content_not_template_label(tmp_path, monkeypatch, text, request_count):
    result, requests = _run(tmp_path, monkeypatch, "U07", "U07:1", {"PFMEA": [[text]]})
    assert result["status"] == "UNABLE", result
    assert len(requests) == request_count


@pytest.mark.parametrize(
    ("severity", "detection", "effect", "expected"),
    [
        (3, 10, "安全风险", "NG"),
        (9, 5, "安全风险", "NG"),
        (0, 10, "外观", "NG"),
        (9, 10, "安全风险", "UNABLE"),
        (3, 10, "仅轻微外观问题，不影响安全与法规", "UNABLE"),
        (9, 10, "非轻微外观问题", "UNABLE"),
    ],
)
def test_pfmea_source_scores_fail_deterministically_before_semantic_visuals(tmp_path, monkeypatch, severity, detection, effect, expected):
    rows = [["S", "O", "D", "失效后果", "失效模式", "失效原因", "预防控制", "探测控制"], [severity, 2, detection, effect, "断裂", "材料", "材料验证", "无"]]
    result, requests = _run(tmp_path, monkeypatch, "U07", "U07:2", {"PFMEA": rows})
    assert result["status"] == expected, result
    assert len(requests) == (1 if expected == "UNABLE" else 0)


@pytest.mark.parametrize(("part", "revision", "expected"), [("P12345", "A", "PASS"), ("P99999", "A", "UNABLE"), ("P12345", "B", "UNABLE")])
def test_pfmea_special_characteristics_match_frozen_part_and_revision(tmp_path, monkeypatch, part, revision, expected):
    target = {"PFMEA": [["PFMEA"], *_identity(), ["功能/要求", "特殊特性"], ["孔径", "CC"]]}
    peer = ("characteristics", "特殊特性", {"特殊特性清单": [["特殊特性清单"], *_identity(part=part, revision=revision), ["特性名称", "特性类别"], ["孔径", "CC"]]})
    result, _ = _run(tmp_path, monkeypatch, "U07", "U07:3", target, peers=[peer])
    assert result["status"] == expected, result
    if expected == "PASS":
        assert result["evidence_file_version_ids"] == ["characteristics", "original"]


@pytest.mark.parametrize(("control", "expected"), [("SPC X-R图", "PASS"), ("人工抽检", "NG"), ("=1+1", "UNABLE")])
def test_control_plan_runway_requires_source_spc_control(tmp_path, monkeypatch, control, expected):
    rows = [["工序名称", "特性名称", "控制方法"], ["磨削", "跑道直径", control]]
    result, _ = _run(tmp_path, monkeypatch, "U08", "U08:6", {"控制计划": rows})
    assert result["status"] == expected, result


@pytest.mark.parametrize("clause", ["U08:7", "U08:10", "U08:11", "U08:12", "U08:13"])
def test_control_plan_missing_independent_drawing_or_pgqp1_cannot_pass(tmp_path, monkeypatch, clause):
    result, _ = _run(tmp_path, monkeypatch, "U08", clause, {"控制计划": [["控制计划"], *_identity()]})
    assert result["status"] == "NG", result


@pytest.mark.parametrize(("change_date", "expected"), [("2026-01-02", "PASS"), ("2026-01-01", "NG"), ("日期无法确认", "UNABLE")])
def test_control_plan_last_change_must_follow_matching_drawing_date(tmp_path, monkeypatch, change_date, expected):
    header = [["控制计划"], *_identity(), ["供应商名称", "供应商A"], ["供应商代码", "S1"], ["编制人", "张"], ["受控日期", "2026-01-03"]]
    target = {"控制计划": header, "变更履历": [["变更日期"], [change_date]]}
    drawing = ("drawing", "图纸", {"图纸": [["图纸"], *_identity(), ["日期", "2026-01-01"]]})
    result, _ = _run(tmp_path, monkeypatch, "U08", "U08:7", target, peers=[drawing])
    assert result["status"] == expected, result


def test_control_plan_covers_matching_pgqp1_outgoing_dimensions(tmp_path, monkeypatch):
    target = {"控制计划": [["控制计划"], *_identity(), ["工序名称", "特性名称"], ["出货检验", "孔径"]]}
    plan = ("plan", "采购件质量计划", {"PGQP1": [["PGQP1"], *_identity(), ["特性名称", "特性类型"], ["孔径", "尺寸"]]})
    result, _ = _run(tmp_path, monkeypatch, "U08", "U08:10", target, peers=[plan])
    assert result["status"] == "PASS", result
    assert result["evidence_file_version_ids"] == ["original", "plan"]


@pytest.mark.parametrize(("frequency", "expected"), [("SPC", "PASS"), ("每小时一次", "NG")])
def test_control_plan_uses_drawing_cpk_inspection_method_for_spc_requirement(tmp_path, monkeypatch, frequency, expected):
    target = {"控制计划": [["控制计划"], *_identity(), ["工序名称", "特性名称", "频率"], ["磨削", "孔径", frequency]]}
    drawing = ("drawing", "图纸", {"图纸": [["图纸"], *_identity(), ["尺寸", "特殊特性", "检验方法"], ["孔径", "CC", "CPK"]]})
    result, _ = _run(tmp_path, monkeypatch, "U08", "U08:12", target, peers=[drawing])
    assert result["status"] == expected, result


def test_control_plan_spc_in_control_method_does_not_replace_required_frequency(tmp_path, monkeypatch):
    target = {"控制计划": [["控制计划"], *_identity(), ["工序名称", "特性名称", "控制方法", "频率"], ["磨削", "孔径", "SPC", "每小时一次"]]}
    drawing = ("drawing", "图纸", {"图纸": [["图纸"], *_identity(), ["尺寸", "特殊特性", "检验方法"], ["孔径", "CC", "CPK"]]})
    result, _ = _run(tmp_path, monkeypatch, "U08", "U08:12", target, peers=[drawing])
    assert result["status"] == "NG", result


@pytest.mark.parametrize(("relation", "expected"), [("supports", "PASS"), ("contradicts", "NG"), ("unknown", "UNABLE")])
@pytest.mark.parametrize(("entry", "clause"), [("U07", "U07:1"), ("U07", "U07:2"), ("U08", "U08:2")])
def test_semantic_clauses_use_configured_text_client_with_original_quotes(tmp_path, monkeypatch, entry, clause, relation, expected):
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        payload = json.loads(kwargs["user_prompt"])
        source = next((item for item in payload["sources"] if "历史问题" in item["text"] or "追溯" in item["text"]), payload["sources"][-1])
        return json.dumps({"relation": relation, "scope": "local", "citations": [{"id": source["id"], "quote": source["text"]}], "explanation": "按所引原件内容核对当前条款。"}, ensure_ascii=False)

    monkeypatch.setattr("audit.engine.checkers.client.AIClient", lambda: SimpleNamespace(structured_completion=request))
    if clause == "U07:1":
        sheets = {"PFMEA": [["历史问题：2025年孔径偏小造成总装返工，已追加失效模式及预防措施。"]]}
    elif clause == "U07:2":
        rows = [["S", "O", "D", "失效后果", "失效模式", "失效原因", "预防控制", "探测控制"], [9, 2, 10, "安全风险", "断裂", "材料", "材料验证", "无"]]
        sheets = {"PFMEA": rows}
    else:
        sheets = {"控制计划": [["批次号关联生产工单完成追溯，按入库时间先进先出。"]]}
    result, requests = _run(tmp_path, monkeypatch, entry, clause, sheets, real_text=True)
    assert result["status"] == expected, result
    assert len(calls) == 1 and requests == [{"__text__": True}]
    assert "image_base64_list" not in calls[0]
    assert calls[0]["operation"] == "rule_text_evidence"
    assert calls[0]["request_timeout"] == 20 and calls[0]["retry_limit"] == 0
    for source in result["evidence"]["sources"]:
        assert source["kind"] == "structure"
        assert json.loads(source["locator"])["file_version_id"] == "original"


def test_pdf_text_semantics_follow_native_excel_preparation(tmp_path, monkeypatch):
    import audit.pdf_tables
    import fitz

    events = []
    prepare = audit.pdf_tables.prepare_pdf_workbook

    def prepared(*args, **kwargs):
        events.append("pdf-to-excel")
        return prepare(*args, **kwargs)

    def request(**kwargs):
        events.append("text")
        payload = json.loads(kwargs["user_prompt"])
        source = next(item for item in payload["sources"] if "追溯" in item["text"])
        return json.dumps({"relation": "supports", "scope": "local", "citations": [{"id": source["id"], "quote": source["text"]}], "explanation": "原文说明了追溯和先进先出措施。"}, ensure_ascii=False)

    monkeypatch.setattr(audit.pdf_tables, "prepare_pdf_workbook", prepared)
    monkeypatch.setattr("audit.engine.checkers.client.AIClient", lambda: SimpleNamespace(structured_completion=request))
    path = tmp_path / "native-control.pdf"
    with fitz.open() as document:
        page = document.new_page(width=600, height=200)
        for y in (20, 70, 150):
            page.draw_line((20, y), (580, y))
        for x in (20, 580):
            page.draw_line((x, 20), (x, 150))
        page.insert_text((40, 50), "控制计划", fontname="china-s", fontsize=11)
        page.insert_text((40, 110), "批次号关联生产工单完成追溯，按入库时间先进先出。", fontname="china-s", fontsize=11)
        document.save(path)
    result, requests = _run(tmp_path, monkeypatch, "U08", "U08:2", original=path, real_text=True)
    assert result["status"] == "PASS", result
    assert events == ["pdf-to-excel", "text"]
    assert requests == [{"__text__": True}]
    assert result["evidence"]["prepared_format"] == "xlsx"
    assert all(json.loads(source["locator"])["page"] == 1 for source in result["evidence"]["sources"])


@pytest.mark.parametrize("clause", ["U16:1", "U16:2", "U16:3"])
def test_flow_native_symbol_labels_cannot_replace_actual_graphic_facts(tmp_path, monkeypatch, clause):
    result, requests = _run(tmp_path, monkeypatch, "U16", clause, {"过程流程图": [["过程流程图", "K表示关键过程"], ["受控章", "检验符号"]]})
    assert result["status"] == "UNABLE", result
    assert len(requests) == 1
