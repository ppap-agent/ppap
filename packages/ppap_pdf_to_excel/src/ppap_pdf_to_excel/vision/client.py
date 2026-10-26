"""One Responses client for local multimodal document evidence."""

import asyncio
import base64
import inspect
import json
import logging
import mimetypes
import os
import re
from typing import Any, Dict, List, Optional, Union

from openai import APITimeoutError, AsyncOpenAI
from ppap_core.settings import get_settings

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
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.get_ocr_openai_api_key(),
        base_url=settings.get_ocr_openai_base_url(),
        max_retries=0,
    )


async def _request_response(kwargs: dict[str, Any]) -> Any:
    """Bound the whole asynchronous response body, not just each socket read."""
    client = _get_llm_client()
    try:
        async with asyncio.timeout(RESPONSE_WALL_TIMEOUT_SECONDS):
            try:
                response = client.responses.create(**kwargs)
                return await response if inspect.isawaitable(response) else response
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    closing = close()
                    if inspect.isawaitable(closing):
                        await closing
    except (TimeoutError, APITimeoutError) as exc:
        raise ResponsesEvidenceError("responses_timeout", "Responses request exceeded its deadline") from exc


def _get_llm_model_name() -> str:
    return get_settings().get_ocr_model_name()


def encode_image(path: str) -> str:
    """将本地图片路径转换为 Base64 字符串"""
    with open(path, "rb") as img:
        return base64.b64encode(img.read()).decode("utf-8")


def _prepare_messages(image_input: str | bytes | list[str | bytes], text_prompt: str) -> List[Dict[str, Any]]:
    """构建标准的多模态消息体"""
    if isinstance(image_input, (str, bytes)):
        paths = [image_input]
    else:
        paths = image_input

    images_content = []
    for path in paths:
        if isinstance(path, bytes):
            b64 = base64.b64encode(path).decode("ascii")
            mime = "image/png"
        elif os.path.exists(path):
            b64 = encode_image(path)
            mime = mimetypes.guess_type(path)[0] or "image/jpeg"
            if mime not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValueError(f"Unsupported vision image type: {mime}")
        else:
            raise FileNotFoundError(f"Vision input image does not exist: {path}")
        images_content.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}", "detail": "high"})

    content_payload = [{"type": "input_text", "text": text_prompt}, *images_content]
    return [{"role": "user", "content": content_payload}]


async def call_llm_async(
    image_paths: str | bytes | list[str | bytes],
    prompt: str,
    retry_count: int = 3,
    json_mode: bool = False,
    response_schema: Optional[Dict[str, Any]] = None,
) -> Union[str, Dict[str, Any]]:
    """
    Responses 多模态模型调用入口
    :param json_mode: 是否强制输出 JSON (Tagging 阶段使用)
    :param response_schema: 如果 json_mode 为 True，可传入具体的 Schema 约束
    """
    messages = _prepare_messages(image_paths, prompt)

    # 构造参数
    kwargs: dict[str, Any] = {
        "model": _get_llm_model_name(),
        "input": messages,
        "store": False,
        "max_output_tokens": 16384,
        "timeout": 120,
    }

    # 处理 Response Format
    if json_mode:
        if response_schema:
            # 如果平台支持 strict schema (如 Vertex AI / OpenAI 最新版)
            kwargs["text"] = {"format": {"type": "json_schema", **response_schema}}
        else:
            # 通用 JSON 模式
            kwargs["text"] = {"format": {"type": "json_object"}}

    for attempt in range(retry_count):
        try:
            response = await _request_response(kwargs)
            status = getattr(response, "status", None)
            if status != "completed":
                raise ResponsesEvidenceError("responses_incomplete", f"Responses status is {str(status)[:40]}")
            content_text = getattr(response, "output_text", None)
            if not isinstance(content_text, str) or not content_text.strip():
                raise ResponsesEvidenceError("responses_empty_text", "Responses completed without nonempty text")

            # 如果是 JSON 模式，尝试解析为字典返回
            if json_mode:
                try:
                    # === 这里调用清洗函数 ===
                    clean_content = _clean_json_response(content_text)
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

            return content_text

        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{retry_count} failed. Error: {e}")
            if attempt == retry_count - 1:
                logger.error("Max retries reached.")
                raise e

    return ""


def call_llm(
    image_paths: str | bytes | list[str | bytes],
    prompt: str,
    retry_count: int = 3,
    json_mode: bool = False,
    response_schema: Optional[Dict[str, Any]] = None,
) -> Union[str, Dict[str, Any]]:
    """Synchronous compatibility entrypoint for non-batched callers."""
    return asyncio.run(
        call_llm_async(
            image_paths=image_paths,
            prompt=prompt,
            retry_count=retry_count,
            json_mode=json_mode,
            response_schema=response_schema,
        )
    )


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
