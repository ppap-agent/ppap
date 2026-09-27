"""Identity and Feishu OAuth surface. Not an S02 live login proof."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import tests.pathsetup  # noqa: F401

from fastapi.testclient import TestClient

from ppap_core.feishu import (
    AppMismatch,
    AppTicket,
    Challenge,
    EncryptedEvent,
    UnauthenticatedEvent,
    accept_event,
    parse_event,
    user_info,
    FeishuError,
)
from ppap_core.fixtures import RESOURCE_A, RESOURCE_B, SUBJECTS
from ppap_core.identity import Principal, Resource, can_read, can_review, identity_key, safe_return_to
from ppap_core.settings import Settings
from ppap_api import create_app
from tests.integration.memory_auth import MemoryAuthStore


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class DecisionTests(unittest.TestCase):
    def test_identity_ignores_email(self):
        key = identity_key("feishu", "cli_1", "tenant-a", "ou_1")
        self.assertEqual(key, ("feishu", "cli_1", "tenant-a", "ou_1"))
        with self.assertRaises(ValueError):
            identity_key("feishu", "cli_1", "", "ou_1")

    def test_cross_tenant_read_denied(self):
        alice = Principal("u1", "t1", "feishu", "app", "tk1", "ou_1", "member")
        other = Resource("r2", "t2", "fixture")
        self.assertFalse(can_read(alice, other, {"read", "review"}))

    def test_login_membership_is_not_review(self):
        alice = Principal("u1", "t1", "feishu", "app", "tk1", "ou_1", "member")
        resource = Resource("r1", "t1", "fixture")
        self.assertTrue(can_read(alice, resource, {"read"}))
        self.assertFalse(can_review(alice, resource, {"read"}))
        self.assertTrue(can_review(alice, resource, {"read", "review"}))

    def test_accepted_round_is_tenant_owned_without_personal_grant(self):
        alice = Principal("u1", "t1", "feishu", "app", "tk1", "ou_1", "member")
        bob = Principal("u2", "t1", "feishu", "app", "tk1", "ou_2", "member")
        carol = Principal("u3", "t2", "feishu", "app", "tk2", "ou_3", "member")
        round_resource = Resource("round-1", "t1", "audit_round")
        draft = Resource("draft-1", "t1", "audit_draft")
        self.assertTrue(can_read(alice, round_resource, set()))
        self.assertTrue(can_read(bob, round_resource, set()))
        self.assertTrue(can_review(bob, round_resource, set()))
        self.assertFalse(can_read(carol, round_resource, {"read", "review"}))
        self.assertFalse(can_read(bob, draft, set()))
        self.assertTrue(can_read(alice, draft, {"read"}))

    def test_return_to_rejects_foreign_origin(self):
        with _clean_env():
            settings = Settings()
        self.assertTrue(safe_return_to("http://127.0.0.1:4173/#/r", settings).startswith("http://127.0.0.1:4173"))
        self.assertEqual(safe_return_to("https://evil.example/#/r", settings), "http://127.0.0.1:4173/")

    def test_public_web_origin_is_canonical_return_and_cors(self):
        with _clean_env(PPAP_WEB_URL="https://ppap.example"):
            settings = Settings()
        dest = "https://ppap.example/?resource=abc"
        self.assertEqual(safe_return_to(dest, settings), dest)
        self.assertEqual(safe_return_to("https://evil.example/#/r", settings), "https://ppap.example/")
        self.assertEqual(settings.canonical_web_url(), "https://ppap.example")
        self.assertIn("https://ppap.example", settings.web_origins())
        self.assertIn("http://127.0.0.1:4173", settings.web_origins())

    def test_production_web_origin_is_explicit(self):
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
            PPAP_WEB_URL="https://app.example",
        ):
            settings = Settings()
        self.assertEqual(settings.web_origins(), ["https://app.example"])
        self.assertEqual(safe_return_to("http://127.0.0.1:4173/", settings), "https://app.example/")


class FeishuParseTests(unittest.TestCase):
    def test_challenge_and_ticket(self):
        self.assertIsInstance(parse_event({"type": "url_verification", "challenge": "abc"}), Challenge)
        self.assertIsInstance(parse_event({"header": {"event_type": "app_ticket"}, "event": {"app_ticket": "tik"}}), AppTicket)
        self.assertIsInstance(parse_event({"encrypt": "blob"}), EncryptedEvent)

    def test_accept_event_requires_verification_token_and_app(self):
        with _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", FEISHU_VERIFICATION_TOKEN="verify-test"):
            settings = Settings()
        ticket = {
            "header": {"event_type": "app_ticket", "token": "verify-test", "app_id": "cli_test"},
            "event": {"app_ticket": "tik", "app_id": "cli_test"},
        }
        self.assertIsInstance(accept_event(settings, ticket), AppTicket)
        self.assertIsInstance(accept_event(settings, {**ticket, "header": {**ticket["header"], "token": "nope"}}), UnauthenticatedEvent)
        missing_token = {"header": {"event_type": "app_ticket", "app_id": "cli_test"}, "event": {"app_ticket": "tik"}}
        self.assertIsInstance(accept_event(settings, missing_token), UnauthenticatedEvent)
        other_app = {
            "header": {"event_type": "app_ticket", "token": "verify-test", "app_id": "cli_other"},
            "event": {"app_ticket": "tik", "app_id": "cli_other"},
        }
        self.assertIsInstance(accept_event(settings, other_app), AppMismatch)
        with _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret"):
            bare = Settings()
        self.assertIsInstance(accept_event(bare, ticket), UnauthenticatedEvent)

    def test_user_info_requires_tenant_and_subject(self):
        class Http:
            def get(self, *args, **kwargs):
                class R:
                    status_code = 200
                    def json(self):
                        return {"data": {"email": "a@b.c", "name": "Ada"}}
                return R()
        with self.assertRaises(FeishuError):
            user_info(Http(), "token")


class HttpAuthTests(unittest.TestCase):
    def setUp(self):
        self._env = _clean_env(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_VERIFICATION_TOKEN="verify-test",
            PPAP_BUILD_SHA="a" * 40,
        )
        self._env.start()
        self.settings = Settings()
        self.store = MemoryAuthStore(self.settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store))

    def tearDown(self):
        self._env.stop()

    def _cookie(self, subject: str) -> dict[str, str]:
        self.client.cookies.clear()
        response = self.client.post("/isolated-dev/sessions", json={"subject": subject})
        self.assertEqual(response.status_code, 200)
        raw = response.cookies.get("ppap_session")
        self.client.cookies.clear()
        return {"ppap_session": raw}

    def test_me_and_logout(self):
        cookies = self._cookie("alice")
        me = self.client.get("/me", cookies=cookies)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["identity"]["subject"], SUBJECTS["alice"]["subject"])
        self.assertNotIn("email", str(me.json()).lower())
        gone = self.client.post("/logout", cookies=cookies)
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(self.client.get("/me", cookies=cookies).status_code, 401)

    def test_object_grants_two_tenants(self):
        alice = self._cookie("alice")
        bob = self._cookie("bob")
        carol = self._cookie("carol")
        allowed = self.client.get("/resources/" + RESOURCE_A, cookies=alice)
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(allowed.json()["review_allowed"])
        member_only = self.client.get("/resources/" + RESOURCE_A, cookies=bob)
        self.assertEqual(member_only.status_code, 200)
        self.assertFalse(member_only.json()["review_allowed"])
        denied = self.client.get("/resources/" + RESOURCE_A, cookies=carol)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json(), {"error": "access_denied"})
        self.assertNotIn("kind", denied.json())
        other = self.client.get("/resources/" + RESOURCE_B, cookies=alice)
        self.assertEqual(other.status_code, 403)

    def test_oauth_state_and_off_origin_return(self):
        from urllib.parse import parse_qs, urlparse
        start = self.client.get("/login/feishu?return_to=https://evil.example/x", follow_redirects=False)
        self.assertEqual(start.status_code, 302)
        query = parse_qs(urlparse(start.headers["location"]).query)
        self.assertEqual(query["client_id"], ["cli_test"])
        self.assertTrue(query["state"][0])
        self.assertIn("redirect_uri", query)
        bad = self.client.get("/login/feishu/callback?code=x&state=forged", follow_redirects=False)
        self.assertEqual(bad.status_code, 303)
        self.assertTrue(bad.headers["location"].startswith("http://127.0.0.1:4173"))

    def test_report_consent_is_explicit_and_limited_to_document_creation_and_writing(self):
        from urllib.parse import parse_qs, urlparse
        normal = self.client.get('/login/feishu', follow_redirects=False)
        self.assertEqual(parse_qs(urlparse(normal.headers['location']).query).get('scope'), ['offline_access'])
        report = self.client.get('/login/feishu?purpose=report', follow_redirects=False)
        query = parse_qs(urlparse(report.headers['location']).query)
        self.assertEqual(query.get('scope'), ['offline_access docx:document:create docx:document:write_only'])
        self.assertEqual(self.client.get('/login/feishu?purpose=arbitrary', follow_redirects=False).status_code, 400)

    def test_oauth_callback_binds_tenant_subject_not_email(self):
        from urllib.parse import parse_qs, urlparse

        class FakeHttp:
            def post(self, url, **kwargs):
                class R:
                    status_code = 200

                    def json(self):
                        return {
                            "code": 0,
                            "access_token": "u-ada",
                            "refresh_token": "ur-ada",
                            "expires_in": 7200,
                            "refresh_token_expires_in": 2592000,
                        }

                return R()

            def get(self, url, **kwargs):
                class R:
                    status_code = 200

                    def json(self):
                        return {
                            "data": {
                                "tenant_key": "tk-live",
                                "open_id": "ou_ada",
                                "name": "Ada",
                                "email": "hidden@example.com",
                            }
                        }

                return R()

        client = TestClient(
            create_app(
                self.settings,
                lambda _s: {"state": "ok"},
                lambda _s: {"state": "ok"},
                MemoryAuthStore(self.settings),
                FakeHttp(),
            )
        )
        dest = "http://127.0.0.1:4173/?resource=" + RESOURCE_A
        start = client.get("/login/feishu", params={"return_to": dest}, follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        done = client.get("/login/feishu/callback", params={"code": "ok", "state": state}, follow_redirects=False)
        self.assertEqual(done.status_code, 303)
        self.assertEqual(done.headers["location"], dest)
        raw = done.cookies.get("ppap_session")
        self.assertTrue(raw)
        me = client.get("/me", cookies={"ppap_session": raw})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["identity"]["subject"], "ou_ada")
        self.assertEqual(me.json()["identity"]["tenant_key"], "tk-live")
        self.assertNotIn("email", str(me.json()).lower())
        replay = client.get("/login/feishu/callback", params={"code": "ok", "state": state}, follow_redirects=False)
        self.assertEqual(replay.status_code, 303)
        self.assertEqual(replay.headers["location"], "http://127.0.0.1:4173/")
        self.assertFalse(replay.cookies.get("ppap_session"))

    def test_login_disabled_without_feishu_credentials(self):
        with _clean_env(PPAP_BUILD_SHA="a" * 40):
            settings = Settings()
        client = TestClient(
            create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, MemoryAuthStore(settings))
        )
        self.assertEqual(client.get("/login/feishu").status_code, 409)

    def test_unauthenticated_and_unknown_resource(self):
        self.assertEqual(self.client.get("/me").status_code, 401)
        self.assertEqual(self.client.get("/resources/" + RESOURCE_A).status_code, 401)
        cookies = self._cookie("alice")
        denied = self.client.get("/resources/not-a-uuid", cookies=cookies)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json(), {"error": "access_denied"})

    def test_event_challenge_and_ticket(self):
        challenge = self.client.post(
            "/integrations/feishu/events",
            json={"type": "url_verification", "challenge": "ping", "token": "verify-test"},
        )
        self.assertEqual(challenge.json(), {"challenge": "ping"})
        missing = self.client.get("/capabilities").json()["feishu"]
        self.assertEqual(missing["app_ticket"], "missing")
        ticket = self.client.post(
            "/integrations/feishu/events",
            json={
                "header": {"event_type": "app_ticket", "token": "verify-test", "app_id": "cli_test"},
                "event": {"app_ticket": "tik", "app_id": "cli_test"},
            },
        )
        self.assertEqual(ticket.status_code, 200)
        self.assertTrue(self.store.has_app_ticket("cli_test"))
        self.assertEqual(self.client.get("/capabilities").json()["feishu"]["app_ticket"], "received")
        encrypted = self.client.post("/integrations/feishu/events", json={"encrypt": "blob"})
        self.assertEqual(encrypted.status_code, 400)

    def test_unverified_event_cannot_mark_ticket_received(self):
        forged = {
            "header": {"event_type": "app_ticket", "app_id": "cli_test"},
            "event": {"app_ticket": "forged", "app_id": "cli_test"},
        }
        denied = self.client.post("/integrations/feishu/events", json=forged)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(self.client.get("/capabilities").json()["feishu"]["app_ticket"], "missing")
        self.assertFalse(self.store.has_app_ticket("cli_test"))
        wrong = self.client.post(
            "/integrations/feishu/events",
            json={
                "header": {"event_type": "app_ticket", "token": "nope", "app_id": "cli_test"},
                "event": {"app_ticket": "forged", "app_id": "cli_test"},
            },
        )
        self.assertEqual(wrong.status_code, 401)
        mismatch = self.client.post(
            "/integrations/feishu/events",
            json={
                "header": {"event_type": "app_ticket", "token": "verify-test", "app_id": "cli_other"},
                "event": {"app_ticket": "tik", "app_id": "cli_other"},
            },
        )
        self.assertEqual(mismatch.status_code, 403)
        self.assertEqual(self.client.get("/capabilities").json()["feishu"]["app_ticket"], "missing")

    def test_production_hides_isolated_dev_mint(self):
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
            PPAP_WEB_URL="https://app.example",
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
        ):
            settings = Settings()
        client = TestClient(create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, MemoryAuthStore(settings)))
        self.assertEqual(client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code, 404)

    def _oauth_client(self, http, settings=None):
        settings = settings or self.settings
        return TestClient(
            create_app(
                settings,
                lambda _s: {"state": "ok"},
                lambda _s: {"state": "ok"},
                MemoryAuthStore(settings),
                http,
            )
        )

    def _complete_login(self, client, return_to="http://127.0.0.1:4173/"):
        from urllib.parse import parse_qs, urlparse

        start = client.get("/login/feishu", params={"return_to": return_to}, follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        done = client.get("/login/feishu/callback", params={"code": "ok", "state": state}, follow_redirects=False)
        self.assertEqual(done.status_code, 303)
        raw = done.cookies.get("ppap_session")
        self.assertTrue(raw)
        return done, raw

    def test_oauth_refresh_renews_expired_access(self):
        http = _TokenHttp(expires_in=0, refresh_expires_in=2592000)
        client = self._oauth_client(http)
        _done, raw = self._complete_login(client)
        self.assertIn("authorization_code", http.grants)
        me = client.get("/me", cookies={"ppap_session": raw})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["identity"]["subject"], "ou_ada")
        self.assertIn("refresh_token", http.grants)
        self.assertIn('Max-Age=43200', _done.headers['set-cookie'])
        # Rotation keeps the same product session, and a fresh access token
        # does not trigger another provider refresh on the next request.
        refreshes = http.grants.count('refresh_token')
        self.assertEqual(client.get('/me', cookies={'ppap_session': raw}).status_code, 200)
        self.assertEqual(http.grants.count('refresh_token'), refreshes)

    def test_oauth_invalid_refresh_revokes_session(self):
        http = _TokenHttp(expires_in=0, refresh_expires_in=2592000, fail_refresh=True)
        client = self._oauth_client(http)
        _done, raw = self._complete_login(client)
        me = client.get("/me", cookies={"ppap_session": raw})
        self.assertEqual(me.status_code, 401)
        again = client.get("/me", cookies={"ppap_session": raw})
        self.assertEqual(again.status_code, 401)

    def test_oauth_expired_refresh_cannot_continue(self):
        http = _TokenHttp(expires_in=0, refresh_expires_in=0)
        client = self._oauth_client(http)
        _done, raw = self._complete_login(client)
        me = client.get("/me", cookies={"ppap_session": raw})
        self.assertEqual(me.status_code, 401)
        self.assertNotIn("refresh_token", http.grants)

    def test_public_web_return_to_survives_oauth(self):
        from urllib.parse import parse_qs, urlparse

        with _clean_env(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_VERIFICATION_TOKEN="verify-test",
            PPAP_WEB_URL="https://ppap.example",
            PPAP_BUILD_SHA="a" * 40,
        ):
            settings = Settings()
        http = _TokenHttp()
        client = self._oauth_client(http, settings)
        dest = "https://ppap.example/?resource=" + RESOURCE_A
        start = client.get("/login/feishu", params={"return_to": dest}, follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        done = client.get("/login/feishu/callback", params={"code": "ok", "state": state}, follow_redirects=False)
        self.assertEqual(done.status_code, 303)
        self.assertEqual(done.headers["location"], dest)


    def test_allowlist_blocks_unknown_tenant_session(self):
        from urllib.parse import parse_qs, urlparse

        with _clean_env(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_VERIFICATION_TOKEN="verify-test",
            PPAP_CURRENT_TENANT_ALLOWLIST="2caeb428f08e175d",
            PPAP_BUILD_SHA="a" * 40,
        ):
            settings = Settings()
        client = self._oauth_client(_TokenHttp(), settings)
        start = client.get("/login/feishu", params={"return_to": "http://127.0.0.1:4173/"}, follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        done = client.get("/login/feishu/callback", params={"code": "ok", "state": state}, follow_redirects=False)
        self.assertEqual(done.status_code, 303)
        self.assertIn("error=tenant_not_allowed", done.headers["location"])
        self.assertFalse(done.cookies.get("ppap_session"))
        host = client.post("/login/feishu/host", json={"code": "host-code"})
        self.assertEqual(host.status_code, 403)
        self.assertEqual(host.json(), {"error": "tenant_not_allowed"})
        self.assertFalse(host.cookies.get("ppap_session"))

    def test_enter_edition_requires_allowlisted_session(self):
        with _clean_env(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_VERIFICATION_TOKEN="verify-test",
            PPAP_CURRENT_TENANT_ALLOWLIST="tk-live",
            PPAP_WEB_URL="http://127.0.0.1:4173",
            PPAP_PUBLIC_API_URL="http://127.0.0.1:8080",
            PPAP_BUILD_SHA="a" * 40,
        ):
            settings = Settings()
        client = self._oauth_client(_TokenHttp(), settings)
        missing = client.get("/enter-current", follow_redirects=False)
        self.assertEqual(missing.status_code, 302)
        self.assertIn("/login/feishu", missing.headers["location"])
        self.assertIn("enter-current", missing.headers["location"])
        self.assertFalse(missing.cookies.get("ppap_edition"))
        denied_host = client.post("/login/feishu/host", json={"code": "host-code"})
        self.assertEqual(denied_host.status_code, 200)
        cookie = denied_host.cookies.get("ppap_session")
        allowed = client.get("/enter-current", cookies={"ppap_session": cookie}, follow_redirects=False)
        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(allowed.headers["location"], "http://127.0.0.1:4173/")
        self.assertEqual(allowed.cookies.get("ppap_edition"), "current")
        legacy = client.get("/enter-legacy", cookies={"ppap_session": cookie}, follow_redirects=False)
        self.assertEqual(legacy.status_code, 302)
        self.assertEqual(legacy.cookies.get("ppap_edition"), "legacy")

    def test_enter_edition_hides_route_from_other_tenants(self):
        with _clean_env(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_VERIFICATION_TOKEN="verify-test",
            PPAP_CURRENT_TENANT_ALLOWLIST="2caeb428f08e175d",
            PPAP_WEB_URL="http://127.0.0.1:4173",
            PPAP_PUBLIC_API_URL="http://127.0.0.1:8080",
            PPAP_BUILD_SHA="a" * 40,
        ):
            settings = Settings()
        store = MemoryAuthStore(settings)
        store.seed_isolated_dev()
        client = TestClient(
            create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, store, _TokenHttp())
        )
        logged = client.post("/login/feishu/host", json={"code": "isolated-dev:alice"})
        self.assertEqual(logged.status_code, 200)
        cookie = logged.cookies.get("ppap_session")
        hidden = client.get("/enter-current", cookies={"ppap_session": cookie}, follow_redirects=False)
        self.assertEqual(hidden.status_code, 404)
        self.assertFalse(hidden.cookies.get("ppap_edition"))
        hidden_legacy = client.get("/enter-legacy", cookies={"ppap_session": cookie}, follow_redirects=False)
        self.assertEqual(hidden_legacy.status_code, 404)

    def test_allowlist_accepts_listed_tenant(self):
        with _clean_env(
            FEISHU_APP_ID="cli_test",
            FEISHU_APP_SECRET="secret",
            FEISHU_VERIFICATION_TOKEN="verify-test",
            PPAP_CURRENT_TENANT_ALLOWLIST="tk-live,2caeb428f08e175d",
            PPAP_BUILD_SHA="a" * 40,
        ):
            settings = Settings()
        client = self._oauth_client(_TokenHttp(), settings)
        host = client.post("/login/feishu/host", json={"code": "host-code"})
        self.assertEqual(host.status_code, 200)
        self.assertEqual(host.json()["identity"]["tenant_key"], "tk-live")
        self.assertTrue(host.cookies.get("ppap_session"))


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _TokenHttp:
    def __init__(self, expires_in=7200, refresh_expires_in=2592000, fail_refresh=False):
        self.expires_in = expires_in
        self.refresh_expires_in = refresh_expires_in
        self.fail_refresh = fail_refresh
        self.grants: list[str] = []

    def post(self, url, **kwargs):
        body = kwargs.get("json") or {}
        grant = str(body.get("grant_type") or "")
        self.grants.append(grant)
        if grant == "refresh_token":
            if self.fail_refresh:
                return _Resp({"code": 99991663, "msg": "invalid refresh token"})
            return _Resp({
                "code": 0,
                "access_token": "u-new",
                "refresh_token": "ur-new",
                "expires_in": 7200,
                "refresh_token_expires_in": self.refresh_expires_in,
            })
        return _Resp({
            "code": 0,
            "access_token": "u-ada",
            "refresh_token": "ur-ada",
            "expires_in": self.expires_in,
            "refresh_token_expires_in": self.refresh_expires_in,
        })

    def get(self, url, **kwargs):
        return _Resp({
            "data": {
                "tenant_key": "tk-live",
                "open_id": "ou_ada",
                "name": "Ada",
                "email": "hidden@example.com",
            }
        })
