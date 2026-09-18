"""Settings and HTTP surface tests. They do not prove Live Stories."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages"), str(ROOT / "services" / "api"), str(ROOT / "services" / "worker")]

from pydantic import ValidationError

from ppap_core.capabilities import report
from ppap_core.llm import llm_configured, responses_url
from ppap_core.settings import ISOLATED_DEV_SECRET, Settings
from ppap_api import create_app
from tools.stack import map_llm_names


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class SettingsTests(unittest.TestCase):
    def test_isolated_dev_defaults_do_not_require_extra_secrets(self):
        with _clean_env():
            settings = Settings()
        self.assertEqual(settings.profile, "isolated-dev")
        self.assertEqual(settings.pg_api_password, ISOLATED_DEV_SECRET)
        self.assertEqual(settings.s3_endpoint, "http://127.0.0.1:9000")
        self.assertEqual(settings.llm_api_key, "")

    def test_openai_names_are_not_application_authority(self):
        with _clean_env(OPENAI_API_KEY="from-openai", OPENAI_BASE_URL="https://example.invalid/v1", MODEL_NAME="hidden"):
            settings = Settings()
        self.assertEqual(settings.llm_api_key, "")
        self.assertEqual(settings.llm_base_url, "")
        self.assertEqual(settings.llm_model, "")

    def test_environment_side_mapping_fills_llm_names(self):
        mapped = map_llm_names({
            "OPENAI_API_KEY": "from-openai",
            "OPENAI_BASE_URL": "https://example.invalid/v1",
            "MODEL_NAME": "hidden",
        })
        self.assertEqual(mapped["LLM_API_KEY"], "from-openai")
        self.assertEqual(mapped["LLM_BASE_URL"], "https://example.invalid/v1")
        self.assertEqual(mapped["LLM_MODEL"], "hidden")

    def test_production_rejects_isolated_dev_credentials(self):
        with _clean_env(PPAP_PROFILE="production"):
            with self.assertRaises(ValidationError):
                Settings()

    def test_production_requires_explicit_llm_endpoint(self):
        with _clean_env(
            PPAP_PROFILE="production",
            PPAP_PG_API_PASSWORD="prod-api",
            PPAP_PG_WORKER_PASSWORD="prod-worker",
            PPAP_PG_MIGRATOR_PASSWORD="prod-migrator",
            PPAP_PG_BOOTSTRAP_PASSWORD="prod-boot",
            PPAP_S3_ACCESS_KEY="prod-access",
            PPAP_S3_SECRET_KEY="prod-secret",
            PPAP_S3_ENDPOINT="https://objects.example.internal",
            LLM_BASE_URL="",
            LLM_MODEL="x",
        ):
            with self.assertRaises(ValidationError):
                Settings()

    def test_responses_url_does_not_invent_protocol(self):
        self.assertEqual(responses_url("https://example.invalid/v1/"), "https://example.invalid/v1/responses")


class ApiSurfaceTests(unittest.TestCase):
    def setUp(self):
        self._env = _clean_env(PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        TestClient = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient
        self.client = TestClient(
            create_app(
                self.settings,
                postgres_probe=lambda _s: {"state": "ok"},
                object_store_probe=lambda _s: {"state": "ok"},
            )
        )

    def tearDown(self):
        self._env.stop()

    def test_health_is_process_alive(self):
        body = self.client.get("/health").json()
        self.assertEqual(body, {"status": "alive"})

    def test_ready_uses_core_dependency_probes(self):
        body = self.client.get("/ready").json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["git_sha"], "a" * 40)

    def test_ready_fails_without_object_store(self):
        TestClient = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient
        client = TestClient(
            create_app(
                self.settings,
                postgres_probe=lambda _s: {"state": "ok"},
                object_store_probe=lambda _s: {"state": "unavailable"},
            )
        )
        response = client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")

    def test_public_config_has_no_secrets(self):
        body = self.client.get("/public-config").json()
        self.assertEqual(set(body), {"git_sha", "profile", "api_url"})
        blob = " ".join(body.values()).lower()
        self.assertNotIn("ppap_isolated_dev", blob)

    def test_missing_optional_integrations_do_not_block_core(self):
        caps = self.client.get("/capabilities").json()
        self.assertEqual(caps["feishu"]["state"], "disabled")
        self.assertEqual(caps["partner_ai"]["state"], "disabled")
        self.assertEqual(caps["legacy_engine"]["state"], "disabled")
        self.assertEqual(caps["llm"]["state"], "disabled")
        self.assertFalse(llm_configured(self.settings))
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_feishu_present_is_configured_not_proven(self):
        with _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret"):
            settings = Settings()
        self.assertEqual(report(settings)["feishu"]["state"], "configured")
        self.assertEqual(report(settings)["feishu"]["ready_proven_by"], "issue_4")
