"""Preparation facts retain their own identity and never imply formal approval."""

from __future__ import annotations

import hashlib
import json

STATUSES = {"satisfied", "defect", "unknown", "not_checked", "error", "handoff", "not_applicable"}


def input_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def fact(source_id, status, message, *, code=None, evidence=None):
    return {
        "id": source_id,
        "source_id": source_id,
        "status": status,
        "code": code,
        "message": message,
        "critical": False,
        "certified": False,
        "certainty": "unknown",
        "evidence": evidence,
        "next_step": "继续正式审核。" if status in {"satisfied", "handoff"} else "重试预检；若仍失败，联系产品维护。" if status == "error" else "查看原件并人工确认；未核实前不作通过结论。",
    }


def empty_result(payload):
    return {
        "input_fingerprint": input_digest(payload),
        "execution_revision": payload["execution_revision"],
        "files": [],
        "notice": "只展示实际执行的准备检查；未覆盖和未知项不代表通过，正式工程结论留给正式审核。",
    }


def result_row(source):
    return {"file_version_id": source["file"]["id"], "rule_version_id": (source.get("rule") or {}).get("id"), "suite_execution": source.get("rule_execution"), "facts": []}


def complete_coverage(payload, result):
    for source, row in zip(payload["files"], result["files"]):
        expected = [*(source.get("required_structure") or []), *(source.get("precheck_criteria") or [])]
        observed = {item["id"] for item in row["facts"]}
        for criterion in expected:
            if criterion["id"] not in observed:
                item = fact(criterion["id"], "not_checked", criterion["criterion"])
                item["source_clause"] = criterion.get("source_clause") or criterion.get("source_cells")
                item["next_step"] = "此结构准则尚未核实，请人工检查原件；不能视为通过。"
                row["facts"].append(item)
        actual = {item["id"]: item for item in row["facts"] if item["id"] in {criterion["id"] for criterion in expected}}
        row["coverage"] = {"expected": len(expected), "by_status": {status: sum(item["status"] == status for item in actual.values()) for status in sorted(STATUSES)}}
    return result


def customer_conflict(payload):
    customers = {
        customer
        for source in payload["files"]
        if source["selection"] == "selected"
        for customer in ((source.get("rule") or {}).get("body") or {}).get("customers", [])
        if source.get("suite_target") or ((source.get("rule") or {}).get("body") or {}).get("rule_profile") == "v4"
    }
    return bool(payload.get("customer_conflict") or len(customers) > 1)


def validate_result(payload, result):
    if result.get("input_fingerprint") != input_digest(payload) or result.get("execution_revision") != payload["execution_revision"]:
        raise ValueError("precheck_output_identity_mismatch")
    rows = result.get("files")
    if not isinstance(rows, list) or len(rows) != len(payload["files"]):
        raise ValueError("precheck_output_files_mismatch")
    allowed = {source["file"]["id"] for source in payload["files"] if source["selection"] == "selected"}
    for source, row in zip(payload["files"], rows):
        identity = result_row(source)
        if any(row.get(key) != identity[key] for key in ("file_version_id", "rule_version_id", "suite_execution")):
            raise ValueError("precheck_output_identity_mismatch")
        facts = row.get("facts")
        if (
            not isinstance(facts, list)
            or len(facts) > 512
            or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or item.get("source_id") != item["id"] or item.get("status") not in STATUSES for item in facts)
        ):
            raise ValueError("invalid_precheck_fact")
        if len({item["id"] for item in facts}) != len(facts):
            raise ValueError("duplicate_precheck_fact")
        for item in facts:
            # Guest/enterprise assertions cannot self-certify a blocking defect.
            if item.get("certified") is not False:
                raise ValueError("uncertified_precheck_output")
            for evidence in (item.get("evidence") or {}).get("sources") or []:
                try:
                    locator = json.loads(evidence["locator"])
                except (KeyError, TypeError, ValueError):
                    continue  # Existing local locators refer only to this target.
                if isinstance(locator, dict) and locator.get("file_version_id") is not None and locator["file_version_id"] not in allowed:
                    raise ValueError("precheck_foreign_evidence")
    expected = complete_coverage(payload, json.loads(json.dumps(result, allow_nan=False)))
    if expected != result:
        raise ValueError("precheck_output_coverage_mismatch")
    return result
