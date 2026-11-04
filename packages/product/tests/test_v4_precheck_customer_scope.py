"""Auto recognition may expose a conflict, but must never form a mixed customer pair."""

from types import SimpleNamespace
from uuid import uuid4

from audit.presets import preset_rule_body
from product.prechecks import frozen_precheck_input
from product.settings import Settings
from worker.prechecks import execute_precheck


def test_mixed_customer_snapshot_does_not_read_or_pair_any_original():
    settings = Settings(usage_charge_enabled=False)
    files = []
    for customer, entry in [("蔚来", "N04"), ("理想", "L12")]:
        files.append(
            {
                "file": {"id": str(uuid4()), "file_id": str(uuid4()), "original_filename": "特殊特性清单.xlsx", "sha256": "a" * 64},
                "selection": "selected",
                "match": {"state": "matched", "rule": {"id": str(uuid4()), "title": entry, "body": preset_rule_body(customer, entry)}},
            }
        )
    task = {"id": str(uuid4()), "files": files, "customer_preference": None, "input_revision": 0, "analysis_mode": "standard"}
    payload = frozen_precheck_input(task, str(uuid4()), settings.standard_execution_policy().model_dump(mode="json"))
    assert payload["customer_conflict"] is True

    def no_original(*args, **kwargs):
        raise AssertionError("mixed customers must not form a shared original context")

    store = SimpleNamespace(settings=settings, get_version=no_original, version_bytes=no_original)
    result = execute_precheck(store, payload)
    # Older frozen payloads must also be safe if the derived flag was absent.
    legacy = dict(payload)
    legacy.pop("customer_conflict")
    assert execute_precheck(store, legacy)["files"] == result["files"]
    assert len(result["files"]) == 2
    for row in result["files"]:
        assert row["facts"][0]["status"] == "unknown" and row["facts"][0]["code"] == "B03"
        assert row["facts"][0]["critical"] is False and row["facts"][0]["certified"] is False
        assert "拆分" in row["facts"][0]["message"]
