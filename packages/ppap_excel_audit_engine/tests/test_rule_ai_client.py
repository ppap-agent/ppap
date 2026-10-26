"""Bounded rule calls use the real adapter with an external SDK stub."""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from ppap_core.contracts import AuditInput, RuleInput, RuleItem
from ppap_core.token_tracker import get_current_request_id, get_token_tracker, set_current_request_id
from ppap_excel_audit_engine.checkers.prompts import PromptManager


def test_rule_prompt_remains_usable_without_extracted_file_content():
    prompt = PromptManager.get_audit_prompt(
        AuditInput(audit_id="version-one", file_url="/tmp/original.xlsx", content=None),
        RuleInput(rule_id="confirmed-rules", type="控制计划"),
        RuleItem(id="1", category="任务项", priority="P1", rule_to_check="检查工序", type_of_rule="text"),
        agentic_prompt="",
    )

    assert "规则编号：1" in prompt
    assert "文件内容" in prompt


class SDK:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.options = []
        self.direct_calls = 0
        self.responses = self

    def with_options(self, **options):
        self.options.append(options)
        child = SDK(self.response, self.error)
        child.calls = self.calls
        return child

    def create(self, **kwargs):
        self.calls.append(kwargs)
        self.direct_calls += 1
        if self.error:
            raise self.error
        return self.response.pop(0) if isinstance(self.response, list) else self.response


@contextmanager
def tracked_request():
    tracker = get_token_tracker()
    previous = get_current_request_id()
    request_id = tracker.start_request_tracking()
    try:
        yield request_id
    finally:
        tracker.cleanup_request(request_id)
        set_current_request_id(previous)


