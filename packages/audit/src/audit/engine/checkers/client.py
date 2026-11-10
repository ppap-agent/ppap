"""AI客户端 - 统一管理OpenAI连接和基础配置"""

import atexit
import json
import logging
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Self
from urllib.parse import urlsplit

import httpx
from documents.model_access import ModelUnavailable, current_model_access, model_access_scoped, model_request_scope
from openai import OpenAI as BaseOpenAI
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from audit.usage import record_token_usage, record_unreported_usage

ISOLATED_DEV_LLM_BASE_URL = "https://api.trustedtunnel.app/v1"  # pragma: allowlist secret
ISOLATED_DEV_LLM_MODEL = "gpt-5.6-luna"  # pragma: allowlist secret


class _CheckerLlm(BaseSettings):
    """Model view used by the checker client. It does not read product state."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True, case_sensitive=True, env_file=None)

    profile: str = Field(default="isolated-dev", alias="PPAP_PROFILE")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="", alias="LLM_BASE_URL")
    llm_model: str = Field(default="", alias="LLM_MODEL")
    DEVELOPMENT: bool = False
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    MODEL_NAME: str = ""
    OPENAI_EXTRA_BODY_JSON: str = ""
    OPENAI_REASONING_EFFORT: str | None = None
    OPENAI_TEMPERATURE: float | None = None
    OPENAI_TOP_P: float | None = None

    @model_validator(mode="after")
    def fill_from_product_llm(self) -> Self:
        if self.profile == "isolated-dev":
            if not self.llm_base_url.strip():
                self.llm_base_url = ISOLATED_DEV_LLM_BASE_URL
            if not self.llm_model.strip():
                self.llm_model = ISOLATED_DEV_LLM_MODEL
        elif not self.llm_base_url.strip() or not self.llm_model.strip():
            raise ValueError("production must set LLM_BASE_URL and LLM_MODEL explicitly")
        if not self.OPENAI_API_KEY.strip():
            self.OPENAI_API_KEY = self.llm_api_key
        if not self.OPENAI_BASE_URL.strip():
            self.OPENAI_BASE_URL = self.llm_base_url
        if not self.MODEL_NAME.strip():
            self.MODEL_NAME = self.llm_model
        return self


def get_settings() -> _CheckerLlm:
    return _CheckerLlm()


logger = logging.getLogger(__name__)


class _InvalidAIResponseError(RuntimeError):
    """Raised when AI response schema is incomplete and may be transient."""


class ModelTransportError(Exception):
    """A Responses call failed. The message is only a code, never a provider body."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TextCompletion:
    """One completed text response and its provider token counts, when known."""

    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None


def completion_text(value: str | TextCompletion) -> str:
    """Accept the real result or a test double that still returns text."""
    if isinstance(value, TextCompletion):
        return value.text
    if isinstance(value, str):
        return value
    raise TypeError("model response must be text")


