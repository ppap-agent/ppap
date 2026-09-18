"""真实旧引擎的窄边界证明；不把旧引擎通过算成新产品通过。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


class GateError(Exception):
    def __init__(self, code: str, attribution: str = "inconclusive"):
        super().__init__(code)
        self.code, self.attribution = code, attribution


def require(condition: bool, code: str, attribution: str = "inconclusive") -> None:
    if not condition:
        raise GateError(code, attribution)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require_sha(value: Any) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None,
            "expected_full_git_sha_required")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never resend an authenticated mutation through a redirect.


def fetch_bytes(url: str, token: str = "", payload: Any = None, timeout: float = 30,
                byte_limit: int = 8_000_000) -> bytes:
    u = urlsplit(url)
    require(u.scheme in ("http", "https") and bool(u.hostname) and not u.username
            and not u.password and not u.fragment, "invalid_endpoint")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None if payload is None else json.dumps(payload).encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        with build_opener(NoRedirect()).open(Request(url, data=body, headers=headers),
                                             timeout=timeout) as response:
            raw = response.read(byte_limit + 1)
    except HTTPError as exc:
        raise GateError(f"http_{exc.code}") from None
    except URLError as exc:
        reason = "dns_unavailable" if isinstance(exc.reason, socket.gaierror) else "transport_unavailable"
        raise GateError(reason, "external_environment") from None
    except (TimeoutError, OSError):
        raise GateError("transport_incomplete") from None
    require(len(raw) <= byte_limit, "response_limit_exceeded")
    return raw


def fetch(url: str, token: str = "", payload: Any = None, timeout: float = 30) -> Any:
    raw = fetch_bytes(url, token, payload, timeout)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise GateError("invalid_json_response") from None


def read_case(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise GateError("case_unavailable") from None
    require(isinstance(data, dict), "invalid_case")
    return data


def validate_oracle(rows: Any) -> dict[tuple[str, str], str]:
    require(isinstance(rows, list) and bool(rows), "nonempty_oracle_required")
    result: dict[tuple[str, str], str] = {}
    for row in rows:
        require(isinstance(row, dict), "invalid_oracle_row")
        key = (row.get("classification"), row.get("item"))
        require(all(isinstance(v, str) and v.strip() for v in key), "invalid_oracle_key")
        require(key not in result, "duplicate_oracle_key")
        require(row.get("status") in ("PASS", "NG", "ERR"), "invalid_oracle_status")
        result[key] = row["status"]
    return result


def compare_results(actual: Any, oracle: list[dict]) -> dict:
    expected = validate_oracle(oracle)
    require(isinstance(actual, list) and bool(actual), "empty_results")
    observed, locators = {}, 0
    for row in actual:
        require(isinstance(row, dict), "invalid_result_row")
        key = (row.get("classification"), row.get("item"))
        require(all(isinstance(v, str) and v.strip() for v in key), "invalid_result_key")
        require(key not in observed, "duplicate_result_key")
        require(row.get("status") in ("PASS", "NG", "ERR"), "unknown_result_status")
        observed[key] = row["status"]
        evidence = row.get("evidence") or {}
        require(isinstance(evidence, dict), "invalid_evidence")
        sources = evidence.get("sources") or []
        require(isinstance(sources, (list, tuple)), "invalid_evidence_sources")
        has_locator = any(isinstance(s, dict) and isinstance(s.get("locator"), str)
                          and s["locator"].strip() for s in sources)
        locators += int(has_locator)
        if any((e["classification"], e["item"]) == key and e.get("require_locator") is True for e in oracle):
            require(has_locator, "required_locator_absent")
    require(observed.keys() == expected.keys(), "check_coverage_mismatch")
    require(observed == expected, "oracle_mismatch")
    return {"checks": len(observed), "with_locator": locators,
            "status_counts": {s: list(observed.values()).count(s) for s in ("PASS", "NG", "ERR")}}


def response_data(response: Any) -> Any:
    require(isinstance(response, dict) and "data" in response, "invalid_api_envelope")
    return response["data"]


def batch_success(response: Any, expected_ids: set[str], key: str) -> list[dict]:
    data = response_data(response)
    require(isinstance(data, dict) and data.get("failed") == [], "batch_not_fully_accepted")
    rows = data.get("success")
    require(isinstance(rows, list) and all(isinstance(r, dict) for r in rows), "invalid_success_rows")
    ids = [r.get(key) for r in rows]
    require(len(ids) == len(expected_ids) and set(ids) == expected_ids, "wrong_accepted_identity")
    return rows


def version_probe(url: str, expected_sha: str, token: str = "") -> dict:
    require_sha(expected_sha)
    data = fetch(url, token)
    require(isinstance(data, dict) and data.get("git_sha") == expected_sha, "artifact_mismatch")
    return {"observed_git_sha": expected_sha}


def engine_run(case: dict, report: dict, directory: Path) -> None:
    """One analysis batch, one task batch, read-only polling; never resubmit."""
    base = os.environ.get("PPAP_LEGACY_ENGINE_URL", "").rstrip("/")
    token = os.environ.get("PPAP_LEGACY_ENGINE_TOKEN", "")
    project = os.environ.get("PPAP_LEGACY_PROJECT_ID", "")
    require(bool(base and token and project), "engine_configuration_missing", "external_environment")
    sha = require_sha(case.get("engine_git_sha"))
    version_url = os.environ.get("PPAP_LEGACY_VERSION_URL", "")
    require(bool(version_url), "version_endpoint_missing", "external_environment")
    require(urlsplit(base).netloc == urlsplit(version_url).netloc,
            "version_and_engine_origin_mismatch")
    files = case.get("files")
    require(isinstance(files, list) and bool(files), "files_required")
    payload, tasks, oracles = [], [], {}
    for i, entry in enumerate(files):
        require(isinstance(entry, dict), "invalid_file_case")
        validate_oracle(entry.get("expected"))
        require(isinstance(entry.get("rule_id"), str) and bool(entry["rule_id"]), "rule_binding_required")
        require(isinstance(entry.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is not None,
                "source_digest_required")
        url = os.environ.get(entry.get("source_url_env", ""), "")
        require(bool(url), "source_url_missing", "external_environment")
        # Private run state keeps remote identifiers for reconciliation, not publication.
        audit_id, task_id = f"live-{report['run_id']}-{i}", f"live-task-{report['run_id']}-{i}"
        payload.append({"auditId": audit_id, "url": url, "projectId": project,
                        "ruleProfile": entry.get("rule_profile", "v3")})
        tasks.append({"taskId": task_id, "auditId": audit_id, "ruleId": entry["rule_id"],
                      "projectId": project, "contextOnly": False})
        oracles[task_id] = entry["expected"]
    wait = case.get("max_wait_seconds", 300)
    interval = case.get("poll_seconds", 3)
    require(isinstance(wait, (int, float)) and 0 < wait <= 3600
            and isinstance(interval, (int, float)) and 0 < interval <= wait, "invalid_wait_budget")
    report["stage"] = "version"
    report["environment_sha256"] = digest(base.encode())
    report.update(version_probe(version_url, sha, token))
    report["stage"] = "source_integrity"
    for entry, request in zip(files, payload):
        raw = fetch_bytes(request["url"], byte_limit=64_000_000)
        require(digest(raw) == entry["sha256"], "source_digest_mismatch")
    report["verified_sources"] = len(files)
    private = directory / "reconcile.local.json"
    private.write_text(json.dumps({"task_ids": list(oracles), "audit_ids": [p["auditId"] for p in payload]}))
    private.chmod(0o600)
    report["stage"] = "analysis"
    report["analysis_invocations"] = 1
    analysis = batch_success(fetch(base + "/audits/analysis", token, payload),
                             {p["auditId"] for p in payload}, "auditId")
    matches = {r["auditId"]: r.get("matchedRuleId") for r in analysis}
    require(all(matches[t["auditId"]] == t["ruleId"] for t in tasks), "rule_match_changed")
    report["stage"] = "submission"
    report["task_invocations"] = 1
    accepted = batch_success(fetch(base + "/tasks", token, tasks), set(oracles), "taskId")
    expected_audits = {t["taskId"]: t["auditId"] for t in tasks}
    require(all(r.get("auditId") == expected_audits[r["taskId"]] for r in accepted),
            "accepted_task_source_mismatch")
    report["stage"] = "polling"
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        data = response_data(fetch(base + "/tasks?" + urlencode({"ids": ",".join(oracles)}), token))
        require(isinstance(data, list) and all(isinstance(r, dict) for r in data), "invalid_task_list")
        ids = [r.get("taskId") for r in data]
        require(len(ids) == len(oracles) and set(ids) == set(oracles), "wrong_task_identity")
        states = [r.get("status") for r in data]
        require(all(s in ("pending", "running", "completed", "failed") for s in states), "unknown_execution_status")
        require("failed" not in states, "engine_execution_failed")
        if all(s == "completed" for s in states):
            report["stage"] = "oracle"
            report["results"] = [compare_results(r.get("result"), oracles[r["taskId"]]) for r in data]
            report["stage"] = "complete"
            return
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    raise GateError("terminal_result_missing")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path(".local/engine-live"))
    args = parser.parse_args(argv)
    directory = args.out / uuid.uuid4().hex
    directory.mkdir(parents=True, mode=0o700)
    report = {"run_id": directory.name, "time_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "legacy.engine.oracle", "outcome": "NOT_PROVEN",
              "product_story_outcome": "NOT_PROVEN", "stage": "configuration",
              "gate_sha256": digest(Path(__file__).read_bytes()),
              "analysis_invocations": 0, "task_invocations": 0}
    try:
        case = read_case(args.case)
        report["case_sha256"] = digest(args.case.read_bytes())
        engine_run(case, report, directory)
        report["outcome"] = "PROVEN"
    except GateError as exc:
        report.update(reason=exc.code, attribution=exc.attribution)
    except Exception as exc:
        # Exception text may contain credentials, signed URLs or customer content.
        report.update(reason="runner_exception", error_type=type(exc).__name__, attribution="inconclusive")
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["outcome"] == "PROVEN" else 2


if __name__ == "__main__":
    sys.exit(main())
