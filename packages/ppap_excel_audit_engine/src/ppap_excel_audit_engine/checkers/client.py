"""AI客户端 - 统一管理OpenAI连接和基础配置"""

import asyncio
import atexit
import json
import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from openai import OpenAI as BaseOpenAI
from ppap_core.settings import get_settings
from ppap_core.token_tracker import record_token_usage, record_unreported_usage

logger = logging.getLogger(__name__)


class _InvalidAIResponseError(RuntimeError):
    """Raised when AI response schema is incomplete and may be transient."""


async def _bounded_visual_request(settings: Any, kwargs: dict[str, Any], timeout: float) -> Any:
    """Use cancellation to bound an entire provider response, not one socket read."""
    async with asyncio.timeout(timeout):
        async with AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL or None,
            max_retries=0,
        ) as client:
            return await client.responses.create(**kwargs)


def _parse_extra_body(raw: str) -> Dict[str, Any] | None:
    value = raw.strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        logger.warning("Invalid OPENAI_EXTRA_BODY_JSON; ignoring it")
        return None
    if not isinstance(parsed, dict):
        logger.warning("OPENAI_EXTRA_BODY_JSON must be a JSON object; ignoring it")
        return None
    return parsed


class AIClient:
    """统一的AI客户端，集成Langfuse追踪 - 单例模式"""

    _instance: "AIClient | None" = None
    _lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> "AIClient":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(AIClient, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self.settings = get_settings()
        self.development_mode = bool(self.settings.DEVELOPMENT)
        self.langfuse_enabled = False
        self.langfuse_client = None

        openai_cls = BaseOpenAI
        if self.development_mode:
            try:
                from langfuse.openai import OpenAI as LangfuseOpenAI

                openai_cls = LangfuseOpenAI
                self.langfuse_enabled = True
                logger.debug("Development mode enabled with Langfuse tracking")
            except ImportError as exc:
                logger.warning("Langfuse unavailable: error_type=%s", type(exc).__name__)

        if self.langfuse_enabled:
            try:
                from langfuse import get_client

                self.langfuse_client = get_client()
                logger.debug("Langfuse客户端初始化成功")
            except Exception as exc:
                logger.error("Langfuse initialization failed: error_type=%s", type(exc).__name__)

        # Register shutdown handler
        atexit.register(self.shutdown)

        # Mark as initialized
        self._initialized = True

        # Rate limiting setup
        self._rate_limit_requests_per_minute = getattr(
            self.settings,
            "AI_RATE_LIMIT_PER_MINUTE",
            60,
        )  # Default 60 requests per minute
        self._request_times: deque[float] = deque()
        self._rate_limit_lock = threading.Lock()

        if self.settings.OPENAI_BASE_URL:
            self.client = openai_cls(
                api_key=self.settings.OPENAI_API_KEY,
                base_url=self.settings.OPENAI_BASE_URL,
            )
        else:
            self.client = openai_cls(api_key=self.settings.OPENAI_API_KEY)
        self.extra_body = _parse_extra_body(self.settings.OPENAI_EXTRA_BODY_JSON)

    @classmethod
    def get_instance(cls) -> "AIClient":
        """获取单例实例的类方法"""
        return cls()

    def _wait_for_rate_limit(self) -> None:
        """等待直到满足速率限制要求"""
        while True:
            wait_time = 0.0
            with self._rate_limit_lock:
                current_time = time.time()

                # 清理60秒前的请求记录
                while self._request_times and current_time - self._request_times[0] >= 60:
                    self._request_times.popleft()

                if len(self._request_times) < self._rate_limit_requests_per_minute:
                    # 记录当前请求时间
                    self._request_times.append(current_time)
                    logger.debug(f"AI请求记录: 当前分钟内已发送{len(self._request_times)}次请求")
                    return

                oldest_request = self._request_times[0]
                wait_time = max(0.0, 60 - (current_time - oldest_request))

            # IMPORTANT: sleep outside lock to avoid blocking other threads.
            if wait_time > 0:
                logger.warning(
                    "AI请求速率限制: 已达到每分钟%s次限制，等待%.2f秒",
                    self._rate_limit_requests_per_minute,
                    wait_time,
                )
                time.sleep(wait_time)

    def get_rate_limit_status(self) -> Dict[str, Any]:
        """获取当前速率限制状态"""
        with self._rate_limit_lock:
            current_time = time.time()

            # 清理60秒前的请求记录
            while self._request_times and current_time - self._request_times[0] >= 60:
                self._request_times.popleft()

            remaining_requests = max(0, self._rate_limit_requests_per_minute - len(self._request_times))
            reset_time = 0.0

            if self._request_times:
                oldest_request = self._request_times[0]
                reset_time = oldest_request + 60

            return {
                "requests_per_minute_limit": self._rate_limit_requests_per_minute,
                "requests_made_this_minute": len(self._request_times),
                "remaining_requests": remaining_requests,
                "reset_time": reset_time,
                "seconds_until_reset": max(0, reset_time - current_time) if reset_time > 0 else 0,
            }

    def update_rate_limit(self, requests_per_minute: int) -> None:
        """动态更新速率限制"""
        with self._rate_limit_lock:
            old_limit = self._rate_limit_requests_per_minute
            self._rate_limit_requests_per_minute = max(1, requests_per_minute)  # 确保至少为1
            logger.info(f"AI速率限制已更新: {old_limit} -> {self._rate_limit_requests_per_minute} 请求/分钟")

    def flush(self) -> None:
        """手动触发所有缓冲观察数据发送到Langfuse API"""
        if self.langfuse_enabled and self.langfuse_client:
            try:
                self.langfuse_client.flush()
                logger.debug("Langfuse数据已刷新")
            except Exception as exc:
                logger.error("Langfuse flush failed: error_type=%s", type(exc).__name__)
        else:
            logger.debug("Langfuse未启用，跳过数据刷新")

    def shutdown(self) -> None:
        """优雅关闭Langfuse客户端"""
        if self.langfuse_enabled and self.langfuse_client:
            try:
                self.langfuse_client.shutdown()
                logger.debug("Langfuse客户端已关闭")
            except Exception as exc:
                logger.error("Langfuse shutdown failed: error_type=%s", type(exc).__name__)
        else:
            logger.debug("Langfuse未启用，无需关闭")

    def response_text(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 1000,
        temperature: float = 0.1,
        text_format: Optional[Dict[str, str]] = None,
        operation: str = "response_text",
        *,
        request_timeout: float | None = None,
        retry_limit: int | None = None,
    ) -> str:
        """
        统一的 Responses 接口，支持文本和图像

        Args:
            messages: 消息列表，支持文本和图像内容
            max_tokens: 最大token数
            temperature: 温度参数
            text_format: 输出格式
            operation: 操作描述，用于token跟踪

        Returns:
            AI响应内容
        """
        try:
            request_kwargs = self._build_request_kwargs(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                text_format=text_format,
            )
            if request_timeout is not None:
                request_kwargs["timeout"] = request_timeout
            # Per-call SDK options do not mutate the shared client or its defaults.
            request_client = self.client if retry_limit is None else self.client.with_options(max_retries=max(0, retry_limit))
            max_retries = max(0, int(getattr(self.settings, "OPENAI_EMPTY_CHOICES_RETRIES", 2) if retry_limit is None else retry_limit))
            base_retry_delay = float(getattr(self.settings, "OPENAI_EMPTY_CHOICES_RETRY_DELAY_SECONDS", 1.0))

            for attempt in range(max_retries + 1):
                # 应用速率限制（每次请求都需要遵守）
                self._wait_for_rate_limit()
                try:
                    if request_timeout is not None and isinstance(self.client, BaseOpenAI):
                        # Only real SDK clients take the cancellable transport path.
                        # Lightweight injected test clients preserve their public stub API.
                        response = asyncio.run(_bounded_visual_request(self.settings, request_kwargs, request_timeout))
                    else:
                        response = request_client.responses.create(**request_kwargs)
                except Exception:
                    record_unreported_usage()
                    raise

                # A visible provider receipt is usage even when its audit output
                # is incomplete. This observes SDK calls, not hidden HTTP retries.
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "input_tokens", None)
                output_tokens = getattr(usage, "output_tokens", None)
                if type(input_tokens) is int and input_tokens >= 0 and type(output_tokens) is int and output_tokens >= 0:
                    details = getattr(usage, "output_tokens_details", None)
                    thinking_tokens = getattr(details, "reasoning_tokens", None)
                    if type(thinking_tokens) is not int or not 0 <= thinking_tokens <= output_tokens:
                        thinking_tokens = None
                    record_token_usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        thinking_tokens=thinking_tokens,
                        model_name=self.settings.MODEL_NAME,
                        operation=operation,
                    )
                else:
                    record_unreported_usage()

                try:
                    if getattr(response, "status", None) != "completed":
                        raise _InvalidAIResponseError("AI response did not complete")
                    content = getattr(response, "output_text", None)
                    if not isinstance(content, str):
                        raise TypeError("AI response content is not text")
                    if not content.strip():
                        raise _InvalidAIResponseError("AI response has no output text")

                    return content
                except _InvalidAIResponseError as exc:
                    if attempt >= max_retries:
                        raise RuntimeError(f"AI响应异常且重试后仍失败: {exc}") from exc

                    delay_seconds = max(0.0, base_retry_delay) * (2**attempt)
                    logger.warning(
                        "AI response retry (%s/%s): operation=%s error_type=%s",
                        attempt + 1,
                        max_retries,
                        operation,
                        type(exc).__name__,
                    )
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)

            raise RuntimeError("AI响应异常且未执行有效重试")

        except Exception as exc:
            logger.error("AI call failed: operation=%s error_type=%s", operation, type(exc).__name__)
            raise

    def _build_request_kwargs(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int,
        temperature: float,
        text_format: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        request_temperature = getattr(self.settings, "OPENAI_TEMPERATURE", None)
        kwargs: Dict[str, Any] = {
            "model": self.settings.MODEL_NAME,
            "input": messages,
            "max_output_tokens": max_tokens,
            "temperature": request_temperature if request_temperature is not None else temperature,
            "store": False,
            "timeout": 900,
        }
        reasoning_effort = getattr(self.settings, "OPENAI_REASONING_EFFORT", None)
        if reasoning_effort:
            kwargs["reasoning"] = {"effort": reasoning_effort}
        top_p = getattr(self.settings, "OPENAI_TOP_P", None)
        if top_p is not None:
            kwargs["top_p"] = top_p
        if text_format:
            kwargs["text"] = {"format": text_format}
        if self.extra_body is not None:
            kwargs["extra_body"] = self.extra_body
        return kwargs

    def structured_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.1,
        json_format: bool = False,
        operation: str = "structured_completion",
        *,
        request_timeout: float | None = None,
        retry_limit: int | None = None,
    ) -> str:
        """
        结构化完成接口

        Args:
            system_prompt: 系统提示
            user_prompt: 用户提示
            max_tokens: 最大token数
            temperature: 温度参数
            json_format: 是否要求JSON格式
            operation: 操作描述，用于token跟踪

        Returns:
            AI响应内容
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        text_format = {"type": "json_object"} if json_format else None

        return self.response_text(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            text_format=text_format,
            operation=operation,
            request_timeout=request_timeout,
            retry_limit=retry_limit,
        )

    def _llm_is_available(self) -> bool:
        return bool(str(getattr(self.settings, "OPENAI_API_KEY", "")).strip() and str(getattr(self.settings, "MODEL_NAME", "")).strip())

    @staticmethod
    def _coerce_json_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() == "true"

    def llm_equal_batch(
        self,
        items: List[Dict[str, str]],
        equality_standard: str,
        operation: str = "llm_equal_batch",
        batch_size: int = 25,
    ) -> Dict[str, bool]:
        """Use the LLM to decide whether pairs of strings should be treated as equal."""
        standard = str(equality_standard or "").strip()
        results: Dict[str, bool] = {}
        if not items or not standard:
            return results

        normalized_items: List[Dict[str, str]] = []
        for idx, item in enumerate(items):
            item_id = str(item.get("id") or idx)
            left = str(item.get("left") or "").strip()
            right = str(item.get("right") or "").strip()
            if not left or not right:
                results[item_id] = False
                continue
            if left == right:
                results[item_id] = True
                continue
            normalized_items.append({"id": item_id, "left": left, "right": right})

        if not normalized_items or not self._llm_is_available():
            for item in normalized_items:
                results.setdefault(item["id"], False)
            return results

        system_prompt = (
            "你是一个严格的文本相等判断器。"
            "你只能根据给定的“判断相等的标准”来判断每一对文本是否应视为相等。"
            "逐项独立判断，不要在不同项目之间迁移结论，不要扩展标准，不要做额外推断。"
            "尤其禁止把同义词、近义词、缩写扩写、范围扩大或缩小、一个更宽泛或更具体的表述判成相等。"
            "只输出 JSON，格式为 "
            '{"results": [{"id": "...", "equal": true}, {"id": "...", "equal": false}]}。'
        )

        safe_batch_size = max(1, int(batch_size))
        for start in range(0, len(normalized_items), safe_batch_size):
            chunk = normalized_items[start : start + safe_batch_size]
            user_prompt = json.dumps(
                {
                    "equality_standard": standard,
                    "items": chunk,
                },
                ensure_ascii=False,
            )

            try:
                response = self.structured_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max(200, min(1200, 80 * len(chunk))),
                    temperature=0.0,
                    json_format=True,
                    operation=operation,
                )
                payload = json.loads(response)
                chunk_results = payload.get("results", []) if isinstance(payload, dict) else []
                chunk_result_map = {str(item.get("id")): self._coerce_json_bool(item.get("equal")) for item in chunk_results if isinstance(item, dict) and item.get("id") is not None}
                for item in chunk:
                    results[item["id"]] = chunk_result_map.get(item["id"], False)
            except Exception as exc:
                logger.error("llm_equal_batch failed: error_type=%s", type(exc).__name__)
                for item in chunk:
                    results[item["id"]] = False

        return results

    def llm_equal(
        self,
        str1: str,
        str2: str,
        equality_standard: str,
        operation: str = "llm_equal",
    ) -> bool:
        """Use the LLM to decide whether two strings should be treated as equal."""
        results = self.llm_equal_batch(
            items=[{"id": "0", "left": str1, "right": str2}],
            equality_standard=equality_standard,
            operation=operation,
            batch_size=1,
        )
        return results.get("0", False)

    def image_analysis_completion(
        self,
        system_prompt: str,
        user_text: str,
        image_base64_list: List[str],
        max_tokens: int = 12000,
        temperature: float = 0.1,
        json_format: bool = False,
        operation: str = "image_analysis",
    ) -> str:
        """
        图像分析完成接口

        Args:
            system_prompt: 系统提示
            user_text: 用户文本描述
            image_base64_list: base64编码的图像列表
            max_tokens: 最大token数
            temperature: 温度参数
            json_format: 是否要求JSON格式
            operation: 操作描述，用于token跟踪

        Returns:
            AI响应内容
        """
        from ppap_vision.image import OpenAIImageMessageBuilder

        messages = OpenAIImageMessageBuilder.create_image_analysis_messages(
            system_prompt=system_prompt,
            user_text=user_text,
            image_base64_list=image_base64_list,
        )

        text_format = {"type": "json_object"} if json_format else None

        # Visual evidence is slow and potentially billed. A single bounded
        # Responses attempt prevents hidden SDK retries and multi-minute
        # stalls under concurrent PDF oracle execution. Never convert a timeout
        # into PASS or silently reuse an older observation.
        return self.response_text(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            text_format=text_format,
            operation=operation,
            request_timeout=120,
            retry_limit=0,
        )
