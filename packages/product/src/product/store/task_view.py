"""Read projection of the existing frozen Round into the V3.1 Task contract."""

from __future__ import annotations

import re
from copy import deepcopy


def has_execution_error(execution: str, coverage: dict, findings: list[dict]) -> bool:
    """Execution faults are separate from terminal state and business verdicts."""
    if execution != "completed":
        return execution == "failed"
    if any(finding.get("status") == "ERR" for finding in findings):
        return True
    for item in coverage.get("rule_items", []):
        if isinstance(item, dict) and item.get("coverage") == "execution_error":
            resolved, finding, _notice = _clause_result({"coverage": coverage, "findings": findings}, str(item.get("file_version_id")), str(item.get("id")))
            if resolved and resolved.get("coverage") == "execution_error" and not finding:
                return True
    return False


def _clause_result(round: dict, version_id: str, clause_id: str) -> tuple[dict | None, dict | None, str | None]:
    coverage = [item for item in (round.get("coverage") or {}).get("rule_items", []) if item.get("file_version_id") == version_id and str(item.get("id")) == clause_id]
    keys = {item["item_key"] for item in coverage if item.get("item_key")}
    findings = []
    for finding in round.get("findings", []):
        locator = finding.get("locator") or {}
        if locator.get("file_version_id") not in {None, version_id} or locator.get("rule_item_id") is not None and str(locator["rule_item_id"]) != clause_id:
            continue
        if locator.get("file_version_id") == version_id and str(locator.get("rule_item_id")) == clause_id or finding.get("item_key") in keys:
            findings.append(finding)
    if len(coverage) == 1 and coverage[0].get("item_key"):
        paired = [item for item in findings if item.get("item_key") == coverage[0]["item_key"]]
        if len(paired) == 1:
            return coverage[0], paired[0], None
        if len(paired) > 1:
            return None, None, "本条要求对应多个机器结果，需核对原始审核记录。"
    if len(coverage) > 1 or len(findings) > 1:
        return None, None, "本条要求对应多个机器结果，需核对原始审核记录。"
    return coverage[0] if coverage else None, findings[0] if findings else None, None


def _legacy_evidence(finding: dict | None, version_id: str) -> list[dict]:
    if not finding:
        return []
    locator = finding.get("locator") or {}
    location = locator.get("locator")
    return [
        {
            "file_version_id": version_id,
            "locator": None,
            "excerpt": "",
            "location_gap": f"原定位：{location}。需核对原件中的位置与内容。" if location else "未提供可验证的原件定位与摘录。",
            "rendition": "original",
            "derived_file_version_id": None,
        }
    ]


def _typed_locator(locator: object) -> dict | None:
    from audit.models import public_locator

    return public_locator(locator)


def _evidence(round: dict, finding: dict | None, version_id: str, rule_id: str, clause_id: str) -> tuple[list[dict], dict | None, str | None]:
    semantic = ((finding or {}).get("locator") or {}).get("semantic")
    if not isinstance(semantic, dict):
        return _legacy_evidence(finding, version_id), None, None
    if semantic.get("rule_version_id") != rule_id or str(semantic.get("clause_id")) != clause_id:
        return [], None, "来源证据与本条冻结规则不一致，请核对原始审核记录。"
    scope = semantic.get("scope")
    if (
        not isinstance(scope, dict)
        or any(type(scope.get(key)) is not int or scope[key] < 0 for key in ("total", "checked", "unknown"))
        or scope["checked"] > scope["total"]
        or scope["unknown"] > scope["total"]
    ):
        scope = None
    versions = {item["file_version_id"] for item in round["inputs"]}
    evidence = []
    notice = None
    for source in semantic.get("sources", []):
        if not isinstance(source, dict) or source.get("file_version_id") not in versions:
            notice = "有来源不在本轮冻结文件中，未作为本条证据。"
            continue
        locator = _typed_locator(source.get("locator"))
        evidence.append(
            {
                "file_version_id": source["file_version_id"],
                "locator": locator,
                "excerpt": source.get("excerpt") if isinstance(source.get("excerpt"), str) else "",
                "location_gap": None if locator else "原审核结果缺少有效的原件定位。",
                "rendition": "original",
                "derived_file_version_id": None,
            }
        )
    return evidence, deepcopy(scope), notice


def _verified(round: dict, coverage: dict | None, finding: dict | None, rule_id: str, rule: dict, evidence: list[dict], scope: dict | None, notice: str | None) -> bool:
    return False

