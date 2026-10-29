"""Grok process adapter confined to the guest image; no application credentials."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from .models import Artifact
from .secure_files import read_json, read_regular, reject_constant, unique_object

T = TypeVar("T", bound=BaseModel)
ReasoningEffort = Literal["xhigh", "medium", "low"]
# CLI aliases select built-ins; Responses function names do not. Isolation and
# permissions remain separate from this context/capability selection.
GROK_TOOL_ALIASES = ("Read", "Bash", "Write", "TaskOutput", "TaskStop")


class ModelProtocolError(ValueError):
    """The model CLI did not return a valid terminal protocol message."""


class ModelBudgetError(ValueError):
    """A configured model turn or token budget was exhausted."""


class ModelGrant(Artifact):
    base_url: str
    token: str = Field(min_length=32, max_length=2048)
    model: str = Field(min_length=1, max_length=128)
    max_turns: int = Field(ge=1, le=128, strict=True)
    max_total_turns: int = Field(default=512, ge=1, le=4096, strict=True)
    max_total_tokens: int = Field(default=500_000, ge=1, le=10_000_000, strict=True)
    wall_seconds: int = Field(ge=1, le=3600, strict=True)


def sandbox_profile() -> str:
    isolation = os.environ.get("PPAP_GUEST_ISOLATION")
    if isolation == "gvisor":
        if os.getuid() != 10001 or not Path("/proc/gvisor/kernel_is_gvisor").is_file():
            raise ModelProtocolError("Grok requires the configured outer isolation")
        return "off"
    if isolation == "container-only":
        profile = os.environ.get("PPAP_GUEST_SANDBOX")
        if os.getuid() != 10001 or profile not in {"off", "ppap"}:
            raise ModelProtocolError("Invalid container-only guest configuration")
        return profile
    raise ModelProtocolError("Unsupported guest isolation")


class GrokRunner:
    def __init__(self, grant: dict[str, Any]) -> None:
        self.grant = ModelGrant.model_validate(grant)
        url = urlsplit(self.grant.base_url)
        container_only = os.environ.get("PPAP_GUEST_ISOLATION") == "container-only" and os.getuid() == 10001
        local_http = container_only and url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"}
        if (url.scheme != "https" and not local_http) or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("A HTTPS or authorized loopback model gateway is required")
        self.wall_seconds = self.grant.wall_seconds
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0
        self.completed_tool_calls = 0
        self.compactions = 0
        self.remaining_turns = self.grant.max_total_turns

    def run(self, work: Path, prompt: str, schema: type[T], deadline: float, *, reasoning_effort: ReasoningEffort) -> T:
        terminal = self._run(work, prompt, schema.model_json_schema(), deadline, reasoning_effort=reasoning_effort)
        return schema.model_validate(terminal.get("structured_output"))

    def run_artifact(self, work: Path, prompt: str, schema: type[T], deadline: float, *, reasoning_effort: ReasoningEffort) -> T:
        """Let the agent finish file work before it emits a small terminal receipt."""
        artifact = "execution.json"
        if (work / artifact).exists() or (work / artifact).is_symlink():
            raise ModelProtocolError("Stage artifact already exists")
        (work / "artifact_schema.json").write_text(json.dumps(schema.model_json_schema()))
        artifact_prompt = (
            prompt + "\nComplete this stage through file tools before the final response. "
            "Read the supplied inputs and artifact_schema.json, then write the complete output as valid JSON to execution.json. "
            "Re-open execution.json and verify its required identities, frozen sources and full schema. Do not add fields outside that schema. "
            'After finishing the work, your final response must be exactly {"completed":true,"artifact":"execution.json"}. '
            "This is only a completion receipt; it must not contain the business output. "
            "A receipt without a complete execution.json fails the stage."
        )
        # A constant-only provider schema can emit a receipt without doing
        # the file work. Validate both the receipt and the actual artifact.
        terminal = self._run(work, artifact_prompt, None, deadline, reasoning_effort=reasoning_effort)
        try:
            receipt = json.loads(terminal.get("result", ""), object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (ValueError, TypeError):
            raise ModelProtocolError("Invalid artifact completion receipt") from None
        if not isinstance(receipt, dict) or receipt.get("completed") is not True or receipt != {"completed": True, "artifact": artifact}:
            raise ModelProtocolError("Invalid artifact completion receipt")
        try:
            output = read_json(work, (artifact,), 16 * 1024 * 1024)
        except (OSError, ValueError) as error:
            raise ModelProtocolError("Missing or invalid stage artifact") from error
        return schema.model_validate(output)

    def _run(
        self,
        work: Path,
        prompt: str,
        output_contract: dict[str, Any] | None,
        deadline: float,
        *,
        reasoning_effort: ReasoningEffort,
    ) -> dict[str, Any]:
        if time.monotonic() >= deadline:
            raise TimeoutError("Model deadline exceeded")
        if self.remaining_turns <= 0 or self.input_tokens + self.output_tokens >= self.grant.max_total_tokens:
            raise ModelBudgetError("Model budget exhausted")
        stage_turns = min(self.grant.max_turns, self.remaining_turns)
        # Reserve the maximum for this stage. This remains a hard aggregate bound
        # even if the CLI does not report its actual turn count.
        self.remaining_turns -= stage_turns
        home = work / "grok-home"
        home.mkdir(mode=0o700)
        grant = self.grant
        config = (
            f"[models]\ndefault = {json.dumps(grant.model)}\n"
            f"[model.{json.dumps(grant.model)}]\nname = {json.dumps(grant.model)}\n"
            f'base_url = {json.dumps(grant.base_url)}\napi_backend = "responses"\napi_key = {json.dumps(grant.token)}\n'
            "max_retries = 0\n"
            "[cli]\nauto_update = false\n"
        )
        (home / "config.toml").write_text(config)
        (home / "config.toml").chmod(0o600)
        (home / "sandbox.toml").write_text(
            '[profiles.ppap]\nextends = "strict"\nrestrict_network = false\nread_only = ["/inputs", "/opt/ppap", "/etc/ssl/certs"]\nread_write = ["/work", "/tmp", "/var/tmp"]\n'
        )
        if output_contract is not None:
            (work / "output_schema.json").write_text(json.dumps(output_contract))
            prompt += "\nThe exact output contract is in output_schema.json in this working directory. Read it before constructing the result. "
        (work / "prompt.txt").write_text(
            prompt + "\nUse tool calls to read inputs and finish the work before the final response. "
            "The final response ends this stage: return it only after completing the requested work, never to announce progress or intent."
        )
        command = [
            "/usr/local/bin/grok",
            "--agent",
            "grok-build",
            "--tools",
            ",".join(GROK_TOOL_ALIASES),
            "--cwd",
            str(work),
            "--prompt-file",
            str(work / "prompt.txt"),
            "--model",
            grant.model,
            "--reasoning-effort",
            reasoning_effort,
            "--output-format",
            "streaming-messages-json",
            "--max-turns",
            str(stage_turns),
            "--sandbox",
            sandbox_profile(),
            "--permission-mode",
            "bypassPermissions",
            "--disable-web-search",
            "--no-subagents",
            "--no-plan",
            "--deny",
            "MCPTool",
        ]
        if output_contract is not None:
            command.extend(["--json-schema", json.dumps(output_contract)])
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(home),
            "GROK_HOME": str(home),
            "LANG": "C.UTF-8",
            "GROK_MEMORY": "0",
            "GROK_DISABLE_AUTOUPDATER": "1",
            "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
            "SSL_CERT_DIR": "/etc/ssl/certs",
            "GROK_EXTRA_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
        }
        # The runtime supplies this host-configured package path to the guest.
        # Preserve it for terminal Python without inheriting the whole environment.
        if pythonpath := os.environ.get("PYTHONPATH"):
            env["PYTHONPATH"] = pythonpath
        with (work / "stdout.jsonl").open("xb") as stdout, (work / "stderr.log").open("xb") as stderr:
            separate_session = os.environ.get("PPAP_GUEST_ISOLATION") != "container-only"
            process = subprocess.Popen(command, cwd=work, env=env, stdout=stdout, stderr=stderr, start_new_session=separate_session)
            try:
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Model stage deadline exceeded")
                    if stdout.tell() > 16 * 1024 * 1024 or stderr.tell() > 1024 * 1024:
                        raise ModelProtocolError("Model log limit exceeded")
                    time.sleep(0.05)
            finally:
                if separate_session:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.poll() is None:
                    # The container runtime owns the guest process group. Never
                    # send a group signal from a member of that same group.
                    process.kill()
                process.wait()
        terminal: dict[str, Any] = {}
        used: Counter[str] = Counter()
        returned: Counter[str] = Counter()
        tool_calls = 0
        compactions = 0
        try:
            output = read_regular(work, ("stdout.jsonl",), 16 * 1024 * 1024)
        except ValueError:
            raise ModelProtocolError("Invalid model output stream") from None
        for line in output.splitlines():
            try:
                event = json.loads(line, object_pairs_hook=unique_object, parse_constant=reject_constant)
            except ValueError:
                raise ModelProtocolError("Invalid model event") from None
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "system" and event.get("subtype") == "compact_boundary":
                compactions += 1
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if event_type == "assistant" and block.get("type") == "tool_use":
                        tool_calls += 1
                        call_id = block.get("id")
                        if isinstance(call_id, str) and call_id:
                            used[call_id] += 1
                    elif event_type == "user" and block.get("type") == "tool_result":
                        call_id = block.get("tool_use_id")
                        if isinstance(call_id, str) and call_id:
                            returned[call_id] += 1
            if event_type == "result":
                if terminal:
                    raise ModelProtocolError("Duplicate terminal model result")
                terminal = event
        self.tool_calls += tool_calls
        self.completed_tool_calls += sum(min(count, returned[call_id]) for call_id, count in used.items())
        self.compactions += compactions
        if process.returncode != 0 or terminal.get("stop_reason") != "end_turn" or terminal.get("is_error") or terminal.get("errors"):
            raise ModelProtocolError("Model stage failed")
        actual_turns = terminal.get("num_turns")
        if type(actual_turns) is int and 0 <= actual_turns <= stage_turns:
            # The Grok terminal event reports actual turns. Unused
            # reservations remain available for the bounded repair stages;
            # the gateway independently caps all paid requests and tokens.
            self.remaining_turns += stage_turns - actual_turns
        usage = terminal.get("usage")
        if not isinstance(usage, dict) or any(name not in usage for name in ("input_tokens", "output_tokens")):
            raise ModelProtocolError("Invalid token usage")
        for name in ("input_tokens", "output_tokens"):
            value = usage.get(name, 0)
            if type(value) is not int or value < 0:
                raise ModelProtocolError("Invalid token usage")
            setattr(self, name, getattr(self, name) + value)
        if self.input_tokens + self.output_tokens > grant.max_total_tokens:
            raise ModelBudgetError("Model token budget exhausted")
        return terminal
