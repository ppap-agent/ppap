"""Read projection of the existing frozen Round into the V3.1 Task contract."""

from __future__ import annotations

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


def _typed_locator(locator: object) -> dict | None:
    from audit.models import public_locator

    return public_locator(locator)


def _evidence(round: dict, finding: dict | None, version_id: str, rule_id: str, clause_id: str) -> tuple[list[dict], dict | None, str | None]:
    if not finding:
        return [], None, None
    semantic = (finding.get("locator") or {}).get("semantic")
    if not isinstance(semantic, dict):
        return [], None, "结果缺少可核对的证据记录，需核对原始审核记录。"
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
    # Only what helps a person check the original reaches them: the checker's read-gap messages (not its
    # informational provenance notes) and the basis of an empty scope. Codes and assertion counts stay internal.
    checker_notice = None
    if verification_scope == "checker_execution":
        diagnostics = semantic.get("diagnostics") if isinstance(semantic, dict) else None
        messages = [str(item.get("message") or "").strip() for item in diagnostics if isinstance(item, dict) and item.get("severity") != "info"] if isinstance(diagnostics, list) else []
        basis = semantic.get("empty_scope_basis") if isinstance(semantic, dict) else None
        suggestions = str((finding or {}).get("suggestions") or "") if isinstance(finding, dict) else ""
        if isinstance(basis, str) and basis and basis not in suggestions:
            messages.append(basis)
        messages = [message.rstrip("。") for message in dict.fromkeys(messages) if message and message not in suggestions]
        checker_notice = "；".join(messages) + "。" if messages else None
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
        "scope_notice": _join_notices(notice, evidence_notice, scope_gap, checker_notice) or ((coverage or {}).get("reason") if not finding else None),
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


REPORTED_STATUSES = frozenset({"PASS", "NG", "NA", "UNABLE"})
# A reviewer's judgment that keeps a clause open; it never changes the machine or confirmed result.
OBJECTIONS = frozenset({"disagree", "manual-unsatisfied", "unknown"})


def _outcome(check: dict, confirmation: dict | None) -> str:
    """One clause's standing: the machine result, or the result recomputed from confirmed original facts."""
    finding = check["finding"] or {}
    if check["execution"] in {"queued", "running"}:
        return "running"
    if check["execution"] == "failed" or finding.get("status") == "ERR":
        return "ERR"
    if check["coverage"] != "executed":
        return "uncovered"
    status = (confirmation or {}).get("status") or finding.get("status")
    return status if status in {"PASS", "NG", "NA"} else "UNABLE"


# Clauses the audit gave no verdict for (无法判断 / 未覆盖): 待审核. They stay 待审核 and are never counted as PASS.
PENDING_OUTCOMES = frozenset({"UNABLE", "uncovered"})


def _conclusion(outcomes: list[str], decidable: bool) -> str:
    """The file label aggregates the reviewed clauses (PRD IL-12.08): any NG is NG; otherwise a reviewed PASS is PASS,
    with the 待审核 clauses counted beside it (``pending_count``). Nothing reviewed (all 待审核 or 不适用) stays UNKNOWN."""
    return "NG" if "NG" in outcomes else "PASS" if decidable and "PASS" in outcomes and set(outcomes) <= {"PASS", "NA"} | PENDING_OUTCOMES else "UNKNOWN"


def pending_count(audit: dict, *, confirmed: bool = True) -> int:
    """待审核 clauses of a file: where they stand now (confirmed), or as the machine left them."""
    if confirmed:
        return sum(check.get("outcome") in PENDING_OUTCOMES for check in audit["checks"])
    return audit["counts"]["unknown_count"] + audit["counts"]["not_covered_count"]


def overall(conclusions: list[str | None]) -> str:
    """A round's one conclusion: any NG is NG; anything undecided (or missing) keeps it open."""
    if "NG" in conclusions:
        return "NG"
    return "PASS" if conclusions and all(value == "PASS" for value in conclusions) else "UNKNOWN"


