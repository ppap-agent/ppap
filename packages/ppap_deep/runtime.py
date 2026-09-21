"""Controller-owned isolated guest. Worker never selects this runtime or passes host execution fields."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from ppap_deep.client import DeepError
from ppap_deep.protocol import ExecuteRequest, ExecuteResponse

DEFAULT_DOCKER_RUNTIME = "ppap-gvisor-verify"
DEFAULT_GUEST_IMAGE = "python:3.12-slim-bookworm"
GUEST_SCRIPT = Path(__file__).with_name("guest.py")
Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess]


def _docker_run(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, timeout=timeout, check=False)


class GvisorRuntime:
    """Spawn a gVisor guest via the docker CLI. Does not accept Worker-supplied image/path/command."""

    def __init__(
        self,
        *,
        docker_runtime: str = DEFAULT_DOCKER_RUNTIME,
        image: str = DEFAULT_GUEST_IMAGE,
        grok_bin: str = "",
        grok_config: str = "",
        runner: Runner | None = None,
        timeout: float = 120.0,
    ):
        self.docker_runtime = str(docker_runtime or "").strip() or DEFAULT_DOCKER_RUNTIME
        self.image = str(image or "").strip() or DEFAULT_GUEST_IMAGE
        self.grok_bin = str(grok_bin or "").strip()
        self.grok_config = str(grok_config or "").strip()
        self._runner = runner or _docker_run
        self.timeout = timeout

    def execute(self, spec: ExecuteRequest, files: dict[str, bytes]) -> ExecuteResponse:
        job_dir = tempfile.mkdtemp(prefix="ppap-deep-job-")
        result: ExecuteResponse | None = None
        error: DeepError | None = None
        try:
            work = Path(job_dir)
            (work / "files").mkdir()
            (work / "home" / ".grok").mkdir(parents=True)
            shutil.copy(GUEST_SCRIPT, work / "guest.py")
            (work / "spec.json").write_text(
                json.dumps(spec.model_dump(), ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            for item in spec.files:
                (work / "files" / item.file_version_id).write_bytes(files[item.file_version_id])
            if self.grok_config:
                dest = work / "home" / ".grok" / "config.toml"
                shutil.copyfile(self.grok_config, dest)
                dest.chmod(stat.S_IRUSR | stat.S_IWUSR)
            argv = self._argv(work)
            try:
                proc = self._runner(argv, self.timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise DeepError("deep_execution_failed") from exc
            if proc.returncode != 0:
                raise DeepError("deep_execution_failed")
            payload = work / "result.json"
            if not payload.is_file():
                raise DeepError("deep_execution_failed")
            result = ExecuteResponse.model_validate_json(payload.read_text(encoding="utf-8"))
        except DeepError as exc:
            error = exc
        except Exception as exc:
            error = DeepError("deep_execution_failed")
            error.__cause__ = exc
        finally:
            try:
                shutil.rmtree(job_dir)
            except OSError:
                if error is None:
                    error = DeepError("deep_cleanup_failed")
        if error is not None:
            raise error
        assert result is not None
        return result

    def _argv(self, work: Path) -> list[str]:
        argv = [
            "docker",
            "run",
            "--rm",
            "--runtime",
            self.docker_runtime,
            "-v",
            f"{work}:/work:rw",
            "-e",
            "HOME=/work/home",
            "-e",
            "TERM=dumb",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
        ]
        if self.grok_bin:
            argv.extend(["-v", f"{Path(self.grok_bin).resolve()}:/grant/bin/grok:ro"])
        else:
            argv.extend(["--network", "none"])
        argv.extend([self.image, "python", "/work/guest.py"])
        return argv


def runtime_from_env() -> GvisorRuntime:
    return GvisorRuntime(
        docker_runtime=os.environ.get("PPAP_DEEP_DOCKER_RUNTIME", DEFAULT_DOCKER_RUNTIME),
        image=os.environ.get("PPAP_DEEP_GUEST_IMAGE", DEFAULT_GUEST_IMAGE),
        grok_bin=os.environ.get("PPAP_DEEP_GROK_BIN", ""),
        grok_config=os.environ.get("PPAP_DEEP_GROK_CONFIG", ""),
    )
