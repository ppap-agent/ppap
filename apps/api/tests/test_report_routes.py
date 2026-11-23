import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api.report_routes import register_report_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product.feishu_writeback import FeishuWritebackError

PDFS = {"v1": {"report_id": "r1", "filename": "a-审核报告.pdf", "fingerprint": "0123456789ab"}}
BODY = {"source_draft_id": "t1", "inputs": [{"role": "target", "file_version_id": "v1"}, {"role": "reference", "file_version_id": "v2"}]}


class Routes(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.claim_report.return_value = "claim"
        self.store.get_report.return_value = {"status": "ready", "document_id": "doc123"}
        self.store.round_task.return_value = None
        self.store.get_round.return_value = BODY
        self.store.round_report_pdfs.return_value = PDFS
        self.store.report_export.return_value = {"id": "claim", "status": "ready", "document_id": "doc123"}
        self.store.lease_report.return_value = {
            "lease": "lease1",
            "status": "ready",
            "document_id": "doc123",
            "read_only": "failed",
            "attachments": [{"report_id": "r1", "fingerprint": "0123456789ab", "state": "failed"}],
        }
        self.session = SimpleNamespace(principal=SimpleNamespace(user_id="user"), access_token="test-token")
        app = FastAPI()
        settings = Mock()
        settings.canonical_web_url.return_value = "https://ppap.example"
        register_report_routes(app, self.store, lambda request: self.session, Mock(), settings)
        self.client = TestClient(app)
        for name, value in [
            ("can_read", True),
            ("writeback_state", "ready"),
            ("report_blocks", [{"block_type": 2}]),
            ("round_return_url", "https://example.test"),
            ("create_report_document", "doc123"),
            ("append_report_blocks", None),
            ("make_read_only", ("set", None)),
            ("attach_report_pdfs", []),
        ]:  # noqa: E501
            patcher = patch(("api.access." if name == "can_read" else "api.report_routes.") + name, return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)


class ReportRouteTests(Routes):
    def test_creates_and_persists_document_before_writing(self):
        response = self.client.post("/integrations/feishu/rounds/round1/report")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([call.args[2:] for call in self.store.settle_report.call_args_list], [("writing", "doc123"), ("ready",)])
        self.create_report_document.assert_called_once()

    def test_the_document_names_and_attaches_every_audited_files_pdf(self):
        """#359: each audited file's frozen PDF, linked to its report page; the claim records what it attaches."""
        self.client.post("/integrations/feishu/rounds/round1/report")
        (original,) = self.report_blocks.call_args.args[3]
        self.assertEqual(original, {**PDFS["v1"], "url": "https://ppap.example/tasks/t1?report=v1"})
        (item,) = self.store.claim_report.call_args.kwargs["attachments"]
        self.assertEqual((item["report_id"], item["fingerprint"], item["state"]), ("r1", "0123456789ab", "pending"))
        self.attach_report_pdfs.assert_called_once()
        self.assertEqual(self.attach_report_pdfs.call_args.args[2:4], ("doc123", [item]))
        # The delivery is released once done.
        self.store.release_report.assert_called_with(self.session.principal, "claim", "claim")

    def test_no_document_without_every_pdf(self):
        self.store.round_report_pdfs.return_value = {}
        for method in ("get", "post"):
            response = getattr(self.client, method)("/integrations/feishu/rounds/round1/report")
            self.assertEqual((response.status_code, response.json()), (409, {"error": "report_pdf_not_ready"}))
        self.store.claim_report.assert_not_called()

    def test_a_document_not_set_read_only_is_still_delivered_and_says_so(self):
        self.make_read_only.return_value = ("failed", "feishu_report_permission_rejected:99991679")
        response = self.client.post("/integrations/feishu/rounds/round1/report")
        self.assertEqual(response.status_code, 200)
        self.store.save_report_step.assert_any_call(self.session.principal, "claim", "claim", read_only=("failed", "feishu_report_permission_rejected:99991679"))
        self.assertEqual([call.args[2:] for call in self.store.settle_report.call_args_list], [("writing", "doc123"), ("ready",)])
        self.append_report_blocks.assert_called_once()
        self.attach_report_pdfs.assert_called_once()

    def test_existing_claim_does_not_create_again(self):
        self.store.claim_report.return_value = None
        self.client.post("/integrations/feishu/rounds/round1/report")
        self.create_report_document.assert_not_called()

    def test_unknown_preserves_record_without_retry(self):
        self.create_report_document.side_effect = FeishuWritebackError("feishu_report_create_unknown")
        self.client.post("/integrations/feishu/rounds/round1/report")
        self.assertEqual(self.store.settle_report.call_args.args[2], "unknown")
        self.create_report_document.assert_called_once()
        self.append_report_blocks.assert_not_called()
        self.attach_report_pdfs.assert_not_called()
        self.store.release_report.assert_called_once()

    def test_only_definite_creation_rejection_allows_explicit_retry(self):
        for code, status in [("feishu_report_create_rejected:99991672", "failed"), ("feishu_report_write_rejected:99991672", "unknown")]:
            with self.subTest(code=code):
                self.create_report_document.side_effect = FeishuWritebackError(code)
                self.client.post("/integrations/feishu/rounds/round1/report")
                self.assertEqual(self.store.settle_report.call_args.args[2], status)
                self.assertEqual(self.store.settle_report.call_args.kwargs["error_code"], code)

    def test_denied_object_cannot_export(self):
        self.can_read.return_value = False
        self.assertEqual(self.client.post("/integrations/feishu/rounds/round1/report").status_code, 403)
        self.store.claim_report.assert_not_called()


class ReportRetryTests(Routes):
    path = "/integrations/feishu/rounds/round1/report/retry"

    def test_retrying_read_only_sets_it_under_a_lease_and_releases_it(self):
        response = self.client.post(self.path, json={"report_id": "claim", "step": "read_only"})
        self.assertEqual((response.status_code, response.json()), (200, {"report": {"id": "claim", "status": "ready", "document_id": "doc123"}}))
        self.make_read_only.assert_called_once()
        self.assertEqual(self.make_read_only.call_args.args[1:], ("test-token", "doc123"))
        self.store.save_report_step.assert_called_once_with(self.session.principal, "claim", "lease1", read_only=("set", None))
        self.store.release_report.assert_called_once_with(self.session.principal, "claim", "lease1")
        self.attach_report_pdfs.assert_not_called()

    def test_retrying_attachments_continues_from_the_record(self):
        self.client.post(self.path, json={"report_id": "claim", "step": "attachments"})
        self.assertEqual(self.attach_report_pdfs.call_args.args[2:4], ("doc123", self.store.lease_report.return_value["attachments"]))
        self.make_read_only.assert_not_called()
        self.store.release_report.assert_called_once_with(self.session.principal, "claim", "lease1")

    def test_only_the_deliverer_retries_and_never_while_it_is_held(self):
        self.store.report_export.return_value = None
        refused = self.client.post(self.path, json={"report_id": "claim", "step": "read_only"})
        self.assertEqual((refused.status_code, refused.json()), (403, {"error": "access_denied"}))
        self.store.report_export.return_value = {"id": "claim"}
        self.store.lease_report.return_value = None
        held = self.client.post(self.path, json={"report_id": "claim", "step": "read_only"})
        self.assertEqual((held.status_code, held.json()), (409, {"error": "report_retry_unavailable"}))
        for body in ({}, {"report_id": "claim", "step": "delete"}, {"report_id": 1, "step": "read_only"}):
            self.assertEqual(self.client.post(self.path, json=body).status_code, 400)
        self.make_read_only.assert_not_called()
        self.attach_report_pdfs.assert_not_called()

    def test_retry_needs_the_persons_feishu_token(self):
        self.session.access_token = ""
        response = self.client.post(self.path, json={"report_id": "claim", "step": "read_only"})
        self.assertEqual((response.status_code, response.json()), (409, {"error": "feishu_user_token_missing"}))
        self.store.lease_report.assert_not_called()
