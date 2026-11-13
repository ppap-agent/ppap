"""One audit guest at a time inside a trusted Linux application container.

This boundary drops the guest's uid, capabilities, environment and inherited
descriptors. It is deliberately named container-only: on old kernels it does
not promise a separate filesystem or network namespace for terminal tools.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from audit.policy import GUEST_UID, PI_VERSION, DeepExecutionPolicy
from audit.protocol import CompileSpec, ControllerFault, GuestFault, SnapshotSpec, SubmissionSpec
from pydantic import Field

from agent.checker_spec import CheckerJobSpec
from agent.core_program import AuditJobSpec
from agent.execution.activity import ACTIVITY_FILE, Activity, parse_steps
from agent.execution.budget import MAX_INPUT_FILES, MAX_INPUT_TOTAL
from agent.execution.gateway import grant_request
from agent.execution.guest_model import ModelGrant
from agent.execution.secure_files import read_appended, read_json, read_regular

from .progress import Progress
from .storage import write_json

logger = logging.getLogger(__name__)

MAX_OUTPUT = 16 * 1024 * 1024
MAX_OUTER_STDERR = 4096


class RuntimeConfig(DeepExecutionPolicy):
    state: Path
    gateway_socket: Path
    gateway_host: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")
    python_executable: Path = Path("/app/.venv/bin/python")
    pythonpath: Path = Path("/app/.venv/lib/python3.12/site-packages")
    agent_executable: Path = Path("/usr/local/bin/ppap-pi")
    setpriv_executable: Path = Path("/usr/bin/setpriv")
    prlimit_executable: Path = Path("/usr/bin/prlimit")


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
    usage_source = "provider"
    adapts_checkers = True

    def __init__(self, config: RuntimeConfig, *, model_grant_binding=None) -> None:
        if not _running_in_container():
            raise ValueError("ContainerRuntime requires root inside a Linux container")
        for path in (config.state, config.gateway_socket, config.python_executable, config.pythonpath, config.agent_executable, config.setpriv_executable, config.prlimit_executable):
            if not path.is_absolute():
                raise ValueError("Runtime paths must be absolute")
        if not config.pythonpath.is_dir() or any(not path.is_file() for path in (config.python_executable, config.agent_executable, config.setpriv_executable, config.prlimit_executable)):
            raise ValueError("Guest Python runtime is unavailable")
        version = subprocess.run(
            [str(config.agent_executable), "--version"],
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "PI_OFFLINE": "1", "PI_TELEMETRY": "0"},
            capture_output=True,
            timeout=10,
            check=False,
        )
        if version.returncode != 0 or version.stdout.decode(errors="replace").strip() != PI_VERSION:
            raise ValueError(f"Verified Pi {PI_VERSION} is required")
        self.config = config
        self.model_grant_binding = model_grant_binding
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
        if self.degraded or not self.config.python_executable.is_file() or not self.config.agent_executable.is_file():
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
        with self.model_grant_binding(execution_id) if self.model_grant_binding is not None else nullcontext():
            grant = ModelGrant.model_validate(grant_request(str(self.config.gateway_socket), "issue", execution_id))
        url = urlsplit(grant.base_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost"}
            or url.hostname != self.config.gateway_host
            or url.username
            or url.password
            or url.query
            or url.fragment
            or grant.model != self.config.model
        ):  # noqa: E501
            raise ControllerFault("ISOLATION_UNAVAILABLE")
        target = run / "grant" / "model.json"
        write_json(target, grant.model_dump(mode="json"))
        os.chown(target, 0, GUEST_UID)
        target.chmod(0o440)

    def _execute(
        self, run: Path, deadline: float, cancelled: threading.Event, on_progress: Callable[[Progress], None] | None, execution_id: UUID, on_activity: Callable[[Activity], None] | None = None
    ) -> None:
        from agent.execution.paddle_bridge import paddle_bridge

        with paddle_bridge(run / "inputs", deadline, cancelled) as tool_env:
            self._execute_guest(run, deadline, cancelled, on_progress, execution_id, tool_env, on_activity)

    def _execute_guest(
        self,
        run: Path,
        deadline: float,
        cancelled: threading.Event,
        on_progress: Callable[[Progress], None] | None,
        execution_id: UUID,
        tool_env: dict[str, str],
        on_activity: Callable[[Activity], None] | None = None,
    ) -> None:
        env = {
            **tool_env,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(run / "work"),
            "LANG": "C.UTF-8",
            # Guest interpreter boundary: PATH excludes the venv, so this
            # process env points only at the installed site-packages.
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
                "--fsize=268435456",
                "--nofile=128",
                "--core=0",
                "--",
                str(self.config.setpriv_executable),
                "--no-new-privs",
                "--reuid=10001",
                "--regid=10001",
                "--clear-groups",
                "--bounding-set=-all",
                "--inh-caps=-all",
                "--ambient-caps=-all",
                "--",
                str(self.config.python_executable),
                "-m",
                "agent.execution.isolation.guest_entry",
            ],
            cwd=run / "work",
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
        self.active[execution_id] = process
        last_progress: Progress | None = None
        activity_offset = 0

        def forward_activity() -> None:
            # Untrusted guest lines: only valid, bounded steps pass; a bad file only stops the steps.
            nonlocal activity_offset
            if on_activity is None or activity_offset < 0:
                return
            try:
                steps, used = parse_steps(read_appended(run / "work", ACTIVITY_FILE, activity_offset, 64 * 1024))
            except (OSError, ValueError):
                activity_offset = -1
                return
            activity_offset += used
            for step in steps:
                try:
                    on_activity(step)
                except Exception:  # noqa: BLE001 - a lost live step never affects the audit
                    logger.debug("deep_activity_forward_failed", exc_info=True)

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
                forward_activity()
                time.sleep(0.1)
            forward_activity()
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

    def _seal_usage(self, execution_id: UUID, deadline: float, cancelled: threading.Event, *, drain: bool = True) -> dict[str, Any]:
        try:
            if drain:
                usage = grant_request(str(self.config.gateway_socket), "usage", execution_id)
                while usage.get("in_flight") is True:
                    if cancelled.is_set() or time.monotonic() >= deadline:
                        raise ControllerFault("CANCELLED" if cancelled.is_set() else "MODEL_TIMEOUT")
                    time.sleep(0.1)
                    usage = grant_request(str(self.config.gateway_socket), "usage", execution_id)
            # Removal and the final receipt share the grants lock. A request
            # racing the earlier read is either in this receipt or denied.
            return grant_request(str(self.config.gateway_socket), "revoke_and_usage", execution_id)
        except (OSError, ValueError):
            self.degraded = True
            raise ControllerFault("ISOLATION_UNAVAILABLE") from None

    def _export(
        self,
        run: Path,
        work: Path,
        model_operation: bool,
        execution_id: UUID,
        deadline: float,
        cancelled: threading.Event,
        *,
        settled_usage: dict[str, Any] | None = None,
    ) -> None:
        result = run / "work" / "result"
        try:
            usage = (settled_usage if settled_usage is not None else self._seal_usage(execution_id, deadline, cancelled)) if model_operation else {}
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
                # The request limit stopped a runaway loop: no artifact from it is published.
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
                if usage.get("budget_exhausted") is True:
                    raise ControllerFault("RESOURCE_LIMIT")
                # Provider token counts are observations. Unknown counts leave the result
                # valid; they are reported only when every request reported them.
                for key in ("input_tokens", "output_tokens", "model_requests"):
                    decoded.pop(key, None)
                if usage.get("usage_known") is True and all(type(usage.get(key)) is int and usage[key] >= 0 for key in ("completed_requests", "input_tokens", "output_tokens")):
                    decoded["input_tokens"] = usage["input_tokens"]
                    decoded["output_tokens"] = usage["output_tokens"]
                    decoded["model_requests"] = usage["completed_requests"]
                write_json(self.journal / (execution_id.hex + ".json"), decoded)
            write_json(work / "result" / "bundle.json", decoded)
        except ControllerFault:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise ControllerFault("MODEL_PROTOCOL_ERROR") from None

    def validate_recovery(
        self,
        spec: SubmissionSpec,
        *,
        job_id: str,
        source_attempt: int,
        current_attempt: int,
    ) -> None:
        """The latest durable attempt, not a chosen old source, owns replay."""
        job_key = hashlib.sha256(job_id.encode()).hexdigest()
        runtime_id = uuid5(spec.execution_id, f"{job_key}:{current_attempt}")
        if (self.journal / (runtime_id.hex + ".json")).exists():
            return  # Repeated completed recovery only reads its saved result.
        try:
            previous = read_json(self.attempts, (spec.execution_id.hex + ".json",), 8192)
            allowed = (
                source_attempt < current_attempt
                and previous.get("uncertain") is False
                and type(previous.get("attempt")) is int
                and previous["attempt"] == source_attempt
                and previous.get("job_key") == job_key
            )
        except (OSError, ValueError, AttributeError):
            allowed = False
        if not allowed:
            raise ControllerFault("MODEL_PROTOCOL_ERROR")

    def run(
        self,
        spec: SubmissionSpec | CompileSpec | SnapshotSpec | CheckerJobSpec | AuditJobSpec,
        inputs: Path,
        work: Path,
        deadline: float,
        cancelled: threading.Event,
        on_progress: Callable[[Progress], None] | None = None,
        on_activity: Callable[[Activity], None] | None = None,
    ) -> None:
        runtime_id = spec.execution_id
        runtime_attempt, source_attempt, job_key = 1, None, ""
        if isinstance(spec, SubmissionSpec):
            from agent.execution.guest import Manifest

            manifest = Manifest.model_validate(read_json(inputs, ("manifest.json",), 1024 * 1024))
            if manifest.spec != spec:
                raise ControllerFault("INPUT_INTEGRITY_ERROR")
            if manifest.runtime_attempt is not None and manifest.runtime_job_id is not None:
                runtime_attempt = manifest.runtime_attempt
                source_attempt = manifest.runtime_source_attempt
                job_key = hashlib.sha256(manifest.runtime_job_id.encode()).hexdigest()
                if source_attempt is not None:
                    runtime_id = uuid5(spec.execution_id, f"{job_key}:{runtime_attempt}")
        elif isinstance(spec, CheckerJobSpec) and spec.operation == "adapt-checker":
            from agent.checker_program import CheckerManifest

            try:
                checker_manifest = CheckerManifest.model_validate(read_json(inputs, ("manifest.json",), 1024 * 1024))
            except (OSError, ValueError):
                raise ControllerFault("INPUT_INTEGRITY_ERROR") from None
            if checker_manifest.spec != spec or checker_manifest.operation != spec.operation or not checker_manifest.runtime_job_id or spec.execution_id != spec.expected_execution_id():
                raise ControllerFault("INPUT_INTEGRITY_ERROR")
            # The program/input identity is reusable, but paid development is
            # not: a new explicit operation can have different prior/feedback.
            # Never include the technical attempt, which would bypass an
            # uncertain operation's existing fence and repeat provider work.
            job_key = hashlib.sha256(checker_manifest.runtime_job_id.encode()).hexdigest()
            runtime_id = uuid5(spec.execution_id, job_key)
        while True:
            remaining = deadline - time.monotonic()
            if cancelled.is_set() or remaining <= 0:
                raise ControllerFault("CANCELLED" if cancelled.is_set() else "MODEL_TIMEOUT")
            if self.slot.acquire(timeout=min(0.1, remaining)):
                break
        try:
            if self.degraded:
                raise ControllerFault("ISOLATION_UNAVAILABLE")
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise ControllerFault("CANCELLED" if cancelled.is_set() else "MODEL_TIMEOUT")
            model_operation = (
                isinstance(spec, (SubmissionSpec, CompileSpec))
                or (isinstance(spec, AuditJobSpec) and spec.allow_model_calls)
                or (isinstance(spec, CheckerJobSpec) and spec.operation == "adapt-checker")
            )
            saved = self.journal / (runtime_id.hex + ".json")
            if model_operation and saved.exists():
                write_json(work / "result" / "bundle.json", read_json(self.journal, (saved.name,), MAX_OUTPUT))
                return
            audit_marker = self.attempts / (spec.execution_id.hex + ".json") if isinstance(spec, SubmissionSpec) else None
            if isinstance(spec, SubmissionSpec) and source_attempt is not None:
                self.validate_recovery(spec, job_id=manifest.runtime_job_id or "", source_attempt=source_attempt, current_attempt=runtime_attempt)
            attempt = self.attempts / (runtime_id.hex + ".json")
            if model_operation and attempt.exists():
                raise ControllerFault("MODEL_PROTOCOL_ERROR")
            run = self._new_run_dir(runtime_id, inputs)
            final_usage = None
            try:
                if model_operation:
                    write_json(attempt, {})
                    if audit_marker is not None:
                        write_json(audit_marker, {"attempt": runtime_attempt, "job_key": job_key, "uncertain": True})
                    self._write_grant(runtime_id, run)
                self._execute(run, deadline, cancelled, on_progress, runtime_id, on_activity)
                if cancelled.is_set() or time.monotonic() >= deadline:
                    raise ControllerFault("CANCELLED" if cancelled.is_set() else "MODEL_TIMEOUT")
                if model_operation:
                    final_usage = self._seal_usage(runtime_id, deadline, cancelled)
                self._export(run, work, model_operation, runtime_id, deadline, cancelled, settled_usage=final_usage)
            except ControllerFault as fault:
                try:
                    if model_operation and final_usage is None and not self.degraded:
                        try:
                            final_usage = self._seal_usage(runtime_id, deadline, cancelled, drain=False)
                        except ControllerFault:
                            pass
                    self._diagnostic(run, work, fault.code, usage=final_usage)
                except Exception:
                    pass  # Preserve the original fault if private diagnostic I/O fails.
                self._export_candidate(run, work)
                raise
            finally:
                if model_operation:
                    if final_usage is None and not self.degraded:
                        try:
                            final_usage = self._seal_usage(runtime_id, deadline, cancelled, drain=False)
                        except ControllerFault:
                            pass
                    if audit_marker is not None:
                        # The fence stays unknown until the grant is closed with no
                        # request in flight. A later attempt cannot skip an
                        # unfinished or newer source.
                        try:
                            settled = final_usage is not None and final_usage.get("in_flight") is False
                            write_json(audit_marker, {"attempt": runtime_attempt, "job_key": job_key, "uncertain": not settled})
                        except (OSError, ValueError, TypeError):
                            pass  # The pre-send unknown marker remains authoritative.
                shutil.rmtree(run)
        finally:
            self.slot.release()

    @staticmethod
    def _export_candidate(run: Path, destination: Path) -> None:
        return

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
            if not stage.is_dir() or stage.is_symlink() or stage.name in {"result", "grok-home", "pi-home"}:
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
        payload: dict[str, Any] = {"code": code, "stages": stages}
        if usage is not None:
            names = ("completed_requests", "input_tokens", "output_tokens", "usage_known", "in_flight", "budget_exhausted")
            payload["usage"] = {key: usage[key] for key in names if type(usage.get(key)) in (int, bool)}
            failure = usage.get("transport_failure")
            if isinstance(failure, dict):
                payload["transport_failure"] = {key: failure[key] for key in ("phase", "error_type", "response_bytes", "last_event", "provider_status") if type(failure.get(key)) in (int, str)}
                timing_keys = ("elapsed_ms", "headers_ms", "first_chunk_ms", "last_chunk_ms", "last_chunk_gap_ms", "max_chunk_gap_ms", "chunk_count")
                payload["transport_failure"].update({key: failure[key] for key in timing_keys if key in failure and (type(failure[key]) is int or failure[key] is None)})
                if type(failure.get("client_write_failed")) is bool:
                    payload["transport_failure"]["client_write_failed"] = failure["client_write_failed"]
                client_request_id = failure.get("client_request_id")
                if type(client_request_id) is str:
                    try:
                        parsed_id = UUID(client_request_id)
                    except ValueError:
                        pass
                    else:
                        if parsed_id.version == 4 and str(parsed_id) == client_request_id:
                            payload["transport_failure"]["client_request_id"] = client_request_id
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
