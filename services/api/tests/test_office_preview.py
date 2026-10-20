import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from ppap_api.office_routes import register_office_routes
from ppap_core.logs import RedactQuery
from ppap_core.office_preview import preview_config, read_ticket


class OfficePreviewTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            onlyoffice_url="http://127.0.0.1:8082", onlyoffice_api_url="http://api:8080", onlyoffice_secret="development-test-secret-not-real-1234", public_api_url="http://127.0.0.1:8080"
        )
        self.version = SimpleNamespace(id="version1", file_id="file1", tenant_id="tenant1", original_filename="审核.xlsx", content_type="application/octet-stream", status="ready")

    def ticket(self):
        config = preview_config(self.settings, self.version)
        return parse_qs(urlsplit(config["config"]["document"]["url"]).query)["ticket"][0]

    def test_signed_readonly_config_without_storage_or_login_credentials(self):
        config = preview_config(self.settings, self.version)
        self.assertEqual(config["config"]["editorConfig"]["mode"], "view")
        self.assertFalse(config["config"]["document"]["permissions"]["edit"])
        self.assertFalse(config["config"]["document"]["permissions"]["chat"])
        self.assertFalse(config["config"]["editorConfig"]["customization"]["anonymous"]["request"])
        self.assertFalse(config["config"]["editorConfig"]["customization"]["macros"])
        self.assertEqual(config["config"]["documentType"], "cell")
        self.assertNotIn(self.settings.onlyoffice_secret, str(config))
        claims = read_ticket(self.ticket(), self.settings.onlyoffice_secret)
        self.assertEqual(claims["version_id"], "version1")
        self.assertEqual(claims["tenant_id"], "tenant1")
        self.assertNotIn("callbackUrl", config["config"]["editorConfig"])

    def test_expired_tampered_and_editor_tokens_are_not_file_tickets(self):
        with patch("ppap_core.office_preview.time.time", return_value=1000):
            ticket = self.ticket()
        with patch("ppap_core.office_preview.time.time", return_value=1601), self.assertRaises(ValueError):
            read_ticket(ticket, self.settings.onlyoffice_secret)
        for token in ("bad", self.ticket() + "x", preview_config(self.settings, self.version)["config"]["token"]):
            with self.assertRaises(ValueError):
                read_ticket(token, self.settings.onlyoffice_secret)

    def test_file_scope_and_authorization(self):
        store = Mock()
        store.get_version.return_value = self.version
        store.read_file_version.return_value = self.version
        store.version_bytes.return_value = b"file-content"
        principal = SimpleNamespace(tenant_id="tenant1", user_id="user1")
        app = FastAPI()
        register_office_routes(app, store, lambda request: principal, self.settings)
        client = TestClient(app)
        with patch.object(store, "read_file_version", side_effect=KeyError("missing")):
            self.assertEqual(client.get("/files/file1/versions/version1/office").status_code, 403)
        self.assertEqual(client.get("/files/file1/versions/version1/office?round_id=round1").status_code, 200)
        store.read_file_version.assert_called_with(principal, "file1", "version1", round_id="round1")
        store.resource.assert_not_called()
        store.grants.assert_not_called()
        token = self.ticket()
        response = client.get("/office/content", params={"ticket": token})
        self.assertEqual(response.content, b"file-content")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.version.tenant_id = "tenant2"
        self.assertEqual(client.get("/office/content", params={"ticket": token}).status_code, 403)

    def test_unconfigured_service_has_explicit_fallback(self):
        self.settings.onlyoffice_secret = ""
        self.assertFalse(preview_config(self.settings, self.version)["available"])

    def test_access_logs_strip_query_capabilities(self):
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s", ("client", "GET", "/office/content?ticket=private", "1.1", 200), None)
        RedactQuery().filter(record)
        self.assertNotIn("private", record.getMessage())
