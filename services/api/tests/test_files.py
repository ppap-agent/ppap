"""Immutable file versions. Not an S03 Live Story proof."""
from __future__ import annotations

import io
import os
import socket
import unittest
import zipfile
from unittest.mock import patch
from urllib.parse import quote, urlsplit


from fastapi.testclient import TestClient

from ppap_core.files import (
    MAX_UPLOAD_BYTES,
    ObjectExists,
    UnsafeSource,
    attachment_content_disposition,
    fetch_remote,
    original_filename,
    safe_content_type,
    sha256_bytes,
)
from ppap_core.settings import Settings
from ppap_api import create_app
from tests.integration.store_fixture import auth_store


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

    def test_strips_controls_and_keeps_unicode(self):
        self.assertEqual(original_filename("报告.xlsx"), "报告.xlsx")
        self.assertEqual(original_filename("report\r\n.xlsx"), "report.xlsx")
        with self.assertRaises(ValueError):
            original_filename("\x00\n")

    def test_disposition_is_latin1_with_rfc5987(self):
        header = attachment_content_disposition("报告.xlsx")
        header.encode("latin-1")
        self.assertIn('filename="__.xlsx"', header)
        self.assertIn("filename*=UTF-8''" + quote("报告.xlsx", safe="!#$&+-.^_`|~"), header)
        self.assertNotIn("\r", header)
        self.assertNotIn("\n", header)
        self.assertEqual(safe_content_type("application/pdf\r\nX-Injected: yes"), "application/octet-stream")
        self.assertEqual(safe_content_type("application/zip; charset=binary"), "application/zip")
        self.assertEqual(safe_content_type("not a type"), "application/octet-stream")


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

    def _public_dns(self, ip: str = "8.8.8.8", port: int = 80):
        return patch(
            "ppap_core.files.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, port))],
        )

    def test_rejects_redirect(self):
        class Http:
            def get(self, *args, **kwargs):
                class R:
                    status_code = 302
                    headers = {}
                    content = b""
                return R()

        with self._public_dns("8.8.8.8", 443):
            with self.assertRaises(UnsafeSource):
                fetch_remote("https://example.invalid/x", Http())

    def test_connects_to_resolved_ip_with_original_host_header(self):
        class Http:
            def __init__(self):
                self.url = ""
                self.headers: dict[str, str] = {}
                self.extensions = None

            def get(self, url, **kwargs):
                self.url = url
                self.headers = kwargs.get("headers") or {}
                self.extensions = kwargs.get("extensions")

                class R:
                    status_code = 200
                    headers = {"content-type": "text/plain"}
                    content = b"hello"

                return R()

        http = Http()
        with self._public_dns("8.8.8.8", 443):
            body, ctype, name = fetch_remote("https://example.invalid/file.bin", http)
        self.assertEqual(body, b"hello")
        self.assertEqual(ctype, "text/plain")
        self.assertEqual(name, "file.bin")
        host = urlsplit(http.url).hostname
        self.assertEqual(host, "8.8.8.8")
        self.assertEqual(http.headers.get("Host"), "example.invalid")
        self.assertEqual((http.extensions or {}).get("sni_hostname"), "example.invalid")

    def test_rejects_when_any_resolved_address_is_private(self):
        class Http:
            def get(self, *args, **kwargs):
                raise AssertionError("must_not_fetch")

        infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 80)),
        ]
        with patch("ppap_core.files.socket.getaddrinfo", return_value=infos):
            with self.assertRaises(UnsafeSource):
                fetch_remote("http://rebind.example/x", Http())

    def test_content_body_too_large(self):
        class Http:
            def get(self, *args, **kwargs):
                class R:
                    status_code = 200
                    headers = {}
                    content = b"z" * 20

                return R()

        with self._public_dns():
            with self.assertRaises(UnsafeSource) as ctx:
                fetch_remote("http://example.invalid/x", Http(), max_bytes=5)
        self.assertEqual(str(ctx.exception), "too_large")

    def test_streaming_enforces_byte_limit(self):
        class Http:
            def stream(self, method, url, **kwargs):
                class R:
                    status_code = 200
                    headers = {"content-type": "application/octet-stream"}

                    def iter_bytes(self):
                        yield b"x" * 8
                        yield b"y" * 8

                class CM:
                    def __enter__(self):
                        return R()

                    def __exit__(self, *args):
                        return False

                return CM()

        with self._public_dns():
            with self.assertRaises(UnsafeSource) as ctx:
                fetch_remote("http://example.invalid/x", Http(), max_bytes=10)
        self.assertEqual(str(ctx.exception), "too_large")