def _uses_codex_transport(settings: Any) -> bool:
    return urlsplit(getattr(settings, "OPENAI_BASE_URL", "")).path.rstrip("/").endswith("/backend-api/codex")


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
        if model_access_scoped():
            return object.__new__(cls)
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(AIClient, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        self._personal_scope = model_access_scoped()
        if self._personal_scope:
            return
        if self._initialized:
            return

        self._initialize(get_settings())

    def _initialize(self, settings: Any, *, personal: bool = False) -> None:
        self._personal_scope = False
        self._bound_personal_call = personal
        self.settings = settings
        self.last_usage = None
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
        if not personal:
            atexit.register(self.shutdown)

        # Mark as initialized
        self._initialized = True

        client_options: dict[str, Any] = {"max_retries": 0, "http_client": httpx.Client(follow_redirects=False, trust_env=False)} if personal else {}
        if self.settings.OPENAI_BASE_URL:
            self.client = openai_cls(
                api_key=self.settings.OPENAI_API_KEY,
                base_url=self.settings.OPENAI_BASE_URL,
                **client_options,
            )
        else:
            self.client = openai_cls(api_key=self.settings.OPENAI_API_KEY)
        self.extra_body = _parse_extra_body(self.settings.OPENAI_EXTRA_BODY_JSON)

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
        文本专用 Responses 接口

        Args:
            messages: 文本消息列表
            max_tokens: 最大token数
            temperature: 温度参数
            text_format: 输出格式
            operation: 操作描述，用于token跟踪

        Returns:
            AI响应内容
        """
        if (getattr(self, "_personal_scope", False) or model_access_scoped()) and not getattr(self, "_bound_personal_call", False):
            # Resolve at each request: no singleton key, no platform defaults,
            # no Langfuse export, and no automatic replay after uncertain IO.
            access = current_model_access()
            current = object.__new__(AIClient)
            current._initialize(
                SimpleNamespace(
                    DEVELOPMENT=False,
                    OPENAI_API_KEY=access.api_key.get_secret_value(),
                    OPENAI_BASE_URL=access.base_url,
                    MODEL_NAME=access.model,
                    OPENAI_EXTRA_BODY_JSON="",
                ),
                personal=True,
            )
            try:
                text = current.response_text(
                    messages,
                    max_tokens,
                    temperature,
                    text_format,
                    operation,
                    request_timeout=request_timeout,
                    retry_limit=0,
                )
                self.last_usage = current.last_usage
                return text
            finally:
                current.client.close()
                current.settings.OPENAI_API_KEY = ""
        try:
            request_kwargs = self._build_request_kwargs(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                text_format=text_format,
            )
            if request_timeout is not None:
                request_kwargs["timeout"] = request_timeout
            request_client = self.client
            if retry_limit is not None:
                request_client = self.client.with_options(max_retries=max(0, retry_limit))
            max_retries = max(0, int(getattr(self.settings, "OPENAI_EMPTY_CHOICES_RETRIES", 2) if retry_limit is None else retry_limit))
            base_retry_delay = float(getattr(self.settings, "OPENAI_EMPTY_CHOICES_RETRY_DELAY_SECONDS", 1.0))

            for attempt in range(max_retries + 1):
                try:
                    with model_request_scope(getattr(self.settings, "OPENAI_BASE_URL", ""), request_kwargs) as settle:
                        if _uses_codex_transport(self.settings):
                            with request_client.responses.stream(**request_kwargs) as stream:
                                response = stream.get_final_response()
                        else:
                            response = request_client.responses.create(**request_kwargs)
                        settle(response)
                except ModelUnavailable:
                    # Local refusals did not send another paid request.
                    raise
                except Exception as exc:
                    record_unreported_usage()
                    if getattr(self, "_bound_personal_call", False):
                        name = type(exc).__name__.lower()
                        if "timeout" in name or "connection" in name or "transport" in name:
                            raise
                        # The provider body can echo a credential. Do not chain it.
                        raise ModelTransportError("request_failed") from None
                    raise

                # A visible provider receipt is usage even when its audit output
                # is incomplete. This observes SDK calls, not hidden HTTP retries.
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "input_tokens", None)
                output_tokens = getattr(usage, "output_tokens", None)
                thinking_tokens = None
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
                    self.last_usage = {"input_tokens": input_tokens, "output_tokens": output_tokens, "source": "provider"}
                    if thinking_tokens is not None:
                        self.last_usage["thinking_tokens"] = thinking_tokens
                else:
                    self.last_usage = None
                    record_unreported_usage()

                try:
                    if getattr(response, "status", None) != "completed":
                        raise _InvalidAIResponseError("AI response did not complete")
                    content = getattr(response, "output_text", None)
                    if not isinstance(content, str):
                        raise TypeError("AI response content is not text")
                    if not content.strip():
                        raise _InvalidAIResponseError("AI response has no output text")

                    return TextCompletion(
                        text=content,
                        input_tokens=input_tokens if type(input_tokens) is int and input_tokens >= 0 else None,
                        output_tokens=output_tokens if type(output_tokens) is int and output_tokens >= 0 else None,
                        thinking_tokens=thinking_tokens,
                    )
                except _InvalidAIResponseError as exc:
                    if attempt >= max_retries:
                        raise ModelTransportError("incomplete") from None

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

            raise ModelTransportError("incomplete")

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
        for message in messages:
            content = message.get("content", "")
            if not isinstance(content, str) and (
                not isinstance(content, list) or any(not isinstance(item, dict) or item.get("type") != "input_text" or not isinstance(item.get("text"), str) for item in content)
            ):
                raise ValueError("The text client accepts text only; visual evidence uses OCR configuration")
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
        if _uses_codex_transport(self.settings):
            # The Codex endpoint requires SSE and rejects sampling parameters.
            kwargs.pop("temperature", None)
            kwargs.pop("top_p", None)
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
