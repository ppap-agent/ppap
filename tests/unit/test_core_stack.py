"""Settings and HTTP surface tests. They do not prove Live Stories."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tests import pathsetup

ROOT = pathsetup.ROOT

from pydantic import ValidationError

from ppap_core.capabilities import report
from ppap_core.llm import llm_configured, responses_url
from dotenv import dotenv_values

from ppap_core.settings import ISOLATED_DEV_LLM_BASE_URL, ISOLATED_DEV_LLM_MODEL, ISOLATED_DEV_SECRET, Settings
from ppap_api import create_app


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
        self.assertEqual(settings.llm_base_url, ISOLATED_DEV_LLM_BASE_URL)
        self.assertEqual(settings.llm_model, ISOLATED_DEV_LLM_MODEL)
        self.assertFalse(llm_configured(settings))
        self.assertFalse(hasattr(settings, "LEGACY_DOC_CONVERSION_ENABLED"))
        self.assertIn(".doc", settings.get_allowed_extensions())

    def test_env_dev_records_settings_defaults_without_conversion_flag(self):
        path = ROOT / ".env.dev"
        values = dotenv_values(path)
        self.assertTrue("LEGACY_DOC_CONVERSION_ENABLED" not in values, "Development template must not contain the conversion flag")
        self.assertEqual(values["PPAP_PROFILE"], "isolated-dev")
        self.assertEqual(values["PPAP_PG_PORT"], "5432")
        self.assertEqual(values["PPAP_PG_DATABASE"], "ppap")
        self.assertEqual(values["PPAP_PG_API_USER"], "ppap_api")
        self.assertTrue(values["PPAP_PG_API_PASSWORD"] == ISOLATED_DEV_SECRET, "Development template must use the isolated-dev database password")
        self.assertEqual(values["PPAP_S3_ENDPOINT"], "http://127.0.0.1:9000")
        self.assertEqual(values["PPAP_S3_REGION"], "us-east-1")
        self.assertEqual(values["PPAP_S3_BUCKET"], "ppap-objects")
        self.assertEqual(values["FEISHU_FREE_TRIAL_QUOTA_POINTS"], "10000")
        self.assertEqual(values["LOG_LEVEL"], "INFO")
        self.assertEqual(values["OCR_ADAPTER_MODE"], "auto")
        self.assertTrue(values["LLM_API_KEY"] == "", "Development template LLM_API_KEY must be empty")
        self.assertTrue(values["FEISHU_APP_ID"] == "", "Development template FEISHU_APP_ID must be empty")

    def test_env_production_records_safe_defaults_without_isolated_dev_secrets(self):
        path = ROOT / ".env.production"
        values = dotenv_values(path)
        self.assertTrue("LEGACY_DOC_CONVERSION_ENABLED" not in values, "Production template must not contain the conversion flag")
        self.assertTrue(ISOLATED_DEV_SECRET not in set(values.values()), "Production template must not contain the isolated-dev secret")
        self.assertEqual(values["PPAP_PROFILE"], "production")
        self.assertEqual(values["PPAP_PG_PORT"], "5432")
        self.assertEqual(values["PPAP_PG_DATABASE"], "ppap")
        self.assertEqual(values["PPAP_PG_API_USER"], "ppap_api")
        self.assertTrue(values["PPAP_PG_API_PASSWORD"] == "", "Production template PPAP_PG_API_PASSWORD must be empty")
        self.assertEqual(values["PPAP_PG_HOST"], "")
        self.assertEqual(values["PPAP_S3_ENDPOINT"], "")
        self.assertTrue(values["PPAP_S3_ACCESS_KEY"] == "", "Production template PPAP_S3_ACCESS_KEY must be empty")
        self.assertEqual(values["PPAP_S3_REGION"], "us-east-1")
        self.assertEqual(values["PPAP_S3_BUCKET"], "ppap-objects")
        self.assertEqual(values["PPAP_WEB_URL"], "")
        self.assertEqual(values["PPAP_PUBLIC_API_URL"], "")
        self.assertEqual(values["LLM_BASE_URL"], "")
        self.assertEqual(values["LLM_MODEL"], "")
        self.assertEqual(values["FEISHU_FREE_TRIAL_QUOTA_POINTS"], "10000")
        self.assertEqual(values["LOG_LEVEL"], "INFO")
        self.assertEqual(values["OCR_ADAPTER_MODE"], "auto")
        self.assertTrue(values["FEISHU_APP_ID"] == "", "Production template FEISHU_APP_ID must be empty")

    def test_isolated_dev_with_only_api_key_uses_repository_defaults(self):
        with _clean_env(LLM_API_KEY="sk-test"):
            settings = Settings()
        self.assertEqual(settings.llm_api_key, "sk-test")
        self.assertEqual(settings.llm_base_url, ISOLATED_DEV_LLM_BASE_URL)
        self.assertEqual(settings.llm_model, ISOLATED_DEV_LLM_MODEL)
        self.assertTrue(llm_configured(settings))

    def test_openai_names_are_not_application_authority(self):
        with _clean_env(OPENAI_API_KEY="from-openai", OPENAI_BASE_URL="https://example.invalid/v1", MODEL_NAME="hidden"):
            settings = Settings()
        self.assertEqual(settings.llm_api_key, "")
        self.assertEqual(settings.llm_base_url, ISOLATED_DEV_LLM_BASE_URL)
        self.assertEqual(settings.llm_model, ISOLATED_DEV_LLM_MODEL)
        self.assertNotEqual(settings.llm_base_url, "https://example.invalid/v1")
        self.assertNotEqual(settings.llm_model, "hidden")
        self.assertFalse(llm_configured(settings))

    def test_production_rejects_isolated_dev_credentials(self):
        with _clean_env(PPAP_PROFILE="production"):
            with self.assertRaises(ValidationError):
                Settings()

    def test_production_api_does_not_need_bootstrap_or_peer_db_secrets(self):
        with _clean_env(
            PPAP_PROFILE="production",
            PPAP_SERVICE="api",
            PPAP_PG_API_PASSWORD="prod-api",
            PPAP_S3_ACCESS_KEY="prod-access",
            PPAP_S3_SECRET_KEY="prod-secret",
            PPAP_S3_ENDPOINT="https://objects.example.internal",
            LLM_BASE_URL="https://llm.example",
            LLM_MODEL="x",
            PPAP_WEB_URL="https://app.example",
        ):
            settings = Settings()
        self.assertEqual(settings.pg_api_password, "prod-api")
        self.assertEqual(settings.pg_worker_password, ISOLATED_DEV_SECRET)
        self.assertEqual(settings.pg_migrator_password, ISOLATED_DEV_SECRET)
        self.assertEqual(settings.pg_bootstrap_password, ISOLATED_DEV_SECRET)

    def test_production_worker_does_not_need_bootstrap_or_peer_db_secrets(self):
        with _clean_env(
            PPAP_PROFILE="production",
            PPAP_SERVICE="worker",
            PPAP_PG_WORKER_PASSWORD="prod-worker",
            PPAP_S3_ACCESS_KEY="prod-access",
            PPAP_S3_SECRET_KEY="prod-secret",
            PPAP_S3_ENDPOINT="https://objects.example.internal",
            LLM_BASE_URL="https://llm.example",
            LLM_MODEL="x",
            PPAP_WEB_URL="https://app.example",
        ):
            settings = Settings()
        self.assertEqual(settings.pg_worker_password, "prod-worker")
        self.assertEqual(settings.pg_api_password, ISOLATED_DEV_SECRET)
        self.assertEqual(settings.pg_migrator_password, ISOLATED_DEV_SECRET)
        self.assertEqual(settings.pg_bootstrap_password, ISOLATED_DEV_SECRET)

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
            PPAP_WEB_URL="https://app.example",
        ):
            with self.assertRaises(ValidationError):
                Settings()

    def test_production_requires_explicit_web_url(self):
        with _clean_env(
            PPAP_PROFILE="production",
            PPAP_PG_API_PASSWORD="prod-api",
            PPAP_PG_WORKER_PASSWORD="prod-worker",
            PPAP_PG_MIGRATOR_PASSWORD="prod-migrator",
            PPAP_PG_BOOTSTRAP_PASSWORD="prod-boot",
            PPAP_S3_ACCESS_KEY="prod-access",
            PPAP_S3_SECRET_KEY="prod-secret",
            PPAP_S3_ENDPOINT="https://objects.example.internal",
            LLM_BASE_URL="https://llm.example",
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
        self.assertEqual(set(body), {"git_sha", "profile", "api_url", "login"})
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

    def test_configured_capabilities_do_not_claim_readiness_proof(self):
        with _clean_env(
            LLM_API_KEY="sk-test",
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            PPAP_PARTNER_AI_ENDPOINT="https://example.invalid",
        ):
            settings = Settings()
        caps = report(settings)
        self.assertEqual(caps["llm"]["state"], "configured")
        self.assertEqual(caps["feishu"]["state"], "configured")
        self.assertEqual(caps["partner_ai"]["state"], "configured")
        self.assertEqual(caps["legacy_engine"]["state"], "disabled")
        for body in caps.values():
            self.assertEqual(set(body), {"state"})
            self.assertNotIn("ready_proven_by", body)
