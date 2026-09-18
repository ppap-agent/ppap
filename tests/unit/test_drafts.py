"""Audit drafts, identification, and explicit product rule versions. Not S01."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages"), str(ROOT / "services" / "api"), str(ROOT / "services" / "worker")]

from fastapi.testclient import TestClient
from openpyxl import Workbook

from ppap_core.drafts import IDENT_AMBIGUOUS, IDENT_MATCHED, IDENT_UNCERTAIN, IDENT_UNRECOGNIZED, IDENT_UNSUPPORTED
from ppap_core.fixtures import CATALOG_PROJECT
from ppap_core.identify import classify_file
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


def _xlsx(name: str, rows: list[list[object]] | None = None) -> tuple[bytes, str]:
    from io import BytesIO

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet"
    for r_index, row in enumerate(rows or [["x"]], start=1):
        for c_index, value in enumerate(row, start=1):
            sheet.cell(r_index, c_index, value)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), name


class IdentifyClassifierTests(unittest.TestCase):
    def test_filename_psw_is_matched_not_default_profile(self):
        body, name = _xlsx("PSW.xlsx")
        result = classify_file(name, body)
        self.assertEqual(result.status, IDENT_MATCHED)
        self.assertEqual(result.matched_document_type, "零件提交保证书")
        self.assertIsNone(result.evidence.get("rule_profile"))

    def test_unknown_name_is_unrecognized(self):
        body, name = _xlsx("notes.xlsx")
        result = classify_file(name, body)
        self.assertEqual(result.status, IDENT_UNRECOGNIZED)
        self.assertIsNone(result.matched_document_type)

    def test_text_file_is_unsupported(self):
        result = classify_file("notes.txt", b"hello")
        self.assertEqual(result.status, IDENT_UNSUPPORTED)

    def test_weak_content_is_uncertain(self):
        body, name = _xlsx("data.xlsx", [["尺寸检验"]])
        result = classify_file(name, body)
        self.assertEqual(result.status, IDENT_UNCERTAIN)

    def test_pfmea_and_dfmea_filename_is_ambiguous(self):
        body, name = _xlsx("PFMEA-DFMEA.xlsx")
        result = classify_file(name, body)
        self.assertEqual(result.status, IDENT_AMBIGUOUS)
        self.assertIsNone(result.matched_document_type)


class DraftHttpTests(unittest.TestCase):
    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        self.store = MemoryAuthStore(self.settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(
            create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store)
        )

    def tearDown(self):
        self._env.stop()

    def _login(self, subject: str) -> None:
        self.client.cookies.clear()
        response = self.client.post("/isolated-dev/sessions", json={"subject": subject})
        self.assertEqual(response.status_code, 200)

    def _ready_file(self, filename: str, body: bytes) -> tuple[str, str]:
        created = self.client.post(
            "/files",
            json={"original_filename": filename, "content_type": "application/octet-stream", "byte_size": len(body)},
        )
        self.assertEqual(created.status_code, 201)
        file_id, version_id = created.json()["file_id"], created.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{version_id}/blob", content=body)
        ready = self.client.post(f"/files/{file_id}/versions/{version_id}/finalize")
        self.assertEqual(ready.status_code, 200)
        return file_id, version_id

    def test_round_trip_keeps_roles_and_does_not_accept(self):
        self._login("alice")
        target_body, target_name = _xlsx("PSW.xlsx")
        other_body, other_name = _xlsx("control-plan.xlsx")
        ref_body, ref_name = _xlsx("drawing.xlsx")
        t1 = self._ready_file(target_name, target_body)
        t2 = self._ready_file(other_name, other_body)
        r1 = self._ready_file(ref_name, ref_body)
        created = self.client.post("/drafts", json={"label": "prep", "project_id": CATALOG_PROJECT})
        self.assertEqual(created.status_code, 201)
        self.assertFalse(created.json()["accepted"])
        self.assertEqual(created.json()["status"], "open")
        draft_id = created.json()["id"]
        saved = self.client.put(
            f"/drafts/{draft_id}",
            json={
                "items": [
                    {"file_id": t1[0], "file_version_id": t1[1], "role": "target"},
                    {"file_id": t2[0], "file_version_id": t2[1], "role": "target"},
                    {"file_id": r1[0], "file_version_id": r1[1], "role": "reference"},
                ]
            },
        )
        self.assertEqual(saved.status_code, 200)
        fetched = self.client.get(f"/drafts/{draft_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["items"], saved.json()["items"])
        roles = [item["role"] for item in fetched.json()["items"]]
        self.assertEqual(roles, ["target", "target", "reference"])
        self.assertTrue(all(item["identification_status"] == "pending" for item in fetched.json()["items"]))
        missing = self.client.post(f"/drafts/{draft_id}/accept", json={})
        self.assertEqual(missing.status_code, 404)
        self.assertFalse(any(job["kind"] == "audit.execute" for job in self.store.jobs.values()))

    def test_unknown_project_fails_without_default_catalog(self):
        self._login("alice")
        created = self.client.post("/drafts", json={"project_id": "missing-project"})
        self.assertEqual(created.status_code, 400)
        self.assertEqual(created.json(), {"error": "unknown_project"})

    def test_explicit_rule_bind_keeps_product_and_engine_ids_and_full_checks(self):
        self._login("alice")
        catalog = self.client.get(f"/rule-versions?project_id={CATALOG_PROJECT}")
        self.assertEqual(catalog.status_code, 200)
        psw = next(item for item in catalog.json()["items"] if item["engine_rule_id"] == "零件提交保证书")
        self.assertGreaterEqual(psw["check_count"], 2)
        self.assertNotEqual(psw["id"], psw["engine_rule_id"])
        body, name = _xlsx("PSW.xlsx")
        file_id, version_id = self._ready_file(name, body)
        draft = self.client.post("/drafts", json={"project_id": CATALOG_PROJECT}).json()
        saved = self.client.put(
            f"/drafts/{draft['id']}",
            json={
                "items": [
                    {
                        "file_id": file_id,
                        "file_version_id": version_id,
                        "role": "target",
                        "product_rule_version_id": psw["id"],
                    }
                ]
            },
        )
        item = saved.json()["items"][0]
        self.assertEqual(item["product_rule_version_id"], psw["id"])
        self.assertEqual(item["engine_rule_id"], "零件提交保证书")
        self.assertEqual(item["bound_content_sha256"], psw["content_sha256"])
        self.assertEqual(item["bound_check_count"], psw["check_count"])
        self.assertEqual(item["identification_status"], "pending")

    def test_rule_publish_does_not_change_bound_snapshot(self):
        self._login("alice")
        catalog = self.client.get(f"/rule-versions?project_id={CATALOG_PROJECT}").json()["items"]
        psw = next(item for item in catalog if item["engine_rule_id"] == "零件提交保证书")
        body, name = _xlsx("PSW.xlsx")
        file_id, version_id = self._ready_file(name, body)
        draft = self.client.post("/drafts", json={"project_id": CATALOG_PROJECT}).json()
        self.client.put(
            f"/drafts/{draft['id']}",
            json={
                "items": [
                    {
                        "file_id": file_id,
                        "file_version_id": version_id,
                        "role": "target",
                        "product_rule_version_id": psw["id"],
                    }
                ]
            },
        )
        mutated = dict(psw["content"])
        mutated["checks"] = [{"id": "rule_only_one", "description": "truncated"}]
        published = self.client.post(
            "/isolated-dev/rule-versions",
            json={
                "project_id": CATALOG_PROJECT,
                "engine_rule_id": psw["engine_rule_id"],
                "document_type": psw["document_type"],
                "label": psw["label"],
                "content": mutated,
            },
        )
        self.assertEqual(published.status_code, 201)
        self.assertNotEqual(published.json()["id"], psw["id"])
        fetched = self.client.get(f"/drafts/{draft['id']}").json()["items"][0]
        self.assertEqual(fetched["product_rule_version_id"], psw["id"])
        self.assertEqual(fetched["bound_content_sha256"], psw["content_sha256"])
        self.assertEqual(fetched["bound_check_count"], psw["check_count"])
        self.assertNotEqual(fetched["bound_content_sha256"], published.json()["content_sha256"])

    def test_reference_change_keeps_other_bindings(self):
        self._login("alice")
        catalog = self.client.get(f"/rule-versions?project_id={CATALOG_PROJECT}").json()["items"]
        psw = next(item for item in catalog if item["engine_rule_id"] == "零件提交保证书")
        target = self._ready_file("PSW.xlsx", _xlsx("PSW.xlsx")[0])
        ref_a = self._ready_file("ref-a.xlsx", _xlsx("ref-a.xlsx")[0])
        ref_b = self._ready_file("ref-b.xlsx", _xlsx("ref-b.xlsx")[0])
        draft = self.client.post("/drafts", json={"project_id": CATALOG_PROJECT}).json()
        self.client.put(
            f"/drafts/{draft['id']}",
            json={
                "items": [
                    {
                        "file_id": target[0],
                        "file_version_id": target[1],
                        "role": "target",
                        "product_rule_version_id": psw["id"],
                    },
                    {"file_id": ref_a[0], "file_version_id": ref_a[1], "role": "reference"},
                ]
            },
        )
        updated = self.client.put(
            f"/drafts/{draft['id']}",
            json={
                "items": [
                    {
                        "file_id": target[0],
                        "file_version_id": target[1],
                        "role": "target",
                        "product_rule_version_id": psw["id"],
                    },
                    {"file_id": ref_b[0], "file_version_id": ref_b[1], "role": "reference"},
                ]
            },
        )
        items = updated.json()["items"]
        self.assertEqual(items[0]["product_rule_version_id"], psw["id"])
        self.assertEqual(items[0]["bound_content_sha256"], psw["content_sha256"])
        self.assertEqual(items[1]["file_version_id"], ref_b[1])
        self.assertEqual(items[1]["role"], "reference")

    def test_cross_tenant_draft_and_file_and_rule_are_denied(self):
        self._login("alice")
        body, name = _xlsx("PSW.xlsx")
        alice_file = self._ready_file(name, body)
        alice_draft = self.client.post("/drafts", json={"project_id": CATALOG_PROJECT}).json()
        alice_rules = self.client.get(f"/rule-versions?project_id={CATALOG_PROJECT}").json()["items"]
        alice_psw = next(item for item in alice_rules if item["engine_rule_id"] == "零件提交保证书")
        self._login("carol")
        denied_draft = self.client.get(f"/drafts/{alice_draft['id']}")
        self.assertEqual(denied_draft.status_code, 403)
        carol_draft = self.client.post("/drafts", json={"project_id": CATALOG_PROJECT}).json()
        stolen_file = self.client.put(
            f"/drafts/{carol_draft['id']}",
            json={"items": [{"file_id": alice_file[0], "file_version_id": alice_file[1], "role": "target"}]},
        )
        self.assertEqual(stolen_file.status_code, 403)
        stolen_rule = self.client.put(
            f"/drafts/{carol_draft['id']}",
            json={
                "items": [
                    {
                        "file_id": alice_file[0],
                        "file_version_id": alice_file[1],
                        "role": "target",
                        "product_rule_version_id": alice_psw["id"],
                    }
                ]
            },
        )
        self.assertEqual(stolen_rule.status_code, 403)
        carol_file = self._ready_file("carol.xlsx", _xlsx("carol.xlsx")[0])
        steal_rule_only = self.client.put(
            f"/drafts/{carol_draft['id']}",
            json={
                "items": [
                    {
                        "file_id": carol_file[0],
                        "file_version_id": carol_file[1],
                        "role": "target",
                        "product_rule_version_id": alice_psw["id"],
                    }
                ]
            },
        )
        self.assertEqual(steal_rule_only.status_code, 403)

    def test_identify_enqueues_worker_jobs_not_audit_execute(self):
        self._login("alice")
        file_id, version_id = self._ready_file("PSW.xlsx", _xlsx("PSW.xlsx")[0])
        draft = self.client.post("/drafts", json={"project_id": CATALOG_PROJECT}).json()
        self.client.put(
            f"/drafts/{draft['id']}",
            json={"items": [{"file_id": file_id, "file_version_id": version_id, "role": "target"}]},
        )
        queued = self.client.post(f"/drafts/{draft['id']}/identify")
        self.assertEqual(queued.status_code, 202)
        self.assertFalse(queued.json()["accepted"])
        self.assertEqual(queued.json()["jobs"][0]["kind"], "draft.identify")
        job = self.client.get(f"/isolated-dev/jobs/{queued.json()['jobs'][0]['id']}")
        self.assertEqual(job.status_code, 200)
        self.assertEqual(job.json()["kind"], "draft.identify")
        self.assertFalse(any(item["kind"] == "audit.execute" for item in self.store.jobs.values()))
        item_id = queued.json()["jobs"][0]["item_id"]
        self.store.apply_identification(
            self.store.drafts[draft["id"]]["tenant_id"],
            item_id,
            queued.json()["jobs"][0]["id"],
            status=IDENT_MATCHED,
            matched_document_type="零件提交保证书",
            evidence={"rule_profile": None, "proposed_engine_rule_id": "零件提交保证书"},
        )
        fetched = self.client.get(f"/drafts/{draft['id']}").json()["items"][0]
        self.assertEqual(fetched["identification_status"], IDENT_MATCHED)
        self.assertIsNone(fetched["product_rule_version_id"])
        self.assertIsNone(fetched["match_evidence"].get("rule_profile"))

    def test_same_tenant_without_grant_is_denied(self):
        self._login("alice")
        draft = self.client.post("/drafts", json={"project_id": CATALOG_PROJECT}).json()
        self._login("bob")
        denied = self.client.get(f"/drafts/{draft['id']}")
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
