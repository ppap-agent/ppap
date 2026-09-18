"""Responses-compatible client. No Chat Completions fallback. No OPENAI_* names."""
from __future__ import annotations

import json
from typing import Any

import httpx

from ppap_core.settings import Settings


def responses_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/responses"


def llm_configured(settings: Settings) -> bool:
    return bool(settings.llm_api_key.strip() and settings.llm_base_url.strip() and settings.llm_model.strip())


def _output_text(payload: dict[str, Any]) -> str:
    if payload.get("output_text"):
        return str(payload["output_text"])
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                chunks.append(str(part.get("text") or ""))
    return "".join(chunks)


def _parse_sse(lines: list[str]) -> dict[str, Any]:
    completed: dict[str, Any] | None = None
    event = ""
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
            continue
        if line:
            continue
        blob = "".join(data).strip()
        data = []
        if not blob or blob == "[DONE]":
            event = ""
            continue
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            event = ""
            continue
        if event in {"response.completed", "response.done"} or (
            isinstance(parsed, dict) and parsed.get("type") in {"response.completed", "response.done"}
        ):
            body = parsed.get("response") if isinstance(parsed, dict) else None
            completed = body if isinstance(body, dict) else parsed if isinstance(parsed, dict) else None
        event = ""
    if not completed:
        raise RuntimeError("responses_stream_incomplete")
    return completed


def chat_messages_to_responses(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions = ""
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role == "system":
            if isinstance(content, str):
                instructions = content
            continue
        items.append({"role": role if role in {"user", "assistant"} else "user", "content": _content_parts(content)})
    return instructions, items


def _content_parts(content: Any) -> Any:
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in {"text", "input_text"}:
            parts.append({"type": "input_text", "text": str(part.get("text") or "")})
        elif kind == "image_url":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if url:
                parts.append({"type": "input_image", "image_url": str(url)})
        elif kind == "input_image":
            parts.append(part)
    return parts or ""


def responses_complete(
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    max_output_tokens: int = 1000,
    temperature: float = 0.1,
    json_format: bool = False,
) -> str:
    if not llm_configured(settings):
        raise RuntimeError("llm_not_configured")
    instructions, items = chat_messages_to_responses(messages)
    body: dict[str, Any] = {
        "model": settings.llm_model.strip(),
        "input": items,
        "store": False,
        "stream": True,
        "max_output_tokens": max_output_tokens,
        "temperature": temperature,
    }
    if instructions:
        body["instructions"] = instructions
    if json_format:
        body["text"] = {"format": {"type": "json_object"}}
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key.strip()}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    with httpx.Client(timeout=900.0) as client:
        with client.stream("POST", responses_url(settings.llm_base_url), headers=headers, json=body) as response:
            if response.status_code >= 400:
                response.read()
                raise RuntimeError(f"responses_http_{response.status_code}")
            completed = _parse_sse(list(response.iter_lines()))
    text = _output_text(completed).strip()
    if not text:
        raise RuntimeError("responses_empty")
    return text
