"""Security boundaries for the container guest and model gateway."""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from agent.execution.gateway import Grants, RequestLimitReached, grant_request, local_gateway
from agent.execution.isolation.container import ContainerRuntime, RuntimeConfig
from agent.execution.isolation.progress import Progress
from audit.policy import PI_VERSION
from audit.protocol import CompileSpec, ControllerFault, SnapshotSpec


class GatewayGrantTests(unittest.TestCase):
    def test_real_chunked_stream_distinguishes_termination_from_missing_usage(self) -> None:
        delta = ("data: " + json.dumps({"type": "response.output_text.delta", "delta": "x" * 70_000}) + "\n\n").encode()
        terminal = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":13,"output_tokens":7}}}\n\n'
        dispatched: list[str] = []
        received_ids: list[str] = []
        spoofed_client_id = "7a597aa9-7349-48aa-936d-1676a534e260"
        clock = [100.0]
        release_first, release_second, release_finish = (threading.Event() for _ in range(3))
        provider_timeouts: list[str] = []

        class Provider(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                pass

            def do_POST(self) -> None:
                mode = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["input"]
                dispatched.append(mode)
                received_ids.append(self.headers.get("X-Client-Request-Id", ""))
                clock[0] = 102.0
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.send_header("X-Request-Id", "provider-response-id-must-not-be-retained")
                self.end_headers()
                payload = delta + (terminal if mode in {"complete", "after_usage"} else b"")
                # Split across an SSE line and the gateway's 64 KiB read size.
                if mode != "before_chunk":
                    for gate, chunk in ((release_first, payload[:37]), (release_second, payload[37:])):
                        if not gate.wait(2):
                            provider_timeouts.append(mode)
                            return
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                if not release_finish.wait(2):
                    provider_timeouts.append(mode)
                    return
                if mode in {"complete", "missing_usage"}:
                    self.wfile.write(b"0\r\n\r\n")
                # The other cases close without HTTP's required final chunk.
                self.wfile.flush()
                self.close_connection = True

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        serving = threading.Thread(target=provider.serve_forever, daemon=True)
        serving.start()

        def upstream(*_args, **_kwargs):
            return http.client.HTTPConnection("127.0.0.1", provider.server_address[1], timeout=2)

        modes = ["complete", "before_chunk", "before_usage", "after_usage", "missing_usage"]
        try:
            with (
                tempfile.TemporaryDirectory(prefix="pd-ch-", dir="/tmp") as temporary,
                patch("agent.execution.gateway.http.client.HTTPSConnection", upstream),
                patch("agent.execution.gateway.time", SimpleNamespace(monotonic=lambda: clock[0])),
            ):
                with local_gateway(Path(temporary) / "g.sock", upstream="https://provider.example/v1", model="test-model", provider_key="test-only") as gateway:
                    rejected_id = uuid4()
                    grant_request(str(gateway.socket_path), "issue", rejected_id)
                    rejected = http.client.HTTPConnection("127.0.0.1", gateway.http.server_address[1], timeout=2)
                    try:
                        rejected.request("POST", "/v1/responses", body=json.dumps({"input": "unauthorized", "stream": True}), headers={"Authorization": "Bearer not-issued"})
                        response = rejected.getresponse()
                        self.assertEqual(response.status, 403)
                        response.read()
                    finally:
                        rejected.close()
                    self.assertEqual(dispatched, [])
                    self.assertEqual(received_ids, [])
                    self.assertEqual(
                        grant_request(str(gateway.socket_path), "usage", rejected_id),
                        {"completed_requests": 0, "input_tokens": 0, "output_tokens": 0, "usage_known": True, "in_flight": False, "budget_exhausted": False},
                    )
                    for mode in modes:
                        with self.subTest(mode=mode):
                            clock[0] = 100.0
                            for gate in (release_first, release_second, release_finish):
                                gate.clear()
                            # A finished response completes the request; only its receipt may be missing.
                            finished = mode in {"complete", "missing_usage"}
                            expected = b"" if mode == "before_chunk" else delta + (terminal if mode in {"complete", "after_usage"} else b"")
                            execution_id = uuid4()
                            token = grant_request(str(gateway.socket_path), "issue", execution_id)["token"]
                            connection = http.client.HTTPConnection("127.0.0.1", gateway.http.server_address[1], timeout=2)
                            try:
                                connection.request(
                                    "POST",
                                    "/v1/responses",
                                    body=json.dumps({"model": "test-model", "input": mode, "stream": True}),
                                    headers={"Authorization": "Bearer " + token, "X-Client-Request-Id": spoofed_client_id},
                                )
                                response = connection.getresponse()
                                self.assertEqual(response.status, 200)
                                received = b""
                                if expected:
                                    clock[0] = 105.0
                                    release_first.set()
                                    received = response.read(37)
                                    self.assertEqual(received, expected[:37])
                                    clock[0] = 112.0
                                    release_second.set()
                                    received += response.read(len(expected) - 37)
                                clock[0] = 131.0
                                release_finish.set()
                                if finished:
                                    received += response.read()
                                else:
                                    with self.assertRaises(http.client.IncompleteRead) as failure:
                                        response.read()
                                    received += failure.exception.partial
                            finally:
                                for gate in (release_first, release_second, release_finish):
                                    gate.set()
                                connection.close()
                            self.assertEqual(received, expected)
                            usage = grant_request(str(gateway.socket_path), "usage", execution_id)
                            caller_id = received_ids[-1]
                            self.assertEqual(str(UUID(caller_id)), caller_id)
                            self.assertEqual(UUID(caller_id).version, 4)
                            self.assertNotEqual(caller_id, spoofed_client_id)
                            self.assertEqual(usage["usage_known"], mode == "complete")
                            self.assertFalse(usage["in_flight"])
                            self.assertEqual(usage["completed_requests"], int(finished))
                            self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (13, 7) if mode == "complete" else (0, 0))
                            if finished:
                                self.assertNotIn("transport_failure", usage)
                            else:
                                facts = usage["transport_failure"]
                                self.assertEqual(facts["chunk_count"], 0) if not expected else self.assertGreaterEqual(facts["chunk_count"], 3)
                                self.assertEqual(
                                    {key: value for key, value in facts.items() if key != "chunk_count"},
                                    {
                                        "phase": "provider_read",
                                        "error_type": "IncompleteRead",
                                        "response_bytes": len(expected),
                                        "last_event": ("response.completed" if mode == "after_usage" else "other") if expected else "none",
                                        "provider_status": 200,
                                        "client_request_id": caller_id,
                                        "elapsed_ms": 31_000,
                                        "headers_ms": 2_000,
                                        "first_chunk_ms": 5_000 if expected else None,
                                        "last_chunk_ms": 12_000 if expected else None,
                                        "last_chunk_gap_ms": 19_000 if expected else None,
                                        "max_chunk_gap_ms": 7_000 if expected else None,
                                        "client_write_failed": False,
                                    },
                                )
                            for private in (token, "test-only", spoofed_client_id, "provider-response-id-must-not-be-retained"):
                                self.assertNotIn(private, json.dumps(usage))
            self.assertEqual(dispatched, modes)  # No hidden inference replay.
            self.assertEqual(len(set(received_ids)), len(modes))
            self.assertEqual(provider_timeouts, [])
        finally:
            provider.shutdown()
            provider.server_close()
            serving.join(timeout=2)

    def test_interrupted_provider_stream_retains_safe_failure_facts_without_replay(self) -> None:
        event = b'data: {"type":"response.created","private":"must-not-be-retained"}\n\n'

        class InterruptedResponse:
            status = 200
            sent = False

            def read1(self, _size: int) -> bytes:
                if not self.sent:
                    self.sent = True
                    return event
                raise http.client.IncompleteRead(b"must-not-be-retained")

        with tempfile.TemporaryDirectory() as temporary, patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream:
            upstream.return_value.getresponse.return_value = InterruptedResponse()
            with local_gateway(
                Path(temporary) / "gateway.sock",
                upstream="https://provider.example/v1",
                model="test-model",
                provider_key="test-provider-key",
            ) as gateway:
                execution_id = uuid4()
                token = grant_request(str(gateway.socket_path), "issue", execution_id)["token"]
                connection = http.client.HTTPConnection("127.0.0.1", gateway.http.server_address[1], timeout=2)
                try:
                    connection.request("POST", "/v1/responses", body=json.dumps({"model": "test-model", "input": "private input", "stream": True}), headers={"Authorization": "Bearer " + token})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    with self.assertRaises(http.client.IncompleteRead):
                        response.read()
                finally:
                    connection.close()
                usage = grant_request(str(gateway.socket_path), "usage", execution_id)
                self.assertFalse(usage["usage_known"])
                self.assertFalse(usage["in_flight"])
                self.assertEqual(usage["completed_requests"], 0)
                facts = usage["transport_failure"]
                self.assertEqual(
                    {key: facts[key] for key in ("phase", "error_type", "response_bytes", "last_event", "provider_status")},
                    {
                        "phase": "provider_read",
                        "error_type": "IncompleteRead",
                        "response_bytes": len(event),
                        "last_event": "response.created",
                        "provider_status": 200,
                    },
                )
                self.assertEqual(facts["chunk_count"], 1)
                self.assertIsNone(facts["max_chunk_gap_ms"])
                self.assertEqual(facts["first_chunk_ms"], facts["last_chunk_ms"])
                self.assertGreaterEqual(facts["elapsed_ms"], facts["last_chunk_ms"])
                self.assertFalse(facts["client_write_failed"])
                self.assertEqual(upstream.return_value.request.call_count, 1)
                caller_id = upstream.return_value.request.call_args.args[3]["X-Client-Request-Id"]
                self.assertEqual(facts["client_request_id"], caller_id)
                self.assertEqual(UUID(caller_id).version, 4)
                serialized = json.dumps(usage)
                for private in (token, "test-provider-key", "private input", "must-not-be-retained"):
                    self.assertNotIn(private, serialized)
                run = Path(temporary) / "run"
                (run / "work").mkdir(parents=True)
                destination = Path(temporary) / "diagnostic"
                injected = usage | {"transport_failure": facts | {"headers": {"authorization": "test-provider-key"}, "body": "must-not-be-retained", "provider_id": "must-not-be-retained"}}
                ContainerRuntime._diagnostic(run, destination, "MODEL_PROTOCOL_ERROR", usage=injected)
                saved = json.loads((destination / "diagnostic.json").read_text())
                self.assertEqual(saved.get("transport_failure"), usage["transport_failure"])

    def test_concurrent_http_requests_wait_for_usage_before_next_authorization(self) -> None:
        first_completed = threading.Event()
        second_sent = threading.Event()
        release_first_eof = threading.Event()
        upstream_calls: list[dict] = []
        usages = [{"input_tokens": 3, "output_tokens": 5}, {"input_tokens": 2, "output_tokens": 4}]
        events = [("data: " + json.dumps({"type": "response.completed", "response": {"usage": usage}}) + "\n\n").encode() for usage in usages]

        class FakeResponse:
            status = 200

            def __init__(self, index: int) -> None:
                self.index = index
                self.sent = False

            def read1(self, _size: int) -> bytes:
                if not self.sent:
                    self.sent = True
                    return events[self.index]
                if self.index == 0 and not release_first_eof.wait(10):
                    raise TimeoutError("first provider response did not finish")
                return b""

        class FakeHTTPSConnection:
            def __init__(self, *_args, **_kwargs) -> None:
                self.index = -1

            def request(self, method: str, path: str, body: str, headers: dict) -> None:
                self.index = len(upstream_calls)
                upstream_calls.append({"method": method, "path": path, "body": json.loads(body), "headers": headers})

            def getresponse(self) -> FakeResponse:
                return FakeResponse(self.index)

            def close(self) -> None:
                pass

        def post(port: int, token: str, prompt: str, model: str, *, first: bool = False) -> tuple[int, bytes]:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                connection.request(
                    "POST",
                    "/v1/responses",
                    body=json.dumps({"model": model, "input": prompt, "stream": True, "max_output_tokens": 10}),
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                )
                if not first:
                    second_sent.set()
                response = connection.getresponse()
                if first and response.status == 200:
                    completed_event = response.read(len(events[0]))
                    self.assertEqual(completed_event, events[0])
                    first_completed.set()
                    return response.status, completed_event + response.read()
                return response.status, response.read()
            finally:
                connection.close()

        with tempfile.TemporaryDirectory() as temporary, patch("agent.execution.gateway.http.client.HTTPSConnection", FakeHTTPSConnection):
            with local_gateway(
                Path(temporary) / "gateway.sock",
                upstream="https://provider.example/v1",
                model="test-model",
                provider_key="test-provider-key",
                requests=2,
            ) as gateway:
                execution_id = uuid4()
                token = grant_request(str(gateway.socket_path), "issue", execution_id)["token"]
                port = gateway.http.server_address[1]
                with ThreadPoolExecutor(max_workers=2) as executor:
                    try:
                        first = executor.submit(post, port, token, "main audit", "test-model", first=True)
                        self.assertTrue(first_completed.wait(5), "first completion event was not forwarded")
                        # An agent's next turn can be much larger than an
                        # auxiliary request. Upload must not wait for the
                        # previous provider stream to reach EOF.
                        second = executor.submit(post, port, token, "audit context " * 400_000, "xai-compact")
                        self.assertTrue(second_sent.wait(5), "second request was not sent")
                        self.assertFalse(first.done(), "first provider response unexpectedly reached EOF")
                    finally:
                        release_first_eof.set()
                    first_status, first_body = first.result(timeout=10)
                    second_status, second_body = second.result(timeout=10)
                self.assertEqual((first_status, second_status), (200, 200))
                self.assertEqual((first_body, second_body), tuple(events))
                self.assertEqual([call["body"]["model"] for call in upstream_calls], ["test-model", "test-model"])
                caller_ids = [call["headers"]["X-Client-Request-Id"] for call in upstream_calls]
                self.assertEqual(len(set(caller_ids)), 2)
                self.assertTrue(all(UUID(caller_id).version == 4 for caller_id in caller_ids))
                self.assertEqual(
                    grant_request(str(gateway.socket_path), "usage", execution_id),
                    {"completed_requests": 2, "input_tokens": 5, "output_tokens": 9, "usage_known": True, "in_flight": False, "budget_exhausted": False},
                )

    def test_request_limit_stops_a_runaway_loop_and_allows_identical_retries(self) -> None:
        grants = Grants("test-model", requests=2)
        execution_id = uuid4()
        token = grants.issue(execution_id)
        for _ in range(2):
            # An SDK retry may resend the same body; only the request count limits it.
            lease, purpose = grants.authorize(token, {"model": "test-model", "input": "one", "max_output_tokens": 10}, 20)
            self.assertEqual(purpose, "coding")
            grants.record_usage(lease, {"input_tokens": 60, "output_tokens": 9})
        with self.assertRaises(RequestLimitReached):
            grants.authorize(token, {"model": "test-model", "input": "three", "max_output_tokens": 1}, 1)
        self.assertEqual(grants.usage(execution_id), {"completed_requests": 2, "input_tokens": 120, "output_tokens": 18, "usage_known": True, "in_flight": False, "budget_exhausted": True})
        with self.assertRaises(ValueError):
            grants.authorize(token, {"model": "test-model", "input": "four"}, 1)

    def test_gateway_forwards_shell_web_and_hosted_tools(self) -> None:
        grants = Grants("test-model")
        accepted = {
            "model": "test-model",
            "input": "tools",
            "max_output_tokens": 10,
            "tools": [
                {"type": "function", "name": "run_terminal_command"},
                {"type": "custom", "name": "local"},
                {"type": "web_search"},
                {"type": "x_search"},
                {"type": "code_interpreter"},
            ],
        }
        grants.authorize(grants.issue(uuid4()), accepted, 100)
        self.assertEqual(accepted["tools"][0]["name"], "run_terminal_command")
        with self.assertRaisesRegex(ValueError, "Invalid model tool"):
            grants.authorize(
                grants.issue(uuid4()),
                {"model": "test-model", "input": "other", "max_output_tokens": 10, "tools": ["bash"]},
                100,
            )

    def test_output_cap_applies_to_each_request_without_a_cumulative_budget(self) -> None:
        grants = Grants("test-model", ttl=7200, per_request_output_tokens=262_144)
        token = grants.issue(uuid4())
        first = {"model": "test-model", "input": "first"}
        lease, _ = grants.authorize(token, first, 100)
        self.assertEqual(first["max_output_tokens"], 262_144)
        grants.record_usage(lease, {"input_tokens": 50, "output_tokens": 250_000})
        second = {"model": "test-model", "input": "second", "max_output_tokens": 500_000}
        grants.authorize(token, second, 100)
        self.assertEqual(second["max_output_tokens"], 262_144)
        production = Grants("test-model")
        body = {"model": "test-model", "max_output_tokens": 500_000}
        production.authorize(production.issue(uuid4()), body, 100)
        self.assertEqual(body["max_output_tokens"], 32_768)

    def test_request_byte_limit_remains(self) -> None:
        from agent.execution.gateway import MAX_BODY

        grants = Grants("test-model")
        token = grants.issue(uuid4())
        with self.assertRaises(ValueError):
            grants.authorize(token, {"model": "test-model", "input": "opaque"}, MAX_BODY + 1)
        lease, _ = grants.authorize(token, {"model": "test-model", "input": "opaque"}, MAX_BODY)
        grants.record_usage(lease, {"input_tokens": 1, "output_tokens": 1})

    def test_missing_or_invalid_usage_receipts_keep_the_grant_usable(self) -> None:
        receipts = [None, {"input_tokens": 13}, {"input_tokens": True, "output_tokens": 7}, {"input_tokens": 13, "output_tokens": -1}]
        for receipt in receipts:
            with self.subTest(receipt=receipt):
                grants = Grants("test-model", requests=8)
                token = grants.issue(uuid4())
                lease, _ = grants.authorize(token, {"model": "test-model", "input": "one", "max_output_tokens": 10}, 20)
                grants.record_usage(lease, receipt)
                self.assertEqual(
                    grants.usage(lease.execution_id),
                    {"completed_requests": 1, "input_tokens": 0, "output_tokens": 0, "usage_known": False, "in_flight": False, "budget_exhausted": False},
                )
                self.assertTrue(grants.active(lease))
                grants.authorize(token, {"model": "test-model", "input": "two", "max_output_tokens": 1}, 1)

    def test_a_failed_request_allows_a_bounded_retry(self) -> None:
        grants = Grants("test-model", requests=2)
        token = grants.issue(uuid4())
        lease, _ = grants.authorize(token, {"model": "test-model", "input": "one"}, 20)
        grants.finish_failed(lease, {"phase": "provider_read", "error_type": "IncompleteRead"})
        usage = grants.usage(lease.execution_id)
        self.assertEqual((usage["completed_requests"], usage["usage_known"], usage["in_flight"]), (0, False, False))
        self.assertEqual(usage["transport_failure"]["phase"], "provider_read")
        retried, _ = grants.authorize(token, {"model": "test-model", "input": "one"}, 20)
        grants.record_usage(retried, {"input_tokens": 1, "output_tokens": 1})
        with self.assertRaises(RequestLimitReached):
            grants.authorize(token, {"model": "test-model", "input": "one"}, 20)

    def test_bound_grant_selects_the_frozen_model_for_each_use(self) -> None:
        from documents.model_access import ModelUnavailable

        identity = {"source": "platform", "provider": "responses-compatible", "model": "text-m", "vision_model": "vision-m", "coding_model": "coding-m", "revision": "r" * 64, "deployment": "d" * 64}
        resolved: list[str] = []
        grants = Grants("unconfigured", require_binding=True)
        execution_id = uuid4()
        with self.assertRaises(ValueError):
            grants.issue(execution_id)
        with self.assertRaises(ModelUnavailable), grants.bind(uuid4(), {key: identity[key] for key in ("provider", "model", "revision", "deployment")}, resolved.append):
            pass
        with grants.bind(execution_id, identity, resolved.append):
            token = grants.issue(execution_id)
        self.assertEqual(
            grants.identity(token),
            {"model": "coding-m", "text_model": "text-m", "vision_model": "vision-m", "provider": "responses-compatible", "revision": "r" * 64, "deployment": "d" * 64},
        )
        for requested, purpose in (("vision-m", "vision"), ("text-m", "text"), ("coding-m", "coding"), ("internal-alias", "coding")):
            body = {"model": requested, "input": "x"}
            lease, selected = grants.authorize(token, body, 10)
            self.assertEqual((selected, body["model"]), (purpose, identity["coding_model" if purpose == "coding" else "vision_model" if purpose == "vision" else "model"]))
            grants.record_usage(lease, None)


