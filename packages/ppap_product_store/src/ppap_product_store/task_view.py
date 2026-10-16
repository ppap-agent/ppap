"""Read projection of the existing frozen Round into the V3.1 Task contract."""

from __future__ import annotations

from copy import deepcopy


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
    if not isinstance(locator, dict):
        return None
    schemas: dict[str, dict[str, type]] = {
        "xlsx_cell": {"sheet": str, "cell": str},
        "xlsx_range": {"sheet": str, "start": str, "end": str},
        "pdf_block": {"page": int, "block": int},
        "docx_paragraph": {"paragraph": int},
        "docx_cell": {"table": int, "row": int, "column": int},
    }
    kind = locator.get("kind")
    fields = schemas.get(kind) if isinstance(kind, str) else None
    if not fields or any(type(locator.get(key)) is not expected or expected is str and not locator[key].strip() or expected is int and locator[key] < 0 for key, expected in fields.items()):
        return None
    return {"kind": locator["kind"], **{key: locator[key] for key in fields}}


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


def _check(round: dict, version_id: str, rule_id: str, rule: dict) -> dict:
    finished = round["execution"] == "completed"
    coverage, original, notice = _clause_result(round, version_id, str(rule["id"])) if finished else (None, None, None)
    finding = deepcopy(original)
    if finding:
        finding["item_key"] = rule["id"]
    evidence, scope, evidence_notice = _evidence(round, finding, version_id, rule_id, str(rule["id"]))
    return {
        "rule_item": rule,
        "execution": "failed" if round["execution"] == "failed" or finding and finding["status"] == "ERR" else "completed" if finished else round["execution"],
        "coverage": "executed" if finding and (coverage or {}).get("coverage") != "unsupported" else (coverage or {}).get("coverage", "unsupported") if finished else "mapped",
        "finding": finding,
        "evidence_verified": False,
        "scope": scope,
        "scope_notice": notice or evidence_notice or (coverage or {}).get("reason") or ("请核对原件中的证据和检查范围。" if finding else None),
        "evidence": evidence,
        "review_item_key": original["item_key"] if original else f"coverage:{version_id}:{rule['id']}",
        "review": deepcopy((original or coverage or {}).get("review")),
    }


def _counts(checks: list[dict]) -> dict:
    counts = {"pass_count": 0, "ng_count": 0, "unknown_count": 0, "not_covered_count": 0, "error_count": 0}
    for check in checks:
        if check["execution"] in {"queued", "running"}:
            continue
        finding = check["finding"]
        if check["execution"] == "failed" or finding and finding["status"] == "ERR":
            counts["error_count"] += 1
        elif check["coverage"] != "executed":
            counts["not_covered_count"] += 1
        else:
            # Persisted legacy positions are not verified semantic evidence.
            counts["unknown_count"] += 1
    return counts


def task_round_view(task: dict, round: dict) -> dict:
    finished = round["execution"] in {"completed", "failed"}
    files = []
    for row in task["files"]:
        frozen = next((item for item in round["inputs"] if item["file_version_id"] == row["file"]["id"] and item["role"] == "target"), None)
        if not frozen or row["selection"] != "selected":
            files.append({**row, "audit": None})
            continue
        rules = frozen["rule"]["body"].get("rules", [])
        checks = [_check(round, row["file"]["id"], frozen["rule"]["id"], rule) for rule in rules]
        files.append(
            {
                **row,
                "audit": {
                    "round_id": round["id"],
                    "file_version_id": row["file"]["id"],
                    "result_attempt": round["result_attempt"],
                    "rule": frozen["rule"],
                    "execution": "review_failed" if round["execution"] == "failed" else round["execution"],
                    "progress": {"completed": len(checks) if finished else 0, "total": len(checks)},
                    "failure": round.get("failure"),
                    "conclusion": "UNKNOWN" if finished else None,
                    "counts": _counts(checks),
                    "checks": checks,
                    "summary": None,
                    "report": {"state": "not_eligible", "report_id": None, "reason": "审核依据尚未完整核验" if finished else "审核尚未完成"},
                },
            }
        )
    selected = [row for row in files if row["selection"] == "selected"]
    ended = sum(bool(row.get("audit") and row["audit"]["execution"] in {"completed", "review_failed"}) for row in selected)
    task_finished = finished and ended == len(selected)
    return {
        **task,
        "round_id": round["id"],
        "state": "completed" if task_finished else "reviewing",
        "completion_reason": "processed" if task_finished else None,
        "files": files,
        "progress": {"completed": ended, "total": len(selected)},
    }