class RuleAIClientTests(unittest.TestCase):
    def client(self, sdk):
        from ppap_excel_audit_engine.checkers.client import AIClient

        client = object.__new__(AIClient)
        client.settings = SimpleNamespace(MODEL_NAME="test", OPENAI_EMPTY_CHOICES_RETRY_DELAY_SECONDS=0)
        client.client = sdk
        client.extra_body = None
        client._wait_for_rate_limit = lambda: None
        return client

    def test_rule_call_bounds_sdk_and_empty_response_retries_without_changing_defaults(self):
        sdk = SDK(SimpleNamespace(status="completed", output_text=""))
        client = self.client(sdk)
        with self.assertLogs("ppap_excel_audit_engine.checkers.client", level="ERROR"):
            with self.assertRaises(RuntimeError):
                client.structured_completion("system", "source", request_timeout=120, retry_limit=0)
        self.assertEqual(len(sdk.calls), 1)
        self.assertEqual(sdk.direct_calls, 0)
        self.assertEqual(sdk.options, [{"max_retries": 0}])

        self.assertEqual(sdk.calls[0]["timeout"], 120)
        self.assertEqual(sdk.calls[0]["input"], [{"role": "system", "content": "system"}, {"role": "user", "content": "source"}])
        self.assertEqual(sdk.calls[0]["max_output_tokens"], 1000)
        self.assertFalse(sdk.calls[0]["store"])
        self.assertIs(client.client, sdk)
        sdk.response = SimpleNamespace(status="completed", output_text="ok")
        self.assertEqual(client.structured_completion("system", "source"), "ok")
        self.assertEqual(sdk.calls[-1]["timeout"], 900)
        self.assertEqual(sdk.options, [{"max_retries": 0}])

    def test_default_empty_response_retry_count_is_unchanged(self):
        sdk = SDK(SimpleNamespace(status="incomplete", output_text=""))
        with self.assertLogs("ppap_excel_audit_engine.checkers.client", level="WARNING"):
            with self.assertRaises(RuntimeError):
                self.client(sdk).structured_completion("system", "source")
        self.assertEqual(len(sdk.calls), 3)
        self.assertEqual(sdk.options, [])

    def test_incomplete_outputs_keep_each_visible_provider_usage_before_validation(self):
        sdk = SDK(SimpleNamespace(status="incomplete", output_text="", usage=SimpleNamespace(input_tokens=9, output_tokens=5)))
        with tracked_request() as request_id:
            with self.assertLogs("ppap_excel_audit_engine.checkers.client", level="WARNING"):
                with self.assertRaises(RuntimeError):
                    self.client(sdk).structured_completion("system", "source")
            summary = get_token_tracker().end_request_tracking(request_id).to_dict()
        self.assertEqual(len(sdk.calls), 3)
        self.assertEqual(summary["llm_calls_count"], 3)
        self.assertEqual(summary["total_input_tokens"], 27)
        self.assertEqual(summary["total_output_tokens"], 15)
        self.assertEqual(summary["total_tokens"], 42)
        self.assertEqual(summary["unreported_calls_count"], 0)
        self.assertTrue(summary["usage_complete"])

    def test_empty_or_malformed_output_does_not_erase_or_duplicate_a_valid_receipt(self):
        for status, output, error_type in [("completed", "", RuntimeError), ("failed", "discarded", RuntimeError), ("completed", None, TypeError)]:
            with self.subTest(status=status, output=output):
                sdk = SDK(SimpleNamespace(status=status, output_text=output, usage=SimpleNamespace(input_tokens=9, output_tokens=5)))
                with tracked_request() as request_id:
                    with self.assertLogs("ppap_excel_audit_engine.checkers.client", level="ERROR"):
                        with self.assertRaises(error_type):
                            self.client(sdk).structured_completion("system", "source", retry_limit=0)
                    summary = get_token_tracker().end_request_tracking(request_id).to_dict()
                self.assertEqual(len(sdk.calls), 1)
                self.assertEqual(summary["llm_calls_count"], 1)
                self.assertEqual(summary["unreported_calls_count"], 0)
                self.assertTrue(summary["usage_complete"])
                self.assertEqual(summary["total_tokens"], 14)

    def test_missing_or_invalid_usage_is_unreported_without_changing_valid_output(self):
        for label, usage in [
            ("missing", None),
            ("missing-input", SimpleNamespace(output_tokens=5)),
            ("missing-output", SimpleNamespace(input_tokens=9)),
            ("negative-input", SimpleNamespace(input_tokens=-1, output_tokens=5)),
            ("negative-output", SimpleNamespace(input_tokens=9, output_tokens=-1)),
            ("boolean-input", SimpleNamespace(input_tokens=True, output_tokens=5)),
            ("boolean-output", SimpleNamespace(input_tokens=9, output_tokens=False)),
            ("fractional-input", SimpleNamespace(input_tokens=1.5, output_tokens=5)),
            ("string-output", SimpleNamespace(input_tokens=9, output_tokens="5")),
        ]:
            with self.subTest(usage=label):
                sdk = SDK(SimpleNamespace(status="completed", output_text="ok", usage=usage))
                with tracked_request() as request_id:
                    self.assertEqual(self.client(sdk).structured_completion("system", "source"), "ok")
                    summary = get_token_tracker().end_request_tracking(request_id).to_dict()
                self.assertEqual(len(sdk.calls), 1)
                self.assertEqual(summary["llm_calls_count"], 1)
                self.assertEqual(summary["unreported_calls_count"], 1)
                self.assertFalse(summary["usage_complete"])
                self.assertEqual(summary["llm_calls"], [])
                for key in ("total_input_tokens", "total_output_tokens", "total_thinking_tokens", "total_tokens"):
                    self.assertIsNone(summary[key])

    def test_sdk_exception_is_one_unreported_call_without_an_application_retry(self):
        original = TimeoutError("private-provider-marker")
        sdk = SDK(error=original)
        with tracked_request() as request_id:
            with self.assertLogs("ppap_excel_audit_engine.checkers.client", level="ERROR") as logs:
                with self.assertRaises(TimeoutError) as raised:
                    self.client(sdk).structured_completion("system", "private-source-marker")
            self.assertIs(raised.exception, original)
            summary = get_token_tracker().end_request_tracking(request_id).to_dict()
        self.assertEqual(len(sdk.calls), 1)
        self.assertEqual(summary["llm_calls_count"], 1)
        self.assertEqual(summary["unreported_calls_count"], 1)
        self.assertFalse(summary["usage_complete"])
        self.assertIsNone(summary["total_tokens"])
        self.assertNotIn("private-provider-marker", "\n".join(logs.output))
        self.assertNotIn("private-source-marker", "\n".join(logs.output))

    def test_mixed_receipts_preserve_known_details_but_not_an_incomplete_total(self):
        sdk = SDK(
            [
                SimpleNamespace(status="incomplete", output_text="", usage=SimpleNamespace(input_tokens=9, output_tokens=5)),
                SimpleNamespace(status="incomplete", output_text="", usage=None),
                SimpleNamespace(status="completed", output_text="ok", usage=SimpleNamespace(input_tokens=3, output_tokens=2)),
            ]
        )
        with tracked_request() as request_id:
            with self.assertLogs("ppap_excel_audit_engine.checkers.client", level="WARNING"):
                self.assertEqual(self.client(sdk).structured_completion("system", "source"), "ok")
            summary = get_token_tracker().end_request_tracking(request_id).to_dict()
        self.assertEqual(len(sdk.calls), 3)
        self.assertEqual(summary["llm_calls_count"], 3)
        self.assertEqual(summary["unreported_calls_count"], 1)
        self.assertFalse(summary["usage_complete"])
        self.assertEqual([(row["input_tokens"], row["output_tokens"]) for row in summary["llm_calls"]], [(9, 5), (3, 2)])
        for key in ("total_input_tokens", "total_output_tokens", "total_thinking_tokens", "total_tokens"):
            self.assertIsNone(summary[key])

    def test_optional_reasoning_stays_unknown_without_invalidating_confirmed_io(self):
        for label, input_tokens, output_tokens, details, expected_thinking in [
            ("confirmed-zero", 0, 0, SimpleNamespace(reasoning_tokens=0), 0),
            ("confirmed-split", 9, 5, SimpleNamespace(reasoning_tokens=2), 2),
            ("all-output-is-reasoning", 9, 5, SimpleNamespace(reasoning_tokens=5), 5),
            ("missing-details", 9, 5, None, None),
            ("missing-reasoning", 9, 5, SimpleNamespace(), None),
            ("null-reasoning", 9, 5, SimpleNamespace(reasoning_tokens=None), None),
            ("negative-reasoning", 9, 5, SimpleNamespace(reasoning_tokens=-1), None),
            ("boolean-reasoning", 9, 5, SimpleNamespace(reasoning_tokens=True), None),
            ("fractional-reasoning", 9, 5, SimpleNamespace(reasoning_tokens=1.5), None),
            ("string-reasoning", 9, 5, SimpleNamespace(reasoning_tokens="2"), None),
            ("reasoning-exceeds-output", 9, 5, SimpleNamespace(reasoning_tokens=6), None),
        ]:
            with self.subTest(usage=label):
                sdk = SDK(
                    SimpleNamespace(
                        status="completed",
                        output_text="ok",
                        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens, output_tokens_details=details),
                    )
                )
                with tracked_request() as request_id:
                    self.assertEqual(self.client(sdk).structured_completion("system", "source"), "ok")
                    summary = get_token_tracker().end_request_tracking(request_id).to_dict()
                self.assertEqual(len(sdk.calls), 1)
                self.assertEqual(summary["llm_calls_count"], 1)
                self.assertEqual(summary["unreported_calls_count"], 0)
                self.assertTrue(summary["usage_complete"])
                self.assertEqual(summary["total_input_tokens"], input_tokens)
                self.assertEqual(summary["total_output_tokens"], output_tokens)
                self.assertEqual(summary["llm_calls"][0]["thinking_tokens"], expected_thinking)
                self.assertEqual(summary["total_thinking_tokens"], expected_thinking)
                self.assertEqual(summary["total_tokens"], 0 if input_tokens == 0 else 14)

    def test_provider_and_invalid_response_details_never_enter_client_logs(self):
        marker = "private-provider-source-marker"
        for sdk in (SDK(error=RuntimeError(marker)), SDK(SimpleNamespace(status="failed", output_text="", error=marker))):
            with self.subTest(error=bool(sdk.error)):
                client = self.client(sdk)
                with self.assertLogs("ppap_excel_audit_engine.checkers.client", level="ERROR") as logs:
                    with self.assertRaises(RuntimeError):
                        client.structured_completion("system", marker, request_timeout=120, retry_limit=0)
                self.assertNotIn(marker, "\n".join(logs.output))
                self.assertEqual(len(sdk.calls), 1)

    def test_json_image_and_usage_use_responses_shape(self):
        sdk = SDK(
            SimpleNamespace(
                status="completed",
                output_text='{"ok": true}',
                usage=SimpleNamespace(input_tokens=9, output_tokens=5, output_tokens_details=SimpleNamespace(reasoning_tokens=2)),
            )
        )
        client = self.client(sdk)
        with tracked_request() as request_id:
            self.assertEqual(client.image_analysis_completion("system", "inspect", ["data:image/png;base64,AAAA"], json_format=True), '{"ok": true}')
            summary = get_token_tracker().end_request_tracking(request_id).to_dict()
        self.assertEqual(summary["llm_calls_count"], 1)
        self.assertEqual(summary["unreported_calls_count"], 0)
        self.assertTrue(summary["usage_complete"])
        self.assertEqual(summary["total_tokens"], 14)
        self.assertEqual(
            {key: value for key, value in summary["llm_calls"][0].items() if key != "timestamp"},
            {"input_tokens": 9, "output_tokens": 5, "thinking_tokens": 2, "total_tokens": 14, "model_name": "test", "operation": "image_analysis"},
        )
        request = sdk.calls[0]
        self.assertEqual(request["text"], {"format": {"type": "json_object"}})
        self.assertEqual(request["timeout"], 120)
        self.assertEqual(sdk.options, [{"max_retries": 0}])
        self.assertEqual(
            request["input"][1]["content"],
            [
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "high"},
                {"type": "input_text", "text": "inspect"},
            ],
        )

    def test_reasoning_tokens_are_part_of_output_total(self):
        from ppap_core.token_tracker import TokenTracker

        tracker = TokenTracker()
        request_id = tracker.start_request_tracking()
        tracker.record_token_usage(input_tokens=9, output_tokens=5, thinking_tokens=2)
        self.assertEqual(tracker.end_request_tracking(request_id).total_tokens, 14)


