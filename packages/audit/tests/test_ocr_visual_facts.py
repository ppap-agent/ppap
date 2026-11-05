"""Dedicated OCR responses require source scope, positions and full negatives."""

import asyncio
from copy import deepcopy
from unittest.mock import Mock

import pytest
from audit.engine.checkers.ocr_visual_facts import OcrImage, observe_ocr_images, request_ocr_visual_facts


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("OCR_MODEL_NAME", "controlled-ocr")
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "controlled-noncredential")
    monkeypatch.setenv("OCR_OPENAI_BASE_URL", "https://ocr.invalid/v1")


def images():
    return [OcrImage(b"controlled-image", {"scope": "pdf:2", "page": 2, "bbox": [10, 20, 110, 220], "context": True}, 100, 200)]


def response(value=False, *, scope="pdf:2", index=0):
    return [{"scope": scope, "value": value, "sources": [{"image_index": index, "bbox": [0, 0, 1, 1]}], "covered_image_indices": [index], "coverage": "complete", "excerpt": "可见区域"}]


def install(monkeypatch, raw):
    calls = []

    async def read(image_paths, prompt, **kwargs):
        calls.append((image_paths, prompt, kwargs))
        return deepcopy(raw)

    monkeypatch.setattr("documents.ocr.client.call_llm_async", read)
    return calls


def test_source_located_ocr_fact_uses_dedicated_responses(monkeypatch):
    calls = install(monkeypatch, {"checked": response(False)})
    witness = Mock()
    assert observe_ocr_images(images(), {"checked": "此区域实际勾选"}, witness) == {"checked": False}
    assert len(calls) == 1 and calls[0][2]["max_output_tokens"] == 4096
    location = witness.call_args.args[0]
    assert location["page"] == 2 and location["bbox"] == [10, 20, 110, 220]
    assert location["engine"] == "ocr_llm"


def test_sync_transport_works_inside_an_existing_async_loop(monkeypatch):
    install(monkeypatch, {"field": []})

    async def caller():
        return request_ocr_visual_facts(images=[b"one"], prompt="facts", response_schema={})

    assert asyncio.run(caller()) == {"field": []}


@pytest.mark.parametrize("change", ["missing_position", "wrong_scope", "omitted_scope", "duplicate_scope", "out_of_bounds", "wrong_type", "unseen_image"])
def test_invalid_provenance_is_a_protocol_error(monkeypatch, change):
    raw = {"checked": response(False)}
    item = raw["checked"][0]
    if change == "missing_position":
        item["sources"] = []
    elif change == "wrong_scope":
        item["scope"] = "pdf:3"
    elif change == "omitted_scope":
        raw["checked"] = []
    elif change == "duplicate_scope":
        raw["checked"] *= 2
    elif change == "out_of_bounds":
        item["sources"][0]["bbox"] = [0, 0, 1.1, 1]
    elif change == "wrong_type":
        item["value"] = "false"
    else:
        item["sources"][0]["image_index"] = 3
    install(monkeypatch, raw)
    with pytest.raises(ValueError):
        observe_ocr_images(images(), {"checked": "实际勾选"}, Mock())


def test_false_without_complete_context_is_unknown(monkeypatch):
    raw = {"checked": response(False)}
    raw["checked"][0]["covered_image_indices"] = []
    install(monkeypatch, raw)
    assert observe_ocr_images(images(), {"checked": "实际勾选"}, Mock()) == {"checked": None}


def test_two_business_pages_conflict_instead_of_selecting_one(monkeypatch):
    frames = images() + [OcrImage(b"other", {"scope": "pdf:3", "page": 3, "bbox": [0, 0, 100, 200], "context": True}, 100, 200)]
    install(monkeypatch, {"checked": response(False) + response(True, scope="pdf:3", index=1)})
    assert observe_ocr_images(frames, {"checked": "实际勾选"}, Mock()) == {"checked": None}


def test_native_known_value_wins_over_ocr(monkeypatch):
    install(monkeypatch, {"checked": response(True)})
    assert observe_ocr_images(images(), {"checked": "实际勾选"}, Mock(), native_by_scope={"checked": {"pdf:2": False}}) == {"checked": False}


def test_global_existence_any_true_needs_no_false_override(monkeypatch):
    frames = images() + [OcrImage(b"other", {"scope": "pdf:3", "page": 3, "context": True}, 100, 200)]
    install(monkeypatch, {"seal": response(False) + response(True, scope="pdf:3", index=1)})
    assert observe_ocr_images(frames, {"seal": "文件中存在实际印章"}, Mock(), any_fields=frozenset({"seal"})) == {"seal": True}


def test_missing_dedicated_config_does_not_borrow_text_credentials(monkeypatch):
    monkeypatch.setenv("OCR_MODEL_NAME", "")
    monkeypatch.setenv("MODEL_NAME", "text-model")
    calls = install(monkeypatch, {})
    assert observe_ocr_images(images(), {"seal": "实际印章"}, Mock()) == {"seal": None}
    assert calls == []


def test_runtime_without_visual_capability_does_not_bypass_its_boundary(monkeypatch):
    from audit.engine.runtime import bind_runtime

    calls = install(monkeypatch, {})
    with bind_runtime(object()):
        assert observe_ocr_images(images(), {"seal": "实际印章"}, Mock()) == {"seal": None}
    assert calls == []


