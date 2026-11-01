"""OCR vision requests use the Responses API contract."""

import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from documents.ocr import client as llm_service

SCHEMA = {"name": "evidence", "strict": True, "schema": {"type": "object", "properties": {"has_checkboxes": {"type": "boolean"}}, "required": ["has_checkboxes"], "additionalProperties": False}}


class OCRResponsesTests(TestCase):
    def test_image_and_schema_request(self):
        calls = []

        async def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status="completed", output_text='{"has_checkboxes":true}')

        sdk = SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock())
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
        ):
            self.assertEqual(
                asyncio.run(llm_service.call_llm_async(b"\x89PNG", "inspect", response_schema=SCHEMA)),
                {"has_checkboxes": True},
            )

        self.assertEqual(calls[0]["text"], {"format": {"type": "json_schema", **SCHEMA}})
        self.assertEqual(
            calls[0]["input"],
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "inspect"},
                        {"type": "input_image", "image_url": "data:image/png;base64,iVBORw==", "detail": "high"},
                    ],
                }
            ],
        )
        self.assertFalse(calls[0]["store"])
        self.assertEqual(calls[0]["max_output_tokens"], 16384)
        sdk.close.assert_awaited_once()

    def test_incomplete_response_is_not_accepted(self):
        calls = []

        async def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status="incomplete", output_text="partial")

        sdk = SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock())
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
        ):
            with self.assertRaises(llm_service.ResponsesEvidenceError) as error:
                asyncio.run(llm_service.call_llm_async(b"\x89PNG", "inspect", response_schema=SCHEMA))
        self.assertEqual(error.exception.code, "responses_incomplete")
        self.assertEqual(len(calls), 1)
        sdk.close.assert_awaited_once()

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

        sdk = SimpleNamespace(responses=SimpleNamespace(create=slow_create), close=AsyncMock())
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
            patch.object(llm_service, "RESPONSE_WALL_TIMEOUT_SECONDS", 0.02),
        ):
            with self.assertRaises(llm_service.ResponsesEvidenceError) as error:
                asyncio.run(llm_service.call_llm_async(b"image", "inspect", response_schema=SCHEMA))

        self.assertEqual(error.exception.code, "responses_timeout")
        self.assertEqual(len(calls), 1)
        self.assertEqual(cancelled, [True])
        sdk.close.assert_awaited_once()

    def test_invalid_json_returns_typed_error_without_retried_or_logged_evidence(self):
        private = "secret-document-evidence"
        sdk = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(status="completed", output_text='{"text":"' + private))), close=AsyncMock())
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
            self.assertLogs("documents.ocr.client", level="WARNING") as logs,
        ):
            with self.assertRaises(llm_service.ResponsesEvidenceError) as error:
                asyncio.run(llm_service.call_llm_async(b"image", "inspect", response_schema=SCHEMA))
        self.assertEqual(error.exception.code, "responses_invalid_json")
        self.assertNotIn(private, str(error.exception))
        self.assertNotIn(private, "\n".join(logs.output))
        self.assertEqual(sdk.responses.create.await_count, 1)

    def test_non_object_json_fails_closed(self):
        sdk = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(status="completed", output_text="[1, 2]"))), close=AsyncMock())
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
        ):
            with self.assertRaises(llm_service.ResponsesEvidenceError) as error:
                asyncio.run(llm_service.call_llm_async(b"image", "inspect", response_schema=SCHEMA))
        self.assertEqual(error.exception.code, "responses_non_object")

    def test_repeated_cell_key_fails_before_json_can_overwrite_evidence(self):
        calls = []
        private = "private-cell-evidence"

        async def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status="completed", output_text='{"cells":{"7":"' + private + '","7":"replacement"}}')

        sdk = SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock())
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
            self.assertLogs("documents.ocr.client", level="WARNING") as logs,
        ):
            with self.assertRaises(llm_service.ResponsesEvidenceError) as error:
                asyncio.run(llm_service.call_llm_async(b"image", "inspect", response_schema=SCHEMA))
        self.assertEqual(error.exception.code, "responses_duplicate_key")
        self.assertEqual(len(calls), 1)
        self.assertNotIn(private, str(error.exception))
        self.assertNotIn(private, "\n".join(logs.output))
