"""Run-local Paddle capability: only frozen PDF identities and selected pages.

The trusted parent owns the upstream URL/token. The guest cannot choose an
upstream, upload other bytes, or reuse this capability after its run ends.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.execution.budget import MAX_INPUT_FILES, MAX_INPUT_TOTAL
from agent.execution.secure_files import read_json, read_regular

MAX_RESPONSE = 16 * 1024 * 1024


def _sources(inputs: Path) -> dict[str, bytes]:
    try:
        manifest = read_json(inputs, ("manifest.json",), 4 * 1024 * 1024)
    except FileNotFoundError:
        return {}  # Snapshot-only legacy jobs do not have a manifest or this capability.
    operation = manifest.get("operation")
    if operation == "execute-audit":
        # Oracle evaluation and explicitly model-free trials must not send their
        # originals to OCR, even when the controller has a configured provider.
        spec = manifest.get("spec")
        if not isinstance(spec, dict) or spec.get("allow_model_calls") is not True:
            return {}
        names = list(manifest.get("source_paths", {}).values())
    elif operation in {"adapt-checker", "execute-checker"}:
        names = [manifest.get("document"), *manifest.get("related_documents", {}).values()]
    else:
        return {}  # Free preparation and legacy operations receive no OCR capability.
    if len(names) > MAX_INPUT_FILES:
        raise ValueError("paddle_source_limit")
    result, remaining = {}, MAX_INPUT_TOTAL
    for name in names:
        if not isinstance(name, str) or "/" in name or "\\" in name or name in {"", ".", ".."}:
            raise ValueError("paddle_source_invalid")
        raw = read_regular(inputs, (name,), remaining)
        remaining -= len(raw)
        if raw.startswith(b"%PDF-"):
            result[hashlib.sha256(raw).hexdigest()] = raw
    return result


@contextmanager
def paddle_bridge(inputs: Path, deadline: float, cancelled: threading.Event):
    endpoint = os.environ.get("PADDLE_OCR_URL", "").strip()
    if not endpoint:
        yield {}
        return
    sources = _sources(inputs)
    if not sources:
        yield {}
        return
    from documents.ocr.paddle.client import PaddleStructureService

    # Capture host configuration once. It is never put into a guest environment,
    # returned receipt, diagnostic, generated artifact, or request argument.
    service = PaddleStructureService(endpoint, token=os.environ.get("PADDLE_TOKEN", ""), timeout=20)
    capability = secrets.token_urlsafe(32)
    lock = threading.Lock()
    closed = threading.Event()
    attempts: dict[tuple[str, int], bytes | None] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            self.connection.settimeout(2)
            authorization = self.headers.get("Authorization", "")
            if self.path != "/paddle-page" or not hmac.compare_digest(authorization, "Bearer " + capability):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 512 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("invalid_request")
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict) or set(request) != {"source_sha256", "page_number"}:
                    raise ValueError("invalid_request")
                digest, page = request["source_sha256"], request["page_number"]
                if not isinstance(digest, str) or digest not in sources or type(page) is not int or page < 1:
                    raise ValueError("invalid_source")
            except (ValueError, TypeError, OSError):
                self.send_error(400)
                return
            with lock:
                if closed.is_set() or cancelled.is_set() or time.monotonic() >= deadline:
                    self.send_error(410)
                    return
                key = (digest, page)
                if key not in attempts:
                    if len(attempts) >= 128:
                        self.send_error(429)
                        return
                    # Pending and failed requests remain fenced. A second clause
                    # cannot blindly repeat an externally uncertain OCR call.
                    attempts[key] = None
                    try:
                        with tempfile.TemporaryDirectory(prefix="ppap-paddle-source-") as folder:
                            source = Path(folder) / "source.pdf"
                            source.write_bytes(sources[digest])
                            response = service.analyze_page(source, page, timeout=min(20, max(0.1, deadline - time.monotonic())))
                        body = json.dumps(response, ensure_ascii=False, allow_nan=False).encode()
                        if len(body) > MAX_RESPONSE:
                            raise ValueError("paddle_response_limit")
                        attempts[key] = body
                    except Exception:
                        pass  # Do not expose provider payloads, credentials or private paths.
                body = attempts[key]
                if body is None:
                    self.send_error(502)
                    return
                if closed.is_set() or cancelled.is_set() or time.monotonic() >= deadline:
                    self.send_error(410)
                    return
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"PPAP_PADDLE_BRIDGE_URL": f"http://127.0.0.1:{server.server_port}/paddle-page", "PPAP_PADDLE_BRIDGE_TOKEN": capability}
    finally:
        closed.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
