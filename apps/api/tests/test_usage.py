"""Provider-neutral usage read path for the Web workbench."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import pytest
from api import create_app
from fastapi.testclient import TestClient
from product.settings import Settings


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))}
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class UsageReadTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory, seed_usage_event_factory):
        self.auth_store = auth_store_factory
        self.seed_usage_event = seed_usage_event_factory

    def setUp(self):
        self._env = _clean_env(PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store))

    def tearDown(self):
        self._env.stop()

    def _login(self, subject: str) -> None:
        self.client.cookies.clear()
        response = self.client.post("/isolated-dev/sessions", json={"subject": subject})
        self.assertEqual(response.status_code, 200)

    def test_usage_requires_authentication(self):
        response = self.client.get("/usage")
        self.assertEqual(response.status_code, 401)

    def test_usage_lists_tenant_events_and_keeps_initiator(self):
        alice = self.store.principal_for_fixture("alice")
        bob = self.store.principal_for_fixture("bob")
        self.seed_usage_event(self.store, alice, "item-a", 400, True, "none", trace_id="alice-1")
        self.seed_usage_event(self.store, alice, "item-a", 50, False, "pending", trace_id="alice-2")
        self.seed_usage_event(self.store, bob, "item-a", 10, True, "none", trace_id="bob-1")

        self._login("alice")
        listed = self.client.get("/usage")
        self.assertEqual(listed.status_code, 200)
        events = listed.json()["events"]
        self.assertEqual({row["trace_id"] for row in events}, {"alice-1", "alice-2", "bob-1"})
        self.assertTrue(all("created_at" in row and row["created_at"] for row in events))
        self.assertTrue(all(row["ai_item_id"] == "item-a" for row in events))
        self.assertEqual({row["user_id"] for row in events}, {alice.user_id, bob.user_id})
        self.assertNotIn("paid_balance", listed.json())

        self._login("bob")
        bob_listed = self.client.get("/usage")
        self.assertEqual(bob_listed.status_code, 200)
        self.assertEqual({row["trace_id"] for row in bob_listed.json()["events"]}, {"alice-1", "alice-2", "bob-1"})

        self._login("carol")
        isolated = self.client.get("/usage")
        self.assertEqual(isolated.json()["events"], [])
