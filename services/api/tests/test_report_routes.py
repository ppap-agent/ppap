import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tests.pathsetup  # noqa: F401
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ppap_api.report_routes import register_report_routes
from ppap_core.feishu_writeback import FeishuWritebackError


class ReportRouteTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.claim_report.return_value = 'claim'
        self.store.get_report.return_value = {'status': 'ready', 'document_id': 'doc123'}
        session = SimpleNamespace(principal=SimpleNamespace(user_id='user'), access_token='test-token')
        app = FastAPI()
        register_report_routes(app, self.store, lambda request: session, Mock(), Mock())
        self.client = TestClient(app)
        for name, value in [('can_read', True), ('report_blocks', [{'block_type': 2}]), ('round_return_url', 'https://example.test'), ('create_report_document', 'doc123'), ('append_report_blocks', None)]:
            patcher = patch('ppap_api.report_routes.'+name, return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def test_creates_and_persists_document_before_writing(self):
        response = self.client.post('/integrations/feishu/rounds/round1/report')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([call.args[2:] for call in self.store.settle_report.call_args_list], [('writing', 'doc123'), ('ready',)])
        self.create_report_document.assert_called_once()

    def test_existing_claim_does_not_create_again(self):
        self.store.claim_report.return_value = None
        self.client.post('/integrations/feishu/rounds/round1/report')
        self.create_report_document.assert_not_called()

    def test_unknown_preserves_record_without_retry(self):
        self.create_report_document.side_effect = FeishuWritebackError('feishu_report_create_unknown')
        self.client.post('/integrations/feishu/rounds/round1/report')
        self.assertEqual(self.store.settle_report.call_args.args[2], 'unknown')
        self.create_report_document.assert_called_once()
        self.append_report_blocks.assert_not_called()

    def test_only_definite_creation_rejection_allows_explicit_retry(self):
        for code, status in [('feishu_report_create_rejected:99991672', 'failed'),
                             ('feishu_report_write_rejected:99991672', 'unknown')]:
            with self.subTest(code=code):
                self.create_report_document.side_effect = FeishuWritebackError(code)
                self.client.post('/integrations/feishu/rounds/round1/report')
                self.assertEqual(self.store.settle_report.call_args.args[2], status)
                self.assertEqual(self.store.settle_report.call_args.kwargs['error_code'], code)

    def test_denied_object_cannot_export(self):
        self.can_read.return_value = False
        self.assertEqual(self.client.post('/integrations/feishu/rounds/round1/report').status_code, 403)
        self.store.claim_report.assert_not_called()
