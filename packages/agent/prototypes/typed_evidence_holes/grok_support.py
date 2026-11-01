"""Shared local experiment serialization and grok-build configuration."""

import base64
import hashlib
import json

from hole_experiment import MODEL


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def append(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def scrub(value, secrets, blobs):
    """Keep observable actions, not reasoning blocks; images become local blobs."""
    if isinstance(value, dict):
        if isinstance(value.get("type"), str) and value["type"] in {"thinking", "redacted_thinking", "reasoning", "reasoning_text", "reasoning_summary_text"}:
            return {"type": "reasoning_omitted"}
        if value.get("type") == "base64" and str(value.get("media_type", "")).startswith("image/"):
            return scrub("data:" + value["media_type"] + ";base64," + value["data"], secrets, blobs)
        return {key: scrub(item, secrets, blobs) for key, item in value.items() if key not in {"encrypted_content", "signature", "thinking", "reasoning_content"}}
    if isinstance(value, list):
        return [scrub(item, secrets, blobs) for item in value]
    if isinstance(value, str):
        if value.startswith("data:image/") and ";base64," in value:
            mime, encoded = value.split(";base64,", 1)
            raw = base64.b64decode(encoded)
            digest = hashlib.sha256(raw).hexdigest()
            blobs.mkdir(exist_ok=True)
            target = blobs / digest
            target.write_bytes(raw)
            return {"image_blob": str(target.name), "sha256": digest, "mime": mime[5:], "bytes": len(raw)}
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    return value


def cli_config(base_url, token, *, max_completion_tokens=16000):
    config = f"""[models]
default = {json.dumps(MODEL)}
[model.{json.dumps(MODEL)}]
model = {json.dumps(MODEL)}
name = {json.dumps(MODEL)}
base_url = {json.dumps(base_url)}
api_key = {json.dumps(token)}
api_backend = "responses"
max_retries = 0
max_request_bytes = 8388608
max_completion_tokens = {max_completion_tokens}
vision = true
reasoning_effort = "medium"
supports_reasoning_effort = true
[cli]
auto_update = false
use_leader = false
session_registry = false
[memory]
enabled = false
[features]
telemetry = false
title_refresh = false
turn_summary = false
[telemetry]
trace_upload = false
[harness]
disable_workspace_teleport = true
[cursor_worker]
auto_start = false
"""
    for provider, keys in {"claude": ["agents", "hooks", "mcps", "rules", "skills"], "codex": ["hooks", "skills"], "cursor": ["agents", "hooks", "mcps", "rules", "skills"]}.items():
        config += f"[compat.{provider}]\n" + "".join(f"{key} = false\n" for key in keys)
    return config
