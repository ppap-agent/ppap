"""Bounded rule calls use the real adapter with an external SDK stub."""
from types import SimpleNamespace
import unittest

import tests.pathsetup  # noqa: F401


class SDK:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.options = []
        self.direct_calls = 0
        self.chat = SimpleNamespace(completions=self)

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
        sdk = SDK(SimpleNamespace(choices=[]))
        client = self.client(sdk)
        with self.assertLogs("ppap_excel_audit_engine.checkers.client", level="ERROR"):
            with self.assertRaises(RuntimeError):
                client.structured_completion("system", "source", request_timeout=120, retry_limit=0)
        self.assertEqual(len(sdk.calls), 1)
        self.assertEqual(sdk.direct_calls, 0)
        self.assertEqual(sdk.options, [{"max_retries": 0}])

        self.assertEqual(sdk.calls[0]["timeout"], 120)
        self.assertIs(client.client, sdk)
        sdk.response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
        self.assertEqual(client.structured_completion("system", "source"), "ok")
        self.assertEqual(sdk.calls[-1]["timeout"], 900)
        self.assertEqual(sdk.options, [{"max_retries": 0}])

    def test_default_empty_response_retry_count_is_unchanged(self):
        sdk = SDK(SimpleNamespace(choices=[]))
        with self.assertLogs("ppap_excel_audit_engine.checkers.client", level="WARNING"):
            with self.assertRaises(RuntimeError):
                self.client(sdk).structured_completion("system", "source")
        self.assertEqual(len(sdk.calls), 3)
        self.assertEqual(sdk.options, [])

    def test_provider_and_invalid_response_details_never_enter_client_logs(self):
        marker = "private-provider-source-marker"
        for sdk in (SDK(error=RuntimeError(marker)), SDK(SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, refusal=marker))]))):
            with self.subTest(error=bool(sdk.error)):
                client = self.client(sdk)
                with self.assertLogs("ppap_excel_audit_engine.checkers.client", level="ERROR") as logs:
                    with self.assertRaises(RuntimeError):
                        client.structured_completion("system", marker, request_timeout=120, retry_limit=0)
                self.assertNotIn(marker, "\n".join(logs.output))
                self.assertEqual(len(sdk.calls), 1)


if __name__ == "__main__":
    unittest.main()
