"""Partner AI quota check and usage observations. Mocked HTTP only."""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from api import create_app
from fastapi.testclient import TestClient
from product.feishu import UserTokens
from product.feishu_usage import PartnerAiItem, audit_usage_points, estimate_audit, quota_sufficient, remaining_trial_points, split_trial_and_partner
from product.identity import SESSION_COOKIE
from product.settings import Settings


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("PPAP_", "FEISHU_"))}
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

    def test_estimate_uses_server_cost_and_trial_or_partner(self):
        partner = PartnerAiItem(True, 80, True)
        body = estimate_audit(2, 400, partner)
        self.assertEqual(body["estimated_points"], 1000)
        self.assertEqual(body["target_count"], 2)
        self.assertEqual(body["remaining_trial_ai_points"], 400)
        self.assertTrue(body["accept_allowed"])
        blocked = estimate_audit(2, 0, PartnerAiItem(False, 0, False))
        self.assertFalse(blocked["accept_allowed"])
        self.assertEqual(blocked["estimated_points"], 1000)
        trial_only = estimate_audit(1, 500, PartnerAiItem(True, 0, False))
        self.assertTrue(trial_only["accept_allowed"])
        self.assertEqual(trial_only["estimated_points"], 500)
        preview = estimate_audit(2, 0, PartnerAiItem(False, 0, False), charge_enabled=False)
        self.assertTrue(preview["accept_allowed"])
        self.assertEqual(preview["estimated_points"], 0)
        self.assertEqual(preview["accept_reason"], "current_preview_not_charged")

    def test_estimate_requires_enabled_business_switch_and_only_positive_available_balance(self):
        for free, partner, allowed in (
            (1, PartnerAiItem(True, 0, False), True),
            (0, PartnerAiItem(True, 1, True), True),
            (600, PartnerAiItem(True, 0, False), True),
            (0, PartnerAiItem(True, 0, True), False),
            (10000, PartnerAiItem(False, 10000, True), False),
            (10000, None, False),
        ):
            with self.subTest(free=free, partner=partner):
                body = estimate_audit(3, free, partner)
                self.assertEqual(body["estimated_points"], 1500)
                self.assertIs(body["accept_allowed"], allowed)
                if not allowed:
                    self.assertTrue(body["accept_reason"])

    def test_zero_target_and_development_no_charge_estimates_do_not_gain_a_business_gate(self):
        for partner in (None, PartnerAiItem(False, 0, False), PartnerAiItem(True, 0, True)):
            with self.subTest(partner=partner):
                empty = estimate_audit(0, 0, partner)
                self.assertEqual(empty["estimated_points"], 0)
                self.assertIs(empty["accept_allowed"], True)
                self.assertEqual(empty["accept_reason"], "")
                development = estimate_audit(3, 0, partner, charge_enabled=False)
                self.assertEqual(development["estimated_points"], 0)
                self.assertIs(development["accept_allowed"], True)
                self.assertEqual(development["accept_reason"], "current_preview_not_charged")

    def test_disabled_business_switch_is_the_reported_cause_even_with_sufficient_free_points(self):
        body = estimate_audit(1, 1000, PartnerAiItem(False, 0, False))
        self.assertIs(body["accept_allowed"], False)
        self.assertEqual(body["message"], "飞书AI包开关已关闭")
        self.assertEqual(body["accept_reason"], "飞书AI包开关已关闭，请联系管理员在飞书管理后台启用或联系我们获取免费额度")
        self.assertEqual(body["detailed_message"], body["accept_reason"])


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
        if method == "POST" and url.endswith("/auth/v3/app_access_token"):
            body = json.loads(request.content.decode())
            if body.get("app_id") != "cli_test" or body.get("app_ticket") != "tik":
                return httpx.Response(401, json={"code": 1, "msg": "bad ticket"})
            return httpx.Response(200, json={"code": 0, "app_access_token": "app-access"})
        if method == "POST" and url.endswith("/auth/v3/tenant_access_token"):
            body = json.loads(request.content.decode())
            if body != {"app_access_token": "app-access", "tenant_key": "isolated-dev-a"}:
                return httpx.Response(401, json={"code": 1})
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
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory, seed_usage_event_factory, set_trial_baseline_factory):
        self.auth_store = auth_store_factory
        self.seed_usage_event = seed_usage_event_factory
        self.set_trial_baseline = set_trial_baseline_factory

    def setUp(self):
        self._env = _clean_env(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_AI_ITEM_ID="ai-item-1",
            PPAP_BUILD_SHA="a" * 40,
        )
        self._env.start()
        self.settings = Settings()
        self.store = self.auth_store(self.settings)
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

    def _login_feishu_user(self, subject: str = "alice", *, principal=None) -> None:
        self.client.cookies.clear()
        principal = principal or self.store.principal_for_fixture(subject)
        now = datetime.now(timezone.utc)
        raw = self.store.create_session(
            principal,
            UserTokens("user-access", "user-refresh", now + timedelta(hours=1), now + timedelta(days=7)),
        )
        self.client.cookies.set(SESSION_COOKIE, raw)

    def _seed_event(self, *, json, principal=None):
        # Test fixture only; the Product API cannot create arbitrary usage events.
        principal = principal or self.store.principal_for_fixture("alice")
        row = self.seed_usage_event(
            self.store,
            principal,
            "ai-item-1",
            json["consumed_points"],
            json["is_free_trial"],
            "none" if json["is_free_trial"] else "pending",
            trace_id=json.get("trace_id"),
        )
        return httpx.Response(201, json=row)

    def test_arbitrary_usage_creation_is_not_exposed(self):
        self._login_feishu_user("alice")
        response = self.client.post("/integrations/feishu/usage/events", json={"consumed_points": 0})
        self.assertEqual(response.status_code, 404)

    def test_quota_check_uses_partner_ai_and_local_trial_only(self):
        self._login_feishu_user("alice")
        first = self.client.get("/integrations/feishu/usage/quota")
        self.assertEqual(first.status_code, 200)
        body = first.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["user_quota"], 80)
        self.assertTrue(body["ai_package_purchased"])
        self.assertTrue(body["quota_sufficient"])
        self.assertNotIn("unavailable", body)
        self.assertEqual(body["remaining_trial_ai_points"], 10000)
        self.assertEqual(body["message"], "AI额度充足")
        recorded = self._seed_event(
            json={"consumed_points": 4000, "is_free_trial": True, "trace_id": "trial-1"},
        )
        self.assertEqual(recorded.status_code, 201)
        self.assertTrue(recorded.json()["is_free_trial"])
        self.assertEqual(recorded.json()["external_status"], "none")
        replay = self._seed_event(
            json={"consumed_points": 4000, "is_free_trial": True, "trace_id": "trial-1"},
        )
        self.assertEqual(replay.json()["id"], recorded.json()["id"])
        after = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertEqual(after["remaining_trial_ai_points"], 6000)
        pending = self._seed_event(
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
        self.assertTrue(body["unavailable"])
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
        self._seed_event(
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
        estimate = self.client.get("/integrations/feishu/usage/quota", params={"target_count": 1})
        self.assertEqual(estimate.status_code, 409)
        self.assertNotIn("remaining_trial_ai_points", estimate.json())

    def test_cutover_baseline_reduces_remaining_trial_without_inventing_partner_balance(self):
        self.set_trial_baseline(self.store, "isolated-dev-a", "ai-item-1", 1000)
        self._login_feishu_user("alice")
        body = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertEqual(body["remaining_trial_ai_points"], 9000)
        self.assertEqual(body["user_quota"], 80)
        self.assertTrue(body["quota_sufficient"])
        self._login_feishu_user("carol")
        other = self.client.get("/integrations/feishu/usage/quota").json()
        self.assertEqual(other["remaining_trial_ai_points"], 10000)

    def test_cutover_baseline_sql_records_wanxiang_lian_dian_and_baolong(self):
        from pathlib import Path as P

        sql = (P(__file__).resolve().parents[3] / "migrations" / "0018_trial_cutover_baseline.sql").read_text()
        self.assertIn("'1406548d3c95175d'", sql)
        self.assertIn("'1629d31db550975f'", sql)
        self.assertIn("'11c1ad08115b175e'", sql)
        self.assertIn("2500", sql)
        self.assertIn("OpenApp_Lcoastline_PPAPAuditAgent", sql)
        self.assertIn("万向钱潮股份公司", sql)
        self.assertIn("上海保隆汽车科技股份有限公司", sql)
        self.assertIn("联合汽车电子有限公司", sql)

    def test_quota_estimate_includes_accept_gate(self):
        self._login_feishu_user("alice")
        body = self.client.get("/integrations/feishu/usage/quota", params={"target_count": 2}).json()
        self.assertEqual(body["estimated_points"], 1000)
        self.assertEqual(body["target_count"], 2)
        self.assertEqual(body["remaining_trial_ai_points"], 10000)
        self.assertTrue(body["accept_allowed"])
        self.assertTrue(body["quota_sufficient"])

    def test_trial_report_is_skipped_without_partner_http(self):
        self._login_feishu_user("alice")
        self.store.save_app_ticket("cli_test", "tik")
        created = self._seed_event(
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
        missing = self._seed_event(
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

        self._login_feishu_user("bob")
        stolen = self.client.post(f"/integrations/feishu/usage/events/{missing['id']}/report")
        self.assertEqual(stolen.status_code, 409)
        self.assertEqual(stolen.json()["error"], "usage_report_owner_required")
        self.assertEqual(self.store.usage_event(self.store.principal_for_fixture("alice"), missing["id"])["external_status"], "confirmed")

    def test_report_failure_stays_unconfirmed(self):
        self._login_feishu_user("alice")
        self.store.save_app_ticket("cli_test", "tik")
        self.partner.fail_report = True
        created = self._seed_event(
            json={"consumed_points": 9, "is_free_trial": False, "trace_id": "partner-fail"},
        ).json()
        response = self.client.post(f"/integrations/feishu/usage/events/{created['id']}/report")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "feishu_usage_report_unknown")
        row = self.store.usage_event(self.store.principal_for_fixture("alice"), created["id"])
        self.assertEqual(row["external_status"], "unknown")
        self.assertIsNone(row["external_record_id"])
        count = len(self.partner.calls)
        self.partner.fail_report = False
        again = self.client.post(f"/integrations/feishu/usage/events/{created['id']}/report")
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["external_status"], "unknown")
        self.assertEqual(len(self.partner.calls), count)

    def test_charge_switch_blocks_external_reporting_without_changing_pending(self):
        self._login_feishu_user()
        created = self._seed_event(json={"consumed_points": 500, "is_free_trial": False, "trace_id": "disabled-report"}).json()
        self.settings.usage_charge_enabled = False
        response = self.client.post(f"/integrations/feishu/usage/events/{created['id']}/report")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "usage_charging_disabled"})
        self.assertEqual(self.partner.calls, [])
        self.assertEqual(self.store.usage_event(self.store.principal_for_fixture("alice"), created["id"])["external_status"], "pending")

    def test_pending_reporting_resumes_with_original_owner_not_a_usage_viewer(self):
        from api.usage_reporting import dispatch_pending_usage

        self._login_feishu_user("bob")
        self.store.save_app_ticket("cli_test", "tik")
        owner = self.store.upsert_feishu_identity("isolated-dev-a", "report-owner", "报告发起人")
        created = self._seed_event(json={"consumed_points": 500, "is_free_trial": False, "trace_id": "owner-recovery"}, principal=owner).json()
        self.assertFalse(dispatch_pending_usage(self.store, self.http, self.settings))
        self.assertEqual(self.partner.calls, [])
        self._login_feishu_user(principal=owner)
        self.assertTrue(dispatch_pending_usage(self.store, self.http, self.settings))
        row = self.store.usage_event(owner, created["id"])
        self.assertEqual(row["external_status"], "confirmed")
        self.assertIsNone(row["debit_order_id"])
        self.assertIsNone(row["debited_at"])
        before = len(self.partner.calls)
        self.assertFalse(dispatch_pending_usage(self.store, self.http, self.settings))
        self.assertEqual(before, len(self.partner.calls))

    def test_pending_reporting_waits_for_recharge_and_never_retries_unknown(self):
        from api.usage_reporting import dispatch_pending_usage

        owner = self.store.upsert_feishu_identity("isolated-dev-a", "report-owner", "报告发起人")
        self._login_feishu_user(principal=owner)
        self.store.save_app_ticket("cli_test", "tik")
        created = self._seed_event(json={"consumed_points": 500, "is_free_trial": False, "trace_id": "recharge-recovery"}, principal=owner).json()
        self.partner.item["user_quota"] = 0
        self.assertFalse(dispatch_pending_usage(self.store, self.http, self.settings))
        self.assertFalse(any(url.endswith("/usage_records/report") for _method, url in self.partner.calls))
        self.partner.item["user_quota"] = 1
        self.partner.fail_report = True
        self.assertTrue(dispatch_pending_usage(self.store, self.http, self.settings))
        self.assertEqual(self.store.usage_event(owner, created["id"])["external_status"], "unknown")
        before = len(self.partner.calls)
        self.assertFalse(dispatch_pending_usage(self.store, self.http, self.settings))
        self.assertEqual(before, len(self.partner.calls))

    def test_twenty_blocked_obligations_do_not_starve_another_authorized_owner(self):
        from api.usage_reporting import dispatch_pending_usage

        blocked = self.store.upsert_feishu_identity("isolated-dev-a", "no-balance-owner", "等待充值")
        now = datetime.now(timezone.utc)
        self.store.create_session(blocked, UserTokens("empty-quota-user", "", now + timedelta(hours=1), now + timedelta(hours=1)))
        for index in range(20):
            self._seed_event(json={"consumed_points": 500, "is_free_trial": False, "trace_id": f"blocked-{index}"}, principal=blocked)
        ready = self.store.upsert_feishu_identity("isolated-dev-a", "ready-owner", "已有余额")
        self._login_feishu_user(principal=ready)
        event = self._seed_event(json={"consumed_points": 500, "is_free_trial": False, "trace_id": "ready-21"}, principal=ready).json()
        self.store.save_app_ticket("cli_test", "tik")

        def provider(request):
            if request.headers.get("authorization") == "Bearer empty-quota-user":
                return httpx.Response(200, json={"code": 0, "data": {"tenant_ai_item": {"enabled": True, "user_quota": 0, "ai_package_purchased": True}}})
            return self.partner.handler(request)

        with httpx.Client(transport=httpx.MockTransport(provider)) as http:
            self.assertTrue(dispatch_pending_usage(self.store, http, self.settings))
        self.assertEqual(self.store.usage_event(ready, event["id"])["external_status"], "confirmed")
        self.assertEqual(len([url for _method, url in self.partner.calls if url.endswith("/usage_records/report")]), 1)


if __name__ == "__main__":
    unittest.main()
