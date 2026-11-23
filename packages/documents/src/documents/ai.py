"""The one entry for direct model calls (text and vision); the Deep agent has its own runtime.

PydanticAI owns the transport, the output parsing and a bounded output correction. PPAP
keeps its prompts and validates meaning after this returns: a schema-valid answer is not
a correct one, and unknown or missing evidence never becomes PASS here.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import httpx2  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from pydantic_ai import Agent, BinaryContent, NativeOutput, PromptedOutput, StructuredDict  # noqa: E402
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior  # noqa: E402
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings  # noqa: E402
from pydantic_ai.providers.openai import OpenAIProvider  # noqa: E402

from documents.model_access import ModelAccess, ModelPurpose, ModelUnavailable, current_model_access, streams_only  # noqa: E402
from documents.model_calls import CALL_HEADER, FORMAT, CallRecorder, current_recorder, current_scope, image_sha256, recording, text_request  # noqa: E402

# One correction of an unparsable answer; an operation never loops on the model.
OUTPUT_RETRIES = 1
MAX_TOKENS = 1000
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


def _model(access: ModelAccess, timeout: float, call_id: str | None = None) -> OpenAIResponsesModel:
    client = AsyncOpenAI(
        api_key=access.api_key.get_secret_value(),
        base_url=access.base_url,
        max_retries=0,
        timeout=timeout,
        # The SDK's own HTTP client type; never follow a redirect or an environment proxy.
        http_client=httpx2.AsyncClient(follow_redirects=False, trust_env=False, timeout=timeout),
        default_headers={CALL_HEADER: call_id} if call_id else None,
    )
    return OpenAIResponsesModel(access.model, provider=OpenAIProvider(openai_client=client))


def _settings(access: ModelAccess, max_tokens: int, timeout: float, reasoning_effort: str | None = None) -> OpenAIResponsesModelSettings:
    settings = OpenAIResponsesModelSettings(openai_store=False, max_tokens=max_tokens, timeout=timeout)
    # A task that only reformats text may ask for less reasoning than the deployment default.
    effort = reasoning_effort or access.reasoning_effort
    if effort:
        settings["openai_reasoning_effort"] = effort  # type: ignore[typeddict-item]
    return settings


async def _run(
    access: ModelAccess,
    *,
    purpose: ModelPurpose,
    instructions: str,
    prompt: str,
    images: Sequence[bytes],
    schema: dict[str, Any] | None,
    max_tokens: int,
    timeout: float,
    reasoning_effort: str | None = None,
    call_id: str | None = None,
) -> AIResult:
    output_type = StructuredDict(schema or _OBJECT, name=(schema or {}).get("title") or "answer")
    # Vision keeps the provider's native JSON Schema contract; text asks for JSON in the
    # prompt, which every Responses-compatible endpoint we use accepts.
    output = NativeOutput(output_type) if purpose == "vision" and schema else PromptedOutput(output_type)
    model = _model(access, timeout, call_id)
    agent: Agent[None, dict[str, Any]] = Agent(model, output_type=output, instructions=instructions or None, retries=OUTPUT_RETRIES)
    content: list[Any] = [prompt, *(BinaryContent(image, media_type="image/png") for image in images)]
    settings = _settings(access, max_tokens, timeout, reasoning_effort)
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
    instructions: str | None = None,
    purpose: ModelPurpose = "text",
    images: Sequence[bytes] = (),
    schema: dict[str, Any] | None = None,
    max_tokens: int | None = None,
    timeout: float = 900,
    reasoning_effort: str | None = None,
    replay: Callable[[], tuple[str, Any]] | None = None,
) -> AIResult:
    """Ask the model for one JSON object. The model and key come from the current scope.

    Where a recorder is installed, the call leaves one record (#360). ``replay`` names the call as offline
    replay matches it, as (kind, request); a direct text call is matched by its own parameters.
    """
    access = current_model_access(purpose)
    recorder = current_recorder()
    call_id = uuid4().hex if recorder is not None else None
    started = time.monotonic()
    observer = _OBSERVER.get()
    try:
        result = await _run(
            access,
            purpose=purpose,
            instructions=instructions or "",
            prompt=prompt,
            images=images,
            schema=schema,
            max_tokens=MAX_TOKENS if max_tokens is None else max_tokens,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
            call_id=call_id,
        )
    except BaseException as exc:
        seconds = time.monotonic() - started
        if observer is not None:
            observer(task, None, seconds)
        interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError))
        # Provider bodies may echo a credential or the document; keep only a code.
        code = "cancelled" if interrupted else _error_code(exc)
        if recorder is not None and call_id is not None:
            _record(recorder, call_id, task, purpose, access, reasoning_effort, instructions, prompt, images, schema, max_tokens, timeout, replay, None, seconds, code)
        if interrupted:
            raise
        raise AIError(code) from None
    seconds = time.monotonic() - started
    if observer is not None:
        observer(task, result, seconds)
    if recorder is not None and call_id is not None:
        _record(recorder, call_id, task, purpose, access, reasoning_effort, instructions, prompt, images, schema, max_tokens, timeout, replay, result, seconds, "ok")
    return result


def _record(
    recorder: CallRecorder,
    call_id: str,
    task: str,
    purpose: ModelPurpose,
    access: ModelAccess,
    reasoning_effort: str | None,
    instructions: str | None,
    prompt: str,
    images: Sequence[bytes],
    schema: dict[str, Any] | None,
    max_tokens: int | None,
    timeout: float,
    replay: Callable[[], tuple[str, Any]] | None,
    result: AIResult | None,
    seconds: float,
    code: str,
) -> None:
    """Hand one call's record to the recorder. Nothing here changes the call's result."""
    record: dict[str, Any] = {"format": FORMAT, "call_id": call_id, "task": task, "purpose": purpose, "code": code}
    try:
        kind, request = replay() if replay is not None else (purpose, text_request(task, instructions=instructions, prompt=prompt, schema=schema, max_tokens=max_tokens))
        answer = json.dumps(result.output, ensure_ascii=False) if result is not None else None
        stored = {image_sha256(image): bytes(image) for image in images}
        record.update(
            recording(kind, request, answer, access.model),
            # Who answered: never the key or the full endpoint address.
            identity={
                "host": urlsplit(access.base_url).hostname or "",
                "model": access.model,
                "reasoning_effort": reasoning_effort or access.reasoning_effort,
                "revision": access.revision,
                "source": access.source,
            },
            images=[image_sha256(image) for image in images],
            timeout=timeout,
            usage=result.usage() if result is not None else None,
            duration_ms=max(0, round(seconds * 1000)),
            scope=current_scope(),
        )
        recorder.write(record, stored)
    except Exception as exc:  # noqa: BLE001 - a lost record never changes the call
        try:
            recorder.failed(record, "model_call_record_failed:" + type(exc).__name__)
        except Exception:  # noqa: BLE001
            pass


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
