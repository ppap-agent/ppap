"""Run public Deep runtime contracts inside the Linux delivery image.

The external guest process and model responses are controlled. Cache cases run
real guest parsing and validation, not model judgment or hostile-code isolation.
"""

from __future__ import annotations

import http.client
import io
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

from agent.execution.gateway import local_gateway
from agent.execution.guest import Manifest
from agent.execution.isolation.container import ContainerRuntime, RuntimeConfig
from audit.policy import GROK_VERSION
from audit.protocol import ControllerFault, DocumentRef, RuleRef, SnapshotSpec, SubmissionSpec
COMPLETED_EVENT = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":5}}}\n\n'


def request_model(work: Path, *, incomplete_response: bool = False, request_input: str = "stand-in guest") -> None:
    grant = json.loads((work.parent / "grant" / "model.json").read_text())
    connection = http.client.HTTPConnection("127.0.0.1", urlsplit(grant["base_url"]).port, timeout=5)
    try:
        connection.request(
            "POST",
            "/v1/responses",
            body=json.dumps({"model": "test-model", "input": request_input, "stream": True}),
            headers={"Authorization": "Bearer " + grant["token"]},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise AssertionError(f"test guest could not use its execution grant: HTTP {response.status}")
        try:
            response.read()
        except http.client.IncompleteRead:
            if not incomplete_response:
                raise
    finally:
        connection.close()


def make_runtime(root: Path) -> ContainerRuntime:
    grok = root / "grok-version"
    grok.write_text(f"#!/bin/sh\necho 'grok {GROK_VERSION} test-only'\n")
    grok.chmod(0o700)
    return ContainerRuntime(
        RuntimeConfig(
            state=root / "state",
            gateway_socket=root / "gateway.sock",
            gateway_host="127.0.0.1",
            model="test-model",
            model_revision="0" * 64,
            app_revision="0" * 40,
            sandbox_profile="off",
            grok_executable=grok,
        )
    )


class ContainerQueueTests(unittest.TestCase):
    def test_waiting_request_times_out_without_waiting_for_the_running_guest(self):
        self._assert_waiting_caller_returns(cancel=False)

    def test_waiting_request_can_cancel_without_stopping_the_running_guest(self):
        self._assert_waiting_caller_returns(cancel=True)

    def _assert_waiting_caller_returns(self, *, cancel: bool):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = make_runtime(root)
            inputs = root / "inputs"
            inputs.mkdir()
            (inputs / "document.xlsx").write_bytes(b"external guest input")
            first_work = root / "first-work"
            second_work = root / "second-work"
            first_work.mkdir()
            second_work.mkdir()
            started = threading.Event()
            waiting = threading.Event()
            release = threading.Event()

            class HeldGuest:
                # Outside Linux's PID domain: production cleanup cannot signal
                # an unrelated process when observing this external stand-in.
                pid = 99_999_999
                returncode = None

                def __init__(self, *, cwd, **_kwargs):
                    self.stderr = tempfile.TemporaryFile()
                    self.work = Path(cwd)
                    started.set()

                def poll(self):
                    if not release.is_set():
                        return None
                    result = self.work / "result"
                    result.mkdir(exist_ok=True)
                    (result / "bundle.json").write_text(json.dumps({"snapshot": "external guest result"}))
                    self.returncode = 0
                    return 0

                def wait(self):
                    self.returncode = 0
                    return 0

            first_spec = SnapshotSpec(
                execution_id=uuid4(),
                submission_id=uuid4(),
                project_id="test-tenant",
                sha256="0" * 64,
                format=".xlsx",
                semantic_engine_revision="0" * 64,
            )
            second_spec = first_spec.model_copy(update={"execution_id": uuid4()})
            waiting_cancel = threading.Event()
            expected = "CANCELLED" if cancel else "MODEL_TIMEOUT"

            def wait_for_execution():
                waiting.set()
                runtime.run(second_spec, inputs, second_work, time.monotonic() + (20 if cancel else 0.15), waiting_cancel)

            with patch("subprocess.Popen", side_effect=lambda _args, **kwargs: HeldGuest(**kwargs)) as spawn:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(runtime.run, first_spec, inputs, first_work, time.monotonic() + 20, threading.Event())
                    try:
                        self.assertTrue(started.wait(5), "first guest did not start")
                        second = pool.submit(wait_for_execution)
                        self.assertTrue(waiting.wait(5), "waiting caller was not scheduled")
                        if cancel:
                            with self.assertRaises(TimeoutError):
                                second.result(timeout=0.2)
                            waiting_cancel.set()
                        try:
                            with self.assertRaises(ControllerFault) as expired:
                                second.result(timeout=3)
                        except TimeoutError:
                            release.set()
                            first.result(timeout=5)
                            with self.assertRaises(ControllerFault) as after_release:
                                second.result(timeout=5)
                            self.fail(f"Waiting caller returned {after_release.exception.code} only after the running guest was released")
                        self.assertEqual(expired.exception.code, expected)
                        self.assertFalse(first.done(), "running guest was released before the waiting request returned")
                        self.assertEqual(spawn.call_count, 1)
                        self.assertFalse((second_work / "result").exists())
                        third_work = root / "third-work"
                        third_work.mkdir()
                        third_spec = first_spec.model_copy(update={"execution_id": uuid4()})
                        third = pool.submit(runtime.run, third_spec, inputs, third_work, time.monotonic() + 0.15, threading.Event())
                        with self.assertRaises(ControllerFault) as still_held:
                            third.result(timeout=3)
                        self.assertEqual(still_held.exception.code, "MODEL_TIMEOUT")
                        self.assertEqual(spawn.call_count, 1, "a waiting caller released another guest's slot")
                        self.assertFalse(first.done())
                        self.assertFalse((third_work / "result").exists())
                    finally:
                        release.set()
                    first.result(timeout=5)
                fourth_work = root / "fourth-work"
                fourth_work.mkdir()
                runtime.run(first_spec.model_copy(update={"execution_id": uuid4()}), inputs, fourth_work, time.monotonic() + 2, threading.Event())
                self.assertEqual(spawn.call_count, 2)
            self.assertEqual(json.loads((first_work / "result" / "bundle.json").read_text()), {"snapshot": "external guest result"})
            self.assertEqual(json.loads((fourth_work / "result" / "bundle.json").read_text()), {"snapshot": "external guest result"})
            self.assertEqual(list((root / "state").glob("run-*")), [])


class ContainerJournalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = make_runtime(self.root)
        self.gateway = self.enterContext(local_gateway(self.root / "gateway.sock", upstream="https://provider.example/v1", model="test-model", provider_key="test-provider-key"))
        self.provider = self.enterContext(patch("http.client.HTTPSConnection")).return_value
        self.provider.getresponse.side_effect = lambda: SimpleNamespace(status=200, read1=io.BytesIO(COMPLETED_EVENT).read1)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        (self.inputs / "document.xlsx").write_bytes(b"external guest input")

    def _submission(self):
        spec = SubmissionSpec(
            execution_id=uuid4(),
            submission_id=uuid4(),
            project_id="test-tenant",
            documents=[DocumentRef(audit_id="document-1", filename="document.xlsx", sha256="0" * 64, format=".xlsx", role="target")],
            rules=[RuleRef(rule_id="rule-1", rule_version_id="version-1", sha256="0" * 64, format=".json", plan_revision="0" * 64, clauses=[{"id": "1", "rule_to_check": "test clause"}])],
            targets={"document-1": "rule-1"},
            policy_revision="0" * 64,
            semantic_engine_revision=self.runtime.engine_revision(),
        )
        spec = spec.model_copy(update={"execution_id": spec.expected_execution_id()})
        manifest = Manifest(spec=spec, documents={"document-1": "document.xlsx"}, rules={"rule-1": "rule.json"}, cached_snapshots={}, cached_plans={})
        (self.inputs / "manifest.json").write_text(manifest.model_dump_json())
        (self.inputs / "rule.json").write_text('{"test": "controlled guest input"}')
        return spec

    def _work(self, name: str) -> Path:
        work = self.root / name
        work.mkdir()
        return work

    def _guest(self, *, fault: bool = False, incomplete_response: bool = False, started: threading.Event | None = None, release: threading.Event | None = None):
        class ModelGuest:
            pid = 99_999_999
            returncode = None

            def __init__(self, *, cwd, **_kwargs):
                self.stderr = tempfile.TemporaryFile()
                self.work = Path(cwd)
                if started is not None:
                    started.set()

            def poll(self):
                if release is not None and not release.is_set():
                    return None
                request_model(self.work, incomplete_response=incomplete_response)
                result = self.work / "result"
                result.mkdir()
                filename = "fault.json" if fault else "bundle.json"
                value = {"ok": False, "code": "MODEL_PROTOCOL_ERROR"} if fault else {"external_result": "completed once"}
                (result / filename).write_text(json.dumps(value))
                self.returncode = 0
                return 0

            def wait(self):
                self.returncode = 0
                return 0

        return lambda _args, **kwargs: ModelGuest(**kwargs)

    def test_successful_execution_reads_back_after_runtime_recreation_without_new_work(self):
        spec = self._submission()
        first = self._work("first")
        second = self._work("second")
        with patch("subprocess.Popen", side_effect=self._guest()) as spawn:
            self.runtime.run(spec, self.inputs, first, time.monotonic() + 10, threading.Event())
            self.runtime.run(spec, self.inputs, second, time.monotonic() + 10, threading.Event())
            self.assertEqual(spawn.call_count, 1)
        expected = {"external_result": "completed once", "input_tokens": 3, "output_tokens": 5, "model_requests": 1}
        self.assertEqual(json.loads((first / "result" / "bundle.json").read_text()), expected)
        self.assertEqual(json.loads((second / "result" / "bundle.json").read_text()), expected)
        restarted = make_runtime(self.root)
        for _ in range(2):
            restarted.recover()
        third = self._work("after-restart")
        with patch("subprocess.Popen", side_effect=AssertionError("journal replay started a guest")) as spawn:
            restarted.run(spec, self.inputs, third, time.monotonic() + 10, threading.Event())
            spawn.assert_not_called()
        self.assertEqual(json.loads((third / "result" / "bundle.json").read_text()), expected)
        self.provider.request.assert_called_once()

    def test_concurrent_consumers_share_the_same_successful_execution(self):
        spec = self._submission()
        first_work = self._work("first")
        second_work = self._work("second")
        started, waiting, release = threading.Event(), threading.Event(), threading.Event()

        def second_call():
            waiting.set()
            self.runtime.run(spec, self.inputs, second_work, time.monotonic() + 15, threading.Event())

        with patch("subprocess.Popen", side_effect=self._guest(started=started, release=release)) as spawn:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.runtime.run, spec, self.inputs, first_work, time.monotonic() + 15, threading.Event())
                try:
                    self.assertTrue(started.wait(5), "first guest did not start")
                    second = pool.submit(second_call)
                    self.assertTrue(waiting.wait(5), "second caller was not scheduled")
                    with self.assertRaises(TimeoutError):
                        second.result(timeout=0.2)
                    self.assertFalse(first.done())
                    self.assertEqual(spawn.call_count, 1)
                finally:
                    release.set()
                first.result(timeout=5)
                second.result(timeout=5)
            self.assertEqual(spawn.call_count, 1)
        expected = {"external_result": "completed once", "input_tokens": 3, "output_tokens": 5, "model_requests": 1}
        for work in (first_work, second_work):
            self.assertEqual(json.loads((work / "result" / "bundle.json").read_text()), expected)
        self.provider.request.assert_called_once()

    def test_failed_execution_does_not_replay_but_a_new_submission_can_run(self):
        self._assert_failed_execution_is_not_replayed(unknown=False)

    def test_unconfirmed_provider_completion_is_preserved_and_not_replayed(self):
        self._assert_failed_execution_is_not_replayed(unknown=True)

    def _assert_failed_execution_is_not_replayed(self, *, unknown: bool):
        if unknown:
            event = b'data: {"type":"response.created"}\n\n'
            self.provider.getresponse.side_effect = lambda: SimpleNamespace(status=200, read1=io.BytesIO(event).read1)
        spec = self._submission()
        first = self._work("failed")
        with patch("subprocess.Popen", side_effect=self._guest(fault=True, incomplete_response=unknown)) as spawn:
            with self.assertRaises(ControllerFault) as failed:
                self.runtime.run(spec, self.inputs, first, time.monotonic() + 10, threading.Event())
            self.assertEqual(failed.exception.code, "MODEL_PROTOCOL_ERROR")
            self.assertEqual(spawn.call_count, 1)
        self.provider.request.assert_called_once()
        self.assertFalse((first / "result").exists())
        diagnostic = json.loads((first / "diagnostic.json").read_text())
        self.assertEqual(diagnostic["usage"]["uncertain"], unknown)
        self.assertFalse(diagnostic["usage"]["in_flight"])
        self.assertEqual(diagnostic["usage"]["completed_requests"], 0 if unknown else 1)
        self.assertEqual(diagnostic["usage"]["input_tokens"], 0 if unknown else 3)
        self.assertEqual(diagnostic["usage"]["output_tokens"], 0 if unknown else 5)
        if unknown:
            self.assertEqual(diagnostic["transport_failure"]["last_event"], "response.created")

        restarted = make_runtime(self.root)
        for _ in range(2):
            restarted.recover()
        replay = self._work("replay-failed")
        with patch("subprocess.Popen", side_effect=AssertionError("failed paid execution was replayed")) as spawn:
            with self.assertRaises(ControllerFault) as rejected:
                restarted.run(spec, self.inputs, replay, time.monotonic() + 10, threading.Event())
            self.assertEqual(rejected.exception.code, "MODEL_PROTOCOL_ERROR")
            spawn.assert_not_called()
        self.provider.request.assert_called_once()
        self.assertFalse((replay / "result").exists())

        # A separately accepted submission is new work, not an automatic
        # retry of the failed or unconfirmed execution above.
        self.provider.getresponse.side_effect = lambda: SimpleNamespace(status=200, read1=io.BytesIO(COMPLETED_EVENT).read1)
        original_manifest = (self.inputs / "manifest.json").read_bytes()
        new_spec = self._submission()
        self.assertNotEqual(new_spec.execution_id, spec.execution_id)
        self.assertEqual(new_spec.documents, spec.documents)
        self.assertEqual(new_spec.rules, spec.rules)
        retried = self._work("new-submission")
        with patch("subprocess.Popen", side_effect=self._guest()) as spawn:
            restarted.run(new_spec, self.inputs, retried, time.monotonic() + 10, threading.Event())
            spawn.assert_called_once()
        self.assertEqual(self.provider.request.call_count, 2)
        self.assertEqual(json.loads((retried / "result" / "bundle.json").read_text()), {"external_result": "completed once", "input_tokens": 3, "output_tokens": 5, "model_requests": 1})
        (self.inputs / "manifest.json").write_bytes(original_manifest)
        with patch("subprocess.Popen", side_effect=AssertionError("new submission erased the failed execution")) as spawn:
            with self.assertRaises(ControllerFault) as still_failed:
                restarted.run(spec, self.inputs, self._work("failed-after-new"), time.monotonic() + 10, threading.Event())
            self.assertEqual(still_failed.exception.code, "MODEL_PROTOCOL_ERROR")
            spawn.assert_not_called()
        self.assertEqual(self.provider.request.call_count, 2)



if __name__ == "__main__":
    unittest.main()
