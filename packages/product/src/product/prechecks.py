"""Frozen Task preparation facts; no audit Round, charge, or artifact ledger."""

from __future__ import annotations

import html
import json
import uuid

from audit.precheck_sources import l1_criteria, l1_spec, required_structure, structure_source

from product.standard_policy import config_digest

PRECHECK_KIND = "task_precheck"


def frozen_precheck_input(task: dict, tenant_id: str, policy: dict, *, targets: dict | None = None, initiating_user_id: str = "") -> dict:
    files = []
    for row in task["files"]:
        if row["selection"] == "removed":
            continue
        match = row.get("match") or {}
        rule = match.get("rule")
        body = rule.get("body") if rule else None
        target = (targets or {}).get(row["file"]["id"])
        execution = None
        if target is not None:
            frozen = target.to_dict()
            body = {**frozen["rules"], "appendix": frozen["appendix"], "suite_key": target.suite_key}
            rule = {**(rule or {}), "body": body}
            execution = {"mode": "checker_core", "suite_revision_id": target.suite_revision_id, "suite_digest": target.suite_digest, "binding": target.binding.to_dict()}
        elif body:
            execution = {"mode": "unavailable", "reason": "checker_suite_unavailable"}
        files.append(
            {
                "file": dict(row["file"]),
                "selection": row["selection"],
                "role": "reference" if match.get("state") == "reference" or row.get("reference_only") else "target",
                "matching_state": match.get("state"),
                "matching_gaps": list(match.get("gaps") or []),
                "document_type": match.get("document_type"),
                "metadata": match.get("metadata") or {},
                "rule": rule,
                "rule_execution": execution,
                "suite_target": target.to_dict() if target is not None else None,
                "required_structure": list(required_structure((body or {}).get("preset_key") or "")),
                "precheck_criteria": list(l1_criteria((body or {}).get("preset_key") or "")),
                "precheck_spec": l1_spec((body or {}).get("preset_key") or ""),
            }
        )
    customers = {
        customer
        for row in files
        for customer in ((row.get("rule") or {}).get("body") or {}).get("customers", [])
        if row.get("suite_target") or ((row.get("rule") or {}).get("body") or {}).get("rule_profile") == "v4"
    }
    return {
        "effective_customer": next(iter(customers)) if len(customers) == 1 else None,
        "customer_conflict": len(customers) > 1,
        "precheck_source": structure_source(),
        "schema_version": 2,
        "initiating_user_id": initiating_user_id,
        "task_id": task["id"],
        "tenant_id": tenant_id,
        "input_revision": task["input_revision"],
        "customer_preference": task["customer_preference"],
        "analysis_mode": task["analysis_mode"],
        "execution_policy": policy,
        "execution_revision": "standard-v1:" + config_digest(policy),
        "files": files,
    }


def precheck_identity(payload: dict) -> str:
    return str(uuid.uuid5(uuid.UUID(payload["task_id"]), "task-precheck:" + config_digest(payload)))


def blocked_targets(payload: dict, result: dict | None) -> set[str]:
    """A certified, exact-input, located defect is the only positive gate fact."""
    if not result or result.get("input_fingerprint") != config_digest(payload) or not payload.get("execution_policy", {}).get("app_revision"):
        return set()
    if result.get("execution_revision") != payload.get("execution_revision"):
        return set()
    targets = {row["file"]["id"]: row for row in payload["files"] if row["selection"] == "selected" and row["role"] == "target"}
    allowed_versions = {row["file"]["id"] for row in payload["files"]}
    blocked = set()
    for file in result.get("files") or []:
        version = file.get("file_version_id")
        source = targets.get(version)
        if not source or file.get("rule_version_id") != (source.get("rule") or {}).get("id"):
            continue
        execution = source.get("rule_execution") or {}
        if execution.get("mode") == "checker_core":
            if file.get("suite_execution") != execution or not execution.get("suite_revision_id") or not execution.get("binding", {}).get("checker_digest"):
                continue
        elif file.get("checker") != execution.get("checker") or not (execution.get("checker") or {}).get("class"):
            continue  # Historical receipts retain their original identity contract.
        criteria = {item["id"] for item in source.get("required_structure") or []}
        criteria.update(item["id"] for item in source.get("precheck_criteria") or [])
        for fact in file.get("facts") or []:
            if fact.get("source_id") not in criteria:
                continue
            if (
                fact.get("status") != "defect"
                or fact.get("certainty") != "confirmed"
                or fact.get("critical") is not True
                or fact.get("certified") is not True
                or fact.get("code") not in {"B01", "B02", "B04", "B05", "B06"}
            ):
                continue
            sources = (fact.get("evidence") or {}).get("sources") or []
            located = False
            for evidence in sources:
                try:
                    locator = json.loads(evidence["locator"])
                except (KeyError, TypeError, ValueError):
                    located = False
                    break
                if not isinstance(locator, dict) or locator.get("file_version_id") not in allowed_versions:
                    located = False
                    break
                located |= locator.get("file_version_id") == version and bool(evidence.get("excerpt")) and bool(locator.get("cell") or locator.get("page") or locator.get("range"))
            if located:
                blocked.add(version)
    return blocked


