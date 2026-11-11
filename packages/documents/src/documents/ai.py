"""The one entry for direct model calls (text and vision); the Deep agent has its own runtime.

PydanticAI owns the transport, the output parsing and a bounded output correction. PPAP
keeps its prompts and validates meaning after this returns: a schema-valid answer is not
a correct one, and unknown or missing evidence never becomes PASS here.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from typing import Any

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import httpx2  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from pydantic_ai import Agent, BinaryContent, NativeOutput, PromptedOutput, StructuredDict  # noqa: E402
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior  # noqa: E402
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings  # noqa: E402
from pydantic_ai.providers.openai import OpenAIProvider  # noqa: E402

from documents.model_access import ModelAccess, ModelPurpose, ModelUnavailable, current_model_access, streams_only  # noqa: E402

# One correction of an unparsable answer; an operation never loops on the model.
OUTPUT_RETRIES = 1
_OBJECT = {"type": "object", "additionalProperties": True}


class AIError(RuntimeError):
    """A direct call did not produce an answer. The message is only a code, never provider text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AIResult:
    output: dict[str, Any]
    # Provider token counts when reported; observation only, never a success condition.
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str = ""

    def usage(self) -> dict[str, Any] | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "source": "provider"}


_OBSERVER: ContextVar[Callable[[str, AIResult | None, float], None] | None] = ContextVar("ai_usage_observer", default=None)


@contextmanager
def usage_observer(callback: Callable[[str, AIResult | None, float], None]) -> Iterator[None]:
    """Report each call (task, result or None on failure, seconds) to the owner's tracker."""
    token = _OBSERVER.set(callback)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


def _streams(access: ModelAccess) -> bool:
    return streams_only(access.base_url)


def _model(access: ModelAccess, timeout: float) -> OpenAIResponsesModel:
    client = AsyncOpenAI(
        api_key=access.api_key.get_secret_value(),
        base_url=access.base_url,
        max_retries=0,
        timeout=timeout,
        # The SDK's own HTTP client type; never follow a redirect or an environment proxy.
        http_client=httpx2.AsyncClient(follow_redirects=False, trust_env=False, timeout=timeout),
    )
    return OpenAIResponsesModel(access.model, provider=OpenAIProvider(openai_client=client))


def _settings(access: ModelAccess, max_tokens: int, timeout: float) -> OpenAIResponsesModelSettings:
    settings = OpenAIResponsesModelSettings(openai_store=False, max_tokens=max_tokens, timeout=timeout)
    if access.reasoning_effort:
        settings["openai_reasoning_effort"] = access.reasoning_effort  # type: ignore[typeddict-item]
    extra = access.extra_body()
    if extra:
        settings["extra_body"] = extra
    return settings


async def _run(access: ModelAccess, *, purpose: ModelPurpose, instructions: str, prompt: str, images: Sequence[bytes], schema: dict[str, Any] | None, max_tokens: int, timeout: float) -> AIResult:
    output_type = StructuredDict(schema or _OBJECT, name=(schema or {}).get("title") or "answer")
    # Vision keeps the provider's native JSON Schema contract; text asks for JSON in the
    # prompt, which every Responses-compatible endpoint we use accepts.
    output = NativeOutput(output_type) if purpose == "vision" and schema else PromptedOutput(output_type)
    model = _model(access, timeout)
    agent: Agent[None, dict[str, Any]] = Agent(model, output_type=output, instructions=instructions or None, retries=OUTPUT_RETRIES)
    content: list[Any] = [prompt, *(BinaryContent(image, media_type="image/png") for image in images)]
    settings = _settings(access, max_tokens, timeout)
    try:
        if _streams(access):
            async with agent.run_stream(content, model_settings=settings) as run:
                answer = await run.get_output()
                usage = run.usage
        else:
            result = await agent.run(content, model_settings=settings)
            answer, usage = result.output, result.usage
    finally:
        await model.client.close()
    if not isinstance(answer, dict):
        raise AIError("invalid_output")
    known = usage.requests > 0 and (usage.input_tokens or usage.output_tokens)
    return AIResult(answer, usage.input_tokens if known else None, usage.output_tokens if known else None, access.model)


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, ModelUnavailable):
        raise exc
    if isinstance(exc, AIError):
        return exc.code
    if isinstance(exc, (TimeoutError, httpx2.TimeoutException)) or "timeout" in type(exc).__name__.lower():
        return "timeout"
    if isinstance(exc, UnexpectedModelBehavior):
        return "invalid_output"
    if isinstance(exc, ModelHTTPError):
        return "provider_status"
    return "request_failed"


async def run_json_async(
    task: str,
    *,
    prompt: str,
    instructions: str = "",
    purpose: ModelPurpose = "text",
    images: Sequence[bytes] = (),
    schema: dict[str, Any] | None = None,
    max_tokens: int = 1000,
    timeout: float = 900,
) -> AIResult:
    """Ask the model for one JSON object. The model and key come from the current scope."""
    access = current_model_access(purpose)
    started = time.monotonic()
    observer = _OBSERVER.get()
    try:
        result = await _run(access, purpose=purpose, instructions=instructions, prompt=prompt, images=images, schema=schema, max_tokens=max_tokens, timeout=timeout)
    except BaseException as exc:
        if observer is not None:
            observer(task, None, time.monotonic() - started)
        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        # Provider bodies may echo a credential or the document; keep only a code.
        raise AIError(_error_code(exc)) from None
    if observer is not None:
        observer(task, result, time.monotonic() - started)
    return result


def run_json(task: str, **kwargs: Any) -> AIResult:
    """Synchronous form for worker and checker code that is not itself async."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_json_async(task, **kwargs))
    # Called from inside an event loop: run on a private loop in this context's copy.
    box: dict[str, Any] = {}
    context = copy_context()

    def target() -> None:
        try:
            box["result"] = context.run(asyncio.run, run_json_async(task, **kwargs))
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=target, name="ppap-ai-call")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["result"]