if __name__ == "__main__":
    unittest.main()


def test_visual_provider_timeout_is_one_failed_attempt_without_hidden_retries():
    sdk = SDK(error=TimeoutError("private-visual-provider"))
    client = RuleAIClientTests().client(sdk)
    with tracked_request() as request_id:
        with unittest.TestCase().assertLogs("ppap_excel_audit_engine.checkers.client", level="ERROR") as logs:
            with unittest.TestCase().assertRaises(TimeoutError):
                client.image_analysis_completion("system", "private-document", ["data:image/png;base64,AAAA"])
        usage = get_token_tracker().end_request_tracking(request_id).to_dict()
    assert len(sdk.calls) == 1
    assert sdk.calls[0]["timeout"] == 120
    assert sdk.options == [{"max_retries": 0}]
    assert usage["unreported_calls_count"] == 1
    assert "private-visual-provider" not in "\n".join(logs.output)
    assert "private-document" not in "\n".join(logs.output)


def test_visual_async_request_cancels_response_body_with_one_provider_call():
    import asyncio
    from unittest.mock import patch

    from ppap_excel_audit_engine.checkers import client as client_module

    calls = []
    cancelled = []
    closed = []

    async def slow_create(**kwargs):
        calls.append(kwargs)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    class FakeAsyncClient:
        responses = SimpleNamespace(create=slow_create)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            closed.append(True)

    with patch.object(client_module, "AsyncOpenAI", return_value=FakeAsyncClient()):
        with unittest.TestCase().assertRaises(TimeoutError):
            asyncio.run(
                client_module._bounded_visual_request(
                    SimpleNamespace(OPENAI_API_KEY="unit-test-token", OPENAI_BASE_URL="https://example.invalid/v1"),
                    {"model": "unit-test"},
                    0.02,
                )
            )
    assert calls == [{"model": "unit-test"}]
    assert cancelled == [True]
    assert closed == [True]