class FileHttpTests(unittest.TestCase):
    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        self.store = auth_store(self.settings)
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

    def test_unicode_filename_download_uses_safe_headers(self):
        self._login("alice")
        body = b"unicode-original-bytes"
        created = self.client.post(
            "/files",
            json={"original_filename": "报告.xlsx", "content_type": "application/vnd.ms-excel", "byte_size": len(body)},
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["original_filename"], "报告.xlsx")
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=body)
        self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        downloaded = self.client.get(f"/files/{file_id}/versions/{vid}/download")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, body)
        disposition = downloaded.headers["content-disposition"]
        disposition.encode("latin-1")
        downloaded.headers["content-type"].encode("latin-1")
        self.assertIn("filename*=UTF-8''" + quote("报告.xlsx", safe="!#$&+-.^_`|~"), disposition)
        self.assertIn('filename="__.xlsx"', disposition)
        self.assertEqual(downloaded.headers["x-content-sha256"], sha256_bytes(body))

    def test_control_characters_in_metadata_do_not_break_download(self):
        self._login("alice")
        body = b"header-safe-bytes"
        created = self.client.post(
            "/files",
            json={
                "original_filename": "evil\r\nname.xlsx",
                "content_type": "text/plain\r\nX-Injected: 1",
                "byte_size": len(body),
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["original_filename"], "evilname.xlsx")
        self.assertEqual(created.json()["content_type"], "application/octet-stream")
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=body)
        self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        downloaded = self.client.get(f"/files/{file_id}/versions/{vid}/download")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, body)
        disposition = downloaded.headers["content-disposition"]
        content_type = downloaded.headers["content-type"]
        disposition.encode("latin-1")
        content_type.encode("latin-1")
        self.assertNotIn("\r", disposition)
        self.assertNotIn("\n", disposition)
        self.assertNotIn("X-Injected", content_type)
        self.assertTrue(content_type.split(";")[0].strip().startswith("application/octet-stream"))


class StagingRaceTests(unittest.TestCase):
    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.store = auth_store(Settings())
        self.store.seed_isolated_dev()
        self.principal = self.store.principal_for_fixture("alice")

    def tearDown(self):
        self._env.stop()

    def test_stale_put_after_reap_does_not_write_or_succeed(self):
        version = self.store.create_staging(self.principal, "a.bin", "application/octet-stream", 4)
        self.assertEqual(self.store.reap_staging(0), 1)
        with self.assertRaises(ObjectExists):
            self.store.put_blob(version, b"data")
        self.assertNotIn(version.object_key, self.store.objects.data)
        self.assertEqual(self.store.get_version(version.file_id, version.id).status, "failed")

    def test_reap_during_object_write_deletes_orphan_and_fails(self):
        version = self.store.create_staging(self.principal, "a.bin", "application/octet-stream", 4)
        inner = self.store.objects
        store = self.store

        class ReapOnPut:
            def put_exclusive(self, key, body, content_type):
                inner.put_exclusive(key, body, content_type)
                store.reap_staging(0)

            def get(self, key):
                return inner.get(key)

            def delete(self, key):
                inner.delete(key)

        self.store.objects = ReapOnPut()
        with self.assertRaises(ObjectExists):
            self.store.put_blob(version, b"data")
        self.assertNotIn(version.object_key, inner.data)
        self.assertEqual(self.store.get_version(version.file_id, version.id).status, "failed")

    def test_second_staging_put_does_not_overwrite_bytes(self):
        version = self.store.create_staging(self.principal, "a.bin", "application/octet-stream", 3)
        first = self.store.put_blob(version, b"aaa")
        self.assertEqual(first.status, "staging")
        with self.assertRaises(ObjectExists):
            self.store.put_blob(version, b"bbb")
        self.assertEqual(self.store.objects.get(version.object_key), b"aaa")
