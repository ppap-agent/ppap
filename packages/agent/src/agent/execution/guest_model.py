"""Model process adapters confined to the guest; no application credentials."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypeVar, cast
from urllib.parse import urlsplit

from audit.models import Artifact
from pydantic import BaseModel, Field, ValidationError

from agent.execution.secure_files import read_json, read_regular, reject_constant, unique_object

T = TypeVar("T", bound=BaseModel)
ReasoningEffort = Literal["xhigh", "high", "medium", "low"]


class ModelProtocolError(ValueError):
    """The model CLI did not return a valid terminal protocol message."""


class ModelGrant(Artifact):
    """A short-lived local capability. It names one model per use and holds no provider key."""

    base_url: str
    token: str = Field(min_length=32, max_length=2048)
    # The coding model, which the agent uses.
    model: str = Field(min_length=1, max_length=128)
    text_model: str = Field(default="", max_length=128)
    vision_model: str = Field(default="", max_length=128)
    provider: str = Field(default="responses-compatible", min_length=1, max_length=64)
    revision: str = Field(default="", max_length=64)
    deployment: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")
    wall_seconds: int = Field(ge=1, le=3600, strict=True)

    @staticmethod
    def fields_for(identity: Mapping[str, str]) -> dict[str, str]:
        """The non-secret grant fields for a frozen identity: one model per use."""
        model = identity.get("model", "")
        fields = {"model": identity.get("coding_model") or model, "text_model": model, "vision_model": identity.get("vision_model") or model}
        fields.update({key: identity[key] for key in ("provider", "revision", "deployment") if identity.get(key)})
        return fields

    def models(self) -> dict[str, str]:
        return {"text": self.text_model or self.model, "vision": self.vision_model or self.model, "coding": self.model}

    def matches(self, identity: Mapping[str, str]) -> bool:
        """The grant serves exactly the models frozen for this operation."""
        # An identity that names one model uses it for every purpose.
        model = identity.get("model", "")
        frozen = {"text": model, "vision": identity.get("vision_model") or model, "coding": identity.get("coding_model") or model}
        return self.models() == frozen and all(getattr(self, key) == identity.get(key, "") for key in ("provider", "revision", "deployment"))

    def access_scope(self):
        """Every use goes through the local gateway with this grant; never an environment key."""
        from documents.model_access import ModelAccess, model_access_scope
        from pydantic import SecretStr

        models = self.models()
        return model_access_scope(
            lambda purpose: ModelAccess(api_key=SecretStr(self.token), provider=self.provider, model=models[purpose], base_url=self.base_url, source="grant", revision=self.revision)
        )


def sandbox_profile() -> str:
    isolation = os.environ.get("PPAP_GUEST_ISOLATION")
    if isolation == "gvisor":
        if os.getuid() != 10001 or not Path("/proc/gvisor/kernel_is_gvisor").is_file():
            raise ModelProtocolError("Model runner requires the configured outer isolation")
        return "off"
    if isolation == "container-only":
        profile = os.environ.get("PPAP_GUEST_SANDBOX")
        if os.getuid() != 10001 or profile not in {"off", "ppap"}:
            raise ModelProtocolError("Invalid container-only guest configuration")
        return profile
    raise ModelProtocolError("Unsupported guest isolation")


def _draft_from_program_files(work: Path, schema: type[T]) -> T | None:
    """The development guide writes Python files. A missing JSON receipt can still hold that program."""
    checker = work / "checker.py"
    if not checker.is_file() or checker.is_symlink():
        return None
    try:
        source = checker.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if not source.strip():
        return None
    files = {"checker.py": source}
    resources = work / "resources.py"
    if resources.is_file() and not resources.is_symlink():
        try:
            resource_source = resources.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            resource_source = ""
        if resource_source.strip():
            files["resources.py"] = resource_source
    try:
        return schema.model_validate({"entrypoint": "checker:create_audit", "files": files})
    except ValidationError:
        return None


def _with_trial_clauses(drafted: T | None, receipt: object) -> T | None:
    """Attach a named trial set. A malformed set must not become a full run."""
    if not isinstance(receipt, dict) or "clauses" not in receipt:
        return drafted
    if drafted is None or receipt.get("completed") is not True or receipt.get("artifact") != "checker.py" or set(receipt) - {"completed", "artifact", "clauses"}:
        raise ModelProtocolError("Invalid artifact completion receipt")
    raw = receipt["clauses"]
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) or not item.strip() for item in raw):
        raise ModelProtocolError("invalid trial clauses")
    return drafted.model_copy(update={"clauses": [item.strip() for item in raw]})


def read_checker_draft(work: Path, schema: type[T], terminal: dict[str, Any] | None = None) -> T:
    """Both the lab and isolated runner read exactly the submitted program bytes."""
    drafted = _draft_from_program_files(work, schema)
    raw_receipt = (terminal or {}).get("result", "")
    narrative_completion = False
    try:
        receipt = json.loads(raw_receipt, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, TypeError):
        receipt = None
        if isinstance(raw_receipt, str) and raw_receipt.lstrip().startswith(("{", "[")):
            raise ModelProtocolError("Invalid checker receipt") from None
        narrative_completion = isinstance(raw_receipt, str) and bool(raw_receipt.strip())
    if isinstance(receipt, dict) and "clauses" in receipt:
        drafted = _with_trial_clauses(drafted, receipt)
    if drafted is None:
        subtype = str((terminal or {}).get("subtype") or "missing_artifact")
        raise ModelProtocolError(f"checker.py missing or invalid ({subtype})")
    if "submitted" in schema.model_fields:
        valid_receipt = isinstance(receipt, dict) and receipt.get("completed") is True and receipt.get("artifact") == "checker.py" and not set(receipt) - {"completed", "artifact", "clauses"}
        submitted = (terminal or {}).get("subtype") == "success" and (terminal or {}).get("is_error") is not True and (valid_receipt or narrative_completion)
        drafted = drafted.model_copy(update={"submitted": submitted})
    return drafted


class ModelRunner:
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
        # Provider token counts are observed when every turn reports them.
        self.usage_known = True

    def run(self, work: Path, prompt: str, schema: type[T], deadline: float, *, reasoning_effort: str) -> T:
        """Let the agent finish file work before it emits a small terminal receipt."""
        if reasoning_effort not in {"xhigh", "high", "medium", "low"}:
            raise ModelProtocolError("Invalid reasoning effort")
        checker_stage = "entrypoint" in schema.model_fields and "files" in schema.model_fields
        artifact = "checker.py" if checker_stage else "execution.json"
        if (work / artifact).exists() or (work / artifact).is_symlink():
            raise ModelProtocolError("Stage artifact already exists")
        (work / "artifact_schema.json").write_text(json.dumps(schema.model_json_schema()))
        if checker_stage:
            artifact_prompt = prompt
        else:
            artifact_prompt = (
                prompt + "\nRead the supplied inputs and artifact_schema.json, then write the complete output as valid JSON to execution.json. "
                "Re-open execution.json and verify its required identities, frozen sources and full schema. Do not add fields outside that schema. "
                'After finishing the work, your final response must be exactly {"completed":true,"artifact":"execution.json"}. '
                "This is only a completion receipt; it must not contain the business output. "
                "A receipt without a complete execution.json fails the stage."
            )
        try:
            terminal = self._run(work, artifact_prompt, deadline, reasoning_effort=cast(ReasoningEffort, reasoning_effort))
        except ModelProtocolError:
            if not checker_stage:
                raise
            return read_checker_draft(work, schema)
        if checker_stage:
            return read_checker_draft(work, schema, terminal)
        try:
            receipt = json.loads(terminal.get("result", ""), object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (ValueError, TypeError):
            receipt = None
        if not isinstance(receipt, dict) or receipt.get("completed") is not True or receipt != {"completed": True, "artifact": artifact}:
            raise ModelProtocolError("Invalid artifact completion receipt")
        try:
            output = read_json(work, (artifact,), 16 * 1024 * 1024)
        except (OSError, ValueError) as error:
            raise ModelProtocolError("Missing or invalid stage artifact") from error
        return schema.model_validate(output)

    def _run(self, work: Path, prompt: str, deadline: float, *, reasoning_effort: ReasoningEffort) -> dict[str, Any]:
        raise NotImplementedError


class PiRunner(ModelRunner):
    """Fixed Pi SDK process with the same artifact and execution-grant contract."""

    def _run(self, work: Path, prompt: str, deadline: float, *, reasoning_effort: ReasoningEffort) -> dict[str, Any]:
        if time.monotonic() >= deadline:
            raise TimeoutError("Model deadline exceeded")
        sandbox_profile()
        home = work / "pi-home"
        home.mkdir(mode=0o700)
        (work / "prompt.txt").write_text(prompt, encoding="utf-8")
        config = {
            "home": str(home),
            "prompt": str(work / "prompt.txt"),
            "base_url": self.grant.base_url,
            "token": self.grant.token,
            "model": self.grant.model,
            "reasoning_effort": reasoning_effort,
        }
        request = home / "request.json"
        request.write_text(json.dumps(config), encoding="utf-8")
        request.chmod(0o600)
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "PI_CODING_AGENT_DIR": str(home),
            "PI_OFFLINE": "1",
            "PI_TELEMETRY": "0",
            "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
            "SSL_CERT_DIR": "/etc/ssl/certs",
        }
        if pythonpath := os.environ.get("PYTHONPATH"):
            env["PYTHONPATH"] = pythonpath
        with (work / "stdout.jsonl").open("xb") as stdout, (work / "stderr.log").open("xb") as stderr:
            separate_session = os.environ.get("PPAP_GUEST_ISOLATION") != "container-only"
            process = subprocess.Popen(
                ["/usr/local/bin/ppap-pi", str(request)],
                cwd=work,
                env=env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=separate_session,
            )
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
                    process.kill()
                process.wait()
        terminal = None
        try:
            output = read_regular(work, ("stdout.jsonl",), 16 * 1024 * 1024)
        except ValueError:
            raise ModelProtocolError("Invalid model output stream") from None
        for line in output.split(b"\n"):
            if not line:
                continue
            try:
                event = json.loads(line, object_pairs_hook=unique_object, parse_constant=reject_constant)
            except ValueError:
                raise ModelProtocolError("Invalid Pi event") from None
            if isinstance(event, dict) and event.get("type") == "result":
                if terminal is not None:
                    raise ModelProtocolError("Duplicate terminal model result")
                terminal = event
        if terminal is None:
            raise ModelProtocolError("Missing terminal model result")
        # Usage and tool counts are observations. Missing counts never reject a stage.
        usage = terminal.get("usage")
        counts = [usage.get(name) if isinstance(usage, dict) else None for name in ("input_tokens", "output_tokens")]
        if terminal.get("usage_known") is not False and all(type(value) is int and value >= 0 for value in counts):
            self.input_tokens += cast(int, counts[0])
            self.output_tokens += cast(int, counts[1])
        else:
            self.usage_known = False
        for name in ("tool_calls", "completed_tool_calls", "compactions"):
            value = terminal.get(name)
            if type(value) is int and value >= 0:
                setattr(self, name, getattr(self, name) + value)
        if process.returncode != 0 or terminal.get("stop_reason") != "end_turn" or terminal.get("is_error") is not False or terminal.get("subtype") != "success":
            raise ModelProtocolError("Model stage failed")
        return terminal
