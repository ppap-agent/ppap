"""Partner AI quota check and usage observations. Mocked HTTP only."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages"), str(ROOT / "services" / "api"), str(ROOT / "services" / "worker")]

from fastapi.testclient import TestClient

from ppap_core.feishu import UserTokens
from ppap_core.feishu_usage import quota_sufficient, remaining_trial_points
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings
from ppap_api import create_app
from tests.unit.memory_auth import MemoryAuthStore


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class QuotaSemanticsTests(unittest.TestCase):
    def test_quota_sufficient_requires_enabled_and_positive_user_quota(self):
        self.assertTrue(quota_sufficient(True, 10))
        self.assertFalse(quota_sufficient(False, 10))
        self.assertFalse(quota_sufficient(True, 0))
        self.assertTrue(quota_sufficient(True, 10))

    def test_quota_sufficient_does_not_require_package_purchase(self):
        self.assertTrue(quota_sufficient(True, 5))

    def test_remaining_trial_never_goes_negative(self):
        self.assertEqual(remaining_trial_points(10000, 0), 10000)
        self.assertEqual(remaining_trial_points(10000, 4000), 6000)
        self.assertEqual(remaining_trial_points(10000, 20000), 0)


class _PartnerMock:
    def __init__(self):
        self.item = {"enabled": True, "user_quota": 80, "ai_package_purchased": True}
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.headers.get("authorization") != "Bearer user-access":
            return httpx.Response(401, json={"code": 99991663, "msg": "unauthorized"})
        if request.method.upper() == "GET" and "/partner_ai/v1/tenant_ai_items/" in url:
            if self.fail:
                return httpx.Response(500, json={"code": 1, "msg": "down"})
            item_id = url.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={"code": 0, "data": {"tenant_ai_item": {"id": item_id, **self.item}}},
            )
        return httpx.Response(404, json={"code": 1, "msg": "unmocked"})


class FeishuUsageTests(unittest.TestCase):
    def setUp(self):
        self._env = _clean_env(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_AI_ITEM_ID="ai-item-1",
            PPAP_BUILD_SHA="a" * 40,
        )
        self._env.start()
        self.settings = Settings()
        self.store = MemoryAuthStore(self.settings)
        self.store.seed_isolated_dev()
        self.partner = _PartnerMock()
        self.http = httpx.Client(transport=httpx.MockTransport(self.partner.handler))
        self.client = TestClient(
            create_app(
                self.settings,
                lambda _s: {"state": "ok"},
                lambda _s: {"state": "ok"},
                self.store,
                self.http,
            )
        )

    def tearDown(self):
        self.http.close()
        self._env.stop()

    def _login_feishu_user(self, subject: str = "alice") -> None:
        self.client.cookies.clear()
        principal = self.store.principal_for_fixture(subject)
        now = datetime.now(timezone.utc)
        raw = self.store.create_session(
            principal,
            UserTokens("user-access", "user-refresh", now + timedelta(hours=1), now + timedelta(days=7)),
        )
        self.client.cookies.set(SESSION_COOKIE, raw)

    def test_quota_check_uses_partner_ai_and_local_trial_only(self):
        self._login_feishu_user("alice")
        first = self.client.get("/integrations/feishu/usage/quota")
        self.assertEqual(first.status_code, 200)
        body = first.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["user_quota"], 80)
        self.assertTrue(body["ai_package_purchased"])
        self.assertTrue(body["quota_sufficient"])
        self.assertEqual(body["remaining_trial_ai_points"], 10000)
        self.assertEqual(body["message"], "AI额度充足")
        recorded = self.client.post(
            "/integrations/feishu/usage/events",
            json={"consumed_points": 4000, "is_free_trial": True, "trace_id": "trial-1"},
        )
        self.assertEqual(recorded.status_code, 201)
        self.assertTrue(recorded.json()["is_free_trial"])
        self.assertEqual(recorded.json()["external_status"], "none")
        replay = self.client.post(
            "/integrations/feishu/usage/events",
            json={"consumed_points": 4000, "is_free_trial": True, "trace_id": "trial-1"},
        )
        self.assertEqual(replay.json()["id"], recorded.json()["id"])
        after = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertEqual(after["remaining_trial_ai_points"], 6000)
        pending = self.client.post(
            "/integrations/feishu/usage/events",
            json={"consumed_points": 2000, "is_free_trial": False, "trace_id": "partner-1"},
        )
        self.assertEqual(pending.json()["external_status"], "pending")
        self.assertFalse(pending.json()["is_free_trial"])
        still = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertEqual(still["remaining_trial_ai_points"], 6000)
        self.assertNotIn("external_status", still)
        self.assertNotIn("paid_balance", still)

    def test_partner_failure_does_not_invent_paid_balance(self):
        self._login_feishu_user("alice")
        self.partner.fail = True
        body = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertFalse(body["quota_sufficient"])
        self.assertEqual(body["user_quota"], 0)
        self.assertEqual(body["message"], "检查额度异常")
        self.assertEqual(body["remaining_trial_ai_points"], 10000)

    def test_unpurchased_package_keeps_partner_fields(self):
        self._login_feishu_user("alice")
        self.partner.item = {"enabled": False, "user_quota": 0, "ai_package_purchased": False}
        body = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertFalse(body["quota_sufficient"])
        self.assertFalse(body["ai_package_purchased"])
        self.assertEqual(body["message"], "未购买AI包")

    def test_cross_tenant_trial_is_isolated(self):
        self._login_feishu_user("alice")
        self.client.post(
            "/integrations/feishu/usage/events",
            json={"consumed_points": 1000, "is_free_trial": True, "trace_id": "alice-trial"},
        )
        self._login_feishu_user("carol")
        body = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertEqual(body["remaining_trial_ai_points"], 10000)

    def test_fixture_session_cannot_query_partner_quota(self):
        self.client.post("/isolated-dev/sessions", json={"subject": "alice"})
        response = self.client.get("/integrations/feishu/usage/quota")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "feishu_user_token_missing")


if __name__ == "__main__":
    unittest.main()
