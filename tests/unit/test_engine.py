"""Engine contracts and in-repo checkers. Not corpus acceptance and not S01."""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages"), str(ROOT / "services" / "api"), str(ROOT / "services" / "worker")]

from openpyxl import Workbook

from ppap_core.audit_run import findings_from_bytes
from ppap_core.corpus import corpus_status
from ppap_core.files import sha256_bytes
from ppap_core.llm import chat_messages_to_responses, responses_url
from ppap_core.rule_profile import RULE_PROFILE_V2, RULE_PROFILE_V3, normalize_rule_profile
from ppap_core.settings import Settings, get_settings
from ppap_docops.excel.loader import ExcelLoadError, load_excel_workbook
from ppap_excel_audit_engine.checkers.client import AIClient


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class RuleProfileTests(unittest.TestCase):
    def test_normalize_known_profiles(self):
        self.assertEqual(normalize_rule_profile(None), RULE_PROFILE_V2)
        self.assertEqual(normalize_rule_profile("v3"), RULE_PROFILE_V3)

    def test_unknown_profile_fails(self):
        with self.assertRaises(ValueError):
            normalize_rule_profile("legacy")


class SettingsProjectionTests(unittest.TestCase):
    def test_openai_env_does_not_fill_product_or_engine_view(self):
        with _clean_env(OPENAI_API_KEY="from-openai", MODEL_NAME="hidden", LLM_API_KEY="from-llm", LLM_BASE_URL="https://example.invalid/v1", LLM_MODEL="ppap-model"):
            get_settings.cache_clear()
            product = Settings()
            view = get_settings()
        self.assertEqual(product.llm_api_key, "from-llm")
        self.assertEqual(view.OPENAI_API_KEY, "from-llm")
        self.assertEqual(view.MODEL_NAME, "ppap-model")
        self.assertEqual(view.get_ocr_openai_base_url(), "https://example.invalid/v1")
        self.assertNotIn("cloudflare", view.get_ocr_openai_base_url())
        get_settings.cache_clear()


class CorpusTests(unittest.TestCase):
    def test_missing_corpus_is_not_proven(self):
        with _clean_env():
            status = corpus_status()
        self.assertEqual(status["state"], "NOT_PROVEN")
        self.assertEqual(status["reason"], "authorized_corpus_not_configured")


class ResponsesClientTests(unittest.TestCase):
    def test_client_does_not_call_chat_completions(self):
        source = inspect.getsource(AIClient.chat_completion)
        self.assertNotIn("chat.completions", source)
        self.assertIn("responses_complete", source)

    def test_image_messages_become_responses_input(self):
        instructions, items = chat_messages_to_responses(
            [
                {"role": "system", "content": "sys"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xx"}},
                    ],
                },
            ]
        )
        self.assertEqual(instructions, "sys")
        self.assertEqual(items[0]["content"][1]["type"], "input_image")
        self.assertEqual(responses_url("https://example.invalid/v1/"), "https://example.invalid/v1/responses")


class LocalCheckerTests(unittest.TestCase):
    def test_remote_workbook_url_is_rejected(self):
        with self.assertRaises(ExcelLoadError):
            load_excel_workbook("https://example.invalid/file.xlsx")

    def test_unknown_document_type_is_err_not_pass(self):
        body, path = _xlsx_bytes()
        digest = sha256_bytes(body)
        findings = findings_from_bytes(
            body=body,
            filename="sample.xlsx",
            sha256=digest,
            file_version_id="00000000-0000-0000-0000-000000000001",
            product_rule_version_id="test-rule-v1",
            engine_rule_id="missing",
            document_type="不是已登记的资料类型",
            rule_profile="v2",
        )
        self.assertTrue(findings)
        self.assertTrue(all(item["status"] in {"PASS", "NG", "ERR"} for item in findings))
        self.assertTrue(any(item["status"] == "ERR" for item in findings))
        self.assertFalse(all(item["status"] == "PASS" for item in findings))

    def test_sha_mismatch_refuses_to_run(self):
        body, _path = _xlsx_bytes()
        with self.assertRaises(ValueError):
            findings_from_bytes(
                body=body,
                filename="sample.xlsx",
                sha256="0" * 64,
                file_version_id="00000000-0000-0000-0000-000000000001",
                product_rule_version_id="test-rule-v1",
                engine_rule_id="零件提交保证书",
                document_type="零件提交保证书",
                rule_profile="v2",
            )

    def test_psw_empty_workbook_does_not_default_pass(self):
        body, _path = _xlsx_bytes()
        digest = sha256_bytes(body)
        findings = findings_from_bytes(
            body=body,
            filename="PSW.xlsx",
            sha256=digest,
            file_version_id="00000000-0000-0000-0000-000000000002",
            product_rule_version_id="test-psw-v1",
            engine_rule_id="零件提交保证书",
            document_type="零件提交保证书",
            rule_profile="v2",
        )
        self.assertGreaterEqual(len(findings), 1)
        self.assertTrue(all(item["status"] in {"PASS", "NG", "ERR"} for item in findings))
        self.assertTrue(any(item["status"] in {"NG", "ERR"} for item in findings))


def _xlsx_bytes() -> tuple[bytes, str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample.xlsx"
        Workbook().save(path)
        return path.read_bytes(), str(path)


if __name__ == "__main__":
    unittest.main()
