"""OCR vision requests use the Responses API contract."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import tests.pathsetup  # noqa: F401

from ppap_ocr_adapter.doc_cache import llm_service


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
            self.assertEqual(llm_service.call_llm("image.jpg", "inspect", json_mode=True, response_schema=schema), {"has_checkboxes": True})

        self.assertEqual(calls[0]["text"], {"format": {"type": "json_schema", **schema}})
        self.assertEqual(calls[0]["input"], [{"role": "user", "content": [
            {"type": "input_text", "text": "inspect"},
            {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"},
        ]}])
        self.assertFalse(calls[0]["store"])

    def test_incomplete_response_is_not_accepted(self):
        sdk = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(status="incomplete", output_text="partial")))
        with (
            patch.object(llm_service, "_get_llm_client", return_value=sdk),
            patch.object(llm_service, "_get_llm_model_name", return_value="test"),
            patch.object(llm_service.os.path, "exists", return_value=False),
        ):
            with self.assertRaises(RuntimeError):
                llm_service.call_llm("missing.jpg", "inspect", retry_count=1)
