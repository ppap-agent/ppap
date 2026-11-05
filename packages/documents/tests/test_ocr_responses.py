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
        self.assertEqual(calls[0]["max_output_tokens"], 4096)
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

    def test_request_timeout_is_twenty_seconds_and_never_retries(self):
        import httpx
        from openai import APITimeoutError

        calls = []

        async def timeout(**kwargs):
            calls.append(kwargs)
            raise APITimeoutError(request=httpx.Request("POST", "https://ocr.example.test/v1/responses"))

        sdk = SimpleNamespace(responses=SimpleNamespace(create=timeout), close=AsyncMock())
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
        ):
            with self.assertRaises(llm_service.ResponsesEvidenceError) as error:
                asyncio.run(llm_service.call_llm_async(b"image", "inspect", response_schema=SCHEMA))
        self.assertEqual(error.exception.code, "responses_timeout")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["timeout"], 20)
        sdk.close.assert_awaited_once()

    def test_user_extra_body_and_token_limit_are_preserved(self):
        sdk = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(status="completed", output_text='{"has_checkboxes":true}'))), close=AsyncMock())
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
            patch.object(llm_service, "ocr_extra_body", return_value={"reasoning": True}),
        ):
            asyncio.run(llm_service.call_llm_async(b"image", "inspect", response_schema=SCHEMA, max_output_tokens=4096))
        kwargs = sdk.responses.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"], {"reasoning": True})
        self.assertEqual(kwargs["max_output_tokens"], 4096)

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


class OCRUsageTests(TestCase):
    def test_receipt_is_counted_before_invalid_json_is_rejected(self):
        receipts = []
        sdk = SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        status="completed",
                        output_text="private-invalid-json",
                        model="actual-ocr-model",
                        usage=SimpleNamespace(input_tokens=12, output_tokens=7, output_tokens_details=SimpleNamespace(reasoning_tokens=3), cost=0.004),
                    )
                )
            ),
            close=AsyncMock(),
        )
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="requested-model"),
            llm_service.ocr_usage_scope(receipts.append),
        ):
            with self.assertRaises(llm_service.ResponsesEvidenceError):
                asyncio.run(llm_service.call_llm_async(b"image", "inspect", response_schema=SCHEMA))
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        self.assertEqual((receipt["model"], receipt["input_tokens"], receipt["output_tokens"], receipt["reasoning_tokens"], receipt["cost_usd"]), ("actual-ocr-model", 12, 7, 3, 0.004))
        self.assertGreaterEqual(receipt["elapsed_seconds"], 0)
        self.assertNotIn("private-invalid-json", str(receipts))

    def test_transport_failure_has_one_unknown_receipt_without_invented_zero_cost(self):
        receipts = []
        sdk = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=ConnectionError("controlled"))), close=AsyncMock())
        with patch.object(llm_service, "_get_llm_client", return_value=sdk), patch.object(llm_service, "_get_llm_model_name", return_value="test"), llm_service.ocr_usage_scope(receipts.append):
            with self.assertRaises(ConnectionError):
                asyncio.run(llm_service.call_llm_async(b"image", "inspect", response_schema=SCHEMA))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["model"], "test")
        self.assertNotIn("input_tokens", receipts[0])
        self.assertNotIn("output_tokens", receipts[0])
        self.assertNotIn("cost_usd", receipts[0])
