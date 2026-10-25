"""OCR vision requests use the Responses API contract."""

import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from ppap_pdf_to_excel.vision import client as llm_service


class OCRResponsesTests(TestCase):
    def test_image_and_schema_request(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status="completed", output_text='{"has_checkboxes":true}')

        sdk = SimpleNamespace(responses=SimpleNamespace(create=create))
        schema = {"name": "tagging", "strict": True, "schema": {"type": "object"}}
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
            patch.object(llm_service.os.path, "exists", return_value=True),
            patch.object(llm_service, "encode_image", return_value="AAAA"),
        ):
            self.assertEqual(
                llm_service.call_llm("image.jpg", "inspect", json_mode=True, response_schema=schema),
                {"has_checkboxes": True},
            )

        self.assertEqual(calls[0]["text"], {"format": {"type": "json_schema", **schema}})
        self.assertEqual(
            calls[0]["input"],
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "inspect"},
                        {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA", "detail": "high"},
                    ],
                }
            ],
        )
        self.assertFalse(calls[0]["store"])
        self.assertEqual(calls[0]["max_output_tokens"], 16384)

    def test_incomplete_response_is_not_accepted(self):
        def create(**kwargs):
            return SimpleNamespace(status="incomplete", output_text="partial")

        sdk = SimpleNamespace(responses=SimpleNamespace(create=create))
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
        ):
            with self.assertRaises(RuntimeError):
                llm_service.call_llm(b"\x89PNG", "inspect", retry_count=1)

    def test_wall_deadline_cancels_slow_responses_body_without_retry(self):
        calls = []
        cancelled = []

        async def slow_create(**kwargs):
            calls.append(kwargs)
            try:
                # Data or keep-alives can arrive frequently enough that a
                # per-read socket timeout would never bound the full response.
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        sdk = SimpleNamespace(responses=SimpleNamespace(create=slow_create))
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
            patch.object(llm_service, "RESPONSE_WALL_TIMEOUT_SECONDS", 0.02),
        ):
            with self.assertRaises(TimeoutError):
                llm_service.call_llm(b"image", "inspect", retry_count=1)

        self.assertEqual(len(calls), 1)
        self.assertEqual(cancelled, [True])

    def test_invalid_json_returns_typed_error_without_retried_or_logged_evidence(self):
        private = "secret-document-evidence"
        sdk = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(status="completed", output_text='{"text":"' + private)))
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
            self.assertLogs("ppap_pdf_to_excel.vision.client", level="WARNING") as logs,
        ):
            with self.assertRaises(llm_service.ResponsesEvidenceError) as error:
                llm_service.call_llm(b"image", "inspect", retry_count=1, json_mode=True)
        self.assertEqual(error.exception.code, "responses_invalid_json")
        self.assertNotIn(private, str(error.exception))
        self.assertNotIn(private, "\n".join(logs.output))

    def test_non_object_json_fails_closed(self):
        sdk = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(status="completed", output_text="[1, 2]")))
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
        ):
            with self.assertRaises(llm_service.ResponsesEvidenceError) as error:
                llm_service.call_llm(b"image", "inspect", retry_count=1, json_mode=True)
        self.assertEqual(error.exception.code, "responses_non_object")
