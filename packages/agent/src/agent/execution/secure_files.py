"""Bounded reads with no-follow checks on every untrusted path component."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Any


def read_regular(root: Path, parts: tuple[str, ...], limit: int) -> bytes:
    if not parts or any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in parts):
        raise ValueError("Invalid artifact components")
    with ExitStack() as stack:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, parent)
        for part in parts[:-1]:
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, parent)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit or before.st_nlink != 1:
            raise ValueError("Artifact must be a bounded regular file with no external hard links")
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(fd, min(1024 * 1024, limit - size + 1)):
            size += len(chunk)
            if size > limit:
                raise ValueError("Artifact exceeds byte limit")
            chunks.append(chunk)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Artifact changed during read")
        return b"".join(chunks)
    raise RuntimeError("Artifact read did not complete")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def read_json(root: Path, parts: tuple[str, ...], limit: int) -> Any:
    return json.loads(read_regular(root, parts, limit).decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)


def stage_source(root: Path, project_key: str, sha256: str, file_format: str, target: Path, limit: int) -> int:
    data = read_regular(root, (project_key, "sources", sha256 + file_format), limit)
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("Source hash mismatch")
    with target.open("xb") as output:
        output.write(data)
    target.chmod(0o444)
    return len(data)
