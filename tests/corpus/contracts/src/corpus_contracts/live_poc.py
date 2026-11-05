"""Explicit, single-invocation V3 acceptance through the current Product API."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from audit.audits import ANALYSIS_DEEP, ANALYSIS_MODES, ANALYSIS_STANDARD, EXECUTOR_DEEP, EXECUTOR_STANDARD
from product.standard_policy import StandardExecutionPolicy

from corpus_contracts.acceptance_report import write_acceptance_report
from corpus_contracts.poc_oracle import POC_SEQ_TO_DOC_TYPE, REPO_ROOT, build_bundle_map, load_poc_cases
from corpus_contracts.product_round import MATCH, compare_file

RULE_DOCS = {
    "1": "1-2规则-尺寸检验报告.docx",
    "8": "8规则-过程流程图.docx",
    "9": "9规则-潜在过程失效模式及后果分析（PFMEA）.docx",
    "10": "10规则-控制计划.docx",
    "11": "11规则-特殊特性清单.docx",
}


class LiveError(ValueError):
    """Only runner-owned codes, never provider bodies or transport details."""


def _json(client, method, path, *, expected=200, **kwargs):
    try:
        response = client.request(method, path, **kwargs)
        if response.status_code != expected:
            raise LiveError(f"http_{response.status_code}")
        body = response.json()
        if body is not None and not isinstance(body, dict):
            raise LiveError("invalid_response_shape")
        return body
    except (httpx.HTTPError, json.JSONDecodeError):
        raise LiveError("unreadable_response") from None


def _submit(client, path, payload, expected, recovery):
    try:
        return _json(client, "POST", path, expected=expected, json=payload)
    except LiveError:
        # A lost response never authorizes another write, even with the same key.
        return _json(client, "GET", recovery)


def _poll(client, path, field, terminal, timeout, interval):
    deadline = time.monotonic() + timeout
    while True:
        body = _json(client, "GET", path)
        if body and body.get(field) in terminal:
            return body
        if time.monotonic() >= deadline:
            raise LiveError("terminal_result_timeout")
        time.sleep(interval)


def _upload(client, path, run, save):
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    receipt = {"stage": "file_upload", "request_id": str(uuid4()), "sha256": digest}
    run["operations"].append(receipt)
    save()
    version = _json(
        client,
        "POST",
        "/files",
        expected=201,
        json={"original_filename": path.name, "content_type": "application/octet-stream", "byte_size": len(content), "sha256": digest, "request_id": receipt["request_id"]},
    )
    receipt.update(file_id=version["file_id"], file_version_id=version["id"])
    save()
    location = f"/files/{version['file_id']}/versions/{version['id']}"
    _json(client, "PUT", location + "/blob", content=content)
    _json(client, "POST", location + "/finalize", json={"sha256": digest})
    loaded = _json(client, "GET", location)
    if loaded["status"] != "ready" or loaded["sha256"] != digest:
        raise LiveError("uploaded_file_digest_mismatch")
    return loaded


def _publish(client, seq, cases, run, save, timeout, interval):
    path = REPO_ROOT / "assets" / "poc测试" / "poc规则" / RULE_DOCS[seq]
    source = _upload(client, path, run, save)
    request_id = str(uuid4())
    receipt = {"stage": "rule_parse", "seq": seq, "request_id": request_id}
    run["operations"].append(receipt)
    save()
    _submit(
        client,
        "/rules/parse",
        {"request_id": request_id, "source_file_id": source["file_id"], "source_file_version_id": source["id"], "document_type": POC_SEQ_TO_DOC_TYPE[seq].get_type_name()},
        202,
        "/rules/parse/" + request_id,
    )
    parsed = _poll(client, "/rules/parse/" + request_id, "status", {"succeeded", "failed", "unknown"}, timeout, interval)
    if parsed["status"] != "succeeded":
        raise LiveError("rule_parse_not_succeeded")
    candidate = parsed["result"]
    body = candidate["body"]
    expected_indices = sorted({index for case in cases if case.file_seq == seq for index in case.expected})
    if body["rule_profile"] != "v3" or body["type"] != POC_SEQ_TO_DOC_TYPE[seq].get_type_name() or [rule["id"] for rule in body["rules"]] != [str(index) for index in expected_indices]:
        raise LiveError("parsed_rule_contract_mismatch")
    units = _json(client, "GET", f"/files/{source['file_id']}/versions/{source['id']}/rule-sources")["units"]
    clauses = []
    for rule in body["rules"]:
        unit = next((unit for unit in units if unit["excerpt"] == rule["rule_to_check"]), None)
        if unit is None:
            raise LiveError("original_rule_text_mismatch")
        clauses.append(
            {
                "id": rule["id"],
                "title": rule["category"],
                "text": rule["rule_to_check"],
                "source": path.name,
                "original": unit["excerpt"],
                "location": unit["location"],
                "checked": True,
                "provenance": {**unit, "fileVersionId": source["id"]},
            }
        )
    registration = {"basis": {"source": {"filename": path.name}, "clauses": clauses}}
    context = {"registration": json.dumps(registration, ensure_ascii=False)}
    if not body.get("customers"):
        context["customer_scope"] = "all"
    request_id = str(uuid4())
    run["operations"].append({"stage": "rule_publish", "seq": seq, "request_id": request_id})
    save()
    _submit(client, "/rules", {"title": path.stem, "body": body, "source_file_version_id": source["id"], "request_id": request_id, "case_context": context}, 201, "/rules/" + request_id)
    published = _json(client, "GET", "/rules/" + request_id)
    if published["body"] != body or published["source_file_version_id"] != source["id"] or published["case_context"] != context:
        raise LiveError("published_rule_readback_mismatch")
    return published


def _frozen_inputs(result, draft_id, items, rules, analysis_mode, candidate_sha, frozen_execution=None):
    if result.get("source_draft_id") != draft_id or result.get("analysis_mode") != analysis_mode:
        raise LiveError("round_identity_mismatch")
    executor = EXECUTOR_DEEP if analysis_mode == ANALYSIS_DEEP else EXECUTOR_STANDARD
    revision = result.get("executor_revision")
    if result.get("executor") != executor:
        raise LiveError("frozen_executor_mismatch")
    execution = {"analysis_mode": analysis_mode, "executor": executor, "executor_revision": revision}
    if analysis_mode == ANALYSIS_DEEP:
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
            raise LiveError("frozen_executor_mismatch")
    else:
        try:
            policy = StandardExecutionPolicy.model_validate(result.get("execution_policy"))
        except ValueError:
            raise LiveError("frozen_executor_mismatch") from None
        if policy.app_revision != candidate_sha or revision != policy.engine_revision():
            raise LiveError("frozen_executor_mismatch")
        execution["execution_policy"] = policy.model_dump(mode="json")
    if frozen_execution is not None and execution != frozen_execution:
        raise LiveError("frozen_executor_mismatch")
    actual = result.get("inputs") or []
    if len(actual) != len(items):
        raise LiveError("frozen_input_mismatch")
    by_version = {row["file_version_id"]: row for row in actual}
    for expected in items:
        actual = by_version.get(expected["file_version_id"], {})
        if any(actual.get(key) != value for key, value in expected.items()) or actual.get("rule", {}).get("body") != rules[expected["rule_version_id"]]["body"]:
            raise LiveError("frozen_input_mismatch")
    return execution


def _rule_results(result, items, rules):
    expected = {(item["file_version_id"], str(rule["id"])) for item in items for rule in rules[item["rule_version_id"]]["body"]["rules"]}
    coverage = result.get("coverage", {}).get("rule_items", [])
    findings = result.get("findings", [])
    identities = [(row.get("file_version_id"), str(row.get("id"))) for row in coverage]
    keys = [row.get("item_key") for row in coverage]
    finding_keys = [row.get("item_key") for row in findings]
    if (
        len(identities) != len(expected)
        or set(identities) != expected
        or any(not isinstance(key, str) or not key for key in keys)
        or len(set(keys)) != len(keys)
        or len(finding_keys) != len(keys)
        or set(finding_keys) != set(keys)
    ):
        raise LiveError("rule_result_identity_mismatch")


def _rows(cases, versions, rule_versions, result, stage, error, round_id, analysis_mode):
    rows = []
    for case in cases:
        verdicts = compare_file(
            file_name=case.file_name,
            version_id=versions.get(case.file_name, ""),
            expected={i: e.status for i, e in case.expected.items()},
            result=result,
            setup_error=error,
            expected_coverage="executed" if analysis_mode == ANALYSIS_DEEP else "mapped",
        )
        coverage = [item for item in (result or {}).get("coverage", {}).get("rule_items", []) if item.get("file_version_id") == versions.get(case.file_name)]
        for verdict in verdicts:
            matched = [item for item in coverage if str(item.get("id")) == str(verdict.rule_index)]
            findings = [item for item in (result or {}).get("findings", []) if matched and item["item_key"] == matched[0].get("item_key")]
            finding = findings[0] if len(findings) == 1 else {}
            kind, actual = verdict.kind, verdict.actual or ""
            frozen_rule = rule_versions.get(versions.get(case.file_name))
            if result is not None and (len(matched) != 1 or matched[0].get("rule_version_id", frozen_rule) != frozen_rule or finding.get("status") != actual):
                kind = "missing_rule"
            rows.append(
                {
                    "bundle": case.bundle_id,
                    "file_seq": case.file_seq,
                    "file_name": case.file_name,
                    "rule_index": verdict.rule_index,
                    "expected_status": verdict.expected,
                    "actual_status": actual,
                    "kind": kind,
                    "rule_desc": case.expected[verdict.rule_index].rule_desc,
                    "manual_reason": case.expected[verdict.rule_index].manual_reason,
                    "agent_suggestions": finding.get("suggestions", ""),
                    "failure_stage": stage if error else "comparison" if kind != MATCH else "",
                    "round_id": round_id,
                    "job_id": (result or {}).get("result_job_id") or "",
                }
            )
    return rows


def run_live(
    client: httpx.Client,
    *,
    candidate_sha: str,
    output: Path,
    analysis_mode: str = ANALYSIS_STANDARD,
    bundle_ids: list[str] | None = None,
    isolated_login: bool = False,
    timeout: float = 900,
    poll_interval: float = 1,
) -> dict:
    """Accept each selected canonical bundle once. Existing output is refused."""
    if analysis_mode not in ANALYSIS_MODES:
        raise ValueError("invalid_analysis_mode")
    output.mkdir(parents=True, exist_ok=False)
    run: dict[str, Any] = {
        "candidate_sha": candidate_sha,
        "analysis_mode": analysis_mode,
        "boundary": "canonical Product API / Worker comparison in declared scope; subset is not full-corpus acceptance; not full Deep quality, safety, browser or production acceptance",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "outcome": "NOT_PROVEN",
        "attribution": "inconclusive",
        "operations": [],
        "errors": [],
    }
    rows: list[dict] = []

    def save():
        write_acceptance_report(output / "report.json", rows, markdown_path=output / "report.md", run=run)

    stage = "preflight"
    save()
    try:
        oracle_path = REPO_ROOT / "assets" / "poc测试" / "poc-测试数据统计表.xlsx"
        cases = load_poc_cases(dataset_root=REPO_ROOT / "assets" / "poc测试" / "POC测试样本", expected_xlsx=oracle_path)
        bundles = build_bundle_map(cases)
        if len(cases) != 26 or len(bundles) != 8 or sum(len(case.expected) for case in cases) != 209 or {case.file_seq for case in cases} != set(RULE_DOCS):
            raise LiveError("canonical_universe_incomplete")
        if bundle_ids is not None:
            if not isinstance(bundle_ids, list) or not bundle_ids or any(not isinstance(bundle, str) for bundle in bundle_ids) or len(set(bundle_ids)) != len(bundle_ids):
                raise LiveError("invalid_bundle_selection")
            if any(bundle not in bundles for bundle in bundle_ids):
                raise LiveError("unknown_bundle_selection")
            bundles = {bundle: selected for bundle, selected in bundles.items() if bundle in bundle_ids}
        selected_cases = [case for case in cases if case.bundle_id in bundles]
        selected_rules = sum(len(case.expected) for case in selected_cases)
        required_seqs = {case.file_seq for case in selected_cases}
        run.update(
            scope="full" if len(bundles) == 8 else "subset",
            selected_bundles=sorted(bundles),
            denominators={"full": {"bundles": 8, "files": 26, "rules": 209}, "selected": {"bundles": len(bundles), "files": len(selected_cases), "rules": selected_rules}},
        )
        assets = [oracle_path, *[case.file_path for case in selected_cases], *[REPO_ROOT / "assets" / "poc测试" / "poc规则" / RULE_DOCS[seq] for seq in RULE_DOCS if seq in required_seqs]]
        run["asset_digests"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
        save()
        config = _json(client, "GET", "/public-config")
        if not re.fullmatch(r"[0-9a-f]{40}", candidate_sha) or config.get("git_sha") != candidate_sha or config.get("usage_charge_enabled") is not False:
            raise LiveError("candidate_or_charging_policy_mismatch")
        if config.get("profile") != "isolated-dev":
            raise LiveError("isolated_stack_required")
        ready = _json(client, "GET", "/ready")
        if ready.get("status") != "ready" or ready.get("git_sha") != candidate_sha:
            raise LiveError("stack_not_ready")
        if isolated_login:
            _json(client, "POST", "/isolated-dev/sessions", json={"subject": "alice"})
        published, unavailable = {}, set()
        stage = "rule_setup"
        for seq in RULE_DOCS:
            if seq not in required_seqs:
                continue
            try:
                published[seq] = _publish(client, seq, cases, run, save, timeout, poll_interval)
            except (LiveError, KeyError, TypeError, OSError) as exc:
                unavailable.add(seq)
                run["errors"].append({"stage": stage, "seq": seq, "code": str(exc) if isinstance(exc, LiveError) else "invalid_rule_response"})
                save()
        by_rule = {rule["id"]: rule for rule in published.values()}
        for bundle, selected in sorted(bundles.items()):
            versions, items, result, round_id, error = {}, [], None, "", None
            stage = "file_upload"
            try:
                if any(case.file_seq in unavailable for case in selected):
                    raise LiveError("group_rule_setup_incomplete")
                for case in selected:
                    version = _upload(client, case.file_path, run, save)
                    versions[case.file_name] = version["id"]
                    items.append({"file_version_id": version["id"], "role": "target", "rule_version_id": published[case.file_seq]["id"]})
                stage = "draft_save"
                draft_id = str(uuid4())
                run["operations"].append({"stage": stage, "bundle": bundle, "request_id": draft_id, "analysis_mode": analysis_mode})
                save()
                _submit(client, "/drafts", {"request_id": draft_id}, 201, "/drafts/" + draft_id)
                _json(client, "PUT", "/drafts/" + draft_id, json={"analysis_mode": analysis_mode, "items": items})
                stage = "accept"
                run["operations"].append({"stage": stage, "bundle": bundle, "draft_id": draft_id, "idempotency_key": str(uuid4()), "analysis_mode": analysis_mode})
                save()
                accepted = _submit(client, f"/drafts/{draft_id}/accept", {"idempotency_key": run["operations"][-1]["idempotency_key"]}, 201, f"/drafts/{draft_id}/round")
                if not accepted:
                    raise LiveError("acceptance_not_observed")
                round_id = accepted["id"]
                run["operations"][-1]["round_id"] = round_id
                save()
                execution = _frozen_inputs(accepted, draft_id, items, by_rule, analysis_mode, candidate_sha)
                run["operations"][-1].update(execution)
                save()
                stage = "audit"
                result = _poll(client, "/rounds/" + round_id, "execution", {"completed", "failed"}, timeout, poll_interval)
                run["operations"][-1]["job_id"] = result.get("result_job_id")
                _frozen_inputs(result, draft_id, items, by_rule, analysis_mode, candidate_sha, execution)
                if result["execution"] != "completed" or not result.get("result_job_id"):
                    raise LiveError("audit_not_completed")
                _rule_results(result, items, by_rule)
            except (LiveError, KeyError, TypeError, OSError) as exc:
                error = str(exc) if isinstance(exc, LiveError) else "invalid_product_response"
                run["errors"].append({"stage": stage, "bundle": bundle, "code": error, "round_id": round_id})
            group_rows = _rows(selected, versions, {item["file_version_id"]: item["rule_version_id"] for item in items}, result, stage, error, round_id, analysis_mode)
            if stage == "audit" and error:
                for row in group_rows:
                    row["kind"] = "execution_failure"
            rows.extend(group_rows)
            save()
        if len(rows) == selected_rules and all(row["kind"] == MATCH for row in rows) and not run["errors"]:
            run["outcome"] = "PROVEN" if run["scope"] == "full" else "SUBSET_PROVEN"
            run.pop("attribution")
    except (LiveError, KeyError, TypeError, OSError, AssertionError, ValueError) as exc:
        run["errors"].append({"stage": stage, "code": str(exc) if isinstance(exc, LiveError) else "invalid_setup"})
    run["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
    return run


def main():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--analysis-mode", choices=sorted(ANALYSIS_MODES), default=ANALYSIS_STANDARD)
    parser.add_argument("--bundle", dest="bundle_ids", action="append", help="exact canonical bundle name; repeat to select whole bundles (default: all eight)")
    parser.add_argument("--isolated-login", action="store_true")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    from urllib.parse import urlsplit

    url = urlsplit(args.api)
    if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment or url.path.strip("/") or not 0 < args.timeout <= 1800:
        parser.error("API must be an origin without credentials; timeout must be 0..1800 seconds")
    headers = {"Cookie": os.environ["PPAP_LIVE_COOKIE"]} if os.environ.get("PPAP_LIVE_COOKIE") else {}
    with httpx.Client(base_url=args.api.rstrip("/"), headers=headers, timeout=30, follow_redirects=False) as client:
        result = run_live(
            client, candidate_sha=args.candidate_sha, output=args.output, analysis_mode=args.analysis_mode, bundle_ids=args.bundle_ids, isolated_login=args.isolated_login, timeout=args.timeout
        )
    print(
        json.dumps({"outcome": result["outcome"], "scope": result.get("scope"), "candidate_sha": args.candidate_sha, "analysis_mode": args.analysis_mode, "report": str(args.output / "report.json")})
    )
    raise SystemExit(0 if result["outcome"] in {"PROVEN", "SUBSET_PROVEN"} else 1)


if __name__ == "__main__":
    main()
