"""Bounded diagnostic facts for durable storage; free text stays ephemeral."""

from __future__ import annotations

import re
from uuid import UUID


def _counts(raw: object, names: tuple[str, ...]) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in names if type(raw.get(key)) is int and 0 <= raw[key] <= 2**63 - 1}


def failure_facts(raw: object) -> dict:
    """Never persist arbitrary stderr, model content, headers or validation text."""
    if not isinstance(raw, dict):
        return {}
    facts: dict = {}
    usage = raw.get("usage")
    if isinstance(usage, dict):
        facts["usage"] = _counts(usage, ("completed_requests", "input_tokens", "output_tokens"))
        facts["usage"].update({key: usage[key] for key in ("uncertain", "in_flight", "budget_exhausted") if type(usage.get(key)) is bool})
    stages = raw.get("stages")
    if isinstance(stages, list):
        facts["stages"] = []
        for stage in stages[:32]:
            if not isinstance(stage, dict):
                continue
            saved: dict = {}
            name = stage.get("stage")
            if isinstance(name, str) and re.fullmatch(r"trial-[0-5]|prior-trial|fixed-program|unable-program", name):
                saved["stage"] = name
            stderr = stage.get("stderr")
            if isinstance(stderr, str) and stderr:
                saved["runtime_error"] = "node_wasm_memory_allocation_failed" if "WebAssembly.instantiate(): Out of memory: Cannot allocate Wasm memory" in stderr else "unclassified_stderr"
            terminal = stage.get("terminal")
            if isinstance(terminal, dict):
                result = _counts(terminal, ("num_turns",))
                if type(terminal.get("is_error")) is bool:
                    result["is_error"] = terminal["is_error"]
                if terminal.get("stop_reason") in ("error", "end_turn", "length", "max_tokens", "tool_use", "stop_sequence"):
                    result["stop_reason"] = terminal["stop_reason"]
                if isinstance(terminal.get("usage"), dict):
                    result["usage"] = _counts(terminal["usage"], ("input_tokens", "output_tokens"))
                saved["terminal"] = result
            facts["stages"].append(saved)
    guest = raw.get("guest")
    if isinstance(guest, dict) and type(guest.get("exit_code")) is int and -255 <= guest["exit_code"] <= 255:
        facts["guest"] = {"exit_code": guest["exit_code"]}
    failure = raw.get("transport_failure")
    if isinstance(failure, dict):
        saved = _counts(failure, ("response_bytes", "provider_status", "elapsed_ms", "headers_ms", "first_chunk_ms", "last_chunk_ms", "last_chunk_gap_ms", "max_chunk_gap_ms", "chunk_count"))
        allowed = {
            "phase": ("request", "provider_request", "provider_headers", "client_headers", "provider_read", "response_limits", "provider_event", "provider_usage"),
            "error_type": (
                "IncompleteResponse",
                "ProviderStatus",
                "IncompleteRead",
                "RemoteDisconnected",
                "TimeoutError",
                "ConnectionResetError",
                "ConnectionRefusedError",
                "BrokenPipeError",
                "SSLError",
                "SSLEOFError",
                "ValueError",
                "TypeError",
                "AttributeError",
                "OSError",
                "JSONDecodeError",
            ),
            "last_event": ("none", "other", "response.created", "response.in_progress", "response.completed", "response.failed", "response.incomplete", "error"),
        }
        for key, values in allowed.items():
            if failure.get(key) in values:
                saved[key] = failure[key]
        if type(failure.get("client_write_failed")) is bool:
            saved["client_write_failed"] = failure["client_write_failed"]
        try:
            request_id = UUID(failure.get("client_request_id", ""))
            if request_id.version == 4 and str(request_id) == failure["client_request_id"]:
                saved["client_request_id"] = str(request_id)
        except (ValueError, TypeError, AttributeError):
            pass
        facts["transport_failure"] = saved
    return facts
