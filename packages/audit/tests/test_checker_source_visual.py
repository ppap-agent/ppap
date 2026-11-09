"""Original visual source scopes, native Excel dispatch, and frozen observations."""

import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import fitz
import pytest
from audit.checker_lib.visual_facts import VisualFactsUnavailable, read_original_visual_facts
from audit.contracts import AuditInput
from audit.engine.runtime import bind_runtime
from openpyxl import Workbook
from PIL import Image


@pytest.fixture(autouse=True)
def controlled_ocr(monkeypatch):
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled-visual")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled-key")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://controlled.invalid/v1")
    monkeypatch.delenv("PADDLE_OCR_URL", raising=False)
    monkeypatch.delenv("PPAP_PADDLE_BRIDGE_URL", raising=False)


def pdf_source(tmp_path, *, pages=1, rotated=False):
    path = tmp_path / "source.pdf"
    with fitz.open() as document:
        for _ in range(pages):
            page = document.new_page(width=200, height=100)
            page.draw_rect(fitz.Rect(20, 20, 160, 90), color=(1, 0, 0), fill=(1, 0, 0))
            if rotated:
                page.set_rotation(90)
        document.save(path)
    return path


def audit_input(path):
    return AuditInput(audit_id="frozen-version", file_url=str(path), file_metadata={"file_version_id": "file-version", "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})


def test_pdf_observations_keep_original_unrotated_scope_and_reuse_receipt(tmp_path):
    path = pdf_source(tmp_path, rotated=True)
    original, cache, witness = path.read_bytes(), {}, Mock()
    calls = []

    def read(**request):
        calls.append(request)
        with Image.open(BytesIO(request["images"][0])) as image:
            assert image.size == (400, 200)
            assert image.convert("RGB").getpixel((160, 100)) == (255, 0, 0)
        return {"signature": True}

    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        for _ in range(2):
            assert read_original_visual_facts(audit_input(path), {"signature": "目标签字区域存在实际签字。"}, witness, cache=cache) == {"signature": True}
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert path.read_bytes() == original
    assert witness.call_count == 2
    locator = witness.call_args.args[0]
    assert locator["page"] == 1
    assert locator["bbox"] == [0, 0, 200, 100]
    assert locator["scope"] == "pdf:1"
    assert locator["coordinate_system"] == "pdf_points_unrotated"
    assert locator["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert locator["file_version_id"] == "file-version"


@pytest.mark.parametrize("second", [False, None])
def test_partial_or_disputed_page_cannot_prove_document_wide_fact(tmp_path, second):
    path = pdf_source(tmp_path, pages=2)
    read = Mock(side_effect=[{"signature": True}, {"signature": True}, {"signature": second}, {"signature": second}])
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        assert read_original_visual_facts(audit_input(path), {"signature": "每页目标字段均有实际签字。"}, Mock(), cache={}) == {"signature": None}
    read.assert_not_called()


def test_changed_original_cannot_publish_observations(tmp_path):
    path = pdf_source(tmp_path)
    audit, witness = audit_input(path), Mock()
    calls = []

    def read(**request):
        calls.append(request)
        path.write_bytes(path.read_bytes() + b"\n")
        return {"signature": True}

    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        with pytest.raises(ValueError, match="Frozen original identity changed"):
            read_original_visual_facts(audit, {"signature": "实际签字"}, witness, cache={})
    assert len(calls) == 2
    witness.assert_not_called()


def test_cached_success_still_checks_frozen_original_and_policy(tmp_path, monkeypatch):
    path = pdf_source(tmp_path)
    audit, cache = audit_input(path), {}
    read = Mock(return_value={"signature": True})
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        read_original_visual_facts(audit, {"signature": "实际签字"}, Mock(), cache=cache)
        monkeypatch.setenv("OCR_MODEL_NAME", "changed-model")
        with pytest.raises(ValueError, match="configuration changed"):
            read_original_visual_facts(audit, {"signature": "实际签字"}, Mock(), cache=cache)
        monkeypatch.setenv("OCR_MODEL_NAME", "controlled-visual")
        path.write_bytes(path.read_bytes() + b"\n")
        with pytest.raises(ValueError, match="Frozen original identity changed"):
            read_original_visual_facts(audit, {"signature": "实际签字"}, Mock(), cache=cache)
    assert read.call_count == 2


def test_technical_failure_is_not_unknown_or_retried(tmp_path):
    path = pdf_source(tmp_path)
    audit, cache, witness = audit_input(path), {}, Mock()
    read = Mock(side_effect=TimeoutError("controlled timeout"))
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        for _ in range(2):
            with pytest.raises(TimeoutError):
                read_original_visual_facts(audit, {"signature": "实际签字"}, witness, cache=cache)
    assert read.call_count == 1
    witness.assert_not_called()


@pytest.mark.parametrize("carrier", ["docx", "too_many_pages"])
def test_unsupported_or_incomplete_carrier_is_explicitly_unavailable(tmp_path, carrier):
    path = pdf_source(tmp_path, pages=11) if carrier == "too_many_pages" else tmp_path / "source.docx"
    if carrier == "docx":
        path.write_bytes(b"unlocated embedded images cannot prove a form field")
    read = Mock(side_effect=AssertionError("A provider must not be called"))
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        with pytest.raises(VisualFactsUnavailable):
            read_original_visual_facts(audit_input(path), {"signature": "实际签字"}, Mock(), cache={})
    read.assert_not_called()


def test_raster_has_original_pixel_locator_and_dual_observations(tmp_path):
    path = tmp_path / "source.png"
    Image.new("RGB", (300, 200), "red").save(path)
    witness, read = Mock(), Mock(return_value={"signature": False})
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        assert read_original_visual_facts(audit_input(path), {"signature": "目标字段完整且为空"}, witness, cache={}) == {"signature": False}
    assert read.call_count == 2
    assert witness.call_args.args[0]["coordinate_system"] == "image_pixels"
    assert witness.call_args.args[0]["bbox"] == [0, 0, 300, 200]


def workbook_source(tmp_path, suffix=".xlsx", *, sheets=1):
    workbook = Workbook()
    for index in range(sheets):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = f"Sheet{index + 1}"
        sheet["B2"] = f"Signature{index + 1}"
        sheet["D6"] = "ScopeEnd"
    path = tmp_path / f"source{suffix}"
    workbook.save(path)
    workbook.close()
    return path


def rendered_excel(monkeypatch):
    calls = []

    def render(content, *, sheet_name, cell_range):
        calls.append((sheet_name, cell_range))
        with fitz.open() as document:
            page = document.new_page(width=200, height=100)
            page.insert_text((10, 30), f"Signature{sheet_name[-1]}")
            return document.tobytes()

    monkeypatch.setattr("documents.excel.visual.render_xlsx_pdf", render)
    return calls


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm", ".xls"])
def test_excel_uses_native_workbook_and_neutral_region_reader(tmp_path, monkeypatch, suffix):
    # Content-based loading also accepts ZIP workbooks with an old extension.
    # The Corpus-owned OLE test exercises the actual XLS loader.
    path = workbook_source(tmp_path, suffix)
    rendering, witness = rendered_excel(monkeypatch), Mock()
    read, cache = Mock(return_value={"signature": True}), {}
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        for _ in range(2):
            assert read_original_visual_facts(audit_input(path), {"signature": "实际签字"}, witness, cache=cache) == {"signature": True}
    assert read.call_count == 2
    assert rendering == [("Sheet1", "B2:D6")]
    locator = witness.call_args.args[0]
    assert locator["kind"] == "xlsx_field"
    assert locator["sheet"] == "Sheet1"
    assert locator["cell_range"] == "B2:D6"
    assert locator["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert locator["file_version_id"] == "file-version"


def test_unreadable_excel_sheet_cannot_be_covered_by_another(tmp_path, monkeypatch):
    path = workbook_source(tmp_path, sheets=2)
    rendering = rendered_excel(monkeypatch)
    read = Mock(side_effect=[{"signature": True}, {"signature": True}, {"signature": None}, {"signature": None}])
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        assert read_original_visual_facts(audit_input(path), {"signature": "每份表单都有实际签字"}, Mock(), cache={}) == {"signature": None}
    assert rendering == []
    read.assert_not_called()


@pytest.mark.parametrize("suffix", [".pdf", ".xlsx"])
def test_shared_cache_does_not_borrow_another_file_versions_witnesses(tmp_path, monkeypatch, suffix):
    path = pdf_source(tmp_path) if suffix == ".pdf" else workbook_source(tmp_path)
    if suffix == ".xlsx":
        rendered_excel(monkeypatch)
    first = audit_input(path)
    second = first.model_copy(update={"audit_id": "another-version", "file_metadata": {**first.file_metadata, "file_version_id": "another-file-version"}})
    cache, witness = {}, Mock()
    read = Mock(return_value={"signature": True})
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        read_original_visual_facts(first, {"signature": "实际签字"}, Mock(), cache=cache)
        read_original_visual_facts(second, {"signature": "实际签字"}, witness, cache=cache)
    assert witness.call_args.args[0]["file_version_id"] == "another-file-version"


def test_excel_original_replaced_during_observation_cannot_publish(tmp_path, monkeypatch):
    path = workbook_source(tmp_path)
    rendered_excel(monkeypatch)
    audit, witness = audit_input(path), Mock()

    def read(**request):
        path.write_bytes(path.read_bytes() + b"\n")
        return {"signature": True}

    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        with pytest.raises(ValueError, match="Frozen original identity changed"):
            read_original_visual_facts(audit, {"signature": "实际签字"}, witness, cache={})
    witness.assert_not_called()


def test_paddle_presence_has_verified_original_region_and_does_not_need_llm(tmp_path):
    path = pdf_source(tmp_path)
    audit, witness = audit_input(path), Mock()
    paddle = Mock(
        return_value={
            "source_sha256": audit.file_metadata["source_sha256"],
            "pages": [{"page": 1, "frame": {"pdf_width": 200, "pdf_height": 100}, "seals": [{"detected": True, "pdf_bbox": [20, 20, 60, 60], "recognized_text": "受控章"}]}],
        }
    )
    read = Mock(side_effect=AssertionError("No second tool is needed for a proven presence fact"))
    with bind_runtime(SimpleNamespace(read_paddle_page=paddle, read_ocr_visual_facts=read)):
        assert read_original_visual_facts(audit, {"controlled_stamp": "原件有受控章"}, witness, cache={}, existential_fields=frozenset({"controlled_stamp"})) == {"controlled_stamp": True}
    assert witness.call_args.args[0]["bbox"] == [20, 20, 60, 60]
    assert witness.call_args.args[0]["engine"] == "paddle"
    read.assert_not_called()


def test_empty_paddle_receipt_does_not_prove_absent_seal(tmp_path):
    path = pdf_source(tmp_path)
    audit = audit_input(path)
    paddle = Mock(return_value={"source_sha256": audit.file_metadata["source_sha256"], "pages": [{"page": 1, "frame": {"pdf_width": 200, "pdf_height": 100}, "seals": []}]})
    read = Mock(return_value={"seal": None})
    with bind_runtime(SimpleNamespace(read_paddle_page=paddle, read_ocr_visual_facts=read)):
        assert read_original_visual_facts(audit, {"seal": "真实印章"}, Mock(), cache={}, existential_fields=frozenset({"seal"})) == {"seal": None}
    assert read.call_count == 2


@pytest.mark.parametrize("second", [False, None])
@pytest.mark.parametrize("suffix", [".pdf", ".xlsx"])
def test_only_explicit_existential_fact_can_use_one_scopes_positive(tmp_path, monkeypatch, second, suffix):
    path = pdf_source(tmp_path, pages=2) if suffix == ".pdf" else workbook_source(tmp_path, sheets=2)
    if suffix == ".xlsx":
        rendered_excel(monkeypatch)
    read = Mock(side_effect=[{"controlled_stamp": True}, {"controlled_stamp": True}, {"controlled_stamp": second}, {"controlled_stamp": second}])
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        result = read_original_visual_facts(audit_input(path), {"controlled_stamp": "文件中存在真实受控章"}, Mock(), cache={}, existential_fields=frozenset({"controlled_stamp"}))
    assert result == {"controlled_stamp": True}
    assert read.call_count == 4


def test_existential_fact_still_needs_complete_coverage_to_prove_absence(tmp_path):
    path = pdf_source(tmp_path, pages=2)
    read = Mock(side_effect=[{"seal": False}, {"seal": False}, {"seal": None}, {"seal": None}])
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        assert read_original_visual_facts(audit_input(path), {"seal": "存在真实印章"}, Mock(), cache={}, existential_fields=frozenset({"seal"})) == {"seal": None}


def test_noncontrolled_stamp_does_not_establish_controlled_stamp(tmp_path):
    path = pdf_source(tmp_path)
    audit = audit_input(path)
    paddle = Mock(
        return_value={
            "source_sha256": audit.file_metadata["source_sha256"],
            "pages": [{"page": 1, "frame": {"pdf_width": 200, "pdf_height": 100}, "seals": [{"detected": True, "pdf_bbox": [20, 20, 60, 60], "recognized_text": "非受控文件"}]}],
        }
    )
    read = Mock(return_value={"controlled_stamp": None})
    with bind_runtime(SimpleNamespace(read_paddle_page=paddle, read_ocr_visual_facts=read)):
        assert read_original_visual_facts(audit, {"controlled_stamp": "原件有受控章"}, Mock(), cache={}, existential_fields=frozenset({"controlled_stamp"})) == {"controlled_stamp": None}
    assert read.call_count == 2


def test_picture_outside_native_cell_dimension_is_not_cropped_away(tmp_path, monkeypatch):
    from openpyxl.drawing.image import Image as ExcelImage

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet["A1"] = "Signature1"
    picture = BytesIO()
    Image.new("RGB", (20, 20), "red").save(picture, format="PNG")
    picture.seek(0)
    sheet.add_image(ExcelImage(picture), "K20")
    path = tmp_path / "drawing-outside-cells.xlsx"
    workbook.save(path)
    workbook.close()
    rendering = rendered_excel(monkeypatch)
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=Mock(return_value={"signature": True}))):
        assert read_original_visual_facts(audit_input(path), {"signature": "实际签字图形"}, Mock(), cache={}) == {"signature": True}
    assert rendering == [("Sheet1", None)]


def test_paddle_technical_failure_is_cached_and_not_silently_replaced(tmp_path):
    path = pdf_source(tmp_path)
    paddle = Mock(side_effect=TimeoutError("controlled"))
    read, cache = Mock(), {}
    with bind_runtime(SimpleNamespace(read_paddle_page=paddle, read_ocr_visual_facts=read)):
        for _ in range(2):
            with pytest.raises(TimeoutError):
                read_original_visual_facts(audit_input(path), {"seal": "真实印章"}, Mock(), cache=cache, existential_fields=frozenset({"seal"}))
    assert paddle.call_count == 1
    read.assert_not_called()


@pytest.mark.parametrize("suffix", [".pdf", ".xlsx"])
def test_mixed_multiscope_request_observes_only_explicit_existence(tmp_path, monkeypatch, suffix):
    path = pdf_source(tmp_path, pages=2) if suffix == ".pdf" else workbook_source(tmp_path, sheets=2)
    if suffix == ".xlsx":
        rendered_excel(monkeypatch)
    calls = []

    def read(**request):
        calls.append(request)
        assert set(request["response_schema"]["schema"]["properties"]) == {"seal"}
        return {"seal": True}

    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        observed = read_original_visual_facts(
            audit_input(path), {"seal": "全文件存在实际印章", "consistent": "全部页面中，同一活动类型符号一致"}, Mock(), cache={}, existential_fields=frozenset({"seal"})
        )
    assert observed == {"seal": True, "consistent": None}
    assert len(calls) == 4


def test_empty_or_missing_field_cannot_establish_visual_facts(tmp_path):
    path = pdf_source(tmp_path)
    audit, witness = audit_input(path), Mock()
    read = Mock(return_value={})
    with bind_runtime(SimpleNamespace(read_ocr_visual_facts=read)):
        for fields in ({}, {"signature": ""}, {"": "实际签字"}):
            with pytest.raises(VisualFactsUnavailable):
                read_original_visual_facts(audit, fields, witness, cache={})
        read.assert_not_called()
        with pytest.raises(ValueError, match="keys do not match"):
            read_original_visual_facts(audit, {"signature": "实际签字"}, witness, cache={})
    witness.assert_not_called()


def test_any_detected_seal_cannot_prove_an_undeclared_complete_predicate(tmp_path):
    path = pdf_source(tmp_path)
    paddle = Mock(side_effect=AssertionError("A seal detection is not a full-field observation"))
    read = Mock(return_value={"seal": None})
    with bind_runtime(SimpleNamespace(read_paddle_page=paddle, read_ocr_visual_facts=read)):
        result = read_original_visual_facts(audit_input(path), {"seal": "同页四份报告各自签章处全部有实际印章"}, Mock(), cache={})
    assert result == {"seal": None}
    paddle.assert_not_called()
    assert read.call_count == 2


def test_paddle_cache_does_not_borrow_another_audits_runtime_receipt(tmp_path):
    path = pdf_source(tmp_path)
    first, cache = audit_input(path), {}
    second = first.model_copy(update={"audit_id": "second-audit"})
    receipt = {"source_sha256": first.file_metadata["source_sha256"], "pages": [{"page": 1, "frame": {"pdf_width": 200, "pdf_height": 100}, "seals": []}]}
    paddle = Mock(return_value=receipt)
    with bind_runtime(SimpleNamespace(read_paddle_page=paddle, read_ocr_visual_facts=Mock(return_value={"seal": None}))):
        for audit in (first, second):
            assert read_original_visual_facts(audit, {"seal": "实际印章"}, Mock(), cache=cache, existential_fields=frozenset({"seal"})) == {"seal": None}
    assert paddle.call_count == 2
