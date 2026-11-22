"""Worker job dispatch. Does not prove a full product audit round."""

from __future__ import annotations

from types import SimpleNamespace

from worker import _process_claimed


class Store:
    def __init__(self):
        self.completed = []

    def complete_job(self, claimed, ident, result, outcome):
        self.completed.append((result, outcome))
        return True


def test_unknown_job_kind_fails_without_running():
    store = Store()
    claimed = SimpleNamespace(id="job", attempt=1, round_id=None, tenant_id=None, kind="heartbeat", payload={})
    result = _process_claimed(store, claimed, "worker")
    assert result["error"] == "unsupported_job_kind"
    assert store.completed == [(result, "failed")]
