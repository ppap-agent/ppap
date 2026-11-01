"""One Responses client for local multimodal document evidence."""

import asyncio
import base64
import json
import logging
import re
from typing import Any

from openai import APITimeoutError, AsyncOpenAI

from documents.ocr.env import ocr_model_name, ocr_openai_api_key, ocr_openai_base_url

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
RESPONSE_WALL_TIMEOUT_SECONDS = 120


def _get_llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=ocr_openai_api_key(),
        base_url=ocr_openai_base_url(),
        max_retries=0,
    )


async def _request_response(kwargs: dict[str, Any]) -> Any:
    """Bound the whole asynchronous response body, not just each socket read."""
    client = _get_llm_client()
    try:
        async with asyncio.timeout(RESPONSE_WALL_TIMEOUT_SECONDS):
            try:
                return await client.responses.create(**kwargs)
            finally:
                await client.close()
    except (TimeoutError, APITimeoutError) as exc:
        raise ResponsesEvidenceError("responses_timeout", "Responses request exceeded its deadline") from exc


def _get_llm_model_name() -> str:
    return ocr_model_name()


def _prepare_messages(image_input: bytes | list[bytes], text_prompt: str) -> list[dict[str, Any]]:
    """Use the PNG bytes rendered by the page and cell readers."""
    images = [image_input] if isinstance(image_input, bytes) else image_input
    images_content = [{"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(image).decode("ascii"), "detail": "high"} for image in images]
    content_payload = [{"type": "input_text", "text": text_prompt}, *images_content]
    return [{"role": "user", "content": content_payload}]


async def call_llm_async(
    image_paths: bytes | list[bytes],
    prompt: str,
    *,
    response_schema: dict[str, Any],
) -> dict[str, Any]:
    """Make one bounded schema-constrained request and decode one JSON object."""
    messages = _prepare_messages(image_paths, prompt)

    kwargs: dict[str, Any] = {
        "model": _get_llm_model_name(),
        "input": messages,
        "store": False,
        "max_output_tokens": 16384,
        "timeout": 120,
        "text": {"format": {"type": "json_schema", **response_schema}},
    }
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
