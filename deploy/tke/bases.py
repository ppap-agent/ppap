"""The shared base images, in three levels, and their build on the Yunxiao runner.

    system     Debian system packages (LibreOffice, CJK fonts) and uv   ~300 MB, changes rarely
    deps       Python dependencies from uv.lock and the legacy-document
               sandbox binary, on system                                ~180 MB
    deep-base  Node and the Pi runner, on deps                          ~70 MB

Releases build only source layers on the recorded images (deploy/tke/base-images.json). Each level
is tagged by a digest of its own inputs chained with the level below, so a change rebuilds and
re-pushes only the changed level and the ones above it; lower levels are referenced, not re-sent.

Usage (repository root, on the runner; python3 there is 3.6, so no annotations):
    python3 deploy/tke/bases.py build          build stale levels, print PPAP_BASE_IMAGE lines
    python3 deploy/tke/bases.py release-args   bake --set arguments; fails when any level is stale
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DOCKERFILE = "deploy/app/Dockerfile"
RECORD = "deploy/tke/base-images.json"
# (level, Dockerfile stages, files, build argument that receives the level below)
LEVELS = (
    ("system", ("system",), (), None),
    ("deps", ("converter-builder", "deps"), ("pyproject.toml", "uv.lock", "packages/documents/native/legacy_doc_sandbox.c"), "PPAP_SYSTEM_BASE"),
    (
        "deep-base",
        ("pi", "deep-base"),
        ("deploy/deep/package.json", "deploy/deep/package-lock.json", "deploy/deep/pi-runner.ts", "deploy/deep/pi-runner.test.mjs", "deploy/deep/tsconfig.json"),
        "PPAP_DEPS_BASE",
    ),
)
MIRROR = "docker-image://docker.m.daocloud.io/library/"


def stages(dockerfile):
    """Instruction text of each named stage; comments and blank lines do not count."""
    result, name = {}, None
    for line in dockerfile.splitlines():
        match = re.match(r"FROM\s+\S+\s+AS\s+(\S+)", line, re.IGNORECASE)
        if match:
            name = match.group(1)
            result[name] = ""
        elif re.match(r"FROM\s", line, re.IGNORECASE):
            name = None
        if name and line.strip() and not line.lstrip().startswith("#"):
            result[name] += line + "\n"
    return result


def level_inputs(read):
    """{level: digest} for read(path) -> bytes. A level's digest covers the level below it."""
    text = stages(read(DOCKERFILE).decode())
    inputs, below = {}, ""
    for level, names, files, _arg in LEVELS:
        digest = hashlib.sha256(below.encode())
        for name in names:
            digest.update(b"stage " + name.encode() + b"\0" + text[name].encode() + b"\0")
        for path in files:
            digest.update(path.encode() + b"\0" + read(path) + b"\0")
        inputs[level] = below = digest.hexdigest()
    return inputs


def stale(inputs, record):
    return [level for level in inputs if record.get(level, {}).get("inputs") != inputs[level]]


def mirrored_contexts(dockerfile):
    """Docker Hub bases pinned by digest in the Dockerfile, fetched through the mirror."""
    images = set(re.findall(r"(?m)^FROM\s+([a-z0-9.-]+:[^@\s]+@sha256:[0-9a-f]{64})", dockerfile))
    return [arg for image in sorted(images) for arg in ("--build-context", image + "=" + MIRROR + image)]


def recorded(root):
    path = root / RECORD
    return json.loads(path.read_text()) if path.exists() else {}


def build(root, prefix):
    read = lambda path: (root / path).read_bytes()  # noqa: E731
    inputs = level_inputs(read)
    record = recorded(root)
    todo = stale(inputs, record)
    contexts = mirrored_contexts(read(DOCKERFILE).decode())
    below = None
    for level, _names, _files, arg in LEVELS:
        if level in todo:
            command = ["docker", "buildx", "build", "-f", DOCKERFILE, "--target", level, "--platform", "linux/amd64"]
            command += contexts + (["--build-arg", arg + "=" + below] if arg else [])
            command += ["-t", prefix + level + ":" + inputs[level], "--provenance=false", "--metadata-file", "/tmp/ppap-base.json", "--push", "."]
            # The mirror and registry are reached over the network; a retry repeats only unfinished steps.
            for attempt in (1, 2, 3):
                if subprocess.call(command, cwd=str(root)) == 0:
                    break
                if attempt == 3:
                    sys.exit("base image " + level + " failed to build")
                time.sleep(20)
            digest = json.loads(Path("/tmp/ppap-base.json").read_text())["containerimage.digest"]
            assert re.match(r"sha256:[0-9a-f]{64}$", digest)
            image = prefix + level + "@" + digest
        else:
            image = record[level]["image"]
        print("PPAP_BASE_IMAGE " + level + " " + inputs[level] + " " + image, flush=True)
        below = image


def release_args(root):
    read = lambda path: (root / path).read_bytes()  # noqa: E731
    record = recorded(root)
    outdated = stale(level_inputs(read), record)
    if outdated:
        sys.exit("base images are stale (" + ", ".join(outdated) + "): run a bases build first")
    deps, deep = record["deps"]["image"], record["deep-base"]["image"]
    for target, image in (("app", deps), ("deep", deep)):
        print("--set " + target + ".args.PPAP_APP_BASE=" + image)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    if sys.argv[1:] == ["build"]:
        build(root, os.environ["PPAP_IMAGE_PREFIX"])
    elif sys.argv[1:] == ["release-args"]:
        release_args(root)
    else:
        sys.exit(__doc__)
