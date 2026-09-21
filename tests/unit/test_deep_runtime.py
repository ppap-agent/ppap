"""Controller gVisor runtime and guest auditor. No live Docker required."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import pathsetup

ROOT = pathsetup.ROOT

from ppap_core.audits import DEEP_PROTOCOL_REVISION
from ppap_deep.client import DeepError
from ppap_deep.guest import evaluate
from ppap_deep.protocol import ExecuteRequest, FrozenFile
from ppap_deep.runtime import DEFAULT_DOCKER_RUNTIME, DEFAULT_GUEST_IMAGE, GvisorRuntime


def _spec(*roles: str) -> ExecuteRequest:
    files = []
    for index, role in enumerate(roles, start=1):
        ident = f"00000000-0000-4000-8000-00000000000{index}"
        files.append(
            FrozenFile(
                file_version_id=ident,
                role=role,  # type: ignore[arg-type]
                filename="report.xlsx" if role == "target" else "context.pdf",
                sha256="ab",
                rule_version_id="00000000-0000-4000-8000-0000000000a5" if role == "target" else None,
                rule_title="资料就绪" if role == "target" else None,
                rule_body={"kind": "presence"} if role == "target" else None,
            )
        )
    return ExecuteRequest(
        protocol_revision=DEEP_PROTOCOL_REVISION,
        round_id="r1",
        files=files,
        executor="ppap-deep",
        executor_revision=DEEP_PROTOCOL_REVISION,
    )


class GuestAuditorTests(unittest.TestCase):
    def test_presence_target_and_reference(self):
        spec = _spec("target", "reference").model_dump()
        result = evaluate(
            spec,
            {
                spec["files"][0]["file_version_id"]: b"target-bytes",
                spec["files"][1]["file_version_id"]: b"reference-bytes",
            },
        )
        self.assertEqual(result["findings"][0]["status"], "PASS")
        self.assertEqual(result["findings"][0]["item"], "资料就绪")
        self.assertIn("report.xlsx", result["coverage"]["checked"])
        self.assertTrue(any("context.pdf" in row for row in result["coverage"]["unchecked"]))
        self.assertTrue(str(result["note"]).startswith("deep_audit kernel="))

    def test_empty_target_is_ng(self):
        spec = _spec("target").model_dump()
        result = evaluate(spec, {spec["files"][0]["file_version_id"]: b""})
        self.assertEqual(result["findings"][0]["status"], "NG")


class GvisorRuntimeTests(unittest.TestCase):
    def test_argv_uses_named_runtime_without_host_socket_flag(self):
        runtime = GvisorRuntime()
        argv = runtime._argv(Path("/tmp/ppap-deep-job"))
        self.assertEqual(argv[0], "docker")
        self.assertIn("--runtime", argv)
        self.assertEqual(argv[argv.index("--runtime") + 1], DEFAULT_DOCKER_RUNTIME)
        self.assertEqual(argv[argv.index("-v") + 1], "/tmp/ppap-deep-job:/work:rw")
        self.assertIn("--user", argv)
        self.assertEqual(argv[argv.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn(DEFAULT_GUEST_IMAGE, argv)
        self.assertNotIn("docker.sock", " ".join(argv))
        joined = " ".join(argv)
        self.assertNotIn("/var/run", joined)

    def test_execute_reads_guest_result_and_cleans_job_dir(self):
        kept: dict[str, str] = {}

        def runner(argv, timeout):
            work = Path(argv[argv.index("-v") + 1].split(":", 1)[0])
            kept["job"] = str(work)
            spec = json.loads((work / "spec.json").read_text(encoding="utf-8"))
            files = {path.name: path.read_bytes() for path in (work / "files").iterdir()}
            (work / "result.json").write_text(json.dumps(evaluate(spec, files)), encoding="utf-8")
            self.assertTrue((work / "guest.py").is_file())
            self.assertGreater(timeout, 0)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        runtime = GvisorRuntime(runner=runner)
        spec = _spec("target")
        result = runtime.execute(spec, {spec.files[0].file_version_id: b"live-bytes"})
        self.assertEqual(result.findings[0].status, "PASS")
        self.assertFalse(Path(kept["job"]).exists())

    def test_nonzero_docker_status_is_execution_failure(self):
        def runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 1, b"", b"")

        runtime = GvisorRuntime(runner=runner)
        spec = _spec("target")
        with self.assertRaises(DeepError) as raised:
            runtime.execute(spec, {spec.files[0].file_version_id: b"x"})
        self.assertEqual(raised.exception.code, "deep_execution_failed")

    def test_cleanup_failure_does_not_return_success(self):
        def runner(argv, timeout):
            work = Path(argv[argv.index("-v") + 1].split(":", 1)[0])
            spec = json.loads((work / "spec.json").read_text(encoding="utf-8"))
            files = {path.name: path.read_bytes() for path in (work / "files").iterdir()}
            (work / "result.json").write_text(json.dumps(evaluate(spec, files)), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        runtime = GvisorRuntime(runner=runner)
        spec = _spec("target")
        job_dir = tempfile.mkdtemp(prefix="ppap-deep-cleanup-")
        self.addCleanup(lambda: shutil.rmtree(job_dir, ignore_errors=True))
        with patch("ppap_deep.runtime.tempfile.mkdtemp", return_value=job_dir):
            with patch("ppap_deep.runtime.shutil.rmtree", side_effect=OSError("busy")):
                with self.assertRaises(DeepError) as raised:
                    runtime.execute(spec, {spec.files[0].file_version_id: b"x"})
        self.assertEqual(raised.exception.code, "deep_cleanup_failed")


class DeepLiveOverlayTests(unittest.TestCase):
    def test_overlay_shares_uds_without_giving_worker_docker(self):
        overlay = (ROOT / "deploy" / "isolated-dev" / "deep-live.yaml").read_text()
        default = (ROOT / "deploy" / "isolated-dev" / "compose.yaml").read_text()
        self.assertIn("PPAP_DEEP_CONTROLLER_SOCKET: /run/ppap-deep/controller.sock", overlay)
        self.assertIn("${PPAP_DEEP_SOCKET_DIR:-/tmp/ppap-deep}:/run/ppap-deep", overlay)
        self.assertIn("127.0.0.1:9000:9000", overlay)
        self.assertNotIn("docker.sock", overlay)
        self.assertNotIn("/var/run/docker", overlay)
        self.assertNotIn("127.0.0.1:9000:9000", default)
        main = (ROOT / "packages" / "ppap_deep" / "__main__.py").read_text()
        self.assertIn("runtime_from_env", main)
        self.assertNotIn("ScriptedRuntime", main)

    def test_controller_cli_is_not_selected_by_worker_settings(self):
        settings = (ROOT / "packages" / "ppap_core" / "settings.py").read_text()
        self.assertNotIn("PPAP_DEEP_DOCKER_RUNTIME", settings)
        self.assertNotIn("PPAP_DEEP_GUEST_IMAGE", settings)
        self.assertNotIn("PPAP_DEEP_GROK_BIN", settings)
        worker = (ROOT / "services" / "worker" / "ppap_worker" / "__init__.py").read_text()
        self.assertNotIn("GvisorRuntime", worker)
        self.assertNotIn("docker run", worker)
