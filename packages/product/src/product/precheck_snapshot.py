"""The precheck of a task as P3 shows it, and the snapshot frozen with its acceptance.

Before acceptance the precheck page and report read the task as it stands. Acceptance freezes the same
result, its cover values and time on the Round; from then on the page, the report and the audit report's
C class read only that snapshot, so one snapshot always gives one report.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from product.customers import full_name
from product.precheck import named_by_user, precheck_task, rule_customer
from product.precheck_content import entry_key, precheck_content

# Content results depend only on the bytes and the rule entry, so one read serves every later view.
_CACHE: OrderedDict[tuple, list[dict]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="precheck")

COVER_FIELDS = ("customer", "project", "model", "part_number", "part")


def _cached_content(key: tuple, read) -> list[dict]:
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    result = read()
    with _CACHE_LOCK:
        _CACHE[key] = result
        while len(_CACHE) > 512:
            _CACHE.popitem(last=False)
    return result


def cover_recognition(task: dict) -> dict[str, str | None]:
    """Cover values from the frozen recognition (match metadata) of the submitted files, never display corrections.

    Differing values are all listed; a field no file recognized stays empty. The customer reads as its registered full name.
    """
    rows = [row for row in task.get("files", []) if row.get("selection") == "selected" and not row.get("reference_only") and (row.get("match") or {}).get("state") == "matched"]
    cover: dict[str, str | None] = {}
    for field in COVER_FIELDS:
        # The customer falls back to the task's customer, as the precheck routes it.
        found = [((row.get("match") or {}).get("metadata") or {}).get(field) or (task.get("customer_preference") if field == "customer" else None) for row in rows]
        values = [full_name(value) if field == "customer" else value.strip() for value in found if isinstance(value, str) and value.strip()]
        cover[field] = "、".join(dict.fromkeys(values)) or None
    return cover


def _original(store, principal):
    """Bytes of a task row's original, read through the tenant and object authorization of ``principal``."""

    def read(row: dict) -> bytes:
        return store.version_bytes(store.read_file_version(principal, row["file"]["file_id"], row["file"]["id"]))

    return read


def _content(store, principal, task: dict) -> dict[str, list[dict]]:
    """Read each recognized target once and run the rule entry routed to it; an unreadable file is left to the file level."""
    jobs = {}
    for row in task.get("files", []):
        match = row.get("match") or {}
        if row.get("selection") != "selected" or match.get("state") != "matched" or row.get("reference_only"):
            continue
        customer = rule_customer(task, row)
        key = entry_key(customer, match.get("document_type"))
        if key is None:
            continue

        def read(row=row, key=key, customer=customer, named=named_by_user(row)):
            # The version read applies tenant and object authorization.
            version = store.read_file_version(principal, row["file"]["file_id"], row["file"]["id"])
            return _cached_content((version.sha256, key, customer, named), lambda: precheck_content(store.version_bytes(version), version.original_filename, key, customer=customer, named=named))

        jobs[row["file"]["id"]] = _POOL.submit(read)
    content = {}
    for file_version_id, job in jobs.items():
        try:
            content[file_version_id] = job.result(timeout=60)
        except Exception:  # noqa: BLE001 - a file that cannot be read stays at the file-level result
            continue
    return content


def current_precheck(store, principal, task: dict) -> dict[str, Any]:
    """The task's precheck as it stands now: ``generated_at``, cover ``recognition`` and the ``precheck`` result."""
    return {
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M"),
        "recognition": cover_recognition(task),
        # Same-name copies are compared cell by cell from their originals (R-03 evidence).
        "precheck": precheck_task(task, _content(store, principal, task), read=_original(store, principal)),
    }


def file_warnings(snapshot: dict | None, file_version_id: str) -> list[dict]:
    """The audited file's own precheck warnings in a frozen snapshot; a Round frozen without one has none."""
    return [item for item in ((snapshot or {}).get("precheck") or {}).get("warnings") or [] if item.get("file_version_id") == file_version_id]