def _settled(audit: dict, *, confirmed: bool) -> str | None:
    """A file's part in the round's one conclusion: a PASS with 待审核 clauses does not settle the round."""
    value = audit["confirmed_conclusion" if confirmed else "conclusion"]
    return "UNKNOWN" if value == "PASS" and pending_count(audit, confirmed=confirmed) else value


def report_eligible(checks: list[dict], failed: bool, unmapped: list[dict]) -> bool:
    """Every clause ended without a technical error and at least one has a result. A clause the checker could not
    decide (无法判断) or does not cover (未覆盖) stays 待审核 in the report."""
    return (
        bool(checks)
        and not failed
        and not unmapped
        and any(check["finding"] is not None for check in checks)
        and all(
            check["execution"] == "completed"
            and (check["coverage"] == "executed" and check["finding"] is not None and check["finding"]["status"] in REPORTED_STATUSES or check["coverage"] != "executed" and check["finding"] is None)
            for check in checks
        )
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
    # The file label aggregates the reviewed clauses; 待审核 clauses are counted beside it, never as PASS.
    decidable = finished and not file_has_error and not unmapped
    conclusion = ("NG" if counts["ng_count"] else "PASS" if decidable and counts["pass_count"] else "UNKNOWN") if finished else None
    # Each clause's standing is decided here, once, for every reader. A confirmed original fact can only
    # settle a clause the machine could not decide; it is kept beside the machine result, never over it.
    confirmed = round.get("fact_results") or {}
    for check in checks:
        machine_unable = check["coverage"] == "executed" and (check["finding"] or {}).get("status") == "UNABLE"
        confirmation = confirmed.get(check.get("review_item_key")) if machine_unable else None
        if confirmation:
            check["confirmation"] = {key: deepcopy(confirmation[key]) for key in ("status", "suggestions", "confirmed_by", "confirmed_at", "sources")}
        check["outcome"] = _outcome(check, confirmation)
        check["objected"] = (check.get("review") or {}).get("judgment") in OBJECTIONS
        check["attention"] = check["outcome"] not in {"PASS", "NA", "running"} or check["objected"]
    confirmed_conclusion = _conclusion([check["outcome"] for check in checks], decidable) if finished else None
    return {
        "round_id": round["id"],
        "file_version_id": version_id,
        "result_attempt": round["result_attempt"],
        "rule": rule,
        "checker_verification": deepcopy(frozen.get("checker_verification")),
        "execution": "review_failed" if file_has_error or round["execution"] == "failed" else round["execution"],
        "progress": {"completed": len(checks) if finished else 0, "total": len(checks)},
        "failure": {"code": "file_execution_error", "message": "本文件审核执行失败，未取得完整结果。", "retryable": False}
        if file_has_error and round["execution"] != "failed"
        else round.get("failure"),
        "conclusion": conclusion,
        "confirmed_conclusion": confirmed_conclusion,
        "counts": counts,
        "checks": checks,
        "unmapped_findings": unmapped,
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


def run_view(round: dict) -> tuple[dict | None, dict[str, str]]:
    """While a round runs: the task under way, and each target file's place in the round.

    A deep round first prepares every file (its programs), then audits each one; a standard round audits each
    file once. Read from the progress the Worker records; nothing here is stored.
    """
    progress = round.get("progress") or {}
    targets = [item["file_version_id"] for item in round["inputs"] if item["role"] == "target"]
    if round["execution"] in {"completed", "failed"} or not progress:
        return None, {}
    if progress.get("phase") == "validating":
        return {"stage": "summary"}, dict.fromkeys(targets, "done")
    stage, target = progress.get("stage"), progress.get("target")
    if stage not in {"check", "review"} or target not in targets:
        return None, {}
    deep = bool(round.get("deep_mode"))
    shown = "prepare" if deep and stage == "check" else "audit"
    finished = [item for item in progress.get("finished") or [] if item in targets and item != target]
    states = {}
    for item in targets:
        if item == target:
            states[item] = "processing"
        elif item in finished:
            states[item] = "prepared" if shown == "prepare" else "done"
        else:
            # Every file was prepared before the first one was audited.
            states[item] = "prepared" if deep and stage == "review" else "pending"
    return {"stage": shown, "file_version_id": target, "index": len(finished) + 1, "total": len(targets)}, states


def task_round_view(task: dict, round: dict, reports: dict[str, dict] | None = None) -> dict:
    finished = round["execution"] in {"completed", "failed"}
    run, run_states = run_view(round)
    files = []
    for row in task["files"]:
        frozen = next((item for item in round["inputs"] if item["file_version_id"] == row["file"]["id"] and item["role"] == "target"), None)
        if not frozen or row["selection"] != "selected":
            files.append({**row, "audit": None})
            continue
        audit = file_round_audit(round, frozen)
        if reports and row["file"]["id"] in reports:
            audit["report"] = reports[row["file"]["id"]]
        match = deepcopy(row.get("match"))
        if isinstance(match, dict):
            match["rule"] = deepcopy(frozen["rule"])
        deep = {"deep_review": deepcopy(frozen.get("deep_review"))} if round.get("deep_mode") else {}
        place = {"run_state": run_states[row["file"]["id"]]} if row["file"]["id"] in run_states else {}
        files.append({**row, "match": match, "audit": audit, **deep, **place})
    references = {item["file_version_id"] for item in round["inputs"] if item["role"] == "reference"}
    selected = [row for row in files if row["selection"] == "selected" and row["file"]["id"] not in references]
    ended = sum(bool(row.get("audit") and row["audit"]["execution"] in {"completed", "review_failed"}) for row in selected)
    task_finished = (
        finished
        and ended == len(selected)
        and all(row["audit"]["report"]["state"] not in {"pending", "generating"} or row["audit"]["report"].get("generation_request", {}).get("state") == "accepted" for row in selected)
    )
    audits = [row["audit"] for row in selected if row.get("audit")]
    failed = (
        round["execution"] == "failed"
        or has_execution_error(round["execution"], round.get("coverage") or {}, round.get("findings", []))
        or any(audit["execution"] == "review_failed" for audit in audits)
    )
    if not task_finished and round["execution"] == "completed" and not failed:
        # The audit is over and only its reports are still being made: the round is summarizing.
        run = {"stage": "summary"}
    # The round's standing, defined once: what writeback, release, the flow node and every page read.
    standing = {
        "conclusion": None if not finished else "ERROR" if failed else overall([_settled(audit, confirmed=True) for audit in audits]),
        "machine_conclusion": None if not finished else "ERROR" if failed else overall([_settled(audit, confirmed=False) for audit in audits]),
        "objection": any(check["objected"] for audit in audits for check in audit["checks"]),
    }
    return {
        **task,
        **standing,
        "round_id": round["id"],
        # When the accepted Round was created: live views count elapsed time from here.
        "round_started_at": round.get("created_at"),
        # The accepted Round's mode is the truth once a Task is accepted.
        "deep_mode": bool(round.get("deep_mode")),
        "execution": round["execution"],
        "has_execution_error": has_execution_error(round["execution"], round.get("coverage") or {}, round.get("findings", [])),
        "state": "completed" if task_finished else "reviewing",
        "completion_reason": "processed" if task_finished else None,
        "files": files,
        "confirmation_scope": {
            "target_file_version_ids": sorted(item["file_version_id"] for item in round["inputs"] if item["role"] == "target"),
            "reference_file_version_ids": sorted(references),
            "excluded_files": sorted(
                ({"file_version_id": row["file"]["id"], "reason": row["removal_reason"]} for row in files if row["selection"] == "removed"),
                key=lambda row: row["file_version_id"],
            ),
        },
        "progress": {"completed": ended, "total": len(selected)},
        **({"run": run} if run else {}),
    }
