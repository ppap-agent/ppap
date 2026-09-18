"""Immutable file versions. Not an S03 Live Story proof."""
from __future__ import annotations

import io
import os
import socket
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "packages"), str(ROOT / "services" / "api"), str(ROOT / "services" / "worker")]

from fastapi.testclient import TestClient

from ppap_core.files import MAX_UPLOAD_BYTES, UnsafeSource, fetch_remote, original_filename, sha256_bytes
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


class FilenameTests(unittest.TestCase):
    def test_basename_only(self):
        self.assertEqual(original_filename("../secret/report.xlsx"), "report.xlsx")
        with self.assertRaises(ValueError):
            original_filename("..")


class SsrfTests(unittest.TestCase):
    def test_rejects_loopback_and_file(self):
        class Http:
            def get(self, *args, **kwargs):
                raise AssertionError("must_not_fetch")

        with self.assertRaises(UnsafeSource):
            fetch_remote("file:///etc/passwd", Http())
        with self.assertRaises(UnsafeSource):
            fetch_remote("http://127.0.0.1/x", Http())
        with self.assertRaises(UnsafeSource):
            fetch_remote("http://user:pass@example.com/x", Http())

    def test_rejects_redirect(self):
        class Http:
            def get(self, *args, **kwargs):
                class R:
                    status_code = 302
                    headers = {}
                    content = b""
                return R()

        with patch("ppap_core.files.host_blocked", return_value=False):
            with patch("ppap_core.files.socket.getaddrinfo", return_value=[(socket.AF_INET, 0, 0, "", ("8.8.8.8", 80))]):
                with self.assertRaises(UnsafeSource):
                    fetch_remote("https://example.invalid/x", Http())


