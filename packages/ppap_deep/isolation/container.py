"""One audit guest at a time inside a trusted Linux application container.

This boundary drops the guest's uid, capabilities, environment and inherited
descriptors. It is deliberately named container-only: on old kernels it does
not promise a separate filesystem or network namespace for terminal tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field

from ppap_deep.semantic.guest_model import ModelGrant
from ppap_deep.semantic.models import Artifact
from ppap_deep.semantic.protocol import CompileSpec, ControllerFault, GuestFault, Sha256, SnapshotSpec, SubmissionSpec
from ppap_deep.semantic.secure_files import read_json, read_regular

from .model_gateway import grant_request
from .progress import Progress
from .storage import write_json

GUEST_UID = 10001
MAX_OUTPUT = 16 * 1024 * 1024
MAX_INPUT_TOTAL = 128 * 1024 * 1024
MAX_INPUT_FILES = 256
MAX_OUTER_STDERR = 4096


class RuntimeConfig(Artifact):
    state: Path
    gateway_socket: Path
    gateway_host: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")
    model: str = Field(min_length=1, max_length=128)
    model_revision: Sha256
    app_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    # Explicit deployment decision. `off` is needed on the current TKE kernel.
    sandbox_profile: str = Field(pattern=r"^(off|ppap)$")
    python_executable: Path = Path("/usr/local/bin/python")
    pythonpath: Path = Path("/app/packages")
    grok_executable: Path = Path("/usr/local/bin/grok")
    setpriv_executable: Path = Path("/usr/bin/setpriv")
    prlimit_executable: Path = Path("/usr/bin/prlimit")
    memory_mib: int = Field(default=1536, ge=512, le=4096, strict=True)
    max_processes: int = Field(default=256, ge=16, le=1024, strict=True)

    def engine_revision(self) -> str:
        manifest = {
            "adapter": "container-only-v1",
            "app_revision": self.app_revision,
            "model": self.model,
            "model_revision": self.model_revision,
            "sandbox": self.sandbox_profile,
            "memory_mib": self.memory_mib,
            "max_processes": self.max_processes,
            "uid": GUEST_UID,
            "grok_version": "1.0.40",
        }
        return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _running_in_container() -> bool:
    if sys.platform != "linux" or os.geteuid() != 0:
        return False
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text()
    except OSError:
        return False
    if any(marker in cgroup for marker in ("kubepods", "docker", "containerd", "libpod")):
        return True
    # A private cgroup namespace can show only "0::/" inside a Kubernetes pod.
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST") and os.environ.get("KUBERNETES_SERVICE_PORT"))


def _descendants(root_pid: int) -> set[int]:
    parents: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            status = (entry / "status").read_text()
            match = re.search(r"^PPid:\s*(\d+)$", status, re.MULTILINE)
            if match:
                parents[int(entry.name)] = int(match.group(1))
        except (OSError, ValueError):
            continue
    found: set[int] = set()
    frontier = {root_pid}
    while frontier:
        children = {pid for pid, parent in parents.items() if parent in frontier and pid not in found}
        found.update(children)
        frontier = children
    return found


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    # Capture descendants before killing their parent; a terminal tool can
    # still create another process group, so killpg alone is insufficient.
    children = _descendants(process.pid)
    for pid in children:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _drain_stderr(pipe, tail: bytearray, *, max_reads: int | None = 16) -> None:
    reads = 0
    while max_reads is None or reads < max_reads:
        try:
            chunk = os.read(pipe.fileno(), 4096)
        except (BlockingIOError, OSError):
            break
        if not chunk:
            break
        tail.extend(chunk)
        del tail[:-MAX_OUTER_STDERR]
        reads += 1


class ContainerRuntime:
    def __init__(self, config: RuntimeConfig) -> None:
        if not _running_in_container():
            raise ValueError("ContainerRuntime requires root inside a Linux container")
        for path in (config.state, config.gateway_socket, config.python_executable, config.pythonpath, config.grok_executable, config.setpriv_executable, config.prlimit_executable):
            if not path.is_absolute():
                raise ValueError("Runtime paths must be absolute")
        if not config.pythonpath.is_dir() or any(not path.is_file() for path in (config.python_executable, config.grok_executable, config.setpriv_executable, config.prlimit_executable)):
            raise ValueError("Guest Python runtime is unavailable")
        version = subprocess.run(
            [str(config.grok_executable), "--version"],
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "GROK_DISABLE_AUTOUPDATER": "1"},
            capture_output=True, timeout=10, check=False,
        )
        if version.returncode != 0 or not version.stdout.decode(errors="replace").startswith("grok 1.0.40 "):
            raise ValueError("Verified Grok 1.0.40 is required")
        self.config = config
        self.config.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config.state.chmod(0o711)
        self.journal = self.config.state / "journal"
        self.journal.mkdir(mode=0o700, exist_ok=True)
        self.attempts = self.config.state / "attempts"
        self.attempts.mkdir(mode=0o700, exist_ok=True)
        self.slot = threading.Lock()
        self.active: dict[UUID, subprocess.Popen[bytes]] = {}
        self.degraded = False

    def engine_revision(self) -> str:
        return self.config.engine_revision()

    def ready(self, operation: str = "audit") -> bool:
        if self.degraded or not self.config.python_executable.is_file() or not self.config.grok_executable.is_file():
            return False
        return operation == "snapshot" or self.config.gateway_socket.is_socket()

    def _stage_inputs(self, source: Path, destination: Path) -> None:
        if not source.is_absolute() or not source.is_dir():
            raise ControllerFault("INPUT_INTEGRITY_ERROR")
        destination.mkdir(mode=0o700)
        total = 0
        count = 0
        for path in source.iterdir():
            count += 1
            if count > MAX_INPUT_FILES or not path.is_file() or path.is_symlink() or path.name in (".", ".."):
                raise ControllerFault("INPUT_INTEGRITY_ERROR")
            try:
                raw = read_regular(source, (path.name,), MAX_INPUT_TOTAL - total)
            except (OSError, ValueError):
                raise ControllerFault("INPUT_INTEGRITY_ERROR") from None
            total += len(raw)
            target = destination / path.name
            with target.open("xb") as stream:
                stream.write(raw)
            os.chown(target, 0, GUEST_UID)
            target.chmod(0o440)
        os.chown(destination, 0, GUEST_UID)
        destination.chmod(0o550)

    def _new_run_dir(self, execution_id: UUID, inputs: Path) -> Path:
        run = self.config.state / ("run-" + execution_id.hex)
        if run.exists():
            raise ControllerFault("ISOLATION_UNAVAILABLE")
        run.mkdir(mode=0o711)
        try:
            self._stage_inputs(inputs, run / "inputs")
            work = run / "work"
            work.mkdir(mode=0o700)
            os.chown(work, GUEST_UID, GUEST_UID)
            grant = run / "grant"
            grant.mkdir(mode=0o700)
            os.chown(grant, 0, GUEST_UID)
            grant.chmod(0o550)
        except BaseException:
            shutil.rmtree(run)
            raise
        return run

    def _write_grant(self, execution_id: UUID, run: Path) -> None:
        grant = ModelGrant.model_validate(grant_request(str(self.config.gateway_socket), "issue", execution_id))
        url = urlsplit(grant.base_url)
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"} or url.hostname != self.config.gateway_host or url.username or url.password or url.query or url.fragment or grant.model != self.config.model:
            raise ControllerFault("ISOLATION_UNAVAILABLE")
        target = run / "grant" / "model.json"
        write_json(target, grant.model_dump(mode="json"))
        os.chown(target, 0, GUEST_UID)
        target.chmod(0o440)

    def _execute(self, run: Path, deadline: float, cancelled: threading.Event, on_progress: Callable[[Progress], None] | None, execution_id: UUID) -> None:
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(run / "work"),
            "LANG": "C.UTF-8",
            "PYTHONPATH": str(self.config.pythonpath),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PPAP_GUEST_ISOLATION": "container-only",
            "PPAP_GUEST_SANDBOX": self.config.sandbox_profile,
            "PPAP_GUEST_RUN": str(run),
        }
        process = subprocess.Popen(
            [
                str(self.config.prlimit_executable),
                f"--as={self.config.memory_mib * 1024 * 1024}",
                f"--nproc={self.config.max_processes}",
                "--fsize=268435456", "--nofile=128", "--core=0",
                "--",
                str(self.config.setpriv_executable),
                "--no-new-privs", "--reuid=10001", "--regid=10001", "--clear-groups",
                "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all",
                "--",
                str(self.config.python_executable), "-m", "ppap_deep.isolation.guest_entry",
            ],
            cwd=run / "work", env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            close_fds=True, start_new_session=True,
        )
        self.active[execution_id] = process
        last_progress: Progress | None = None
        stderr_tail = bytearray()
        assert process.stderr is not None
        try:
            os.set_blocking(process.stderr.fileno(), False)
            while process.poll() is None:
                _drain_stderr(process.stderr, stderr_tail)
                if cancelled.is_set() or time.monotonic() >= deadline:
                    raise ControllerFault("CANCELLED" if cancelled.is_set() else "MODEL_TIMEOUT")
                if on_progress is not None:
                    try:
                        raw = read_regular(run / "work", ("progress.json",), 512)
                        progress = Progress.model_validate_json(raw)
                    except FileNotFoundError:
                        progress = None
                    except (OSError, ValueError):
                        raise ControllerFault("OUTPUT_VALIDATION_ERROR") from None
                    if progress is not None and progress != last_progress:
                        on_progress(progress)
                        last_progress = progress
                time.sleep(0.1)
        finally:
            try:
                _kill_tree(process)
                _drain_stderr(process.stderr, stderr_tail, max_reads=None)
            finally:
                process.stderr.close()
                self.active.pop(execution_id, None)
        if process.returncode != 0:
            tail = stderr_tail.decode(errors="replace")
            try:
                token = read_json(run / "grant", ("model.json",), 8192).get("token", "")
            except (OSError, ValueError):
                token = ""
            if token:
                tail = tail.replace(token, "[redacted]")
            tail = re.sub(r"[A-Za-z0-9_-]{32,}", "[redacted]", tail)
            try:
                write_json(run / "outer-failure.json", {"exit_code": process.returncode, "stderr_tail": tail})
            except (OSError, ValueError):
                pass  # Diagnostic failure must not replace the guest failure.
            if process.returncode > 0 and "FileNotFoundError" in tail and "execution.json" in tail:
                raise ControllerFault("MODEL_PROTOCOL_ERROR")
            raise ControllerFault("ISOLATION_UNAVAILABLE")
        if on_progress is not None:
            try:
                progress = Progress.model_validate_json(read_regular(run / "work", ("progress.json",), 512))
            except (OSError, ValueError):
                progress = None
            if progress is not None and progress != last_progress:
                on_progress(progress)

    def _export(self, run: Path, work: Path, model_operation: bool, execution_id: UUID, deadline: float, cancelled: threading.Event) -> None:
        result = run / "work" / "result"
        try:
            usage = grant_request(str(self.config.gateway_socket), "usage", execution_id) if model_operation else {}
            # The CLI may finish before an auxiliary request has drained. Its
            # pending usage is not a permanent failure. No inference is replayed.
            while usage.get("in_flight") is True:
                if cancelled.is_set() or time.monotonic() >= deadline:
                    raise ControllerFault("CANCELLED" if cancelled.is_set() else "MODEL_TIMEOUT")
                time.sleep(0.1)
                usage = grant_request(str(self.config.gateway_socket), "usage", execution_id)
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise ControllerFault("CANCELLED" if cancelled.is_set() else "MODEL_TIMEOUT")
            try:
                raw = read_regular(result, ("fault.json",), 1024)
            except FileNotFoundError:
                try:
                    raw = read_regular(result, ("bundle.json",), MAX_OUTPUT)
                except FileNotFoundError:
                    if usage.get("budget_exhausted") is True:
                        raise ControllerFault("RESOURCE_LIMIT")
                    raise
            else:
                if usage.get("budget_exhausted") is True:
                    raise ControllerFault("RESOURCE_LIMIT")
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                raise ValueError("Invalid result")
            if decoded.get("ok") is False:
                if usage.get("budget_exhausted") is True:
                    raise ControllerFault("RESOURCE_LIMIT")
                fault = GuestFault.model_validate(decoded)
                raise ControllerFault(fault.code, fault.diagnostic)
            if model_operation:
                if (
                    usage.get("uncertain") is not False
                    or any(type(usage.get(key)) is not int or usage[key] < 0 for key in ("completed_requests", "input_tokens", "output_tokens"))
                    or usage["completed_requests"] < 1
                ):
                    raise ValueError("Unconfirmed provider usage")
                decoded["input_tokens"] = usage["input_tokens"]
                decoded["output_tokens"] = usage["output_tokens"]
                write_json(self.journal / (execution_id.hex + ".json"), decoded)
            write_json(work / "result" / "bundle.json", decoded)
        except ControllerFault:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise ControllerFault("MODEL_PROTOCOL_ERROR") from None

    def run(
        self,
        spec: SubmissionSpec | CompileSpec | SnapshotSpec,
        inputs: Path,
        work: Path,
        deadline: float,
        cancelled: threading.Event,
        on_progress: Callable[[Progress], None] | None = None,
    ) -> None:
        with self.slot:
            if self.degraded:
                raise ControllerFault("ISOLATION_UNAVAILABLE")
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise ControllerFault("CANCELLED" if cancelled.is_set() else "MODEL_TIMEOUT")
            model_operation = isinstance(spec, (SubmissionSpec, CompileSpec))
            saved = self.journal / (spec.execution_id.hex + ".json")
            if model_operation and saved.exists():
                write_json(work / "result" / "bundle.json", read_json(self.journal, (saved.name,), MAX_OUTPUT))
                return
            attempt = self.attempts / (spec.execution_id.hex + ".json")
            if model_operation and attempt.exists():
                raise ControllerFault("MODEL_PROTOCOL_ERROR")
            run = self._new_run_dir(spec.execution_id, inputs)
            try:
                if model_operation:
                    write_json(attempt, {})
                    self._write_grant(spec.execution_id, run)
                self._execute(run, deadline, cancelled, on_progress, spec.execution_id)
                if cancelled.is_set() or time.monotonic() >= deadline:
                    raise ControllerFault("CANCELLED" if cancelled.is_set() else "MODEL_TIMEOUT")
                self._export(run, work, model_operation, spec.execution_id, deadline, cancelled)
            except ControllerFault as fault:
                try:
                    usage = None
                    if model_operation:
                        try:
                            usage = grant_request(str(self.config.gateway_socket), "usage", spec.execution_id)
                        except (OSError, ValueError):
                            pass
                    self._diagnostic(run, work, fault.code, usage=usage)
                except Exception:
                    pass  # Preserve the original fault if private diagnostic I/O fails.
                raise
            finally:
                if model_operation:
                    try:
                        grant_request(str(self.config.gateway_socket), "revoke", spec.execution_id)
                    except (OSError, ValueError):
                        self.degraded = True
                shutil.rmtree(run)

    @staticmethod
    def _diagnostic(run: Path, destination: Path, code: str, *, usage: dict | None = None) -> None:
        """Keep bounded failure facts; never retain credentials or reasoning traces."""
        try:
            token = read_json(run / "grant", ("model.json",), 8192).get("token", "")
        except (OSError, ValueError):
            token = ""
        try:
            outer = read_json(run, ("outer-failure.json",), 8192)
            outer = {
                "exit_code": outer["exit_code"],
                "stderr_tail": str(outer["stderr_tail"])[-MAX_OUTER_STDERR:].replace(token, "[redacted]") if token else str(outer["stderr_tail"])[-MAX_OUTER_STDERR:],
            }
        except (OSError, ValueError, TypeError, KeyError):
            outer = None
        stages = []
        for stage in sorted((run / "work").iterdir())[:32]:
            if not stage.is_dir() or stage.is_symlink() or stage.name in {"result", "grok-home"}:
                continue
            facts: dict = {"stage": stage.name}
            tool_names = []
            try:
                facts["stderr"] = read_regular(stage, ("stderr.log",), 1024 * 1024).decode(errors="replace")[-8192:]
            except (OSError, ValueError):
                pass
            try:
                for line in read_regular(stage, ("stdout.jsonl",), MAX_OUTPUT).splitlines():
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get("type") == "result":
                        # Keep terminal metadata, never the model's full output.
                        facts["terminal"] = {key: event.get(key) for key in ("stop_reason", "is_error", "usage", "num_turns")}
                    if isinstance(event, dict) and isinstance(event.get("message"), dict):
                        for block in event["message"].get("content", []):
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                tool_names.append(block.get("name"))
            except (OSError, ValueError):
                pass
            if tool_names:
                facts["tool_names"] = tool_names[:256]
            try:
                facts["validation"] = read_json(stage, ("validation-feedback.json",), 8192)
            except (OSError, ValueError):
                pass
            if len(facts) > 1:
                # Terminal errors can echo a request; redact the only guest credential.
                raw = json.dumps(facts, ensure_ascii=False)
                stages.append(json.loads(raw.replace(token, "[redacted]")) if token else facts)
        payload = {"code": code, "stages": stages}
        if usage is not None:
            payload["usage"] = {key: usage[key] for key in ("completed_requests", "input_tokens", "output_tokens", "uncertain", "in_flight", "budget_exhausted") if type(usage.get(key)) in (int, bool)}
            failure = usage.get("transport_failure")
            if isinstance(failure, dict):
                payload["transport_failure"] = {
                    key: failure[key] for key in ("phase", "error_type", "response_bytes", "last_event", "provider_status")
                    if type(failure.get(key)) in (int, str)
                }
        if outer is not None:
            payload["guest"] = outer
        write_json(destination / "diagnostic.json", payload)

    def destroy(self, execution_id: UUID) -> None:
        process = self.active.get(execution_id)
        if process is not None:
            _kill_tree(process)

    def reconcile(self) -> None:
        # Grant recovery requires a live gateway; refuse new work after an
        # uncertain cleanup rather than silently reissue a capability.
        if self.degraded:
            raise ControllerFault("ISOLATION_UNAVAILABLE")

    def recover(self) -> None:
        for path in self.attempts.iterdir():
            if re.fullmatch(r"[a-f0-9]{32}\.json", path.name):
                try:
                    grant_request(str(self.config.gateway_socket), "revoke", UUID(hex=path.stem))
                except (OSError, ValueError):
                    self.degraded = True
                    raise ControllerFault("ISOLATION_UNAVAILABLE") from None
        # A stale directory may still have a live child. Do not erase its
        # workspace or accept new work until the container has been restarted
        # and an operator has resolved the incomplete attempt.
        if any(re.fullmatch(r"run-[a-f0-9]{32}", path.name) for path in self.config.state.iterdir()):
            self.degraded = True
            raise ControllerFault("ISOLATION_UNAVAILABLE")
