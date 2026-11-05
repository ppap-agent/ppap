"""Field identity and scope prevent evidence from a neighbouring field passing a rule."""

from io import BytesIO
from types import SimpleNamespace

import fitz
import pytest
from audit.checkers.v2.psw_excel_agent import PswExcelAgent as V2Psw
from audit.checkers.v3.control_plan_agent import ControlPlanAgent
from audit.checkers.v3.psw_excel_agent import PswExcelAgent as V3Psw
from audit.checkers.v4.visual_facts import _pdf_images
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.engine.checkers.dimension_measurement_policy import check_tolerance
from audit.engine.runtime import bind_runtime
from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorkbookImage
from PIL import Image, ImageDraw


@pytest.mark.parametrize("agent_type,profile", [(V2Psw, "v2"), (V3Psw, "v3")])
def test_company_stamp_and_printed_name_cannot_fill_the_supplier_signature(tmp_path, agent_type, profile):
    path = tmp_path / "warrant.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "零件提交保证书"
    sheet["A25"] = "经授权的供方代表签字"
    sheet["D25"] = "日期"
    sheet["A26"] = "代表印刷体姓名"
    sheet["B26"] = "张工"
    sheet["H22"] = "公司名称（公司章）"
    image = Image.new("RGB", (300, 300), "white")
    ImageDraw.Draw(image).ellipse((100, 100, 230, 230), outline=(220, 0, 0), width=18)
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    sheet.add_image(WorkbookImage(encoded), "H23")
    book.save(path)
    book.close()
    agent = agent_type(AuditInput(audit_id="warrant", file_url=str(path)), RuleInput(rule_id="psw", type="零件提交保证书", rule_profile=profile, rules=[]))
    try:
        with bind_runtime(None):
            result = agent.check(4)
        assert result.status == "NG"
        assert "签字" in result.suggestions
    finally:
        agent.close()


def test_submission_choices_crop_stops_before_submission_results(tmp_path):
    path = tmp_path / "warrant.pdf"
    with fitz.open() as book:
        page = book.new_page(width=600, height=800)
        for y, text in ((100, "提交原因"), (130, "□首次提交"), (220, "提交结果"), (250, "☑尺寸测量")):
            page.insert_text((40, y), text, fontname="china-s", fontsize=12)
        book.save(path)
    images, context = _pdf_images(SimpleNamespace(file_url=str(path), audit_id="warrant"), [1], "frozen", {"submission_any_checked": "提交原因实际选中"})
    assert "首次提交" in context["pdf:1"]
    assert "提交结果" not in context["pdf:1"]
    assert all(image.locator["bbox"][3] <= 220 for image in images)


def test_explicit_measurement_unit_cannot_be_discarded():
    result = check_tolerance("10±0.5mm", ("10kg",), "OK", "卡尺")
    assert result.status != "PASS"
    assert "单位" in result.reason


@pytest.mark.parametrize("part,plan_operation,expected", [("P0019", "10", "NG"), ("P001", "100", "NG"), ("P001", "10", "PASS")])
def test_part_and_operation_numbers_bind_the_corresponding_records(tmp_path, part, plan_operation, expected):
    paths = []
    for name, part, headers in (("控制计划", "P001", ["零件/工序号", "工序名称", "产品", "过程", "特殊特性分类", "控制方法", "频率"]), ("过程流程图", part, ["工序号", "工序名称"])):
        path = tmp_path / (name + ".xlsx")
        book = Workbook()
        sheet = book.active
        sheet.title = name
        sheet.append(["零件号", part])
        for col, text in enumerate(headers, 1):
            sheet.cell(4, col, text)
        sheet.cell(5, 1, plan_operation if name == "控制计划" else "10")
        sheet.cell(5, 2, "切割")
        book.save(path)
        book.close()
        paths.append(path)
    flow = AuditInput(audit_id="flow", file_url=str(paths[1]))
    context = GroupContextInput(by_doc_type={"过程流程图": [flow]}, by_audit_id={"flow": flow})
    agent = ControlPlanAgent(AuditInput(audit_id="plan", file_url=str(paths[0])), RuleInput(rule_id="cp", type="控制计划", rule_profile="v3", rules=[]), group_context=context)
    try:
        with bind_runtime(None):
            result = agent.check(7)
        assert result.status == expected
        if part == "P0019":
            assert "同零件号" in result.suggestions
        elif plan_operation == "100":
            assert "未体现" in result.suggestions
        if expected == "PASS":
            import json

            located = [json.loads(source.locator) for source in result.evidence.sources]
            assert {item["file_version_id"] for item in located} == {"flow", "plan"}
            from openpyxl import load_workbook

            changed = load_workbook(paths[1])
            changed.active["B1"] = "P0019"
            changed.save(paths[1])
            changed.close()
            with bind_runtime(None):
                stale = agent.check(7)
            assert stale.status == "ERR"
            assert "冻结关联资料已变化" in stale.suggestions
    finally:
        agent.close()


