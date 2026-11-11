"""Worker job dispatch. Does not prove a full product audit round."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import worker
from worker import run_claimed_job


class ClaimedJobTests(unittest.TestCase):
    def test_non_audit_jobs_do_not_import_the_engine(self):
        with patch.dict(os.environ, {}, clear=False):
            result = run_claimed_job("heartbeat", {})
        self.assertEqual(result, {"error": "unsupported_job_kind", "note": "unsupported_job_kind"})


class WorkerLoopTests(unittest.TestCase):
    def test_run_closes_heartbeat_connection_before_claiming_and_processing(self):
        events = []
        settings = SimpleNamespace(worker_id="test-worker", git_sha="a" * 40)
        claimed = SimpleNamespace(kind="file_validation")

        @contextmanager
        def connect(*_args):
            events.append("connection_open")
            try:
                yield SimpleNamespace(execute=lambda *_args: events.append("heartbeat"))
            finally:
                events.append("connection_closed")

        def claim_job(ident):
            events.append("claim")
            return claimed

        def process_claimed(store, job, ident):
            self.assertIs(job, claimed)
            self.assertEqual(ident, settings.worker_id)
            events.append("process")
            worker.STOP = True

        with (
            patch.object(worker, "STOP", False),
            patch.object(worker.signal, "signal"),
            patch.object(worker, "get_settings", return_value=settings),
            patch.object(worker, "configure_logs"),
            patch.object(worker, "build_product_store", return_value=SimpleNamespace(claim_job=claim_job)),
            patch.object(worker, "connect", side_effect=connect),
            patch.object(worker, "process_claimed", side_effect=process_claimed),
        ):
            worker.run()

        self.assertEqual(events, ["connection_open", "heartbeat", "connection_closed", "claim", "process"])