def _check(round: dict, version_id: str, rule_id: str, rule: dict, file_failed: bool = False) -> dict:
    finished = round["execution"] == "completed"
    coverage, original, notice = _clause_result(round, version_id, str(rule["id"])) if finished else (None, None, None)
    finding = deepcopy(original)
    if finding:
        finding["item_key"] = rule["id"]
    evidence, scope, evidence_notice = _evidence(round, finding, version_id, rule_id, str(rule["id"]))
    verified = _verified(round, coverage, original, rule_id, rule, evidence, scope, notice or evidence_notice)
    semantic = ((finding or {}).get("locator") or {}).get("semantic") if isinstance(finding, dict) else None
    verification_scope = (coverage or {}).get("verification_scope") if isinstance(coverage, dict) else None
    if not isinstance(verification_scope, str) and isinstance(semantic, dict) and isinstance(semantic.get("verification_scope"), str):
        verification_scope = semantic["verification_scope"]
    scope_gap = f"检查范围说明：已检查 {scope['checked']}/{scope['total']}，未知 {scope['unknown']}。" if scope and (scope["unknown"] or scope["checked"] < scope["total"]) else None
    checker_notice = "这是固定程序的执行结果，不是 trace 证明。" if verification_scope == "checker_execution" else None
    performed = semantic.get("checks_performed") if isinstance(semantic, dict) else None
    if checker_notice and type(performed) is int and performed >= 0:
        checker_notice = f"{checker_notice}已执行 {performed} 次断言。"
    diagnostics = semantic.get("diagnostics") if isinstance(semantic, dict) else None
    if checker_notice and isinstance(diagnostics, list):
        parts: list[str] = []
        for item in diagnostics:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            message = str(item.get("message") or "").strip()
            if code and message:
                parts.append(f"{code}：{message}")
            elif code or message:
                parts.append(code or message)
        if parts:
            checker_notice = f"{checker_notice}诊断：{'；'.join(parts)}。"
    basis = semantic.get("empty_scope_basis") if isinstance(semantic, dict) else None
    suggestions = str((finding or {}).get("suggestions") or "") if isinstance(finding, dict) else ""
    if checker_notice and isinstance(basis, str) and basis and basis not in checker_notice and basis not in suggestions:
        checker_notice = f"{checker_notice}空范围依据：{basis}。"
    return {
        "rule_item": rule,
        "execution": "failed"
        if finished and not finding and (file_failed or (coverage or {}).get("coverage") == "execution_error") or round["execution"] == "failed" or finding and finding["status"] == "ERR"
        else "completed"
        if finished
        else round["execution"],
        "coverage": "executed" if finding else (coverage or {}).get("coverage", "unsupported") if finished else "mapped",
        "finding": finding,
        "evidence_verified": verified,
        "verification_scope": verification_scope if isinstance(verification_scope, str) else None,
        "scope": scope,
        "scope_notice": _join_notices(notice, evidence_notice, scope_gap, checker_notice)
        or ("请核对原件中的证据和检查范围。" if finding and not verified else None)
        or ((coverage or {}).get("reason") if not finding else None),
        "evidence": evidence,
        **({"review_item_key": original["item_key"] if original else f"coverage:{version_id}:{rule['id']}"} if original or coverage and coverage.get("coverage") != "executed" else {}),
        "review": deepcopy((original or coverage or {}).get("review")),
    }


def _join_notices(*items: object) -> str | None:
    """Keep every distinct notice. A location gap must not hide checker diagnostics."""
    notices: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item or item in notices:
            continue
        notices.append(item)
    return "".join(notices) if notices else None


def _counts(checks: list[dict]) -> dict:
    counts = {"pass_count": 0, "ng_count": 0, "na_count": 0, "unknown_count": 0, "not_covered_count": 0, "error_count": 0}
    for check in checks:
        if check["execution"] in {"queued", "running"}:
            continue
        finding = check["finding"]
        if check["execution"] == "failed" or finding and finding["status"] == "ERR":
            counts["error_count"] += 1
        elif check["coverage"] != "executed":
            counts["not_covered_count"] += 1
        elif finding and finding["status"] == "NA":
            counts["na_count"] += 1
        elif finding and finding["status"] in {"PASS", "NG"}:
            counts["pass_count" if finding["status"] == "PASS" else "ng_count"] += 1
        else:
            counts["unknown_count"] += 1
    return counts


def report_eligible(checks: list[dict], failed: bool, unmapped: list[dict]) -> bool:
    return (
        bool(checks)
        and not failed
        and not unmapped
        and all(check["execution"] == "completed" and check["coverage"] == "executed" and check["finding"] is not None and check["finding"]["status"] in {"PASS", "NG", "NA"} for check in checks)
    )


