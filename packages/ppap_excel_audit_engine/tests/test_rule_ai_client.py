"""Bounded rule calls use the real adapter with an external SDK stub."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch



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
        return self.response


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
        sdk = SDK(SimpleNamespace(
            status="completed",
            output_text='{"ok": true}',
            usage=SimpleNamespace(input_tokens=9, output_tokens=5, output_tokens_details=SimpleNamespace(reasoning_tokens=2)),
        ))
        client = self.client(sdk)
        with patch("ppap_excel_audit_engine.checkers.client.record_token_usage") as record_usage:
            self.assertEqual(client.image_analysis_completion("system", "inspect", ["data:image/png;base64,AAAA"], json_format=True), '{"ok": true}')
        record_usage.assert_called_once_with(
            input_tokens=9,
            output_tokens=5,
            thinking_tokens=2,
            model_name="test",
            operation="image_analysis",
        )
        request = sdk.calls[0]
        self.assertEqual(request["text"], {"format": {"type": "json_object"}})
        self.assertEqual(request["input"][1]["content"], [
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "high"},
            {"type": "input_text", "text": "inspect"},
        ])

    def test_reasoning_tokens_are_part_of_output_total(self):
        from ppap_core.token_tracker import TokenTracker

        tracker = TokenTracker()
        request_id = tracker.start_request_tracking()
        tracker.record_token_usage(input_tokens=9, output_tokens=5, thinking_tokens=2)
        self.assertEqual(tracker.end_request_tracking(request_id).total_tokens, 14)


if __name__ == "__main__":
    unittest.main()
