"""Native upload intent stored inside the existing private Draft context.

This is not an upload receipt. File transfer and validation keep their existing
authorities; the Draft only retains the original identities and user choices.
"""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy

from product.files import MAX_UPLOAD_BYTES

PREPARATION_KEY = "_native_upload_preparation"


def _uuid(value) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_upload_preparation") from None


def _text(value, maximum=500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("invalid_upload_preparation")
    return value


def normalize_preparation(value) -> dict:
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("invalid_upload_preparation")
    customer = value.get("customer_preference")
    if customer is not None:
        _text(customer)
    predecessor = value.get("predecessor_id")
    if predecessor is not None:
        predecessor = _uuid(predecessor)
    uploads, files = value.get("uploads"), value.get("files")
    if not isinstance(uploads, list) or not isinstance(files, list):
        raise ValueError("invalid_upload_preparation")
    normalized_uploads, normalized_files = [], []
    for raw in uploads:
        if not isinstance(raw, dict) or raw.get("kind") not in ("local", "feishu") or type(raw.get("removed")) is not bool:
            raise ValueError("invalid_upload_preparation")
        row = {"request_id": _uuid(raw.get("request_id")), "kind": raw["kind"], "name": _text(raw.get("name")), "removed": raw["removed"]}
        if raw["kind"] == "local":
            size, digest = raw.get("byte_size"), raw.get("sha256")
            if type(size) is not int or not 0 <= size <= MAX_UPLOAD_BYTES or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid_upload_preparation")
            row.update(byte_size=size, sha256=digest)
            if raw.get("content_type") is not None:
                row["content_type"] = _text(raw["content_type"], 255)
            if raw.get("folder") is not None:
                folder = raw["folder"]
                if not isinstance(folder, dict):
                    raise ValueError("invalid_upload_preparation")
                row["folder"] = {"token": _text(folder.get("token")), "name": _text(folder.get("name"))}
        else:
            source = raw.get("drive_file")
            if not isinstance(source, dict):
                raise ValueError("invalid_upload_preparation")
            row["drive_file"] = {key: _text(source.get(key)) for key in ("token", "name", "type")}
        normalized_uploads.append(row)
    for raw in files:
        if not isinstance(raw, dict) or raw.get("selection") not in ("selected", "unselected", "removed"):
            raise ValueError("invalid_upload_preparation")
        reason = raw.get("removal_reason")
        if raw["selection"] == "removed":
            reason = _text(reason).strip()
        elif reason is not None:
            raise ValueError("invalid_upload_preparation")
        row = {
            "file_version_id": _uuid(raw.get("file_version_id")),
            "validation_id": _uuid(raw["validation_id"]) if raw.get("validation_id") is not None else None,
            "selection": raw["selection"],
            "removal_reason": reason,
        }
        for key in ("preferred_rule_version_id", "replacement_of_file_version_id"):
            if raw.get(key) is not None:
                row[key] = _uuid(raw[key])
        if raw.get("reference_only") is not None:
            if type(raw["reference_only"]) is not bool:
                raise ValueError("invalid_upload_preparation")
            if raw["reference_only"]:
                row["reference_only"] = True
        normalized_files.append(row)
    if len({row["request_id"] for row in normalized_uploads}) != len(normalized_uploads) or len({row["file_version_id"] for row in normalized_files}) != len(normalized_files):
        raise ValueError("invalid_upload_preparation")
    result = {"version": 1, "customer_preference": customer, "predecessor_id": predecessor, "uploads": normalized_uploads, "files": normalized_files}
    if len(json.dumps(result, ensure_ascii=False).encode()) > 262144:
        raise ValueError("invalid_upload_preparation")
    return result


def validate_preparation_update(previous: dict, current: dict) -> None:
    """A lost response must not erase the identity needed for its readback."""
    uploads = {row["request_id"]: row for row in current["uploads"]}
    for old in previous["uploads"]:
        new = uploads.get(old["request_id"])
        if new is None or {key: value for key, value in old.items() if key != "removed"} != {key: value for key, value in new.items() if key != "removed"}:
            raise ValueError("upload_preparation_identity_conflict")
    if not {row["file_version_id"] for row in previous["files"]}.issubset({row["file_version_id"] for row in current["files"]}):
        raise ValueError("upload_preparation_identity_conflict")


def preparation_inputs(preparation: dict) -> dict:
    result = {key: deepcopy(preparation[key]) for key in ("files", "customer_preference")}
    if preparation["predecessor_id"]:
        result["predecessor_id"] = preparation["predecessor_id"]
    return result
