"""One Responses client for local multimodal document evidence."""

import base64
import json
import logging
import math
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from openai import APITimeoutError, AsyncOpenAI

from documents.ocr.env import ocr_extra_body, ocr_model_name, ocr_openai_api_key, ocr_openai_base_url

logger = logging.getLogger(__name__)


class ResponsesEvidenceError(RuntimeError):
    """A failed provider evidence contract, with a stable non-secret failure code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResponsesEvidenceError("responses_duplicate_key", "Responses JSON contains a repeated object key")
        result[key] = value
    return result


# A request's client is owned by one event loop and always closed at completion.
# Sharing an async client's connection pool across per-page thread event loops is unsafe.
REQUEST_TIMEOUT_SECONDS = 20

_USAGE: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar("ocr_usage", default=None)


@contextmanager
def ocr_usage_scope(callback: Callable[[dict[str, Any]], None]) -> Iterator[None]:
    """Send actual OCR receipts to the owning audit's existing usage tracker."""
    token = _USAGE.set(callback)
    try:
        yield
    finally:
        _USAGE.reset(token)


def _get_llm_client() -> AsyncOpenAI:
    if not ocr_openai_api_key() or not ocr_openai_base_url():
        raise ValueError("OCR endpoint and credential are not configured; set OCR_OPENAI_BASE_URL and OCR_OPENAI_API_KEY")
    return AsyncOpenAI(
        api_key=ocr_openai_api_key(),
        base_url=ocr_openai_base_url(),
        max_retries=0,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


async def _request_response(kwargs: dict[str, Any]) -> Any:
    """One HTTP request with a socket timeout; no aggregate deadline or retry."""
    client = _get_llm_client()
    started = time.monotonic()
    response = None
    try:
        response = await client.responses.create(**kwargs)
        return response
    except (TimeoutError, APITimeoutError) as exc:
        raise ResponsesEvidenceError("responses_timeout", "Responses request timed out") from exc
    finally:
        try:
            await client.close()
        finally:
            callback = _USAGE.get()
            if callback is not None:
                usage = getattr(response, "usage", None)
                receipt: dict[str, Any] = {"model": getattr(response, "model", None) or kwargs.get("model"), "elapsed_seconds": time.monotonic() - started}
                for name in ("input_tokens", "output_tokens"):
                    value = getattr(usage, name, None)
                    if type(value) is int and value >= 0:
                        receipt[name] = value
                details = getattr(usage, "output_tokens_details", None)
                reasoning = getattr(details, "reasoning_tokens", None)
                if type(reasoning) is int and reasoning >= 0:
                    receipt["reasoning_tokens"] = reasoning
                cost = getattr(usage, "cost", None)
                if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
                    receipt["cost_usd"] = cost
                callback(receipt)


def _get_llm_model_name() -> str:
    return ocr_model_name()


def _prepare_messages(image_input: bytes | list[bytes], text_prompt: str) -> list[dict[str, Any]]:
    """Use the original-image PNG bytes provided by the evidence reader."""
    images = [image_input] if isinstance(image_input, bytes) else image_input
    images_content = [{"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(image).decode("ascii"), "detail": "high"} for image in images]
    content_payload = [{"type": "input_text", "text": text_prompt}, *images_content]
    return [{"role": "user", "content": content_payload}]


async def call_llm_async(
    image_paths: bytes | list[bytes],
    prompt: str,
    *,
    response_schema: dict[str, Any],
    max_output_tokens: int = 4096,
) -> dict[str, Any]:
    """Make one bounded schema-constrained request and decode one JSON object."""
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 32768:
        raise ValueError("OCR max_output_tokens is outside the supported range")
    messages = _prepare_messages(image_paths, prompt)

    kwargs: dict[str, Any] = {
        "model": _get_llm_model_name(),
        "input": messages,
        "store": False,
        "max_output_tokens": max_output_tokens,
        "timeout": REQUEST_TIMEOUT_SECONDS,
        "text": {"format": {"type": "json_schema", **response_schema}},
    }
    extra = ocr_extra_body()
    if extra:
        kwargs["extra_body"] = extra
    try:
        response = await _request_response(kwargs)
        status = getattr(response, "status", None)
        if status != "completed":
            raise ResponsesEvidenceError("responses_incomplete", f"Responses status is {str(status)[:40]}")
        content_text = getattr(response, "output_text", None)
        if not isinstance(content_text, str) or not content_text.strip():
            raise ResponsesEvidenceError("responses_empty_text", "Responses completed without nonempty text")
        clean_content = _clean_json_response(content_text)
        try:
            parsed = json.loads(clean_content, object_pairs_hook=_unique_object)
        except json.JSONDecodeError as exc:
            # Never log or persist provider output: it may include document data.
            details = getattr(response, "incomplete_details", None)
            usage = getattr(response, "usage", None)
            output_tokens = getattr(usage, "output_tokens", None)
            logger.warning(
                "Responses invalid JSON: reason=%s line=%d col=%d pos=%d output_chars=%d output_tokens=%s incomplete=%s",
                exc.msg,
                exc.lineno,
                exc.colno,
                exc.pos,
                len(clean_content),
                output_tokens if type(output_tokens) is int else "unknown",
                getattr(details, "reason", None),
            )
            raise ResponsesEvidenceError(
                "responses_invalid_json",
                f"Responses invalid JSON (line={exc.lineno}, col={exc.colno}, pos={exc.pos}, output_chars={len(clean_content)})",
            ) from exc
        if not isinstance(parsed, dict):
            raise ResponsesEvidenceError("responses_non_object", "Responses JSON root is not an object")
        return parsed
    except ResponsesEvidenceError as exc:
        logger.warning("Responses evidence rejected: code=%s", exc.code)
        raise


def _clean_json_response(content: str) -> str:
    """
    清洗 LLM 返回的 JSON 字符串，去除 Markdown 代码块标记
    """
    # 1. 去除 ```json 或 ```
    content = re.sub(r"^```json\s*", "", content, flags=re.MULTILINE | re.IGNORECASE)
    content = re.sub(r"^```\s*", "", content, flags=re.MULTILINE | re.IGNORECASE)
    content = re.sub(r"\s*```$", "", content, flags=re.MULTILINE | re.IGNORECASE)

    # 2. 有时候模型会在 JSON 前后说废话，尝试提取第一个 { ... }
    # 这一步比较激进，如果你的 JSON 很简单可以使用
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        content = match.group(0)

    return content.strip()
