"""Explicit grant revocation against a silent provider, through public HTTP/UDS."""

from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from ppap_deep.isolation.model_gateway import grant_request, local_gateway


@pytest.mark.parametrize("stage", ["headers", "body", "terminal"])
def test_revocation_interrupts_silent_provider_and_releases_next_execution(stage: str) -> None:
    entered = threading.Event()
    streaming = threading.Event()
    disconnected = threading.Event()
    next_dispatched = threading.Event()
    peers: list[socket.socket] = []
    inputs: list[str] = []
    terminal = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":2}}}\n\n'
    created = b'data: {"type":"response.created"}\n\n'

    class Provider(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            inputs.append(payload["input"])
            if payload["input"] == "first":
                peers.append(self.connection)
                self.connection.settimeout(5)
                if stage != "headers":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(terminal if stage == "terminal" else created)
                    self.wfile.flush()
                entered.set()
                try:
                    if self.connection.recv(1) == b"":
                        disconnected.set()
                except OSError:
                    pass
                self.close_connection = True
                return
            next_dispatched.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(terminal)))
            self.end_headers()
            self.wfile.write(terminal)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider.daemon_threads = True
    serving = threading.Thread(target=provider.serve_forever, daemon=True)
    serving.start()

    def upstream(*_args, **_kwargs):
        # Only the external HTTPS adapter is replaced. Provider socket I/O is real.
        return http.client.HTTPConnection("127.0.0.1", provider.server_address[1], timeout=5)

    def post(gateway, token, text):
        connection = http.client.HTTPConnection("127.0.0.1", gateway.http.server_address[1], timeout=5)
        try:
            connection.request("POST", "/v1/responses", body=json.dumps({"model": "test-model", "input": text, "stream": True}), headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            prefix = b""
            if text == "first" and stage != "headers":
                expected_prefix = terminal if stage == "terminal" else created
                prefix = response.read(len(expected_prefix))
                assert prefix == expected_prefix
                streaming.set()
            try:
                return response.status, prefix + response.read()
            except http.client.IncompleteRead as error:
                return response.status, prefix + error.partial
        finally:
            connection.close()

    try:
        with tempfile.TemporaryDirectory(prefix="pd-c-", dir="/tmp") as directory, patch("ppap_deep.isolation.model_gateway.http.client.HTTPSConnection", upstream):
            with local_gateway(Path(directory) / "g.sock", upstream="https://provider.example/v1", model="test-model", provider_key="test-only") as gateway:
                first_id, next_id = uuid4(), uuid4()
                first_token = grant_request(str(gateway.socket_path), "issue", first_id)["token"]
                next_token = grant_request(str(gateway.socket_path), "issue", next_id)["token"]
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(post, gateway, first_token, "first")
                    try:
                        assert entered.wait(2), "first provider request was not dispatched"
                        if stage != "headers":
                            assert streaming.wait(2), "first provider stream was not forwarded"
                        pending = grant_request(str(gateway.socket_path), "usage", first_id)
                        if stage == "terminal":
                            assert pending == {"completed_requests": 1, "input_tokens": 3, "output_tokens": 2, "uncertain": False, "in_flight": False, "budget_exhausted": False}
                        else:
                            assert pending["in_flight"] and pending["uncertain"]
                        assert grant_request(str(gateway.socket_path), "revoke", first_id) == {"ok": True}
                        following = executor.submit(post, gateway, next_token, "next")
                        assert disconnected.wait(2), "revocation did not interrupt the silent provider"
                        assert next_dispatched.wait(2), "revoked provider retained the inference slot"
                        assert following.result(timeout=2) == (200, terminal)
                        first_result = first.result(timeout=2)
                        if stage == "headers":
                            assert first_result[0] == 403
                        else:
                            assert first_result == (200, terminal if stage == "terminal" else created)
                        assert inputs == ["first", "next"]
                        usage = grant_request(str(gateway.socket_path), "usage", next_id)
                        assert usage == {"completed_requests": 1, "input_tokens": 3, "output_tokens": 2, "uncertain": False, "in_flight": False, "budget_exhausted": False}
                    finally:
                        for peer in peers:
                            try:
                                peer.shutdown(socket.SHUT_RDWR)
                            except OSError:
                                pass
    finally:
        provider.shutdown()
        provider.server_close()
        serving.join(timeout=2)