class ContainerBoundaryTests(unittest.TestCase):
    def test_runtime_requires_the_pinned_pi_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packages = root / "packages"
            packages.mkdir()
            executables = {name: root / name for name in ("python", "pi", "setpriv", "prlimit")}
            for path in executables.values():
                path.touch()
            config = RuntimeConfig(
                state=root / "state",
                gateway_socket=root / "gateway.sock",
                gateway_host="127.0.0.1",
                model="test-model",
                model_revision="0" * 64,
                app_revision="0" * 40,
                sandbox_profile="off",
                python_executable=executables["python"],
                pythonpath=packages,
                agent_executable=executables["pi"],
                setpriv_executable=executables["setpriv"],
                prlimit_executable=executables["prlimit"],
            )
            versions = [
                (f"{PI_VERSION}\n", 0, True),
                ("1.4.0\n", 0, False),
                ("1.5.1\n", 0, False),
                ("1.5.00\n", 0, False),
                (f"{PI_VERSION}\n", 1, False),
            ]
            with patch("agent.execution.isolation.container._running_in_container", return_value=True):
                for output, returncode, accepted in versions:
                    with (
                        self.subTest(output=output, returncode=returncode),
                        patch("agent.execution.isolation.container.subprocess.run", return_value=SimpleNamespace(stdout=output.encode(), returncode=returncode)) as version,
                    ):
                        if accepted:
                            runtime = ContainerRuntime(config)
                            self.assertEqual(runtime.engine_revision(), config.engine_revision())
                        else:
                            with self.assertRaisesRegex(ValueError, f"Verified Pi {PI_VERSION} is required"):
                                ContainerRuntime(config)
                        self.assertEqual(version.call_args.args[0], [str(executables["pi"]), "--version"])

    def test_diagnostic_only_retains_canonical_local_request_uuid(self) -> None:
        caller_id = "12345678-1234-4abc-8def-123456789abc"
        candidates = [
            caller_id,
            "private input or provider credential",
            caller_id.upper(),
            UUID(caller_id).hex,
            "urn:uuid:" + caller_id,
            "12345678-1234-5abc-8def-123456789abc",
            caller_id + "\nprivate output",
            {"Authorization": "private credential"},
            None,
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            (run / "work").mkdir(parents=True)
            for index, candidate in enumerate(candidates):
                with self.subTest(candidate=candidate):
                    failure = {"phase": "provider_read", "client_request_id": candidate, "provider_id": "private provider ID", "body": "private output"}
                    destination = root / f"diagnostic-{index}"
                    ContainerRuntime._diagnostic(run, destination, "MODEL_PROTOCOL_ERROR", usage={"transport_failure": failure})
                    saved = json.loads((destination / "diagnostic.json").read_text())
                    expected = {"phase": "provider_read"}
                    if candidate == caller_id:
                        expected["client_request_id"] = caller_id
                    self.assertEqual(saved["transport_failure"], expected)

    def test_export_waits_for_pending_provider_usage_without_repeating_inference(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            (run / "work" / "result").mkdir(parents=True)
            (run / "work" / "result" / "bundle.json").write_text('{"ok":true}')
            runtime = object.__new__(ContainerRuntime)
            runtime.config = SimpleNamespace(gateway_socket=root / "gateway.sock")
            runtime.journal = root / "journal"
            execution_id = uuid4()
            pending = {"completed_requests": 1, "input_tokens": 3, "output_tokens": 5, "usage_known": False, "in_flight": True, "budget_exhausted": False}
            settled = {"completed_requests": 2, "input_tokens": 5, "output_tokens": 9, "usage_known": True, "in_flight": False, "budget_exhausted": False}
            with patch("agent.execution.isolation.container.grant_request", side_effect=[pending, settled, settled]) as request:
                runtime._export(run, root / "work", True, execution_id, time.monotonic() + 2, threading.Event())
            self.assertEqual([call.args[1] for call in request.call_args_list], ["usage", "usage", "revoke_and_usage"])
            result = json.loads((root / "work" / "result" / "bundle.json").read_text())
            self.assertEqual((result["input_tokens"], result["output_tokens"]), (5, 9))
            self.assertEqual(result["model_requests"], 2)

    def test_guest_cannot_report_its_own_model_usage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pd-zero-", dir="/tmp") as temporary:
            root = Path(temporary)
            with local_gateway(root / "gateway.sock", upstream="https://provider.example/v1", model="test-model", provider_key="test-provider-key", requests=2):
                runtime = object.__new__(ContainerRuntime)
                runtime.model_grant_binding = None
                runtime.config = RuntimeConfig(
                    state=root,
                    gateway_socket=root / "gateway.sock",
                    gateway_host="127.0.0.1",
                    model="test-model",
                    model_revision="0" * 64,
                    app_revision="0" * 40,
                    sandbox_profile="off",
                )
                runtime.journal = root / "journal"
                runtime.attempts = root / "attempts"
                runtime.journal.mkdir()
                runtime.attempts.mkdir()
                runtime.slot = threading.Lock()
                runtime.degraded = False
                inputs = root / "inputs"
                inputs.mkdir()
                spec = CompileSpec.model_construct(execution_id=uuid4())
                run = root / "run"
                (run / "work").mkdir(parents=True)
                (run / "grant").mkdir()

                def cached_guest(run, *_args):
                    grant = json.loads((run / "grant" / "model.json").read_text())
                    # The grant carries no token or turn budget, only its wall deadline.
                    self.assertFalse({"max_turns", "max_total_turns", "max_total_tokens"} & set(grant))
                    (run / "work" / "result").mkdir()
                    (run / "work" / "result" / "bundle.json").write_text('{"ok":true,"input_tokens":999,"output_tokens":999,"model_requests":999}')

                with patch("agent.execution.isolation.container.os.chown"), patch.object(runtime, "_new_run_dir", return_value=run), patch.object(runtime, "_execute", side_effect=cached_guest):
                    runtime.run(spec, inputs, root / "host-work", time.monotonic() + 2, threading.Event())
                exported = json.loads((root / "host-work" / "result" / "bundle.json").read_text())
                self.assertEqual(exported, {"ok": True, "input_tokens": 0, "output_tokens": 0, "model_requests": 0})
                self.assertEqual(exported, json.loads((runtime.journal / (spec.execution_id.hex + ".json")).read_text()))

    def test_journal_recovery_preserves_original_counts_without_new_grant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = object.__new__(ContainerRuntime)
            runtime.journal = root / "journal"
            runtime.journal.mkdir()
            runtime.slot = threading.Lock()
            runtime.degraded = False
            spec = CompileSpec.model_construct(execution_id=uuid4())
            original = {"ok": True, "input_tokens": 60, "output_tokens": 9, "model_requests": 1}
            (runtime.journal / (spec.execution_id.hex + ".json")).write_text(json.dumps(original))
            with patch.object(runtime, "_write_grant") as issue, patch.object(runtime, "_execute") as execute:
                runtime.run(spec, root / "unused", root / "host-work", time.monotonic() + 2, threading.Event())
            self.assertEqual(json.loads((root / "host-work" / "result" / "bundle.json").read_text()), original)
            issue.assert_not_called()
            execute.assert_not_called()

    def test_unknown_usage_exports_a_valid_result_without_counts(self) -> None:
        known = {"completed_requests": 1, "input_tokens": 3, "output_tokens": 2, "usage_known": True, "in_flight": False, "budget_exhausted": False}
        unknown = [known | {"usage_known": False}, {key: value for key, value in known.items() if key != "input_tokens"}, known | {"completed_requests": True}]
        for usage in unknown:
            with self.subTest(usage=usage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run = root / "run"
                (run / "work" / "result").mkdir(parents=True)
                (run / "work" / "result" / "bundle.json").write_text('{"ok":true,"input_tokens":999}')
                runtime = object.__new__(ContainerRuntime)
                runtime.config = SimpleNamespace(gateway_socket=root / "gateway.sock")
                runtime.journal = root / "journal"
                runtime.journal.mkdir()
                with patch("agent.execution.isolation.container.grant_request", return_value=usage):
                    runtime._export(run, root / "work", True, uuid4(), time.monotonic() + 2, threading.Event())
                self.assertEqual(json.loads((root / "work" / "result" / "bundle.json").read_text()), {"ok": True})

    def test_confirmed_budget_exhaustion_is_not_a_model_protocol_error(self) -> None:
        from types import SimpleNamespace

        runtime = object.__new__(ContainerRuntime)
        runtime.config = SimpleNamespace(gateway_socket=Path("/unused-gateway.sock"))
        with patch("agent.execution.isolation.container.grant_request", return_value={"budget_exhausted": True}):
            with self.assertRaises(ControllerFault) as failure:
                runtime._export(Path("/unused-run"), Path("/unused-work"), True, uuid4(), time.monotonic() + 2, threading.Event())
        self.assertEqual(failure.exception.code, "RESOURCE_LIMIT")

    def test_exhausted_guest_fault_remains_resource_limit(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            (run / "work" / "result").mkdir(parents=True)
            (run / "work" / "result" / "fault.json").write_text('{"ok":false,"code":"MODEL_PROTOCOL_ERROR"}')
            runtime = object.__new__(ContainerRuntime)
            runtime.config = SimpleNamespace(gateway_socket=root / "gateway.sock")
            runtime.journal = root / "journal"
            with patch("agent.execution.isolation.container.grant_request", return_value={"budget_exhausted": True}):
                with self.assertRaises(ControllerFault) as failure:
                    runtime._export(run, root / "work", True, uuid4(), time.monotonic() + 2, threading.Event())
            self.assertEqual(failure.exception.code, "RESOURCE_LIMIT")
            self.assertFalse((root / "work" / "result" / "bundle.json").exists())

    def test_request_limit_never_exports_an_artifact(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            (run / "work" / "result").mkdir(parents=True)
            (run / "work" / "result" / "bundle.json").write_text('{"ok":true}')
            runtime = object.__new__(ContainerRuntime)
            runtime.config = SimpleNamespace(gateway_socket=root / "gateway.sock")
            runtime.journal = root / "journal"
            usage = {"completed_requests": 200, "input_tokens": 0, "output_tokens": 0, "usage_known": False, "in_flight": False, "budget_exhausted": True}
            with patch("agent.execution.isolation.container.grant_request", return_value=usage):
                with self.assertRaises(ControllerFault) as failure:
                    runtime._export(run, root / "work", True, uuid4(), time.monotonic() + 2, threading.Event())
            self.assertEqual(failure.exception.code, "RESOURCE_LIMIT")
            self.assertFalse((root / "work" / "result" / "bundle.json").exists())
            self.assertFalse(runtime.journal.exists())

    def test_interrupted_or_invalid_result_does_not_export(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            (run / "work" / "result").mkdir(parents=True)
            (run / "work" / "result" / "bundle.json").write_text("[]")
            runtime = object.__new__(ContainerRuntime)
            runtime.config = SimpleNamespace(gateway_socket=root / "gateway.sock")
            runtime.journal = root / "journal"
            usage = {"completed_requests": 42, "input_tokens": 4_025_240, "output_tokens": 89_857, "usage_known": True, "in_flight": False, "budget_exhausted": True}
            for cancelled, deadline, expected in ((True, time.monotonic() + 2, "CANCELLED"), (False, time.monotonic() - 1, "MODEL_TIMEOUT"), (False, time.monotonic() + 2, "MODEL_PROTOCOL_ERROR")):
                with self.subTest(expected=expected), patch("agent.execution.isolation.container.grant_request", return_value=usage):
                    event = threading.Event()
                    if cancelled:
                        event.set()
                    with self.assertRaises(ControllerFault) as failure:
                        runtime._export(run, root / "work", True, uuid4(), deadline, event)
                    self.assertEqual(failure.exception.code, expected)
                    self.assertFalse((root / "work" / "result" / "bundle.json").exists())
                    self.assertFalse(runtime.journal.exists())

    def test_progress_cannot_claim_unvalidated_completion(self) -> None:
        with self.assertRaises(ValueError):
            Progress(phase="checking", total_clauses=5, verified_clauses=5)
        with self.assertRaises(ValueError):
            Progress(phase="validating", total_clauses=5, verified_clauses=6)

    def test_staged_sources_are_group_read_only_and_symlinks_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "manifest.json").write_text("{}")
            runtime = object.__new__(ContainerRuntime)
            destination = root / "staged"
            with patch("agent.execution.isolation.container.os.chown"):
                runtime._stage_inputs(source, destination)
            self.assertEqual((destination / "manifest.json").stat().st_mode & 0o777, 0o440)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o550)
            (source / "escape.json").symlink_to(source / "manifest.json")
            with patch("agent.execution.isolation.container.os.chown"), self.assertRaises(ControllerFault):
                runtime._stage_inputs(source, root / "rejected")

    def test_guest_process_receives_only_explicit_environment(self) -> None:
        class FinishedProcess:
            pid = 999999
            returncode = 0

            def __init__(self) -> None:
                self.stderr = tempfile.TemporaryFile()

            def poll(self):
                return 0

            def wait(self):
                return 0

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = RuntimeConfig(
                state=root,
                gateway_socket=root / "gateway.sock",
                gateway_host="gateway.internal",
                model="test-model",
                model_revision="0" * 64,
                app_revision="0" * 40,
                sandbox_profile="off",
            )
            runtime = object.__new__(ContainerRuntime)
            runtime.config = config
            runtime.active = {}
            captured = {}

            def spawn(argv, **kwargs):
                captured["argv"] = argv
                captured["env"] = kwargs["env"]
                return FinishedProcess()

            with (
                patch.dict(os.environ, {"DATABASE_URL": "private", "S3_SECRET_ACCESS_KEY": "private"}),
                patch("agent.execution.isolation.container.subprocess.Popen", side_effect=spawn),
                patch("agent.execution.isolation.container._kill_tree"),
            ):  # noqa: E501
                runtime._execute(root, time.monotonic() + 2, threading.Event(), None, uuid4())
            self.assertNotIn("DATABASE_URL", captured["env"])
            self.assertNotIn("S3_SECRET_ACCESS_KEY", captured["env"])
            self.assertIn("--reuid=10001", captured["argv"])
            self.assertIn("--no-new-privs", captured["argv"])
            self.assertEqual(captured["env"]["PPAP_GUEST_ISOLATION"], "container-only")

    def test_guest_traceback_records_bounded_private_failure(self) -> None:
        class FailedProcess:
            pid = 999999
            returncode = 1

            def __init__(self) -> None:
                self.stderr = tempfile.TemporaryFile()
                self.stderr.write(b"beginning-only\n")
                self.stderr.write(b"noisy-prefix\n" * 1024)
                self.stderr.write(b"guest grant: " + b"Z" * 64 + b"\n")
                self.stderr.write(b"Traceback (most recent call last):\nFileNotFoundError: execution.json\n")
                self.stderr.seek(0)

            def poll(self):
                return 1

            def wait(self):
                return 1

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "work").mkdir()
            stage = root / "work" / "audit"
            stage.mkdir()
            (stage / "stdout.jsonl").write_text(
                json.dumps(
                    {
                        "type": "result",
                        "stop_reason": "end_turn",
                        "structured_output": "private full reasoning",
                    }
                )
                + "\n"
            )
            (root / "grant").mkdir()
            (root / "grant" / "model.json").write_text(json.dumps({"token": "Z" * 64}))
            runtime = object.__new__(ContainerRuntime)
            runtime.config = RuntimeConfig(
                state=root,
                gateway_socket=root / "gateway.sock",
                gateway_host="127.0.0.1",
                model="test-model",
                model_revision="0" * 64,
                app_revision="0" * 40,
                sandbox_profile="off",
            )
            runtime.active = {}
            with patch("agent.execution.isolation.container.subprocess.Popen", return_value=FailedProcess()), patch("agent.execution.isolation.container._kill_tree"):
                with self.assertRaises(ControllerFault) as raised:
                    runtime._execute(root, time.monotonic() + 2, threading.Event(), None, uuid4())
            self.assertEqual(raised.exception.code, "MODEL_PROTOCOL_ERROR")
            runtime._diagnostic(root, root / "host-work", raised.exception.code)
            diagnostic = json.loads((root / "host-work" / "diagnostic.json").read_text())
            self.assertEqual(diagnostic["guest"]["exit_code"], 1)
            self.assertLessEqual(len(diagnostic["guest"]["stderr_tail"]), 4096)
            self.assertIn("FileNotFoundError: execution.json", diagnostic["guest"]["stderr_tail"])
            self.assertNotIn("beginning-only", diagnostic["guest"]["stderr_tail"])
            self.assertNotIn("Z" * 64, diagnostic["guest"]["stderr_tail"])
            self.assertIn("[redacted]", diagnostic["guest"]["stderr_tail"])
            self.assertNotIn("private full reasoning", json.dumps(diagnostic))

    def test_diagnostic_io_failure_does_not_mask_guest_fault(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            work = root / "host-work"
            work.mkdir()
            runtime = object.__new__(ContainerRuntime)
            runtime.slot = threading.Lock()
            runtime.degraded = False
            runtime.journal = root / "journal"
            runtime.attempts = root / "attempts"
            runtime.config = RuntimeConfig(
                state=root,
                gateway_socket=root / "gateway.sock",
                gateway_host="127.0.0.1",
                model="test-model",
                model_revision="0" * 64,
                app_revision="0" * 40,
                sandbox_profile="off",
            )
            spec = SnapshotSpec(
                execution_id=uuid4(),
                submission_id=uuid4(),
                project_id="test",
                sha256="0" * 64,
                format=".xlsx",
                semantic_engine_revision="0" * 64,
            )
            run = root / "run"
            run.mkdir()
            with (
                patch.object(runtime, "_new_run_dir", return_value=run),
                patch.object(runtime, "_execute", side_effect=ControllerFault("MODEL_PROTOCOL_ERROR")),
                patch.object(runtime, "_diagnostic", side_effect=PermissionError("diagnostic unwritable")),
            ):
                with self.assertRaises(ControllerFault) as raised:
                    runtime.run(spec, inputs, work, time.monotonic() + 2, threading.Event())
            self.assertEqual(raised.exception.code, "MODEL_PROTOCOL_ERROR")


if __name__ == "__main__":
    unittest.main()
