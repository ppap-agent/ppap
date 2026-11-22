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


def test_recognition_without_its_task_fails_without_reading_rules_or_calling_a_model():
    """A recognition job in the old unscoped shape (no task, revision or frozen candidates) is never run."""

    class Untouched(Store):
        def renew_job_lease(self, claimed, ident):
            return True

        def __getattr__(self, name):
            raise AssertionError(f"store.{name} must not be used")

    def model(*_args, **_kwargs):
        raise AssertionError("no model call")

    for payload in (
        {"source_file_id": "f", "source_file_version_id": "v", "customer_preference": None},
        {"source_file_id": "f", "source_file_version_id": "v", "task_id": "t", "task_revision": 0},
    ):
        store = Untouched()
        claimed = SimpleNamespace(id="job", attempt=1, round_id=None, tenant_id="tenant", kind="file_analysis", payload=payload)
        result = _process_claimed(store, claimed, "worker", rule_ai=model)
        assert result == {"error": "recognition_task_required"}
        assert store.completed == [(result, "failed")]
