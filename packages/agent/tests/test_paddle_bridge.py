"""Real local HTTP and child-process OCR transport; no external service calls."""

import base64
import hashlib
import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import fitz
import httpx
import pytest
from agent.execution.isolation.container import ContainerRuntime
from agent.execution.paddle_bridge import paddle_bridge


@contextmanager
def upstream(monkeypatch, *, failure=False):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            raw = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.headers.get("Authorization"), raw))
            document = base64.b64decode(raw["file"])
            with fitz.open(stream=document, filetype="pdf") as pdf:
                assert len(pdf) == 1
            body = json.dumps({"result": {"dataInfo": {"pages": [{"width": 595, "height": 842}]}, "layoutParsingResults": [{"prunedResult": {"parsing_res_list": []}}]}}).encode()
            self.send_response(502 if failure else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("PADDLE_OCR_URL", f"http://127.0.0.1:{server.server_port}/layout-parsing")
    monkeypatch.setenv("PADDLE_TOKEN", "host-paddle-secret-never-in-guest")
    try:
        yield calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def inputs(root):
    directory = root / "inputs"
    directory.mkdir()
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        raw = pdf.tobytes()
    (directory / "source.bin").write_bytes(raw)
    (directory / "manifest.json").write_text(json.dumps({"operation": "execute-audit", "source_paths": {"source": "source.bin"}}))
    return directory, hashlib.sha256(raw).hexdigest()


def request(client, environment, digest, *, page=2, token=None):
    return client.post(
        environment["PPAP_PADDLE_BRIDGE_URL"],
        json={"source_sha256": digest, "page_number": page},
        headers={"Authorization": "Bearer " + (environment["PPAP_PADDLE_BRIDGE_TOKEN"] if token is None else token)},
    )


def test_paddle_capability_authorizes_only_frozen_pages_and_caches_once(tmp_path, monkeypatch):
    directory, digest = inputs(tmp_path)
    cancelled = threading.Event()
    with upstream(monkeypatch) as calls, paddle_bridge(directory, time.monotonic() + 30, cancelled) as environment, httpx.Client(trust_env=False) as client:
        assert "host-paddle-secret" not in json.dumps(environment)
        assert request(client, environment, digest, token="wrong").status_code == 403
        assert request(client, environment, "f" * 64).status_code == 400
        assert request(client, environment, digest, page=True).status_code == 400
        assert calls == []
        assert request(client, environment, digest).status_code == 200
        assert request(client, environment, digest).status_code == 200
        assert len(calls) == 1
        assert calls[0][0] == "token host-paddle-secret-never-in-guest"
        cancelled.set()
        assert request(client, environment, digest).status_code == 410
    with httpx.Client(trust_env=False) as client, pytest.raises(httpx.ConnectError):
        request(client, environment, digest)


def test_uncertain_paddle_failure_is_not_replayed(tmp_path, monkeypatch):
    directory, digest = inputs(tmp_path)
    with upstream(monkeypatch, failure=True) as calls, paddle_bridge(directory, time.monotonic() + 30, threading.Event()) as environment, httpx.Client(trust_env=False) as client:
        assert request(client, environment, digest).status_code == 502
        assert request(client, environment, digest).status_code == 502
        assert len(calls) == 1


def test_container_adapter_passes_only_run_capability_to_real_child(tmp_path, monkeypatch):
    directory, digest = inputs(tmp_path)
    (tmp_path / "work").mkdir()
    runtime = object.__new__(ContainerRuntime)
    runtime.active = {}
    runtime.config = SimpleNamespace(
        pythonpath=Path(sys.prefix) / "lib/python3.12/site-packages",
        python_executable=Path(sys.executable),
        prlimit_executable=Path("/usr/bin/prlimit"),
        setpriv_executable=Path("/usr/bin/setpriv"),
        memory_mib=512,
        max_processes=10,
        sandbox_profile="off",
    )
    script = """import json, os
from pathlib import Path
from documents.ocr.paddle.reader import read_pdf_page_evidence
assert 'PADDLE_TOKEN' not in os.environ and 'PADDLE_OCR_URL' not in os.environ
root = Path(os.environ['PPAP_GUEST_RUN'])
source = root / 'work' / 'copy.pdf'
source.write_bytes((root / 'inputs' / 'source.bin').read_bytes())
evidence = read_pdf_page_evidence(source, 2)
(root / 'work' / 'evidence.json').write_text(json.dumps(evidence))
"""
    popen = subprocess.Popen
    launched = []

    def controlled_launch(command, **kwargs):
        assert command[-1] == "agent.execution.isolation.guest_entry"
        assert "host-paddle-secret" not in json.dumps(kwargs["env"])
        launched.append(kwargs["env"])
        # UID/capability enforcement belongs to container integration. Here the
        # actual production adapter and clean environment drive a real child.
        return popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr("agent.execution.isolation.container.subprocess.Popen", controlled_launch)
    with upstream(monkeypatch) as calls:
        runtime._execute(tmp_path, time.monotonic() + 30, threading.Event(), None, uuid4())
    evidence = json.loads((tmp_path / "work" / "evidence.json").read_text())
    assert evidence["source_sha256"] == digest
    assert [page["page"] for page in evidence["pages"]] == [2]
    assert len(calls) == len(launched) == 1
    assert "PPAP_PADDLE_BRIDGE_TOKEN" in launched[0]
    assert runtime.active == {}


def test_free_precheck_never_receives_paddle_capability(tmp_path, monkeypatch):
    directory, _ = inputs(tmp_path)
    (directory / "manifest.json").write_text(json.dumps({"operation": "execute-precheck", "source_paths": {"source": "source.bin"}}))
    with upstream(monkeypatch) as calls, paddle_bridge(directory, time.monotonic() + 30, threading.Event()) as environment:
        assert environment == {}
        assert calls == []
