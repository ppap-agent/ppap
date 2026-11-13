"""The public shared image check uses the caller's runtime and original pages."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import fitz
import pytest
from audit.checker_lib.li.base_agent import BaseAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.runtime import bind_runtime, get_runtime
from documents.files import download_file_sync
from documents.images.image import extract_images_from_bytes


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
        import documents.ai

        return documents.ai.run_json("image_prompt", **kwargs)


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


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("valid_source", [False, True])
def test_public_image_check_keeps_unsupported_semantics_unknown_without_external_work(image_source, monkeypatch, bound, valid_source):
    url, content, reads = image_source
    calls = []
    monkeypatch.setattr("documents.ai.run_json", lambda self, **kwargs: calls.append(kwargs))
    if not valid_source:
        content[0] = b"not a PDF"
    runtime = OriginalImageRuntime() if bound else None

    with bind_runtime(runtime):
        result = image_agent(url).check(0)

    assert result.status == "UNABLE"
    assert result.checks_performed == 0
    assert get_runtime() is None
    assert calls == [] and reads == []
    assert runtime is None or runtime.rendered == []
