"""Run public Deep runtime contracts inside the Linux delivery image.

The external guest process is controlled; no model is called and this does not
test the guest's semantic output or its isolation from hostile code.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from ppap_deep.isolation.container import ContainerRuntime, RuntimeConfig
from ppap_deep.semantic.protocol import ControllerFault, SnapshotSpec


class ContainerQueueTests(unittest.TestCase):
    def test_waiting_request_times_out_without_waiting_for_the_running_guest(self):
        self._assert_waiting_caller_returns(cancel=False)

    def test_waiting_request_can_cancel_without_stopping_the_running_guest(self):
        self._assert_waiting_caller_returns(cancel=True)

    def _assert_waiting_caller_returns(self, *, cancel: bool):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            grok = root / "grok-version"
            grok.write_text("#!/bin/sh\necho 'grok 1.0.40 test-only'\n")
            grok.chmod(0o700)
            config = RuntimeConfig(
                state=root / "state",
                gateway_socket=root / "gateway.sock",
                gateway_host="127.0.0.1",
                model="test-model",
                model_revision="0" * 64,
                app_revision="0" * 40,
                sandbox_profile="off",
                grok_executable=grok,
            )
            runtime = ContainerRuntime(config)
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
                    finally:
                        release.set()
                    first.result(timeout=5)
                third_work = root / "third-work"
                third_work.mkdir()
                runtime.run(first_spec.model_copy(update={"execution_id": uuid4()}), inputs, third_work, time.monotonic() + 2, threading.Event())
                self.assertEqual(spawn.call_count, 2)
            self.assertEqual(json.loads((first_work / "result" / "bundle.json").read_text()), {"snapshot": "external guest result"})
            self.assertEqual(json.loads((third_work / "result" / "bundle.json").read_text()), {"snapshot": "external guest result"})
            self.assertEqual(list(config.state.glob("run-*")), [])


if __name__ == "__main__":
    unittest.main()
