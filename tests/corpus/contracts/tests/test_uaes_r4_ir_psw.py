"""Signed m4 IR/PSW candidates, through frozen execution. Visual responses substituted only at the reading boundary."""

import hashlib
import json
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from agent.execution.audit_runtime import run_audit
from agent.execution.checker_runtime import CheckerRuntime
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import _load_leaf, library_revision
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelPicture
from PIL import Image

BASE = Path(__file__).resolve().parents[4] / "assets/uaes"
LEAVES = BASE / "rulebook/v1.0/candidates/m4"
BATCH = next((BASE / "samples").glob("F01RD0YA40*/测试文件"))
RELEASE = next(BATCH.glob("17 */*.pdf"))
REPORT = next(BATCH.glob("01-1 */*.xls"))
PART = "F01RD0YA4001"
SUPPLIER = "宁波市法莱欣科技有限公司"
TITLES = ("尺寸报告", "材料报告", "功能报告", "可靠性报告")


def finding(leaf, clause, path, refs=(), reader=None):
    suite = _load_leaf(LEAVES, "uaes", LEAVES / "uaes" / leaf)
    digest = checker_digest(suite.program_files)
    binding = RuleBinding(suite.suite_key, digest, {clause: suite.entrypoints[clause]})
    target = FrozenTarget("0", "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, json.loads(suite.files["rules.json"]), binding)
    inputs, files = [], {}
    for i, (p, kind) in enumerate([(path, suite.document_type), *refs]):
        data = p.read_bytes()
        files[str(i)] = data
        inputs.append(FrozenInput(str(i), hashlib.sha256(data).hexdigest(), "reference" if i else "target", p.name, document_type=kind))
    snapshot = ExecutionSnapshot("test", "r4", tuple(inputs), (target,), "test", library_revision(), {})
    [answer] = run_audit(snapshot, files, {digest: suite.program_files}, runtime=reader)["findings"]
    return answer


def _source_rows(path, sheet, rows):
    book = Workbook()
    book.active.title = sheet
    for row in rows:
        book.active.append(row)
    book.save(path)
    return path


def test_repeated_descriptions_batched_once_with_only_actual_effect(monkeypatch, tmp_path):
    from documents.ai import AIResult

    texts = []
    effect = "不影响行车安全；仅使内部支架需返工。"
    header = ["潜在失效模式", "潜在失效原因", "潜在失效后果", "严重度", "频度", "探测度", "目前的探测控制", "RPN"]
    path = _source_rows(tmp_path / "fixture.xlsx", "PFMEA", [header, *[[f"位置偏移{i}", "定位不准", effect, 5, 4, 4, "在线全数复测", 80] for i in range(5)]])

    def read(task, *, prompt, **_):
        assert task == "pfmea_score_cell_facts"
        cells = json.loads(prompt)
        assert set(cells.values()) == {effect, "在线全数复测"}
        texts.append(cells)
        return AIResult({"cells": [{"id": key, "kind": "other", "quote": source} for key, source in cells.items()]})

    monkeypatch.setattr("documents.ai.run_json", read)
    answer = finding("U07", "PFMEA:2", path, reader=CheckerRuntime(allow_model_tools=True))
    assert answer["status"] == "PASS" and len(texts) == 1, answer["suggestions"]
    assert any(effect in source["excerpt"] for source in answer["evidence"]["sources"])


def test_certificate_accreditation_in_issuer_role_is_sufficient(monkeypatch, tmp_path):
    from audit.checker_lib.uaes.native import UaesSourceAgent

    path = _source_rows(
        tmp_path / "certificate.xlsx",
        "认可证书",
        [
            ["截止日期", "2099-05-10"],
            ["被认可机构：星港计量实验室"],
            ["本机构认可依据为ISO/IEC 17025"],
            ["获得中国合格评定国家认可委员会认可，并非英国UKAS认可。"],
        ],
    )
    seen = []

    def observe(reader, fields, **_):
        seen.append(fields)
        assert {"cnas", "ukas", "iso17025", "issuer_seal"} <= fields.keys()
        reader.witness({"kind": "xlsx_cell", "sheet": "认可证书", "cell": "A4"}, "中国合格评定国家认可委员会", visual=True)
        return {key: key in {"iso17025", "issuer_seal", "cnas", "lab_name"} for key in fields}, set()

    monkeypatch.setattr(UaesSourceAgent, "visual_observation", observe)
    answer = finding("U03", "LAB:1", path, reader=CheckerRuntime(allow_model_tools=True))
    assert answer["status"] == "PASS" and "认可标识：CNAS" in answer["suggestions"] and len(seen) == 1, answer["suggestions"]


def test_engineering_approval_reads_only_the_original_cover_remark(monkeypatch):
    from documents.ai import AIResult

    read = []

    def facts(task, *, prompt, schema, **_):
        assert task == "cea_remark_facts" and set(schema["properties"]) == {"coverage", "facts"}
        assert "完全认可" in prompt and "批准的前提" not in prompt and "组长" not in prompt
        read.append(prompt)
        return AIResult({"coverage": "complete", "facts": [{"kind": "full_approval", "scope": "current_approval", "assertion": "affirmed", "quote": prompt}]})

    monkeypatch.setattr("documents.ai.run_json", facts)
    result = finding("U19", "CEA:1", RELEASE, reader=CheckerRuntime(allow_model_tools=True))
    assert result["status"] == "PASS" and len(read) == 1, result["suggestions"]
    assert any("完全认可" in item["excerpt"] and json.loads(item["locator"]).get("bbox") for item in result["evidence"]["sources"])


@pytest.mark.parametrize(
    ("facts", "expected", "relevant"),
    [
        (
            [
                ("full_approval", "affirmed", "current_approval", "完全认可"),
                ("out_of_tolerance", "negated", "current_approval", "本批确认未发生超差"),
            ],
            "full",
            "完全认可；本批确认未发生超差",
        ),
        (
            [
                ("rectification_commitment", "affirmed", "other", "此前承诺整改截止2039-12-31"),
                ("conditional_approval", "affirmed", "current_approval", "本次条件认可"),
                ("rectification_commitment", "affirmed", "current_approval", "本次整改截止2031-02-28"),
            ],
            "conditional",
            "本次条件认可；本次整改截止2031-02-28",
        ),
    ],
)
def test_approval_uses_current_assertions_and_their_own_deadlines(monkeypatch, facts, expected, relevant):
    from audit.checker_lib.uaes.v1.engineering_approval_r4 import _route
    from audit.engine.runtime import bind_runtime
    from documents.ai import AIResult

    reading = [dict(kind=k, assertion=a, scope=s, quote=q) for k, a, s, q in facts]
    monkeypatch.setattr("documents.ai.run_json", lambda *_, **__: AIResult({"coverage": "complete", "facts": reading}))
    with bind_runtime(CheckerRuntime(allow_model_tools=True)):
        assert _route("；".join(row["quote"] for row in reading)) == (expected, relevant)


def picture(kind):
    png = BytesIO()
    Image.new("RGB", (kind + 2, 5), "black").save(png, "PNG")
    png.seek(0)
    return png


def reports(tmp_path, *, signature=True, seal=None, seal_sheet=0, engineer=False, part=PART):
    book = Workbook()
    book.remove(book.active)
    for i, title in enumerate(TITLES):
        ws = book.create_sheet(title)
        ws["A1"], ws["J1"] = "Supplier/供应商：" + SUPPLIER, "Part Number/零件号：" + part
        ws["A2"], ws["B2"], ws["C2"], ws["F2"] = "No.", "Requirements/要求", "Actual-Value Supplier/供应商实测值", "Evaluation"
        ws.merge_cells("C2:E2")
        ws["A3"], ws["B3"], ws["C3"], ws["D3"], ws["E3"], ws["F3"] = 23, "10±0.1", 10.2, 10.0, 10.0, "OK"
        if engineer:
            ws["G2"], ws["G3"] = "顾客工程师", "王工"
        if i == 3:
            # Existing but empty report does not need a signature or count toward the seal.
            for row in ws["A3:F3"]:
                for cell in row:
                    cell.value = None
        ws["B6"], ws["J6"] = "供应商负责人", "客户负责人"
        if i < 3 and (i != 1 or signature):
            ws.add_image(ExcelPicture(picture(1)), "C6")
        if seal is not None and i == seal_sheet:
            ws.add_image(ExcelPicture(picture(seal)), "F6")
    out = tmp_path / "report.xlsx"
    book.save(out)
    return out


class Pictures(CheckerRuntime):
    def read_ocr_visual_facts(self, *, images, **_):
        # Synthetic image widths distinguish the injected observations, not production detection.
        width = Image.open(BytesIO(images[0])).width
        return {"handwritten_signature": width == 3, "ink_seal": width != 3, "issuer": SUPPLIER if width != 6 else "其他有限公司", "seal_type": "inspection" if width == 5 else "company"}


@pytest.mark.parametrize(("seal", "sheet", "signature", "status"), [(2, 0, True, "PASS"), (2, 3, True, "NG"), (2, 0, False, "NG"), (3, 1, True, "UNABLE"), (4, 0, True, "NG")])
def test_stamp_scope_owner_and_open_question(tmp_path, seal, sheet, signature, status):
    result = finding("U01", "IR:3", reports(tmp_path, seal=seal, seal_sheet=sheet, signature=signature), reader=Pictures())
    assert result["status"] == status, result["suggestions"]


def test_original_report_signatures_with_a_visual_substitute():
    class Signatures(CheckerRuntime):
        def read_ocr_visual_facts(self, **_):
            return {"handwritten_signature": True, "ink_seal": False, "issuer": None, "seal_type": None}

    result = finding("U01", "IR:3", REPORT, reader=Signatures())
    assert result["status"] == "NG" and "没有任何有效印章" in result["suggestions"]
    assert "可靠性" not in result["suggestions"]


def test_general_release_with_matching_part_and_supplier_still_not_a_waiver(tmp_path):
    result = finding("U01", "IR:1", reports(tmp_path), [(RELEASE, "E11")])
    assert result["status"] == "NG" and "仅笼统认可" in result["suggestions"], result["suggestions"]
    assert any("完全认可" in s["excerpt"] and json.loads(s["locator"]).get("file_version_id") == "1" for s in result["evidence"]["sources"])


def test_ambiguous_customer_initial_on_the_actual_exceedance(tmp_path):
    result = finding("U01", "IR:1", reports(tmp_path, engineer=True))
    assert result["status"] == "UNABLE", result["suggestions"]


class Form(CheckerRuntime):
    def __init__(self, comment, level):
        super().__init__()
        self.comment, self.level = comment, level

    def read_ocr_visual_facts(self, *, response_schema, **_):
        keys = response_schema["schema"]["properties"]
        out = {k: {"present": True, "value": "2026-08-01"} for k in keys}
        if "explanation" in keys:
            out.update(
                engineering_change_level={"present": True, "value": self.level},
                safety_government_regulation={"present": True, "options": [{"label": "No", "checked": True}]},
                weight={"present": True, "value": "0.25 kg"},
                explanation={"present": True, "value": self.comment},
            )
        if "reason_for_submission" in keys:
            out["reason_for_submission"] = {"present": True, "options": []}
        return {k: {**v, "bbox": [40, 50, 560, 130]} for k, v in out.items()}


@pytest.mark.parametrize(
    ("comment", "value", "level", "status"),
    [
        ("见《附表.xlsx》生产!B2", "生产节拍：20秒", "A", "PASS"),
        ("见《附表.xlsx》生产!B2", None, "A", "NG"),
        ("见《附表.xlsx》生产!B2", "待定", "A", "NG"),
        ("见《附表.xlsx》", "生产节拍：20秒", "A", "UNABLE"),
        ("见《缺件.xlsx》生产!B2", "生产节拍：20秒", "A", "NG"),
        ("见附件", None, "A", "NG"),
        ("生产节拍：20秒", None, "待定", "UNABLE"),
    ],
)
def test_psw_explanation_is_data_not_just_a_reference(tmp_path, comment, value, level, status):
    path = tmp_path / "form.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((32, 32), "Part Submission Warrant")
        doc.save(path)
    appendix = tmp_path / "附表.xlsx"
    book = Workbook()
    book.active.title = "生产"
    book.active["B2"] = value
    book.active["B3"] = "生产节拍：50秒"  # Uncited data in another cell must not rescue an empty cited cell.
    book.save(appendix)
    result = finding("U17", "PSW:1", path, [(appendix, "附件")], Form(comment, level))
    assert result["status"] == status, result["suggestions"]
    if status == "PASS":
        assert any(s["excerpt"] == value and json.loads(s["locator"]).get("file_version_id") == "1" and json.loads(s["locator"]).get("cell") == "B2" for s in result["evidence"]["sources"])


@pytest.mark.parametrize(
    ("part", "item", "supplier", "customer", "expected"),
    [
        (PART, "23", SUPPLIER, "联合汽车电子有限公司", "PASS"),
        ("Qz-007.4", "23", SUPPLIER, "联合汽车电子有限公司", "PASS"),
        ("00042", "23", SUPPLIER, "联合汽车电子有限公司", "PASS"),
        (PART, "123", SUPPLIER, "联合汽车电子有限公司", "NG"),
        (PART, "23", "另一公司", "联合汽车电子有限公司", "NG"),
        (PART, "23", SUPPLIER, "另一客户", "UNABLE"),
    ],
)
def test_specific_acceptance_has_customer_supplier_and_exact_item_scope(tmp_path, part, item, supplier, customer, expected):
    # A newly generated single-page release; no customer's signed document is modified.
    path = tmp_path / "release.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=620, height=620)
        grid = [
            ("C Sample Release Report", "", ""),
            (customer, "", ""),
            ("公司名称\nName：", supplier, ""),
            ("零件号\nPart No.", part, ""),
            ("UAES Condition of approval / remark\n" + f"同意第{item}项尺寸偏差，要求：10±0.1，同意本次实测值", "", ""),
            ("批准：李工程师 2026-09-14\nApproved by：", "", ""),
        ]
        xs = [15, 330, 510, 600]
        for i, cells in enumerate(grid):
            for j, content in enumerate(cells):
                x, y = xs[j], 10 + i * 60
                page.draw_rect(fitz.Rect(x, y, xs[j + 1], y + 60))
                for k, line in enumerate(content.splitlines()):
                    page.insert_text((x + 3, y + 14 + k * 17), line, fontsize=9, fontname="china-s" if any(ord(v) > 127 for v in line) else "helv")
        doc.save(path)
    result = finding("U01", "IR:1", reports(tmp_path, part=part), [(path, "E11")])
    assert result["status"] == expected, result["suggestions"]
    if expected == "PASS":
        assert any(source["excerpt"] == part and json.loads(source["locator"]).get("file_version_id") == "1" for source in result["evidence"]["sources"])


def test_ir_reads_item_decisions_in_a_customer_letter_as_one_observation(tmp_path, monkeypatch):
    from documents.ai import AIResult
    from docx import Document
    from openpyxl import load_workbook

    part = "QZ-504"
    report = reports(tmp_path, part=part)
    book = load_workbook(report)
    for col, value in enumerate((42, "2±0.02", 2.04, 2.0, 2.0), 1):
        book["尺寸报告"].cell(4, col, value)
    book.save(report)
    permits = []
    for index, spec in (("23", "10±0.1"), ("42", "2±0.02")):
        letter = Document()
        letter.add_paragraph("工程例外处理通知")
        letter.add_paragraph("签发单位：UAES")
        letter.add_paragraph("本次放行件号：" + part)
        letter.add_paragraph("第" + index + "项偏离原要求" + spec + "，本批实测差异获准放行。")
        path = tmp_path / f"通知{index}.docx"
        letter.save(path)
        permits.append((path, "E11"))

    called = []

    def observe(task, *, prompt, **_):
        assert task == "uaes_ir_release"
        sources = json.loads(prompt)
        called.append(task)
        decisions = []
        for index, spec in (("23", "10±0.1"), ("42", "2±0.02")):
            stem, text = next((k.split(":")[0], v) for k, v in sources.items() if v.startswith("第" + index + "项"))

            def quote(value):
                return {"source": next(k for k, v in sources.items() if k.startswith(stem + ":") and value in v), "value": value}

            decisions.append(dict(issuer=quote("UAES"), part=quote(part), supplier=None, item=quote(index), specification=quote(spec), statement=quote(text), action="accepted"))
        return AIResult({"coverage": "complete", "decisions": decisions})

    monkeypatch.setattr("documents.ai.run_json", observe)
    result = finding("U01", "IR:1", report, permits, reader=CheckerRuntime(allow_model_tools=True))
    assert (result["status"], len(called)) == ("PASS", 1), result["suggestions"]
    locations = [json.loads(row["locator"]) for row in result["evidence"]["sources"] if "获准放行" in row["excerpt"]]
    assert {row["file_version_id"] for row in locations} == {"1", "2"}
    assert all(row["kind"] == "docx_paragraph" and row["source_sha256"] for row in locations)


def test_psw_native_document_header_after_an_annex(tmp_path):
    """Read the uploaded warrant page, not the earlier attachment; preserve label/value locations."""
    pdf = tmp_path / "submission.pdf"
    with fitz.open() as doc:
        annex = doc.new_page()
        annex.insert_text((44, 80), "Attachment: Approval record")
        annex.insert_text((44, 130), "Customer Part Number: OLD-439")
        body = doc.new_page()
        body.insert_text((44, 89), "Part Submission Warrant")
        body.insert_text((208, 190), "Customer Part Number: PZ-39.8")
        body.insert_text((60, 275), "Part Name: Sleeve")
        doc.save(pdf)
    plan = tmp_path / "plan.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Control plan"
    sheet["A1"], sheet["A2"], sheet["A5"] = "Part Number: PZ-39.8", "Part Name: Sleeve", "工序名称"
    book.save(plan)
    result = finding("U17", "PSW:7", pdf, [(plan, "控制计划")], reader=CheckerRuntime())
    assert result["status"] == "PASS", result["suggestions"]
    found = [(s["excerpt"], json.loads(s["locator"])) for s in result["evidence"]["sources"]]
    original = {v: loc for v, loc in found if loc.get("fact") in {"part_name", "customer_part_number"} and loc.get("engine") == "native_pdf"}
    assert set(original) == {"PZ-39.8", "Sleeve"}
    for value, loc in original.items():
        assert loc["page"] == 2 and loc["source_sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
        with fitz.open(pdf) as doc:
            assert value in doc[1].get_textbox(fitz.Rect(loc["bbox"]))


def test_psw_scanned_continuation_and_authorization_have_separate_page_evidence(tmp_path):
    # Field extraction double only. The three original pages, their layout/roles and exact source locators are real.
    from PIL import ImageDraw

    raster = Image.new("RGB", (600, 420), "white")
    pen = ImageDraw.Draw(raster)
    pen.text((30, 36), "Part Submission Warrant - continued", fill="black")
    pen.text((30, 80), "Requested Submission Level   [X] Level 4", fill="black")
    pen.text((30, 340), "Page 2 of 2", fill="black")
    buf = BytesIO()
    raster.save(buf, "PNG")
    path = tmp_path / "document.pdf"
    with fitz.open() as doc:
        body = doc.new_page(width=400, height=600)
        body.insert_text((30, 85), "Part Submission Warrant")
        body.insert_text((95, 190), "Customer Part Number: PZ-39.8")
        body.insert_text((60, 440), "Page 1 of 2")
        annex = doc.new_page(width=320, height=600)
        annex.insert_text((40, 50), "Annex: Authorization record")
        annex.insert_text((40, 120), "Requested Submission Level: Level 1")
        scan = doc.new_page(width=600, height=420)
        scan.insert_image(scan.rect, stream=buf.getvalue())
        doc.save(path)

    class Pages(CheckerRuntime):
        def read_ocr_visual_facts(self, *, images, response_schema, **unused):
            with Image.open(BytesIO(images[0])) as view:
                landscape = view.width > view.height
                assert abs(view.width / view.height - 320 / 600) > 0.02  # authorization page is never supplied as warrant evidence
            fields = response_schema["schema"]["properties"]
            if "heading" in fields:
                assert landscape
                return {"heading": "Part Submission Warrant - continued", "bbox": [42, 65, 450, 140], "pagination": "Page 2 of 2"}
            result = {k: {"present": False} for k in fields}
            if landscape and "requested_submission_level" in result:
                result["requested_submission_level"] = {"present": True, "options": [{"label": "Level 4", "checked": True}], "bbox": [45, 165, 480, 250]}
            return result

    result = finding("U17", "PSW:5", path, reader=Pages())
    assert result["status"] == "PASS", result["suggestions"]
    locations = [(s["excerpt"], json.loads(s["locator"])) for s in result["evidence"]["sources"]]
    [(value, loc)] = [(value, loc) for value, loc in locations if loc.get("fact") == "requested_submission_level"]
    assert value == "Level 4" and (loc["page"], loc["bbox"]) == (3, [27, 69.3, 288, 105])


def test_psw_vector_signature_and_stamp_keep_their_original_page(tmp_path):
    path = tmp_path / "signed.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((32, 64), "Annex: Authorization record")
        page = doc.new_page(width=400, height=400)
        page.insert_text((20, 40), "Part Submission Warrant")
        page.insert_text((20, 222), "Supplier Authorized Signature")
        page.draw_polyline([(185, 220), (189, 205), (191, 225), (205, 210)])
        page.draw_circle((65, 76), 15)
        page.insert_text((55, 80), "QC")
        for row, value in enumerate(("Print Name: A Tester", "Title: Engineer", "Phone: 123456789", "Email: a@example.com", "Date: 2026-02-10")):
            page.insert_text((30, 240 + row * 18), value)
        doc.save(path)

    class Marks(CheckerRuntime):
        def read_ocr_visual_facts(self, *, response_schema, **unused):
            fields = response_schema["schema"]["properties"]
            if "supplier_authorized_signature" in fields:
                values = {"print_name": "A Tester", "title": "Engineer", "phone": "123456789", "email": "a@example.com", "signed_date": "2026-02-10"}
                answer = {k: {"present": True, "value": v, "bbox": [65, 565 + i * 45, 525, 612 + i * 45]} for i, (k, v) in enumerate(values.items())}
                answer["title"]["role_as_written"] = "Engineer"
                answer["supplier_authorized_signature"] = {"present": True, "field_box": [45, 475, 560, 575], "bbox": [45, 475, 560, 575]}
                return answer
            if "entire_field" in fields:
                assert fields["mark"]["type"] == "string"
                return {"label": "Supplier Authorized Signature", "entire_field": True, "mark": "handwriting", "detail": "签字栏右侧有折线笔迹"}
            if "fully_examined" in fields:
                return {"fully_examined": True, "candidates": [[115, 245, 210, 438]]}
            assert fields["kind"]["type"] == "string"
            return {"kind": "imprint", "detail": "QC 圆形印迹"}

    result = finding("U17", "PSW:3", path, reader=Marks())
    assert result["status"] == "PASS", result["suggestions"]
    marks = [json.loads(s["locator"]) for s in result["evidence"]["sources"] if json.loads(s["locator"]).get("fact") in {"supplier_authorized_signature", "ink_seal"}]
    assert {m["fact"] for m in marks} == {"supplier_authorized_signature", "ink_seal"}
    assert all(m["kind"] == "pdf_region" and m["page"] == 2 and m["engine"] == "ocr_llm" for m in marks)


@pytest.mark.parametrize(("mark", "status", "reason"), [("printed_logo", "NG", "原件上未找到印章"), ("uncertain", "UNABLE", "原件上是否有印章")])
def test_psw_seal_absent_only_when_every_page_was_looked_at_whole(tmp_path, mark, status, reason):
    path = tmp_path / "signed.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((32, 64), "Annex: Authorization record")
        page = doc.new_page(width=400, height=400)
        page.insert_text((20, 40), "Part Submission Warrant")
        page.draw_circle((65, 76), 15)
        page.insert_text((20, 222), "Supplier Authorized Signature")
        page.draw_polyline([(185, 220), (189, 205), (191, 225), (205, 210)])
        for row, value in enumerate(("Print Name: A Tester", "Title: Engineer", "Phone: 123456789", "Email: a@example.com", "Date: 2026-02-10")):
            page.insert_text((30, 240 + row * 18), value)
        doc.save(path)
    signs = []

    class Marks(CheckerRuntime):
        def read_ocr_visual_facts(self, *, response_schema, **unused):
            fields = response_schema["schema"]["properties"]
            if "supplier_authorized_signature" in fields:
                values = {"print_name": "A Tester", "title": "Engineer", "phone": "123456789", "email": "a@example.com", "signed_date": "2026-02-10"}
                answer = {k: {"present": True, "value": v, "bbox": [65, 565 + i * 45, 525, 612 + i * 45]} for i, (k, v) in enumerate(values.items())}
                answer["title"]["role_as_written"] = "Engineer"
                # The line's box is one row low: its crop shows the printed name below the signature.
                answer["supplier_authorized_signature"] = {"present": True, "field_box": [45, 565, 560, 610], "bbox": [45, 565, 560, 610]}
                return answer
            if "entire_field" in fields:
                signs.append(1)
                if len(signs) == 1:
                    return {"label": "Print Name", "entire_field": True, "mark": "printed_text", "detail": "A Tester"}
                return {"label": "Supplier Authorized Signature", "entire_field": True, "mark": "handwriting", "detail": "签字栏右侧有折线笔迹"}
            if "fully_examined" in fields:
                return {"fully_examined": False, "candidates": [[115, 145, 260, 338]]}
            return {"kind": mark, "detail": "圆形图案"}

    result = finding("U17", "PSW:3", path, reader=Marks())
    assert (result["status"], reason in result["suggestions"]) == (status, True), result["suggestions"]
    assert "是否手写" not in result["suggestions"] and len(signs) == 2, result["suggestions"]
    if status == "NG":
        notes = [loc for loc in (json.loads(s["locator"]) for s in result["evidence"]["sources"]) if loc.get("fact") == "ink_seal"]
        assert {note["page"] for note in notes} == {1, 2}, notes
