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
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from ppap_deep.isolation.container import ContainerRuntime, RuntimeConfig
from ppap_deep.isolation.model_gateway import DuplicateInference, Grants, grant_request, local_gateway
from ppap_deep.isolation.progress import Progress
from ppap_deep.semantic.protocol import ControllerFault, ResultBundle, SnapshotSpec


class GatewayGrantTests(unittest.TestCase):
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

        with tempfile.TemporaryDirectory() as temporary, patch("ppap_deep.isolation.model_gateway.http.client.HTTPSConnection") as upstream:
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
                self.assertTrue(usage["uncertain"])
                self.assertFalse(usage["in_flight"])
                self.assertEqual(usage["completed_requests"], 0)
                self.assertEqual(
                    usage.get("transport_failure"),
                    {
                        "phase": "provider_read",
                        "error_type": "IncompleteRead",
                        "response_bytes": len(event),
                        "last_event": "response.created",
                        "provider_status": 200,
                    },
                )
                self.assertEqual(upstream.return_value.request.call_count, 1)
                serialized = json.dumps(usage)
                for private in (token, "test-provider-key", "private input", "must-not-be-retained"):
                    self.assertNotIn(private, serialized)
                run = Path(temporary) / "run"
                (run / "work").mkdir(parents=True)
                destination = Path(temporary) / "diagnostic"
                ContainerRuntime._diagnostic(run, destination, "MODEL_PROTOCOL_ERROR", usage=usage)
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

        with tempfile.TemporaryDirectory() as temporary, patch("ppap_deep.isolation.model_gateway.http.client.HTTPSConnection", FakeHTTPSConnection):
            with local_gateway(
                Path(temporary) / "gateway.sock",
                upstream="https://provider.example/v1",
                model="test-model",
                provider_key="test-provider-key",
                requests=2,
                input_tokens=1000,
                output_tokens=20,
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
                self.assertEqual(
                    grant_request(str(gateway.socket_path), "usage", execution_id),
                    {"completed_requests": 2, "input_tokens": 5, "output_tokens": 9, "uncertain": False, "in_flight": False, "budget_exhausted": False},
                )

    def test_cumulative_budgets_and_duplicate_requests(self) -> None:
        grants = Grants("test-model", requests=2, input_tokens=100, output_tokens=30)
        execution_id = uuid4()
        token = grants.issue(execution_id)
        first = {"model": "test-model", "input": "one", "max_output_tokens": 10}
        lease = grants.authorize(token, first, 226_359)
        grants.record_usage(lease, {"input_tokens": 60, "output_tokens": 9})
        with self.assertRaises(DuplicateInference):
            grants.authorize(token, {"model": "test-model", "input": "one", "max_output_tokens": 10}, 20)
        second_body = {"model": "test-model", "input": "two", "max_output_tokens": 22}
        second = grants.authorize(token, second_body, 30)
        self.assertEqual(second_body["max_output_tokens"], 21)
        grants.record_usage(second, {"input_tokens": 30, "output_tokens": 20})
        self.assertEqual(grants.usage(execution_id), {"completed_requests": 2, "input_tokens": 90, "output_tokens": 29, "uncertain": False, "in_flight": False, "budget_exhausted": False})
        with self.assertRaises(ValueError):
            grants.authorize(token, {"model": "test-model", "input": "three", "max_output_tokens": 1}, 1)
        self.assertTrue(grants.usage(execution_id)["budget_exhausted"])

    def test_confirmed_input_overrun_closes_grant_without_unknown_usage(self) -> None:
        grants = Grants("test-model", input_tokens=100, output_tokens=30)
        token = grants.issue(uuid4())
        lease = grants.authorize(token, {"model": "test-model", "input": "opaque"}, 226_359)
        grants.record_usage(lease, {"input_tokens": 101, "output_tokens": 2})
        self.assertEqual(
            grants.usage(lease.execution_id),
            {
                "completed_requests": 1,
                "input_tokens": 101,
                "output_tokens": 2,
                "uncertain": False,
                "in_flight": False,
                "budget_exhausted": True,
            },
        )
        self.assertFalse(grants.active(lease))
        with self.assertRaises(ValueError):
            grants.authorize(token, {"model": "test-model", "input": "next"}, 1)

    def test_request_byte_limit_remains_independent_of_token_usage(self) -> None:
        from ppap_deep.isolation.model_gateway import MAX_BODY

        grants = Grants("test-model", input_tokens=100)
        token = grants.issue(uuid4())
        with self.assertRaises(ValueError):
            grants.authorize(token, {"model": "test-model", "input": "opaque"}, MAX_BODY + 1)
        lease = grants.authorize(token, {"model": "test-model", "input": "opaque"}, MAX_BODY)
        grants.record_usage(lease, {"input_tokens": 1, "output_tokens": 1})

    def test_unknown_provider_usage_closes_grant(self) -> None:
        grants = Grants("test-model", requests=8, input_tokens=100, output_tokens=30)
        token = grants.issue(uuid4())
        lease = grants.authorize(token, {"model": "test-model", "input": "one", "max_output_tokens": 10}, 20)
        grants.finish_unknown(lease)
        self.assertEqual(
            grants.usage(lease.execution_id),
            {
                "completed_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "uncertain": True,
                "in_flight": False,
                "budget_exhausted": False,
            },
        )
        with self.assertRaises(ValueError):
            grants.authorize(token, {"model": "test-model", "input": "two", "max_output_tokens": 1}, 1)


class ContainerBoundaryTests(unittest.TestCase):
    def test_confirmed_final_input_overrun_exports_bundle_for_validation(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            (run / "work" / "result").mkdir(parents=True)
            runtime = object.__new__(ContainerRuntime)
            runtime.config = SimpleNamespace(gateway_socket=root / "gateway.sock")
            runtime.journal = root / "journal"
            execution_id = uuid4()
            bundle = ResultBundle.model_validate(
                {
                    "execution_id": str(execution_id),
                    "semantic_engine_revision": "0" * 64,
                    "documents": {},
                    "rule_sources": {},
                    "plans": {},
                    "report": {
                        "checks": [
                            {
                                "audit_id": "file-1",
                                "rule_id": "R1",
                                "status": "UNABLE",
                                "reason": "No reliable evidence",
                                "total": 1,
                                "checked": 0,
                                "unknown": 1,
                                "evidence": [],
                                "diagnostic": "budget_exhausted",
                            }
                        ]
                    },
                    "trace": {
                        "bindings": [],
                        "proofs": [
                            {
                                "kind": "semantic",
                                "audit_id": "file-1",
                                "rule_id": "R1",
                                "scope_basis": "one record",
                                "records": [{"record_id": "row-1", "role": "prerequisite", "status": "UNABLE", "reason": "No reliable evidence", "evidence": []}],
                            }
                        ],
                    },
                }
            )
            (run / "work" / "result" / "bundle.json").write_text(bundle.model_dump_json())
            usage = {"completed_requests": 42, "input_tokens": 4_025_240, "output_tokens": 89_857, "uncertain": False, "in_flight": False, "budget_exhausted": True}
            with patch("ppap_deep.isolation.container.grant_request", return_value=usage):
                runtime._export(run, root / "work", True, execution_id, time.monotonic() + 2, threading.Event())
            exported = json.loads((root / "work" / "result" / "bundle.json").read_text())
            self.assertEqual((exported["input_tokens"], exported["output_tokens"]), (4_025_240, 89_857))
            self.assertEqual(exported, json.loads((runtime.journal / (execution_id.hex + ".json")).read_text()))

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
            pending = {"completed_requests": 1, "input_tokens": 3, "output_tokens": 5, "uncertain": True, "in_flight": True, "budget_exhausted": False}
            settled = {"completed_requests": 2, "input_tokens": 5, "output_tokens": 9, "uncertain": False, "in_flight": False, "budget_exhausted": False}
            with patch("ppap_deep.isolation.container.grant_request", side_effect=[pending, settled]) as request:
                runtime._export(run, root / "work", True, execution_id, time.monotonic() + 2, threading.Event())
            self.assertEqual([call.args[1] for call in request.call_args_list], ["usage", "usage"])
            result = json.loads((root / "work" / "result" / "bundle.json").read_text())
            self.assertEqual((result["input_tokens"], result["output_tokens"]), (5, 9))

    def test_confirmed_budget_exhaustion_is_not_a_model_protocol_error(self) -> None:
        from types import SimpleNamespace

        runtime = object.__new__(ContainerRuntime)
        runtime.config = SimpleNamespace(gateway_socket=Path("/unused-gateway.sock"))
        with patch("ppap_deep.isolation.container.grant_request", return_value={"budget_exhausted": True}):
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
            with patch("ppap_deep.isolation.container.grant_request", return_value={"budget_exhausted": True}):
                with self.assertRaises(ControllerFault) as failure:
                    runtime._export(run, root / "work", True, uuid4(), time.monotonic() + 2, threading.Event())
            self.assertEqual(failure.exception.code, "RESOURCE_LIMIT")
            self.assertFalse((root / "work" / "result" / "bundle.json").exists())

    def test_unknown_exhausted_usage_does_not_export_bundle(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            (run / "work" / "result").mkdir(parents=True)
            (run / "work" / "result" / "bundle.json").write_text('{"ok":true}')
            runtime = object.__new__(ContainerRuntime)
            runtime.config = SimpleNamespace(gateway_socket=root / "gateway.sock")
            runtime.journal = root / "journal"
            usage = {"completed_requests": 42, "input_tokens": 4_025_240, "output_tokens": 89_857, "uncertain": True, "in_flight": False, "budget_exhausted": True}
            with patch("ppap_deep.isolation.container.grant_request", return_value=usage):
                with self.assertRaises(ControllerFault) as failure:
                    runtime._export(run, root / "work", True, uuid4(), time.monotonic() + 2, threading.Event())
            self.assertEqual(failure.exception.code, "MODEL_PROTOCOL_ERROR")
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
            usage = {"completed_requests": 42, "input_tokens": 4_025_240, "output_tokens": 89_857, "uncertain": False, "in_flight": False, "budget_exhausted": True}
            for cancelled, deadline, expected in ((True, time.monotonic() + 2, "CANCELLED"), (False, time.monotonic() - 1, "MODEL_TIMEOUT"), (False, time.monotonic() + 2, "MODEL_PROTOCOL_ERROR")):
                with self.subTest(expected=expected), patch("ppap_deep.isolation.container.grant_request", return_value=usage):
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
            with patch("ppap_deep.isolation.container.os.chown"):
                runtime._stage_inputs(source, destination)
            self.assertEqual((destination / "manifest.json").stat().st_mode & 0o777, 0o440)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o550)
            (source / "escape.json").symlink_to(source / "manifest.json")
            with patch("ppap_deep.isolation.container.os.chown"), self.assertRaises(ControllerFault):
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
                patch("ppap_deep.isolation.container.subprocess.Popen", side_effect=spawn),
                patch("ppap_deep.isolation.container._kill_tree"),
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
            with patch("ppap_deep.isolation.container.subprocess.Popen", return_value=FailedProcess()), patch("ppap_deep.isolation.container._kill_tree"):
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
