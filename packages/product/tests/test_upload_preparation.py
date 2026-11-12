"""Pure upload-intent validation; PG and upload receipts have separate owners."""

from copy import deepcopy
from uuid import uuid4

import pytest
from product.store.upload_preparation import normalize_preparation, preparation_inputs, validate_preparation_update


def preparation():
    return {
        "version": 1,
        "customer_preference": None,
        "predecessor_id": None,
        "uploads": [{"request_id": str(uuid4()), "kind": "local", "name": "控制计划.xlsx", "byte_size": 8, "sha256": "a" * 64, "removed": False}],
        "files": [{"file_version_id": str(uuid4()), "validation_id": None, "selection": "selected", "removal_reason": None}],
    }


def test_preparation_retains_unknown_upload_and_ready_selection_without_inventing_a_receipt():
    raw = preparation()
    fixed = deepcopy(raw)
    saved = normalize_preparation(raw)
    assert saved == fixed and raw == fixed
    assert "state" not in saved["uploads"][0]
    assert preparation_inputs(saved) == {"files": fixed["files"], "customer_preference": None}
    selected = preparation_inputs(saved)
    selected["files"].clear()
    assert saved == fixed


@pytest.mark.parametrize("fault", ["request", "digest", "bool_size", "negative_size", "duplicate_request", "duplicate_file", "reason", "reference", "version"])
def test_preparation_rejects_invalid_or_ambiguous_original_identity(fault):
    raw = preparation()
    if fault == "request":
        raw["uploads"][0]["request_id"] = "not-a-uuid"
    elif fault == "digest":
        raw["uploads"][0]["sha256"] = "not-a-digest"
    elif fault == "bool_size":
        raw["uploads"][0]["byte_size"] = True
    elif fault == "negative_size":
        raw["uploads"][0]["byte_size"] = -1
    elif fault == "duplicate_request":
        raw["uploads"].append(deepcopy(raw["uploads"][0]))
    elif fault == "duplicate_file":
        raw["files"].append(deepcopy(raw["files"][0]))
    elif fault == "reason":
        raw["files"][0]["selection"] = "removed"
    elif fault == "reference":
        raw["files"][0]["reference_only"] = "true"
    else:
        raw["version"] = True
    with pytest.raises(ValueError, match="^invalid_upload_preparation$"):
        normalize_preparation(raw)


@pytest.mark.parametrize("fault", ["drop_request", "change_digest", "change_folder", "drop_ready_file"])
def test_saved_preparation_cannot_erase_or_rebind_original_upload_identity(fault):
    previous = normalize_preparation(preparation())
    current = deepcopy(previous)
    if fault == "drop_request":
        current["uploads"].clear()
    elif fault == "change_digest":
        current["uploads"][0]["sha256"] = "b" * 64
    elif fault == "change_folder":
        current["uploads"][0]["folder"] = {"token": "another-folder", "name": "其他目录"}
    else:
        current["files"].clear()
    with pytest.raises(ValueError, match="^upload_preparation_identity_conflict$"):
        validate_preparation_update(previous, current)


def test_explicit_removal_and_later_validation_preserve_the_rest_of_the_batch():
    previous = normalize_preparation(preparation())
    current = deepcopy(previous)
    current["uploads"][0]["removed"] = True
    current["files"][0].update(selection="removed", removal_reason="用户移除")
    current["files"].append({"file_version_id": str(uuid4()), "validation_id": str(uuid4()), "selection": "selected", "removal_reason": None})
    normalized = normalize_preparation(current)
    validate_preparation_update(previous, normalized)
    assert len(normalized["files"]) == 2
    assert normalized["uploads"][0]["request_id"] == previous["uploads"][0]["request_id"]


@pytest.mark.parametrize("reason", ["用户移除", " 用户移除 \t"])
def test_removal_reason_has_the_same_canonical_form_as_recognition(reason):
    raw = preparation()
    raw["files"][0].update(selection="removed", removal_reason=reason)
    saved = normalize_preparation(raw)
    assert saved["files"][0]["removal_reason"] == "用户移除"
    assert preparation_inputs(saved)["files"][0]["removal_reason"] == "用户移除"
    assert raw["files"][0]["removal_reason"] == reason


def test_feishu_intent_preserves_source_and_same_request_without_claiming_it_was_imported():
    raw = preparation()
    raw["uploads"] = [{"request_id": str(uuid4()), "kind": "feishu", "name": "云端控制计划", "drive_file": {"token": "original-doc", "name": "云端控制计划", "type": "sheet"}, "removed": False}]
    assert normalize_preparation(raw)["uploads"] == raw["uploads"]


def test_a_sent_mode_key_is_ignored():
    raw = preparation()
    legacy = {**deepcopy(raw), "analysis_mode": "deep"}
    assert normalize_preparation(legacy) == normalize_preparation(raw)
    assert "analysis_mode" not in preparation_inputs(legacy)
