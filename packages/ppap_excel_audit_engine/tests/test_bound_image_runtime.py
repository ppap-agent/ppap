"""The public shared image check uses the caller's runtime and original pages."""

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread

import fitz
import pytest
from PIL import Image
from ppap_core.contracts import AuditInput, RuleInput
from ppap_docops.files import download_file_sync
from ppap_excel_audit_engine.checkers.agents.base_agent import BaseAgent
from ppap_excel_audit_engine.checkers.client import AIClient
from ppap_excel_audit_engine.runtime import bind_runtime, get_runtime
from ppap_vision.image import extract_images_from_bytes


class OriginalImageRuntime:
    """A caller-provided runtime using real file transport and page rendering."""

    def __init__(self):
        self.rendered = []

    def load_workbook_auto(self, file_ref):
        pytest.fail("The image check must not load a workbook")

    def convert_pdf_to_excel(self, file_ref, *, enable_cache=True):
        pytest.fail("The image check must not convert a workbook")

    def render_pdf_images(self, file_url, *, render_all_pages=True):
        self.rendered.append((file_url, render_all_pages))
        content, _ = download_file_sync(file_url, max_file_size=100 * 1024 * 1024)
        return extract_images_from_bytes(content, ".pdf", render_all_pages=render_all_pages)

    def run_image_prompt(self, **kwargs):
        return AIClient().image_analysis_completion(**kwargs)


@pytest.fixture
def image_source(tmp_path, monkeypatch):
    path = tmp_path / "original.pdf"
    with fitz.open() as document:
        for color in ((1, 0, 0), (0, 0, 1)):
            page = document.new_page(width=120, height=160)
            page.draw_rect(page.rect, color=color, fill=color)
        document.save(path)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    original = path.read_bytes()
    content, reads = [original], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            reads.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(content[0])))
            self.end_headers()
            self.wfile.write(content[0])

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/original.pdf?version=frozen", content, reads
        finally:
            server.shutdown()
            thread.join()
            assert path.read_bytes() == original


def image_agent(url):
    return BaseAgent(
        AuditInput(audit_id="frozen-original", file_url=url, customer="客户甲"),
        RuleInput(rule_id="confirmed-image-rule", type="控制计划", rules=[{"id": "1", "category": "印章", "priority": "P1", "rule_to_check": "检查原件印章", "type_of_rule": "image"}]),
    )


@pytest.mark.parametrize("outcome", ["PASS", "NG", RuntimeError("model unavailable"), TypeError("invalid model protocol")], ids=["pass", "ng", "runtime-error", "type-error"])
def test_public_image_check_uses_all_original_pages_once_and_restores_the_runtime(image_source, monkeypatch, outcome):
    url, _content, reads = image_source
    calls = []

    def analyze(self, **kwargs):
        calls.append(kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        return json.dumps({"classification": "视觉", "item": "印章", "priority": "P1", "status": outcome, "suggestions": "原件视觉结论"})

    monkeypatch.setattr(AIClient, "image_analysis_completion", analyze)
    runtime = OriginalImageRuntime()

    with bind_runtime(runtime):
        result = image_agent(url).check(0)

    assert result.status == ("ERR" if isinstance(outcome, Exception) else outcome)
    assert get_runtime() is None
    assert runtime.rendered == [(url, True)]
    assert reads == ["/original.pdf?version=frozen"]
    assert len(calls) == 1
    assert "检查原件印章" in calls[0]["user_text"]
    pages = calls[0]["image_base64_list"]
    assert len(pages) == 2
    for encoded, expected in zip(pages, ((255, 0, 0), (0, 0, 255)), strict=True):
        with Image.open(BytesIO(base64.b64decode(encoded.split(",", 1)[-1]))) as image:
            actual = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
            assert all(abs(observed - target) < 10 for observed, target in zip(actual, expected, strict=True))


@pytest.mark.parametrize("failure", ["no-runtime", "corrupt-original"])
def test_image_execution_failure_never_calls_the_model_or_invents_a_business_result(image_source, monkeypatch, failure):
    url, content, reads = image_source
    calls = []
    monkeypatch.setattr(AIClient, "image_analysis_completion", lambda self, **kwargs: calls.append(kwargs))
    if failure == "corrupt-original":
        content[0] = b"not a PDF"
    runtime = OriginalImageRuntime() if failure == "corrupt-original" else None

    with bind_runtime(runtime):
        result = image_agent(url).check(0)

    assert result.status == "ERR"
    assert get_runtime() is None
    assert calls == []
    assert reads == (["/original.pdf?version=frozen"] if failure == "corrupt-original" else [])