def test_pdf_context_and_crops_bound_actual_render_allocations(monkeypatch):
    from types import SimpleNamespace

    import fitz
    from audit.checkers.v4.visual_facts import _pdf_images

    allocations = []

    class Page:
        rect = fitz.Rect(0, 0, 1_000_000, 500_000)

        def set_rotation(self, value):
            pass

        def get_text(self):
            return ""

        def search_for(self, text):
            return []

        def get_image_info(self):
            return []

        def get_pixmap(self, *, matrix, clip, **kwargs):
            allocations.append((matrix.a * clip.width, matrix.d * clip.height))
            return SimpleNamespace(width=100, height=50, tobytes=lambda kind: b"controlled-png")

    class Document:
        needs_pass = False

        def __len__(self):
            return 1

        def __getitem__(self, key):
            return Page()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("audit.checkers.v4.visual_facts.fitz.open", lambda path: Document())
    _pdf_images(SimpleNamespace(file_url="unallocated.pdf", audit_id="frozen"), [1], "a" * 64)
    assert allocations
    assert all(width * height <= 12_000_000 and max(width, height) <= 4096 for width, height in allocations)


def test_known_value_with_unreadable_coverage_remains_unknown(monkeypatch):
    raw = {"checked": response(True)}
    raw["checked"][0]["coverage"] = "unreadable"
    install(monkeypatch, raw)
    assert observe_ocr_images(images(), {"checked": "实际勾选"}, Mock()) == {"checked": None}


@pytest.mark.parametrize("oversize", ["excerpt", "sources"])
def test_oversized_summary_or_repeated_source_list_is_a_protocol_error(monkeypatch, oversize):
    raw = {"checked": response(False)}
    if oversize == "excerpt":
        raw["checked"][0]["excerpt"] = "x" * 161
    else:
        raw["checked"][0]["sources"] *= 5
    install(monkeypatch, raw)
    with pytest.raises(ValueError):
        observe_ocr_images(images(), {"checked": "实际勾选"}, Mock())


@pytest.mark.parametrize("field", ["fixture_numbered_cells_filled", "fixture_attachment_choices_readable"])
def test_universal_positive_fact_cannot_skip_original_context(monkeypatch, field):
    frames = images() + [OcrImage(b"crop", {"scope": "pdf:2", "bbox": [10, 20, 30, 40], "context": False}, 20, 20)]
    raw = {field: response(True, index=1)}
    install(monkeypatch, raw)
    assert observe_ocr_images(frames, {field: "所有适用区域均完整填入"}, Mock()) == {field: None}


def test_psw_stamp_presence_conflicts_across_business_pages_stay_unknown(monkeypatch):
    from audit.checkers.v4.visual_facts import _EXISTENCE_FIELDS

    frames = images() + [OcrImage(b"other", {"scope": "pdf:3", "page": 3, "context": True}, 100, 200)]
    install(monkeypatch, {"psw_stamp_present": response(False) + response(True, scope="pdf:3", index=1)})
    assert observe_ocr_images(frames, {"psw_stamp_present": "保证书实际盖章"}, Mock(), any_fields=_EXISTENCE_FIELDS) == {"psw_stamp_present": None}


def test_psw_image_plan_only_magnifies_requested_regions(monkeypatch):
    from types import SimpleNamespace

    import fitz
    from audit.checkers.v4.visual_facts import _pdf_images

    labels = {"经授权的供方代表签字": (60, 610, 180, 622), "认可情况": (60, 700, 110, 712), "提交结果": (60, 420, 110, 432)}

    class Page:
        rect = fitz.Rect(0, 0, 595, 842)

        def set_rotation(self, value):
            pass

        def get_text(self):
            return ""

        def search_for(self, text):
            return [fitz.Rect(labels[text])] if text in labels else []

        def get_image_info(self):
            return [{"bbox": bounds} for bounds in [(60, 50, 130, 70), (150, 612, 200, 638), (430, 615, 490, 675)]]

        def get_pixmap(self, **kwargs):
            return SimpleNamespace(width=100, height=100, tobytes=lambda kind: b"controlled-png")

    class Document:
        needs_pass = False

        def __len__(self):
            return 1

        def __getitem__(self, key):
            return Page()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("audit.checkers.v4.visual_facts.fitz.open", lambda path: Document())
    audit = SimpleNamespace(file_url="unallocated.pdf", audit_id="frozen")
    core, _ = _pdf_images(audit, [1], "a" * 64, {"psw_stamp_ending_text": "末端原字", "authorized_signature_present": "签字", "approved_checked": "批准"})
    assert len(core) == 4
    assert core[0].locator["context"] is True
    assert not any(image.locator["bbox"][1] < 500 for image in core[1:])
    submission, _ = _pdf_images(audit, [1], "a" * 64, {"dimensional_checked": "尺寸提交结果"})
    assert len(submission) == 2
    assert 400 < submission[1].locator["bbox"][1] < 450


@pytest.mark.parametrize("value", ["司", "示例有限公司部门专用章"])
def test_seal_ending_has_bounded_original_characters(monkeypatch, value):
    install(monkeypatch, {"psw_stamp_ending_text": response(value)})
    with pytest.raises(ValueError, match="two to six"):
        observe_ocr_images(images(), {"psw_stamp_ending_text": "末端原字"}, Mock())
