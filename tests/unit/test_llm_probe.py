"""Tests for the LLM dependency probe. They do not call a real provider."""
import unittest

from tools.llm_probe import (
    DEV_DEFAULT_BASE_URL,
    DEV_DEFAULT_MODEL,
    NoRedirect,
    NotProven,
    load_config,
    responses_url,
    validate_response,
)


class ConfigTests(unittest.TestCase):
    def test_only_api_key_is_required_for_isolated_dev(self):
        key, base_url, model = load_config({"LLM_API_KEY": "secret"})
        self.assertEqual(key, "secret")
        self.assertEqual(base_url, DEV_DEFAULT_BASE_URL)
        self.assertEqual(model, DEV_DEFAULT_MODEL)

    def test_environment_overrides_defaults(self):
        _, base_url, model = load_config(
            {
                "LLM_API_KEY": "secret",
                "LLM_BASE_URL": "https://example.test/custom/v1/",
                "LLM_MODEL": "model-x ",
            }
        )
        self.assertEqual(base_url, "https://example.test/custom/v1/")
        self.assertEqual(model, "model-x")

    def test_missing_key_is_not_ready(self):
        with self.assertRaisesRegex(NotProven, "missing_configuration:LLM_API_KEY"):
            load_config({})

    def test_responses_path_is_derived_once(self):
        self.assertEqual(
            responses_url("https://api.example.test/v1/"),
            "https://api.example.test/v1/responses",
        )

    def test_base_url_cannot_embed_credentials(self):
        with self.assertRaisesRegex(NotProven, "invalid_base_url"):
            responses_url("https://user:pass@example.test/v1")

    def test_base_url_cannot_be_final_responses_endpoint(self):
        with self.assertRaisesRegex(NotProven, "base_url_must_be_api_root"):
            responses_url("https://api.example.test/v1/responses")

    def test_redirects_are_rejected(self):
        handler = NoRedirect()
        with self.assertRaisesRegex(NotProven, "redirect_not_allowed"):
            handler.redirect_request(None, None, 302, "redirect", {}, "https://other.test/v1")


class ResponseTests(unittest.TestCase):
    def test_valid_responses_envelope(self):
        evidence = validate_response(
            {
                "id": "resp_test",
                "object": "response",
                "status": "completed",
                "model": "gpt-5.6-luna",
                "output": [],
            }
        )
        self.assertTrue(evidence["response_identity_present"])
        self.assertTrue(evidence["response_completed"])
        self.assertEqual(evidence["observed_model"], "gpt-5.6-luna")

    def test_chat_completions_is_not_accepted(self):
        with self.assertRaisesRegex(NotProven, "not_responses_protocol"):
            validate_response(
                {
                    "id": "chatcmpl_test",
                    "object": "chat.completion",
                    "choices": [],
                }
            )

    def test_incomplete_response_is_not_ready(self):
        with self.assertRaisesRegex(NotProven, "response_not_completed"):
            validate_response(
                {
                    "id": "resp_test",
                    "object": "response",
                    "status": "in_progress",
                    "output": [],
                }
            )

    def test_output_is_required(self):
        with self.assertRaisesRegex(NotProven, "missing_response_output"):
            validate_response(
                {
                    "id": "resp_test",
                    "object": "response",
                    "status": "completed",
                }
            )


if __name__ == "__main__":
    unittest.main()