class FileHttpTests(unittest.TestCase):
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

    def test_same_name_different_bytes_keep_history(self):
        self._login("alice")
        first = b"version-one-bytes"
        created = self.client.post(
            "/files",
            json={"original_filename": "report.xlsx", "content_type": "application/vnd.ms-excel", "byte_size": len(first)},
        )
        self.assertEqual(created.status_code, 201)
        file_id = created.json()["file_id"]
        v1 = created.json()["id"]
        put = self.client.put(f"/files/{file_id}/versions/{v1}/blob", content=first)
        self.assertEqual(put.status_code, 200)
        ready = self.client.post(f"/files/{file_id}/versions/{v1}/finalize")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["sha256"], sha256_bytes(first))
        self.assertEqual(ready.json()["status"], "ready")
        second = b"version-two-different"
        again = self.client.post(
            f"/files/{file_id}/versions",
            json={"original_filename": "report.xlsx", "content_type": "application/vnd.ms-excel", "byte_size": len(second)},
        )
        v2 = again.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{v2}/blob", content=second)
        later = self.client.post(f"/files/{file_id}/versions/{v2}/finalize")
        self.assertEqual(later.json()["sha256"], sha256_bytes(second))
        old = self.client.get(f"/files/{file_id}/versions/{v1}/download")
        new = self.client.get(f"/files/{file_id}/versions/{v2}/download")
        self.assertEqual(old.content, first)
        self.assertEqual(new.content, second)
        self.assertEqual(old.headers["x-content-sha256"], sha256_bytes(first))
        replay = self.client.post(f"/files/{file_id}/versions/{v1}/finalize")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["sha256"], sha256_bytes(first))
        overwrite = self.client.put(f"/files/{file_id}/versions/{v1}/blob", content=b"nope")
        self.assertEqual(overwrite.status_code, 409)

    def test_cross_tenant_download_denied(self):
        self._login("alice")
        body = b"alice-secret"
        created = self.client.post("/files", json={"original_filename": "a.bin", "content_type": "application/octet-stream", "byte_size": len(body)})
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=body)
        self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        self._login("carol")
        denied = self.client.get(f"/files/{file_id}/versions/{vid}/download")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json(), {"error": "access_denied"})
        self.assertNotIn(b"alice-secret", denied.content)

    def test_staging_is_not_downloadable(self):
        self._login("alice")
        created = self.client.post("/files", json={"original_filename": "a.bin", "content_type": "application/octet-stream", "byte_size": 4})
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=b"wait")
        blocked = self.client.get(f"/files/{file_id}/versions/{vid}/download")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"], "not_ready")

    def test_from_url_rejects_private_hosts(self):
        self._login("alice")
        response = self.client.post("/files/from-url", json={"url": "http://127.0.0.1/secret"})
        self.assertEqual(response.status_code, 400)
        self.assertIn(response.json()["error"], {"blocked_host", "invalid_url", "unsafe_source"})

    def test_unauthenticated_upload_rejected(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/files", json={"original_filename": "a.bin", "byte_size": 1}).status_code, 401)

    def test_declared_size_and_extension_are_not_identity(self):
        self._login("alice")
        too_big = self.client.post(
            "/files",
            json={"original_filename": "a.bin", "content_type": "application/octet-stream", "byte_size": MAX_UPLOAD_BYTES + 1},
        )
        self.assertEqual(too_big.status_code, 400)
        payload = b"not-an-excel-workbook"
        created = self.client.post(
            "/files",
            json={"original_filename": "report.xlsx", "content_type": "application/octet-stream", "byte_size": len(payload)},
        )
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=payload)
        ready = self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        self.assertEqual(ready.json()["content_type"], "application/octet-stream")
        self.assertEqual(ready.json()["sha256"], sha256_bytes(payload))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("inner.txt", "do-not-extract")
        zipped_bytes = archive.getvalue()
        zipped_meta = self.client.post(
            "/files",
            json={"original_filename": "bundle.zip", "content_type": "application/zip", "byte_size": len(zipped_bytes)},
        )
        zid, zvid = zipped_meta.json()["file_id"], zipped_meta.json()["id"]
        self.client.put(f"/files/{zid}/versions/{zvid}/blob", content=zipped_bytes)
        zready = self.client.post(f"/files/{zid}/versions/{zvid}/finalize")
        downloaded = self.client.get(f"/files/{zid}/versions/{zvid}/download")
        self.assertEqual(zready.json()["content_type"], "application/zip")
        self.assertEqual(downloaded.content, zipped_bytes)

    def test_wrong_sha_and_reap_leave_ready_history(self):
        self._login("alice")
        first = b"keep-me"
        created = self.client.post(
            "/files", json={"original_filename": "a.bin", "content_type": "application/octet-stream", "byte_size": len(first)}
        )
        file_id, v1 = created.json()["file_id"], created.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{v1}/blob", content=first)
        self.client.post(f"/files/{file_id}/versions/{v1}/finalize")
        staging = self.client.post(
            f"/files/{file_id}/versions",
            json={"original_filename": "a.bin", "content_type": "application/octet-stream", "byte_size": 4},
        )
        v2 = staging.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{v2}/blob", content=b"temp")
        mismatch = self.client.post(
            f"/files/{file_id}/versions/{v2}/finalize", json={"sha256": "0" * 64}
        )
        self.assertEqual(mismatch.status_code, 409)
        self.assertEqual(mismatch.json()["error"], "sha256_mismatch")
        leftover = self.client.post(
            f"/files/{file_id}/versions",
            json={"original_filename": "a.bin", "content_type": "application/octet-stream", "byte_size": 1},
        )
        self.client.put(f"/files/{file_id}/versions/{leftover.json()['id']}/blob", content=b"x")
        reaped = self.client.post("/isolated-dev/reap-staging", json={"max_age_seconds": 0})
        self.assertEqual(reaped.status_code, 200)
        self.assertGreaterEqual(reaped.json()["reaped"], 1)
        old = self.client.get(f"/files/{file_id}/versions/{v1}/download")
        self.assertEqual(old.content, first)
        abandoned = self.client.get(f"/files/{file_id}/versions/{leftover.json()['id']}/download")
        self.assertEqual(abandoned.status_code, 409)