@pytest.mark.parametrize("measurement,unit,expected", [("1cm", "mm", "PASS"), ("0.01m", "mm", "PASS"), ("10kg", "mm", "NG"), ("10furlong", "mm", "UNABLE")])
def test_measurement_units_use_the_bound_record_unit(measurement, unit, expected):
    assert check_tolerance("10±0.5", (measurement,), "OK", "卡尺", unit=unit).status == expected


@pytest.mark.parametrize("measurement,expected", [("1cm", "PASS"), ("10kg", "NG"), ("10furlong", "UNABLE")])
def test_numeric_public_check_preserves_the_record_unit_and_both_source_cells(tmp_path, measurement, expected):
    import json

    from audit.checkers.v2.dimension_measurement_excel_agent import DimensionMeasurementExcelAgent

    path = tmp_path / "measurement.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "尺寸检验报告"
    sheet.append(["序号", "检查项目", "判定标准", "单位", "供方检查方法/检查工具", "检查数据", None, "判定结果"])
    sheet.append(["尺寸"])
    sheet.append([1, "直径", "10±0.5", "mm", "卡尺", measurement, None, "OK"])
    book.save(path)
    book.close()
    agent = DimensionMeasurementExcelAgent(
        AuditInput(audit_id="measurement", file_url=str(path), file_metadata={"file_version_id": "measurement-v1"}), RuleInput(rule_id="dimension", type="尺寸检验报告", rule_profile="v2", rules=[])
    )
    try:
        result = agent.check(2)
        assert result.status == expected
        located = [json.loads(source.locator) for source in result.evidence.sources]
        assert {item["file_version_id"] for item in located} == {"measurement-v1"}
        assert {"C3", "D3", "F3"}.issubset({item["cell"] for item in located})
    finally:
        agent.close()


def test_standard_and_header_units_keep_their_own_meaning():
    assert check_tolerance("10±0.5mm", (1,), "OK", "卡尺", unit="cm").status == "PASS"
    assert check_tolerance("10±0.5mm", (1.1,), "OK", "卡尺", unit="cm").status == "NG"
    assert check_tolerance("10±0.5mm", (1,), "OK", "卡尺", unit="kg").status == "NG"
    assert check_tolerance("等级2级", ("OK",), "OK", "检具").status == "PASS"
    assert check_tolerance("10±0.5mm", ("10min",), "OK", "卡尺").status == "NG"


@pytest.mark.parametrize("label,left,right", [("零件号", "ABC/123", "ABC/124"), ("零件号/最新更改水平", "P001/A01", "P002/A01")])
def test_compound_identity_does_not_match_a_shared_prefix_or_change_level(label, left, right):
    from audit.checkers.v3.cross_file_extractors import part_numbers_compatible, read_part_number_keys

    books = [Workbook(), Workbook()]
    try:
        for book, value in zip(books, (left, right)):
            book.active.append([label, value])
        assert not part_numbers_compatible(read_part_number_keys(books[0].active), read_part_number_keys(books[1].active))
    finally:
        for book in books:
            book.close()


def test_same_part_with_explicit_different_change_levels_is_not_the_same_object(tmp_path):
    from audit.checkers.v3.cross_file_extractors import check_reference_identity
    from audit.engine.rule_engine import GuardStop

    target, reference = Workbook(), Workbook()
    target.active.append(["零件号/最新更改水平", "P001/A01"])
    reference.active.append(["零件号/最新更改水平", "P001/A02"])
    context = SimpleNamespace(note_cell=lambda *args, **kwargs: None, version_for_worksheet=lambda sheet: "target" if sheet.parent is target else "reference")
    try:
        with pytest.raises(GuardStop, match="工程更改水平不一致"):
            check_reference_identity(context, target, reference)
    finally:
        target.close()
        reference.close()


