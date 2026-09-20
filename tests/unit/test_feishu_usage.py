"""Partner AI quota check and usage observations. Mocked HTTP only."""
from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx

import tests.pathsetup  # noqa: F401

from fastapi.testclient import TestClient

from ppap_core.feishu import UserTokens
from ppap_core.feishu_usage import audit_usage_points, quota_sufficient, remaining_trial_points, split_trial_and_partner
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

    def test_audit_usage_splits_trial_then_partner(self):
        self.assertEqual(audit_usage_points(2), 1000)
        self.assertEqual(split_trial_and_partner(10000, 500), [(500, True)])
        self.assertEqual(split_trial_and_partner(0, 500), [(500, False)])
        self.assertEqual(split_trial_and_partner(200, 500), [(200, True), (300, False)])
        self.assertEqual(split_trial_and_partner(10, 0), [])


class _PartnerMock:
    def __init__(self):
        self.item = {"enabled": True, "user_quota": 80, "ai_package_purchased": True}
        self.fail = False
        self.fail_report = False
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method.upper()
        auth = request.headers.get("authorization", "")
        self.calls.append((method, url))
        if method == "POST" and url.endswith("/auth/v3/tenant_access_token"):
            body = json.loads(request.content.decode())
            if body.get("app_id") != "cli_test" or body.get("app_ticket") != "tik":
                return httpx.Response(401, json={"code": 1, "msg": "bad ticket"})
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-access"})
        if method == "GET" and "/partner_ai/v1/tenant_ai_items/" in url:
            if auth != "Bearer user-access":
                return httpx.Response(401, json={"code": 99991663, "msg": "unauthorized"})
            if self.fail:
                return httpx.Response(500, json={"code": 1, "msg": "down"})
            item_id = url.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={"code": 0, "data": {"tenant_ai_item": {"id": item_id, **self.item}}},
            )
        if method == "POST" and url.endswith("/partner_ai/v1/credential/apply"):
            if auth != "Bearer user-access":
                return httpx.Response(401, json={"code": 99991663, "msg": "unauthorized"})
            return httpx.Response(200, json={"code": 0, "data": {"token": "report-tik", "expired_at": 1800}})
        if method == "POST" and url.endswith("/partner_ai/v1/usage_records/report"):
            if auth != "Bearer tenant-access":
                return httpx.Response(401, json={"code": 99991663, "msg": "unauthorized"})
            if self.fail_report:
                return httpx.Response(500, json={"code": 1, "msg": "report down"})
            payload = json.loads(request.content.decode())
            if payload.get("report_token") != "report-tik":
                return httpx.Response(400, json={"code": 1, "msg": "bad report token"})
            return httpx.Response(200, json={"code": 0, "data": {"id": "fs-rec-1"}})
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

    def test_trial_report_is_skipped_without_partner_http(self):
        self._login_feishu_user("alice")
        self.store.save_app_ticket("cli_test", "tik")
        created = self.client.post(
            "/integrations/feishu/usage/events",
            json={"consumed_points": 100, "is_free_trial": True, "trace_id": "trial-skip"},
        ).json()
        self.partner.calls.clear()
        response = self.client.post(f"/integrations/feishu/usage/events/{created['id']}/report")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["skipped"])
        self.assertEqual(response.json()["external_status"], "none")
        self.assertEqual(response.json()["skip_reason"], "消费在赠送额度内")
        self.assertEqual(self.partner.calls, [])

    def test_pending_report_confirms_only_after_feishu_record_id(self):
        self._login_feishu_user("alice")
        missing = self.client.post(
            "/integrations/feishu/usage/events",
            json={"consumed_points": 50, "is_free_trial": False, "trace_id": "partner-report"},
        ).json()
        blocked = self.client.post(f"/integrations/feishu/usage/events/{missing['id']}/report")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"], "feishu_app_ticket_missing")
        self.assertEqual(self.store.usage_event(self.store.principal_for_fixture("alice"), missing["id"])["external_status"], "pending")

        self.store.save_app_ticket("cli_test", "tik")
        confirmed = self.client.post(f"/integrations/feishu/usage/events/{missing['id']}/report")
        self.assertEqual(confirmed.status_code, 200)
        self.assertFalse(confirmed.json()["skipped"])
        self.assertEqual(confirmed.json()["external_status"], "confirmed")
        self.assertEqual(confirmed.json()["external_record_id"], "fs-rec-1")
        self.assertTrue(any(url.endswith("/partner_ai/v1/credential/apply") for _method, url in self.partner.calls))
        self.assertTrue(any(url.endswith("/partner_ai/v1/usage_records/report") for _method, url in self.partner.calls))
        replay = self.client.post(f"/integrations/feishu/usage/events/{missing['id']}/report")
        self.assertEqual(replay.json()["external_record_id"], "fs-rec-1")
        reports = [url for _method, url in self.partner.calls if url.endswith("/usage_records/report")]
        self.assertEqual(len(reports), 1)

        self._login_feishu_user("carol")
        denied = self.client.post(f"/integrations/feishu/usage/events/{missing['id']}/report")
        self.assertEqual(denied.status_code, 403)

    def test_report_failure_stays_unconfirmed(self):
        self._login_feishu_user("alice")
        self.store.save_app_ticket("cli_test", "tik")
        self.partner.fail_report = True
        created = self.client.post(
            "/integrations/feishu/usage/events",
            json={"consumed_points": 9, "is_free_trial": False, "trace_id": "partner-fail"},
        ).json()
        response = self.client.post(f"/integrations/feishu/usage/events/{created['id']}/report")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "feishu_usage_report_failed")
        row = self.store.usage_event(self.store.principal_for_fixture("alice"), created["id"])
        self.assertEqual(row["external_status"], "failed")
        self.assertIsNone(row["external_record_id"])


if __name__ == "__main__":
    unittest.main()
