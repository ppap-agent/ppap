import io
import unittest
import zipfile


from ppap_core.bundles import archive_member_names
from ppap_core.fixtures import RULE_A
from ppap_core.settings import Settings
from fastapi.testclient import TestClient
from ppap_api import create_app
from unittest.mock import patch
import os


class ArchiveNameTests(unittest.TestCase):
    def test_duplicate_names_keep_version_prefix(self):
        names = archive_member_names([
            ("aaaaaaaa-1111-4000-8000-000000000001", "report.xlsx"),
            ("bbbbbbbb-2222-4000-8000-000000000002", "report.xlsx"),
        ])
        self.assertEqual(names[0], "report.xlsx")
        self.assertEqual(names[1], "report-bbbbbbbb.xlsx")
        self.assertEqual(len(set(names)), 2)


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class RoundBundleTests(unittest.TestCase):
    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store))

    def tearDown(self):
        self._env.stop()

    def test_bundle_downloads_frozen_round_inputs(self):
        self.assertEqual(self.client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code, 200)
        meta = {"original_filename": "report.xlsx", "content_type": "application/octet-stream", "byte_size": 12}
        created = self.client.post("/files", json=meta).json()
        self.client.put(f"/files/{created['file_id']}/versions/{created['id']}/blob", content=b"frozen-bytes")
        ready = self.client.post(f"/files/{created['file_id']}/versions/{created['id']}/finalize").json()
        ref_meta = {"original_filename": "context.pdf", "content_type": "application/octet-stream", "byte_size": 3}
        ref = self.client.post("/files", json=ref_meta).json()
        self.client.put(f"/files/{ref['file_id']}/versions/{ref['id']}/blob", content=b"ref")
        ref_ready = self.client.post(f"/files/{ref['file_id']}/versions/{ref['id']}/finalize").json()
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(f"/drafts/{draft}", json={"items": [
            {"file_version_id": ready["id"], "role": "target", "rule_version_id": RULE_A},
            {"file_version_id": ref_ready["id"], "role": "reference"},
        ]})
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "bundle-1"}).json()["id"]
        response = self.client.get(f"/rounds/{round_id}/bundle")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(set(archive.namelist()), {"report.xlsx", "context.pdf"})
        self.assertEqual(archive.read("report.xlsx"), b"frozen-bytes")
        self.assertEqual(self.client.post("/isolated-dev/sessions", json={"subject": "carol"}).status_code, 200)
        self.assertEqual(self.client.get(f"/rounds/{round_id}/bundle").status_code, 403)