def render_precheck_record(receipt: dict) -> bytes:
    """Render the same frozen receipt, with no embedded originals or live URLs."""

    def escape(value):
        return html.escape(str(value if value is not None else ""))

    statuses = {"satisfied": "本项已核实", "defect": "发现问题", "unknown": "未核实", "not_checked": "未执行", "error": "执行失败", "handoff": "转正式审核判断", "not_applicable": "本项不适用"}
    source, result = receipt["input"], receipt.get("result") or {}
    receipt_status = receipt.get("status")
    status_key = receipt_status if isinstance(receipt_status, str) else ""
    overall = {
        "succeeded": "本次预检已完成，仍须核对各项未核实要求。",
        "failed": "本次预检执行失败，尚未取得完整结论。请读取原检查或重试。",
        "cancelled": "本次预检已取消，尚未取得完整结论。",
        "queued": "本次预检等待执行。",
        "leased": "本次预检正在执行。",
        "unknown": "本次预检执行状态尚未核实，请读取原检查。",
    }
    parts = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PPAP 预检检查记录</title>',
        "<style>body{font:16px system-ui;max-width:64rem;margin:2rem auto;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%}td,th{padding:.5rem;border:1px solid #bbb;text-align:left}small{color:#555}</style>",
        "<h1>PPAP 预检检查记录</h1><p>本记录只说明资料准备检查，不是正式审核合格结论。预检免费。</p>",
        f"<p>{escape(overall.get(status_key, '本次预检执行状态尚未核实。'))}</p>",
        f"<p>审核客户：{escape(source.get('effective_customer') or source.get('customer_preference') or '自动识别')}<br>任务：{escape(source['task_id'])}"
        f"<br>输入修订：{escape(source['input_revision'])}<br>快照：{escape(receipt['id'])}<br>保存时间：{escape(receipt['created_at'])}</p>",
        f"<p>执行版本：{escape(source.get('execution_revision'))}<br>应用版本：{escape((source.get('execution_policy') or {}).get('app_revision'))}</p>",
    ]
    by_version = {row["file_version_id"]: row for row in result.get("files") or []}
    for file in source["files"]:
        version = file["file"]["id"]
        observed = by_version.get(version) or {}
        execution = file.get("rule_execution") or {}
        suite = file.get("suite_target") or {}
        checker = observed.get("checker") or (file.get("rule_execution") or {}).get("checker") or {}
        rule = file.get("rule") or {}
        body = rule.get("body") or {}
        origin = "冻结 Suite" if suite else "客户预置规则" if body.get("rule_profile") == "v4" and body.get("preset_key") else "用户自定义规则" if rule else "尚无适用规则"
        spec = file.get("precheck_spec") or source.get("precheck_source") or {}
        implementation = (
            f"Suite：{escape(suite.get('suite_key'))} · 修订：{escape(execution.get('suite_revision_id'))} · 内容摘要：{escape(execution.get('suite_digest'))}"
            if execution.get("mode") == "checker_core"
            else f"Checker：{escape(checker.get('profile'))} {escape(checker.get('class'))}"
        )
        parts += [
            f"<h2>{escape(file['file']['original_filename'])}</h2>",
            f"<p>文件版本：{escape(version)}<br>角色：{escape(file['role'])}<br>规则：{escape(rule.get('title'))}（{escape(rule.get('id'))}）"
            f"<br>规则来源：{escape(origin)} {escape(body.get('preset_key'))}<br>任务模式：{escape(source.get('analysis_mode'))}"
            f"<br>实际 {implementation}</p>",
            f"<p>预检判定源：{escape(spec.get('source'))}<br>源文件摘要：{escape(spec.get('source_sha256'))}</p>",
            "<table><thead><tr><th>检查事实</th><th>状态与原因</th><th>来源与下一步</th></tr></thead><tbody>",
        ]
        for fact in observed.get("facts") or []:
            evidence = "；".join(f"{item.get('locator', '')} {item.get('excerpt', '')}" for item in (fact.get("evidence") or {}).get("sources") or [])
            codes = " ".join(str(code) for code in [fact.get("code"), *(fact.get("warning_codes") or [])] if code)
            parts.append(
                f"<tr><td>{escape(fact.get('source_id'))}<br>{escape(fact.get('message'))}</td><td>{escape(fact.get('status'))} {escape(codes)}"
                f"{escape(statuses.get(fact.get('status'), '状态未核实'))}</td><td>{escape(evidence)}<br>{escape(fact.get('next_step'))}</td></tr>"
            )
        if not observed.get("facts"):
            parts.append('<tr><td colspan="3">尚无已执行的内容预检结果，不能视为全部通过。</td></tr>')
        parts.append("</tbody></table>")
        if body.get("appendix"):
            parts.append(
                "<details><summary>本次冻结的自定义规则附录与作用范围</summary><pre>"
                f"{escape(json.dumps(body['appendix'], ensure_ascii=False, sort_keys=True, indent=2))}</pre>"
                "<p>本预检记录不代替正式 Deep 自定义规则审核结论。</p></details>"
            )
    parts.append("<p>未覆盖、未核实、技术失败和受控模板待核均不等于文件不合格。原件读取仍须单独授权；本记录不包含原件访问令牌。</p></html>")
    return "\n".join(parts).encode("utf-8")
