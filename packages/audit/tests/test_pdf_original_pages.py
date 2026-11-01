import base64
import io
import json

import fitz
import pytest
from audit.contracts import (
    AuditInput,
    RuleInput,
)
from audit.checkers.v2 import pdf_agent as v2_pdf
from audit.checkers.v2.psw_pdf_agent import PswPDFAgent as V2PswPDFAgent
from audit.checkers.v3 import pdf_agent as v3_pdf
from audit.checkers.v3.psw_pdf_agent import PswPDFAgent as V3PswPDFAgent
from audit.engine.checkers.client import AIClient
from audit.engine.runtime import bind_runtime
from documents.contracts import ConversionArtifact
from PIL import Image


@pytest.fixture(params=[("v2", V2PswPDFAgent, v2_pdf), ("v3", V3PswPDFAgent, v3_pdf)], ids=["v2", "v3"])
def agent_profile(request):
    return request.param


def stamp_rules(profile):
    return RuleInput(
        rule_id="confirmed-psw-rules",
        rule_profile=profile,
        type="零件提交保证书",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 11)],
    )


def check_original_image(monkeypatch, agent_profile, source, observation=None):
    profile, agent_class, pdf_module = agent_profile
    seen_images = []

    def convert(file_ref, *, enable_cache=True, doc_type=None):
        assert file_ref.location == source
        assert enable_cache is True
        return ConversionArtifact(success=False)

    def analyze(self, **kwargs):
        seen_images.extend(kwargs["image_base64_list"])
        return json.dumps(observation if observation is not None else {"psw_stamp_ends_with_company": True})

    monkeypatch.setattr(pdf_module, "convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr(AIClient, "image_analysis_completion", analyze)
    with bind_runtime(None):
        agent = agent_class(AuditInput(audit_id="original", file_url=source), stamp_rules(profile))
        result = agent.check(2)
    assert result is not None
    return result, seen_images


def test_local_image_is_read_from_original_bytes_without_a_runtime(tmp_path, monkeypatch, agent_profile):
    path = tmp_path / "original.png"
    Image.new("RGB", (8, 8), "white").save(path)

    result, images = check_original_image(monkeypatch, agent_profile, str(path))

    assert result.status == "PASS"
    assert len(images) == 1
    assert images[0].startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(images[0].split(",", 1)[1]))) as image:
        assert image.size == (8, 8)
        assert image.getpixel((4, 4)) == (255, 255, 255)


def test_signed_image_url_reads_the_original_downloaded_image(tmp_path, monkeypatch, agent_profile):
    path = tmp_path / "original.png"
    Image.new("RGB", (12, 12), (0, 128, 255)).save(path)
    source = "https://files.example.test/original.png?signature=fixture"
    _, _, pdf_module = agent_profile

    def download(url, *, max_file_size):
        assert url == source
        assert max_file_size == 100 * 1024 * 1024
        return path.read_bytes(), ".png"

    monkeypatch.setattr(pdf_module, "download_file_sync", download)

    result, images = check_original_image(monkeypatch, agent_profile, source)

    assert result.status == "PASS"
    assert len(images) == 1
    assert images[0].startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(images[0].split(",", 1)[1]))) as image:
        assert image.size == (12, 12)
        assert all(abs(actual - expected) <= 2 for actual, expected in zip(image.getpixel((6, 6)), (0, 128, 255)))


@pytest.fixture
def original_pdf(tmp_path):
    path = tmp_path / "original.pdf"
    with fitz.open() as document:
        for title, color in [("Cover", (1, 1, 1)), ("Authorization", (0.2, 0.7, 0.3)), ("Appendix", (0.2, 0.3, 0.8))]:
            page = document.new_page(width=120, height=120)
            page.draw_rect(page.rect, color=color, fill=color)
            page.insert_text((10, 20), title, fontsize=10)
        document.save(path)
    return path


@pytest.mark.parametrize(("stamp_present", "expected_status"), [(True, "PASS"), (False, "NG")])
def test_failed_conversion_keeps_the_original_page_fallback_verdict(original_pdf, monkeypatch, agent_profile, stamp_present, expected_status):
    result, images = check_original_image(
        monkeypatch,
        agent_profile,
        str(original_pdf),
        {"psw_stamp_ends_with_company": stamp_present, "authorization_table_present": False},
    )

    assert result.status == expected_status
    assert len(images) == 3
    with Image.open(io.BytesIO(base64.b64decode(images[1].split(",", 1)[1]))) as image:
        assert all(abs(actual - expected) <= 3 for actual, expected in zip(image.getpixel((100, 100)), (51, 179, 77)))


@pytest.mark.parametrize(("keyword", "expected_colors"), [("Authorization", [(51, 179, 77)]), ("Not in this document", [(255, 255, 255), (51, 179, 77), (51, 77, 204)])])
def test_keyword_selection_reads_matching_original_pages_or_all_pages(original_pdf, monkeypatch, agent_profile, keyword, expected_colors):
    profile, agent_class, pdf_module = agent_profile

    def convert(file_ref, *, enable_cache=True):
        assert file_ref.location == str(original_pdf)
        assert enable_cache is True
        return ConversionArtifact(success=False)

    monkeypatch.setattr(pdf_module, "convert_pdf_to_excel_auto", convert)
    monkeypatch.setattr(AIClient, "image_analysis_completion", lambda self, **kwargs: "No additional OCR text")
    with bind_runtime(None):
        agent = agent_class(AuditInput(audit_id="selected-pages", file_url=str(original_pdf)), stamp_rules(profile))

        images = agent.get_images_for_keywords([keyword])

    assert len(images) == len(expected_colors)
    for encoded, expected_color in zip(images, expected_colors):
        with Image.open(io.BytesIO(base64.b64decode(encoded.split(",", 1)[1]))) as image:
            assert all(abs(actual - expected) <= 3 for actual, expected in zip(image.getpixel((100, 100)), expected_color))


@pytest.mark.parametrize(
    ("selection", "expected_colors"),
    [([2], [(51, 77, 204)]), ([1], [(51, 179, 77)]), ([], [(255, 255, 255), (51, 179, 77), (51, 77, 204)])],
    ids=["custom-appendix-page", "custom-authorization-page", "empty-selection-falls-back-to-all-pages"],
)
def test_custom_page_selector_controls_actual_original_pages(original_pdf, monkeypatch, agent_profile, selection, expected_colors):
    profile, agent_class, pdf_module = agent_profile
    original_bytes = original_pdf.read_bytes()

    class SelectedPagesAgent(agent_class):
        def target_page_selector(self, target_keywords):
            assert target_keywords == ["Authorization"]
            return selection

    def convert(file_ref, *, enable_cache=True, doc_type=None):
        assert file_ref.location == str(original_pdf)
        return ConversionArtifact(success=False)

    monkeypatch.setattr(pdf_module, "convert_pdf_to_excel_auto", convert)
    with bind_runtime(None):
        agent = SelectedPagesAgent(AuditInput(audit_id="custom-pages", file_url=str(original_pdf)), stamp_rules(profile))
        try:
            images = agent.get_images_for_keywords(["Authorization"])
        finally:
            agent.close()

    assert len(images) == len(expected_colors)
    for encoded, expected_color in zip(images, expected_colors):
        with Image.open(io.BytesIO(base64.b64decode(encoded.split(",", 1)[1]))) as image:
            assert all(abs(actual - expected) <= 3 for actual, expected in zip(image.getpixel((100, 100)), expected_color))
    assert original_pdf.read_bytes() == original_bytes