def file_round_audit(round: dict, frozen: dict) -> dict:
    version_id, rule = frozen["file_version_id"], frozen["rule"]
    finished = round["execution"] in {"completed", "failed"}
    file_failed = finished and any(
        finding["status"] == "ERR"
        and (
            finding["item_key"] == "execution"
            and not (finding.get("locator") or {}).get("file_version_id")
            or finding["item_key"] == f"{version_id}:execution"
            and (finding.get("locator") or {}).get("file_version_id") == version_id
        )
        for finding in round.get("findings", [])
    )
    checks = [_check(round, version_id, rule["id"], item, file_failed) for item in rule["body"].get("rules", [])]
    mapped = {check["review_item_key"] for check in checks if check["finding"]}
    unmapped = []
    for finding in round.get("findings", []):
        source = (finding.get("locator") or {}).get("file_version_id")
        key = finding["item_key"]
        key_versions = {item["file_version_id"] for item in round["inputs"] if key.startswith(f"{item['file_version_id']}:") or key == f"target:{item['file_version_id']}"}
        conflict = source is not None and key_versions and source not in key_versions
        if key not in mapped and not conflict and (source == version_id or source is None and key_versions == {version_id}):
            unmapped.append(deepcopy(finding))
    file_has_error = finished and (file_failed or any(check["execution"] == "failed" for check in checks) or any(finding["status"] == "ERR" for finding in unmapped))
    counts = _counts(checks)
    complete = report_eligible(checks, file_has_error, unmapped)
    conclusion = ("NG" if counts["ng_count"] else "PASS" if complete and counts["pass_count"] else "UNKNOWN") if finished else None
    summary: dict | None = None
    if complete:
        not_applicable = sum(check["finding"]["status"] == "NA" for check in checks)
        summary = {
            "conclusion": conclusion,
            "text": f"已审核 {len(checks)} 项要求：满足 {counts['pass_count']} 项，不符合 {counts['ng_count']} 项，不适用 {not_applicable} 项。",
            "corrective_actions": [],
            "extended_suggestions": [],
        }
    return {
        "round_id": round["id"],
        "file_version_id": version_id,
        "result_attempt": round["result_attempt"],
        "rule": rule,
        "execution": "review_failed" if file_has_error or round["execution"] == "failed" else round["execution"],
        "progress": {"completed": len(checks) if finished else 0, "total": len(checks)},
        "failure": {"code": "file_execution_error", "message": "本文件审核执行失败，未取得完整结果。", "retryable": False}
        if file_has_error and round["execution"] != "failed"
        else round.get("failure"),
        "conclusion": conclusion,
        "counts": counts,
        "checks": checks,
        "unmapped_findings": unmapped,
        "summary": summary,
        "report": {"state": "not_eligible", "report_id": None, "reason": "正式报告暂不可用" if complete else "尚未取得全部审核项的结果" if finished else "审核尚未完成"},
    }


def round_read_view(round: dict, versions: dict[str, dict]) -> dict:
    files = [
        {"file": versions[item["file_version_id"]], "role": item["role"], "rule": item["rule"], "audit": file_round_audit(round, item) if item["role"] == "target" and item["rule"] else None}
        for item in round["inputs"]
    ]
    assigned = {
        key
        for row in files
        if row["audit"]
        for key in [*[check["review_item_key"] for check in row["audit"]["checks"] if check["finding"]], *[finding["item_key"] for finding in row["audit"]["unmapped_findings"]]]
    }
    return {"files": files, "unassigned_findings": [deepcopy(finding) for finding in round.get("findings", []) if finding["item_key"] not in assigned]}


def task_round_view(task: dict, round: dict, reports: dict[str, dict] | None = None) -> dict:
    finished = round["execution"] in {"completed", "failed"}
    files = []
    for row in task["files"]:
        frozen = next((item for item in round["inputs"] if item["file_version_id"] == row["file"]["id"] and item["role"] == "target"), None)
        if not frozen or row["selection"] != "selected":
            files.append({**row, "audit": None})
            continue
        audit = file_round_audit(round, frozen)
        if reports and row["file"]["id"] in reports:
            audit["report"] = reports[row["file"]["id"]]
        files.append({**row, "audit": audit})
    references = {item["file_version_id"] for item in round["inputs"] if item["role"] == "reference"}
    selected = [row for row in files if row["selection"] == "selected" and row["file"]["id"] not in references]
    ended = sum(bool(row.get("audit") and row["audit"]["execution"] in {"completed", "review_failed"}) for row in selected)
    task_finished = (
        finished
        and ended == len(selected)
        and all(row["audit"]["report"]["state"] not in {"pending", "generating"} or row["audit"]["report"].get("generation_request", {}).get("state") == "accepted" for row in selected)
    )
    return {
        **task,
        "round_id": round["id"],
        "execution": round["execution"],
        "has_execution_error": has_execution_error(round["execution"], round.get("coverage") or {}, round.get("findings", [])),
        "state": "completed" if task_finished else "reviewing",
        "completion_reason": "processed" if task_finished else None,
        "files": files,
        "progress": {"completed": ended, "total": len(selected)},
    }
