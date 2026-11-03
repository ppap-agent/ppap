"""Render the canonical Kustomize objects and bind process configuration to Pod templates."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_ANNOTATION = "ppap.io/configuration-digest"
OWNERS = {"ppap-current-api", "ppap-current-worker", "ppap-current-web"}


def stamp_configuration(objects: list[dict]) -> list[dict]:
    config = next(obj["data"] for obj in objects if obj.get("kind") == "ConfigMap" and obj["metadata"]["name"] == "ppap-current")
    for obj in objects:
        if obj.get("kind") != "Deployment" or obj["metadata"]["name"] not in OWNERS:
            continue
        # Web consumes only the public API address; model changes do not restart it.
        effective = {"PPAP_PUBLIC_API_URL": config.get("PPAP_PUBLIC_API_URL", "")} if obj["metadata"]["name"] == "ppap-current-web" else config
        raw = json.dumps(effective, sort_keys=True, separators=(",", ":")).encode()
        annotations = obj["spec"]["template"].setdefault("metadata", {}).setdefault("annotations", {})
        annotations[CONFIG_ANNOTATION] = hashlib.sha256(raw).hexdigest()
    return objects


def main() -> None:
    rendered = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{HERE}:/src:ro", "-w", "/src", "registry.k8s.io/kustomize/kustomize:v5.6.0", "build", "."],
        check=True,
        capture_output=True,
    ).stdout
    # Use the existing native YAML tool; do not implement another YAML parser.
    converted = subprocess.run(["docker", "run", "--rm", "-i", "mikefarah/yq:4.44.6", "-o=json", "-I=0", "."], input=rendered, check=True, capture_output=True).stdout.decode().strip()
    objects = []
    decoder = json.JSONDecoder()
    while converted:
        obj, end = decoder.raw_decode(converted)
        objects.append(obj)
        converted = converted[end:].lstrip()
    print("\n---\n".join(json.dumps(obj) for obj in stamp_configuration(objects)))


if __name__ == "__main__":
    main()
