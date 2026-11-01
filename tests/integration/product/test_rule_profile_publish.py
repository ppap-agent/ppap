"""Default profile becomes v2 through parse and publish. Explicit v3 stays v3."""

from __future__ import annotations

import json
import os
import unittest
import uuid
from unittest.mock import patch

import pytest
from api import create_app
from audit.rule_profile import RULE_PROFILE_V2, RULE_PROFILE_V3
from fastapi.testclient import TestClient
from product.settings import Settings
from worker import process_claimed


def _item() -> dict:
    return {
        "id": "1",
        "category": "基本信息",
        "priority": "P0",
        "rule_to_check": "必须填写零件号",
        "type_of_rule": "table",
    }


def _clean_env():
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))}
    return patch.dict(os.environ, keep, clear=True)


class TestRuleProfilePublish(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory):
        self.auth_store = auth_store_factory

    def setUp(self):
        self._env = _clean_env()
        self._env.start()
        self.settings = Settings()
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store))
        self.client.post("/isolated-dev/sessions", json={"subject": "alice"})

    def tearDown(self):
        self.client.close()
        self._env.stop()

    def _upload(self, name: str, body: bytes) -> dict:
        created = self.client.post(
            "/files",
            json={"original_filename": name, "content_type": "application/json", "byte_size": len(body)},
        )
        assert created.status_code == 201
        version = created.json()
        path = f"/files/{version['file_id']}/versions/{version['id']}"
        assert self.client.put(path + "/blob", content=body).status_code == 200
        assert self.client.post(path + "/finalize").status_code == 200
        return version

    def _parse(self, source: dict) -> dict:
        accepted = self.client.post(
            "/rules/parse",
            json={
                "request_id": str(uuid.uuid4()),
                "source_file_id": source["file_id"],
                "source_file_version_id": source["id"],
            },
        )
        assert accepted.status_code == 202
        job = self.store.claim_job("rule-worker")
        assert job.id == accepted.json()["id"]
        process_claimed(self.store, job, "rule-worker")
        view = self.client.get("/rules/parse/" + job.id)
        assert view.status_code == 200
        return view.json()["result"]

    def _publish(self, candidate: dict) -> dict:
        published = self.client.post("/rules", json=candidate)
        assert published.status_code == 201
        return published.json()

    def test_missing_profile_parses_and_publishes_as_v2(self):
        payload = {"kind": "engine", "type": "控制计划", "rules": [_item()]}
        source = self._upload("default-profile.json", json.dumps(payload).encode())
        candidate = self._parse(source)
        assert candidate["body"]["rule_profile"] == RULE_PROFILE_V2
        published = self._publish(candidate)
        assert published["body"]["rule_profile"] == RULE_PROFILE_V2

    def test_explicit_v3_survives_parse_and_publish(self):
        payload = {"kind": "engine", "type": "控制计划", "rule_profile": "v3", "rules": [_item()]}
        source = self._upload("v3-profile.json", json.dumps(payload).encode())
        candidate = self._parse(source)
        assert candidate["body"]["rule_profile"] == RULE_PROFILE_V3
        published = self._publish(candidate)
        assert published["body"]["rule_profile"] == RULE_PROFILE_V3

    def test_default_token_parses_and_publishes_as_v2(self):
        payload = {"kind": "engine", "type": "控制计划", "rule_profile": "default", "rules": [_item()]}
        source = self._upload("default-token.json", json.dumps(payload).encode())
        candidate = self._parse(source)
        assert candidate["body"]["rule_profile"] == RULE_PROFILE_V2
        published = self._publish(candidate)
        assert published["body"]["rule_profile"] == RULE_PROFILE_V2