@pytest.mark.parametrize("failed", [False, True])
def test_overlapping_rules_reuse_the_same_located_fact_or_failure(tmp_path, monkeypatch, failed):
    from audit.engine.checkers.excel_field_evidence import observe_excel_region

    for key, value in {"OCR_MODEL_NAME": "controlled", "OCR_OPENAI_API_KEY": "controlled", "OCR_OPENAI_BASE_URL": "https://ocr.invalid/v1"}.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "stamp.xlsx"
    book = Workbook()
    book.active["A1"] = "公司章"
    book.save(path)
    png = BytesIO()
    Image.new("RGB", (100, 100), "white").save(png, format="PNG")
    monkeypatch.setattr("documents.excel.visual.render_xlsx_field", lambda *args, **kwargs: png.getvalue())
    calls = []

    def read(**request):
        calls.append(request)
        if failed:
            raise TimeoutError("controlled failure")
        return {"psw_stamp_present": True, "psw_stamp_ending_text": "公司"}

    cache = {}
    audit = AuditInput(audit_id="stamp", file_url=str(path))
    all_fields = {"psw_stamp_present": "实际盖章", "psw_stamp_ending_text": "实际印章末尾汉字"}
    try:
        with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
            for fields in (all_fields, {"psw_stamp_present": "实际盖章"}):
                if failed:
                    with pytest.raises(TimeoutError):
                        observe_excel_region(audit, book.active, "A1:B1", fields, ("公司章",), cache=cache, witness=lambda *a, **k: None)
                else:
                    facts = observe_excel_region(audit, book.active, "A1:B1", fields, ("公司章",), cache=cache, witness=lambda *a, **k: None)
                    assert facts["psw_stamp_present"] is True
        assert len(calls) == 1
    finally:
        book.close()


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2Psw), ("v3", V3Psw)])
@pytest.mark.parametrize(("heading_row", "control_row", "expected"), [(12, 33, "NG"), (70, 72, "PASS")])
def test_submission_reason_uses_its_labelled_section_after_rows_move(tmp_path, monkeypatch, profile, agent_type, heading_row, control_row, expected):
    from audit.engine.rule_engine.excel_ops import DefaultExcelOps

    path = tmp_path / "moved-submission.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet.cell(heading_row, 1, "提交原因")
    sheet.cell(heading_row + 1, 2, "首次提交")
    sheet.cell(heading_row + 4, 1, "提交结果")
    sheet.cell(control_row, 2, "尺寸测量" if expected == "NG" else "首次提交")
    workbook.save(path)
    workbook.close()
    monkeypatch.setattr(DefaultExcelOps, "get_checkbox_controls", lambda *args, **kwargs: [(control_row, 2, True)])
    agent = agent_type(AuditInput(audit_id="moved-reason", file_url=str(path)), RuleInput(rule_id="reason", rule_profile=profile, type="零件提交保证书", rules=[]))
    try:
        with bind_runtime(None):
            result = agent.check(6)
        assert result.status == expected
    finally:
        agent.close()


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2Psw), ("v3", V3Psw)])
def test_unlabelled_disposition_control_cannot_imply_approval(tmp_path, monkeypatch, profile, agent_type):
    from audit.engine.rule_engine.excel_ops import DefaultExcelOps

    path = tmp_path / "unlabelled-disposition.xlsx"
    workbook = Workbook()
    workbook.active.title = "零件提交保证书"
    workbook.active["A60"] = "认可情况"
    workbook.save(path)
    workbook.close()
    monkeypatch.setattr(DefaultExcelOps, "get_checkbox_controls", lambda *args, **kwargs: [(60, 3, True)])
    agent = agent_type(AuditInput(audit_id="unlabelled-disposition", file_url=str(path)), RuleInput(rule_id="approval", rule_profile=profile, type="零件提交保证书", rules=[]))
    try:
        with bind_runtime(None):
            result = agent.check(8)
        assert result.status == "UNABLE"
    finally:
        agent.close()


@pytest.mark.parametrize(("profile", "agent_type"), [("v2", V2Psw), ("v3", V3Psw)])
@pytest.mark.parametrize("appearance_checked", [False, True])
def test_submission_result_options_cannot_share_a_neighbouring_checked_control(tmp_path, monkeypatch, profile, agent_type, appearance_checked):
    from audit.engine.rule_engine.excel_ops import DefaultExcelOps

    path = tmp_path / "adjacent-results.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet["A20"] = "提交结果"
    controls = [(60, 2, True), (60, 3, False), (60, 4, False), (23, 2, True)]
    for column, label in enumerate(("尺寸测量", "外观标准", "材料和功能试验", "统计过程数据"), 1):
        checked = appearance_checked if column == 2 else True
        sheet.cell(21, column, ("☑" if checked else "☐") + label)
        controls.append((21, column, checked))
    sheet["A23"] = "这些结果均满足所有图纸和规范要求"
    sheet["B23"] = "☑是"
    sheet["C23"] = "☐否"
    sheet["A60"] = "认可情况"
    sheet["B60"] = "批准"
    sheet["C60"] = "条件批准"
    sheet["D60"] = "拒绝"
    workbook.save(path)
    workbook.close()
    monkeypatch.setattr(DefaultExcelOps, "get_checkbox_controls", lambda *args, **kwargs: controls)
    agent = agent_type(AuditInput(audit_id="adjacent-results", file_url=str(path)), RuleInput(rule_id="approval", rule_profile=profile, type="零件提交保证书", rules=[]))
    try:
        with bind_runtime(None):
            result = agent.check(7)
        assert result.status == ("PASS" if appearance_checked else "NG")
    finally:
        agent.close()
