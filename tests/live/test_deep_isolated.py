"""Live Deep round against isolated-dev + host Controller. Skipped unless PPAP_DEEP_LIVE=1."""
from __future__ import annotations

import json
import os
import time
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

from tests import pathsetup

from ppap_core.db import connect
from ppap_core.fixtures import RULE_A
from ppap_core.settings import Settings

LIVE = os.environ.get("PPAP_DEEP_LIVE", "").strip() == "1"
API = os.environ.get("PPAP_PUBLIC_API_URL", "http://127.0.0.1:8080").rstrip("/")


class _Client:
    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None) -> tuple[int, dict]:
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(API + path, data=body, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=30) as response:
                raw = response.read().decode()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"error": raw}
            return exc.code, parsed

    def json(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        body = None if payload is None else json.dumps(payload).encode()
        return self.request(method, path, body, "application/json" if body is not None else None)


@unittest.skipUnless(LIVE, "set PPAP_DEEP_LIVE=1 against an isolated-dev Deep stack")
class LiveDeepRoundTests(unittest.TestCase):
    def test_deep_round_completes_through_controller_guest(self):
        client = _Client()
        status, caps = client.json("GET", "/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(caps["deep"]["state"], "configured")
        status, ready = client.json("GET", "/ready")
        self.assertEqual(status, 200)
        self.assertEqual(ready["status"], "ready")
        status, _me = client.json("POST", "/isolated-dev/sessions", {"subject": "alice"})
        self.assertEqual(status, 200)
        payload = b"live-deep-target"
        status, created = client.json(
            "POST",
            "/files",
            {
                "original_filename": "report.xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "byte_size": len(payload),
            },
        )
        self.assertEqual(status, 201, created)
        file_id, version_id = created["file_id"], created["id"]
        status, _blob = client.request("PUT", f"/files/{file_id}/versions/{version_id}/blob", payload)
        self.assertEqual(status, 200)
        status, ready_file = client.json("POST", f"/files/{file_id}/versions/{version_id}/finalize")
        self.assertEqual(status, 200, ready_file)
        status, draft = client.json("POST", "/drafts")
        self.assertEqual(status, 201, draft)
        status, saved = client.json(
            "PUT",
            f"/drafts/{draft['id']}",
            {
                "analysis_mode": "deep",
                "items": [{"file_version_id": ready_file["id"], "role": "target", "rule_version_id": RULE_A}],
            },
        )
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["analysis_mode"], "deep")
        status, accepted = client.json("POST", f"/drafts/{draft['id']}/accept", {"idempotency_key": "live-deep-1"})
        self.assertEqual(status, 201, accepted)
        round_id = accepted["id"]
        self.assertEqual(accepted["analysis_mode"], "deep")
        deadline = time.time() + 180
        body: dict = {}
        while time.time() < deadline:
            status, body = client.json("GET", f"/rounds/{round_id}")
            self.assertEqual(status, 200, body)
            if body.get("execution") in {"completed", "failed"}:
                break
            time.sleep(1)
        self.assertEqual(body.get("analysis_mode"), "deep")
        self.assertEqual(body.get("executor"), "ppap-deep")
        self.assertEqual(body.get("execution"), "completed", body)
        self.assertTrue(body.get("findings"))
        self.assertEqual({row["status"] for row in body["findings"]}, {"PASS"})
        self.assertIn("report.xlsx", body.get("coverage", {}).get("checked", []))
        with connect(Settings(), "worker") as conn:
            diagnostic = conn.execute(
                "SELECT diagnostic FROM engine.attempts ORDER BY finished_at DESC NULLS LAST LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(diagnostic)
        note = str((diagnostic[0] or {}).get("note") if isinstance(diagnostic[0], dict) else diagnostic[0])
        self.assertIn("gvisor", note)
