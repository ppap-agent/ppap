"""Digest of everything the shared dependency images (deps, deep-base) are built from.

Releases build only source layers on top of those images (deploy/tke/base-images.json). A release is
allowed only when the recorded images were built from exactly these inputs; any change to them needs
a base build first (build-current.py --bases).

Usage: python3 deploy/tke/base-inputs.py   (prints the digest of the working tree's inputs)
"""

import hashlib
import sys
from collections.abc import Callable
from pathlib import Path

# The whole Dockerfile counts: a stage edit is rare, and a stale base must never pass silently.
INPUTS = (
    "deploy/app/Dockerfile",
    "pyproject.toml",
    "uv.lock",
    "deploy/deep/package.json",
    "deploy/deep/package-lock.json",
    "deploy/deep/pi-runner.ts",
    "deploy/deep/pi-runner.test.mjs",
    "deploy/deep/tsconfig.json",
)


def base_inputs_digest(read: Callable[[str], bytes]) -> str:
    digest = hashlib.sha256()
    for path in INPUTS:
        digest.update(path.encode() + b"\0" + read(path) + b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    sys.stdout.write(base_inputs_digest(lambda path: (root / path).read_bytes()) + "\n")
