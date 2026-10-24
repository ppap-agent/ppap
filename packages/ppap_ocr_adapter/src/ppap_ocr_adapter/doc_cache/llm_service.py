# src/llm_service.py

import base64
import mimetypes
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Union

from openai import OpenAI
from ppap_core.settings import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_llm_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.get_ocr_openai_api_key(),
        base_url=settings.get_ocr_openai_base_url(),
        max_retries=0,
    )


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


def call_llm(
    image_paths: str | bytes | list[str | bytes],
    prompt: str,
    retry_count: int = 3,
    json_mode: bool = False,
    response_schema: Optional[Dict[str, Any]] = None,
) -> Union[str, Dict[str, Any]]:
    """
    通用 Gemma 调用入口
    :param json_mode: 是否强制输出 JSON (Tagging 阶段使用)
    :param response_schema: 如果 json_mode 为 True，可传入具体的 Schema 约束
    """
    messages = _prepare_messages(image_paths, prompt)

    # 构造参数
    kwargs: dict[str, Any] = {
        "model": _get_llm_model_name(),
        "input": messages,
        "store": False,
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
            response = _get_llm_client().responses.create(**kwargs)
            if response.status != "completed" or not response.output_text.strip():
                raise RuntimeError("AI response did not complete with text")
            content_text = response.output_text

            # 如果是 JSON 模式，尝试解析为字典返回
            if json_mode:
                try:
                    # === 这里调用清洗函数 ===
                    clean_content = _clean_json_response(content_text)
                    parsed = json.loads(clean_content)
                    return parsed if isinstance(parsed, dict) else {"error": "Invalid JSON", "raw": content_text}
                except json.JSONDecodeError:
                    logger.warning(f"LLM returned invalid JSON: {content_text}")
                    # 可以在这里做简单的清理重试，或者直接抛出
                    return {"error": "Invalid JSON", "raw": content_text}

            return content_text

        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{retry_count} failed. Error: {e}")
            if attempt == retry_count - 1:
                logger.error("Max retries reached.")
                raise e

    return ""


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
