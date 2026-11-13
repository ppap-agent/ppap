"""Fixed checker program identity. Execution of the program stays in the guest."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    """Generated factory fixed for one rules, appendix, file version, and role."""

    model_config = ConfigDict(extra="forbid")

    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    executor_revision: str = Field(min_length=1)
    files: dict[str, bytes]
    program_digest: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")
    entrypoint: Literal["checker:create_audit"]

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
