"""Fixed checker program identity. Execution of the program stays in the guest."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


def program_digest(files: dict[str, bytes]) -> str:
    """Digest the fixed program bytes. This is not the input digest."""
    hasher = hashlib.sha256()
    for name in sorted(files):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(files[name])
        hasher.update(b"\0")
    return hasher.hexdigest()


class CheckerArtifact(BaseModel):
    """Code and resources fixed for one rules, appendix, file version, and role."""

    model_config = ConfigDict(extra="forbid")

    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    executor_revision: str = Field(min_length=1)
    entrypoint: Literal["checker:create_audit"]
    files: dict[str, bytes]
    program_digest: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def supported_files(self) -> CheckerArtifact:
        if "checker.py" not in self.files:
            raise ValueError("checker.py is required")
        unknown = set(self.files) - {"checker.py", "resources.py"}
        if unknown:
            raise ValueError(f"unsupported checker file: {sorted(unknown)}")
        if any(not content for content in self.files.values()):
            raise ValueError("empty checker file")
        digest = program_digest(self.files)
        if self.program_digest and self.program_digest != digest:
            raise ValueError("checker program digest mismatch")
        self.program_digest = digest
        return self


class CheckerJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: UUID
    operation: Literal["adapt-checker", "execute-checker"]
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    executor_revision: str = Field(min_length=1)

    def expected_execution_id(self) -> UUID:
        payload = {"operation": self.operation, "input_digest": self.input_digest, "executor_revision": self.executor_revision}
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return uuid5(NAMESPACE_URL, "checker:" + hashlib.sha256(data).hexdigest())


class CheckerProgramBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["adapt-checker", "execute-checker"]
    execution_id: UUID
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    executor_revision: str = Field(min_length=1)
    artifact: CheckerArtifact | None = None
    results: list[dict] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model_requests: int = 0

    @model_validator(mode="after")
    def result_matches_operation(self) -> CheckerProgramBundle:
        if self.operation == "adapt-checker" and self.artifact is None:
            raise ValueError("adapt did not fix a checker program")
        if self.operation == "execute-checker" and self.artifact is not None:
            raise ValueError("execute cannot accept a new program")
        if (
            self.execution_id
            != CheckerJobSpec(
                execution_id=self.execution_id,
                operation=self.operation,
                input_digest=self.input_digest,
                executor_revision=self.executor_revision,
            ).expected_execution_id()
        ):
            raise ValueError("checker execution identity mismatch")
        return self


class SourceArtifact(BaseModel):
    """Reusable source identity, deliberately independent of any audit input.

    Binding requires a validator receipt for this exact code and new input. A
    previous input's successful execution is not evidence for another document.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    entrypoint: Literal["checker:create_audit"] = "checker:create_audit"
    files: Mapping[str, bytes]
    program_digest: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def fixed_source(self) -> SourceArtifact:
        checked = CheckerArtifact(input_digest="0" * 64, executor_revision="source", entrypoint=self.entrypoint, files=dict(self.files), program_digest=self.program_digest)
        object.__setattr__(self, "files", MappingProxyType(dict(checked.files)))
        object.__setattr__(self, "program_digest", checked.program_digest)
        return self

    @field_serializer("files")
    def serialize_files(self, value: Mapping[str, bytes]) -> dict[str, bytes]:
        return dict(value)

    def bind(self, *, input_digest: str, executor_revision: str, validated_program_digest: str, validated_input_digest: str) -> CheckerArtifact:
        if validated_program_digest != self.program_digest or validated_input_digest != input_digest or program_digest(dict(self.files)) != self.program_digest:
            raise ValueError("checker requires validation for this code and input")
        return CheckerArtifact(input_digest=input_digest, executor_revision=executor_revision, entrypoint=self.entrypoint, files=dict(self.files), program_digest=self.program_digest)

    @classmethod
    def from_artifact(cls, artifact: CheckerArtifact) -> SourceArtifact:
        return cls(files=dict(artifact.files), entrypoint=artifact.entrypoint, program_digest=artifact.program_digest)
