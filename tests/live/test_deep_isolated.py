"""Real-model protocol smoke, not acceptance for customer A1–A12 samples."""
from __future__ import annotations

from io import BytesIO
import json
import os
import time
import unittest
import urllib.error
import urllib.request
from uuid import uuid4
from http.cookiejar import CookieJar

from openpyxl import Workbook

from tests import pathsetup

from ppap_core.db import connect
from ppap_core.settings import Settings
from ppap_deep.client import DEEP_REQUEST_TIMEOUT

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


@unittest.skipUnless(LIVE, "set PPAP_DEEP_LIVE=1 against a semantic Deep sidecar")
class LiveDeepRoundTests(unittest.TestCase):
    def test_real_model_protocol_smoke_has_semantic_evidence(self):
        client = _Client()
        status, caps = client.json("GET", "/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(caps["deep"]["state"], "configured")
        status, ready = client.json("GET", "/ready")
        self.assertEqual(status, 200)
        self.assertEqual(ready["status"], "ready")
        status, _me = client.json("POST", "/isolated-dev/sessions", {"subject": "alice"})
        self.assertEqual(status, 200)

        def upload(name: str, content_type: str, payload: bytes) -> dict:
            status, created = client.json("POST", "/files", {
                "original_filename": name,
                "content_type": content_type,
                "byte_size": len(payload),
            })
            self.assertEqual(status, 201, created)
            file_id, version_id = created["file_id"], created["id"]
            status, _blob = client.request("PUT", f"/files/{file_id}/versions/{version_id}/blob", payload)
            self.assertEqual(status, 200)
            status, ready_file = client.json("POST", f"/files/{file_id}/versions/{version_id}/finalize")
            self.assertEqual(status, 200, ready_file)
            return ready_file

        rule_body = {
            "kind": "engine", "type": "控制计划", "rule_profile": "v3",
            "customers": [], "models": [], "parts": [],
            "rules": [{
                "id": "part-number", "category": "基本信息", "priority": "P1",
                "rule_to_check": "核对控制计划中所有数据行的零件号均非空；列名为零件号，数据行从第2行开始。",
                "type_of_rule": "table",
            }],
        }
        rule_source = upload("semantic-rule.json", "application/json", json.dumps(rule_body, ensure_ascii=False).encode())
        status, published = client.json("POST", "/rules", {
            "title": "Live semantic rule " + uuid4().hex[:8],
            "body": rule_body,
            "source_file_version_id": rule_source["id"],
        })
        self.assertEqual(status, 201, published)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "控制计划"
        sheet.append(["零件号"])
        sheet.append(["P-100"])
        buffer = BytesIO()
        workbook.save(buffer)
        ready_file = upload(
            "semantic-target.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            buffer.getvalue(),
        )
        status, draft = client.json("POST", "/drafts")
        self.assertEqual(status, 201, draft)
        status, saved = client.json(
            "PUT",
            f"/drafts/{draft['id']}",
            {
                "analysis_mode": "deep",
                "items": [{"file_version_id": ready_file["id"], "role": "target", "rule_version_id": published["id"]}],
            },
        )
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["analysis_mode"], "deep")
        status, accepted = client.json(
            "POST",
            f"/drafts/{draft['id']}/accept",
            {"idempotency_key": "live-deep-" + uuid4().hex},
        )
        self.assertEqual(status, 201, accepted)
        round_id = accepted["id"]
        self.assertEqual(accepted["analysis_mode"], "deep")
        deadline = time.time() + DEEP_REQUEST_TIMEOUT
        body: dict = {}
        while time.time() < deadline:
            status, body = client.json("GET", f"/rounds/{round_id}")
            self.assertEqual(status, 200, body)
            if body.get("execution") in {"completed", "failed"}:
                break
            time.sleep(1)
        self.assertEqual(body.get("analysis_mode"), "deep")
        self.assertEqual(body.get("executor"), "ppap-deep")
        self.assertEqual(body.get("executor_revision"), "ppap-deep-v2")
        self.assertEqual(body.get("execution"), "completed", body)
        self.assertEqual(len(body.get("findings", [])), 1)
        finding = body["findings"][0]
        self.assertEqual(finding["status"], "PASS")
        self.assertIn("semantic-target.xlsx", body.get("coverage", {}).get("checked", []))
        semantic = finding["locator"]["semantic"]
        self.assertEqual(semantic["clause_id"], "part-number")
        self.assertGreaterEqual(semantic["scope"]["total"], 1)
        self.assertEqual(semantic["scope"]["checked"], semantic["scope"]["total"])
        self.assertEqual(semantic["scope"]["unknown"], 0)
        self.assertTrue(any("P-100" in source["excerpt"] for source in semantic["sources"]))
        with connect(Settings(), "worker") as conn:
            diagnostic = conn.execute(
                """SELECT a.diagnostic FROM engine.attempts a
                   JOIN engine.jobs j ON j.id = a.job_id
                   WHERE j.audit_round_id = %s AND j.kind = 'audit'
                   ORDER BY a.finished_at DESC NULLS LAST LIMIT 1""",
                (round_id,),
            ).fetchone()
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic[0]["note"], "deep_semantic_audit")
        self.assertGreater(diagnostic[0]["usage"]["input_tokens"], 0)
        self.assertGreater(diagnostic[0]["usage"]["output_tokens"], 0)