def test_visual_provider_uses_bounded_async_path_with_real_sdk_type_simulated():
    import asyncio
    from unittest.mock import patch

    from ppap_excel_audit_engine.checkers import client as client_module

    sdk = SDK(SimpleNamespace(status="completed", output_text="verified"))
    client = RuleAIClientTests().client(sdk)

    async def provider(settings, kwargs, timeout):
        assert timeout == 120
        assert kwargs["timeout"] == 120
        return SimpleNamespace(status="completed", output_text="verified")

    with (
        patch.object(client_module, "BaseOpenAI", SDK),
        patch.object(client_module, "_run_bounded_visual_sync", side_effect=lambda settings, kwargs, timeout: asyncio.run(provider(settings, kwargs, timeout))),
    ):
        assert client.image_analysis_completion("system", "inspect", ["data:image/png;base64,AAAA"]) == "verified"
    assert sdk.direct_calls == 0
    assert sdk.options == []


def test_bounded_visual_request_from_running_event_loop_uses_isolated_loop():
    import asyncio
    from unittest.mock import patch

    from ppap_excel_audit_engine.checkers import client as client_module

    closed = []

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            closed.append(True)

        class responses:
            @staticmethod
            async def create(**kwargs):
                return SimpleNamespace(status="completed", output_text="verified")

    async def inside_event_loop():
        return client_module._run_bounded_visual_sync(
            SimpleNamespace(OPENAI_API_KEY="unit-test-token", OPENAI_BASE_URL="https://example.invalid/v1"),
            {"model": "unit-test"},
            1,
        )

    with patch.object(client_module, "AsyncOpenAI", return_value=FakeAsyncClient()):
        response = asyncio.run(inside_event_loop())
    assert response.output_text == "verified"
    assert closed == [True]
