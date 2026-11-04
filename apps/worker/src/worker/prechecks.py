"""Execute free, immutable Task preparation facts using existing customer checkers."""

from __future__ import annotations

import hashlib
import tempfile
import threading
from pathlib import Path

from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.engine.checkers.rule_declarations import _agent_for_target, discover_declared_rules, freeze_target_rule_execution
from audit.precheck_sources import structure_source
from product.standard_policy import config_digest


def _fact(source_id, status, message, *, code=None, evidence=None):
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
        "next_step": "继续正式审核。" if status == "satisfied" else "查看原件并确认；修正资料后重新检查。",
    }


def execute_precheck(store, payload):
    """Read only the frozen file versions. No round, charge, review or notification."""
    if payload.get("precheck_source") != structure_source():
        raise ValueError("precheck_source_changed")
    if payload["execution_policy"] != store.settings.standard_execution_policy().model_dump(mode="json"):
        raise ValueError("precheck_execution_policy_changed")
    result = {
        "input_fingerprint": config_digest(payload),
        "execution_revision": payload["execution_revision"],
        "files": [],
        "notice": "只展示实际执行的准备检查；未覆盖和未知项不代表通过，正式工程结论留给正式审核。",
    }
    with tempfile.TemporaryDirectory(prefix="ppap-precheck-") as directory:
        audits, failures = {}, {}
        group = GroupContextInput(task_group_id=payload["task_id"])
        for source in payload["files"]:
            if source["selection"] != "selected":
                continue
            file = source["file"]
            try:
                version = store.get_version(file["file_id"], file["id"])
                if version is None or version.tenant_id != payload["tenant_id"] or version.status != "ready" or version.sha256 != file["sha256"]:
                    raise ValueError("frozen_file_unavailable")
                data = store.version_bytes(version)
                if hashlib.sha256(data).hexdigest() != file["sha256"]:
                    raise ValueError("frozen_file_digest_mismatch")
                path = Path(directory) / (file["id"] + Path(file["original_filename"]).suffix)
                path.write_bytes(data)
                audit = AuditInput(audit_id=file["id"], file_url=str(path), file_metadata={"file_version_id": file["id"], "role": source["role"], "original_filename": file["original_filename"]})
                audits[file["id"]] = audit
                group.by_audit_id[file["id"]] = audit
                kind = source.get("document_type")
                if kind:
                    group.by_doc_type.setdefault(kind, []).append(audit)
            except Exception:
                failures[file["id"]] = True
                group.unavailable_files[file["id"]] = source.get("document_type")
        for source in payload["files"]:
            file, rule = source["file"], source.get("rule") or {}
            execution = source.get("rule_execution") or {}
            row = {"file_version_id": file["id"], "rule_version_id": rule.get("id"), "checker": execution.get("checker"), "facts": []}
            result["files"].append(row)
            facts = row["facts"]
            if source["selection"] != "selected":
                facts.append(_fact("selection", "not_checked", "用户未选择此文件；未执行预检。", code="S02"))
                continue
            if file["id"] in failures:
                facts.append(_fact("original", "error", "冻结原件读取失败；尚无业务结论。", code="S05"))
                continue
            if source["role"] == "reference":
                facts.append(_fact("reference", "handoff", "此文件是关联参考资料；不创建收费审核目标。", code="S03"))
                continue
            body = rule.get("body") or {}
            if not body or source.get("matching_state") != "matched":
                facts.append(_fact("routing", "unknown", "尚未绑定可执行的客户规则。", code="S02"))
                continue
            try:
                if freeze_target_rule_execution(body, file["original_filename"]) != execution:
                    raise ValueError("checker_binding_changed")
                cls = _agent_for_target(
                    body.get("type") or "", file["original_filename"], body.get("rule_profile") or "v2", preset_key=body.get("preset_key"), preset_customer=(body.get("customers") or [None])[0]
                )
                if cls is None:
                    raise ValueError("checker_unavailable")
                declarations = discover_declared_rules(cls)
                numeric = [{"id": str(index), "category": text, "priority": "P0", "rule_to_check": text, "type_of_rule": "text"} for index, (_, text) in enumerate(declarations, 1)]
                checker = cls(audits[file["id"]], RuleInput(rule_id=rule["id"], **{**{key: value for key, value in body.items() if key != "kind"}, "rules": numeric}), group)
                methods = getattr(cls, "PRECHECK_METHODS", {})
                for source_id, method in methods.items():
                    indices = [index for index, (name, _) in enumerate(declarations) if name == method]
                    if len(indices) != 1:
                        facts.append(_fact(source_id, "error", "预检方法绑定无效。", code="S05"))
                        continue
                    observed = checker.check(indices[0])
                    status = getattr(observed, "status", None)
                    evidence = getattr(observed, "evidence", None)
                    # A formal NG is never promoted into a certified precheck defect.
                    mapped = "satisfied" if status == "PASS" and evidence and evidence.sources else "error" if status == "ERR" else "unknown"
                    facts.append(_fact(source_id, mapped, getattr(observed, "suggestions", "未取得检查结论。"), evidence=evidence.model_dump(mode="json") if evidence else None))
                custom = getattr(checker, "precheck", None)
                if custom:
                    for fact in custom():
                        if not isinstance(fact, dict) or fact.get("status") not in {"satisfied", "defect", "unknown", "not_checked", "error", "handoff"}:
                            raise ValueError("invalid_precheck_fact")
                        facts.append({**_fact(fact.get("source_id") or fact.get("id") or "unbound", fact["status"], fact.get("message") or ""), **fact, "certified": False})
                if not facts:
                    facts.append(_fact("coverage", "not_checked", "此客户条目的准备期检查尚未实现；不能视为通过。", code="S02"))
                facts.append(_fact("engineering", "handoff", "正式工程结论由正式审核处理；预检未声明覆盖全部规则。", code="S03"))
            except Exception:
                facts.append(_fact("execution", "error", "内容检查执行失败；未取得业务结论。", code="S05"))
    for source, row in zip(payload["files"], result["files"]):
        expected = source.get("required_structure") or []
        observed = {fact["id"] for fact in row["facts"]}
        for criterion in expected:
            if criterion["id"] not in observed:
                fact = _fact(criterion["id"], "not_checked", criterion["criterion"], code="S02")
                fact["source_clause"] = criterion["source_clause"]
                fact["next_step"] = "此结构准则尚未核实，请人工检查原件；不能视为通过。"
                row["facts"].append(fact)
        actual = {fact["id"]: fact for fact in row["facts"] if fact["id"] in {item["id"] for item in expected}}
        row["coverage"] = {
            "required_structure": len(expected),
            "evaluated_structure": sum(fact["status"] in {"satisfied", "defect"} for fact in actual.values()),
            "unresolved_structure": sum(fact["status"] not in {"satisfied", "defect"} for fact in actual.values()),
            "complete": False,
        }
    return result


def process_task_precheck(store, claimed, ident):
    stopped = threading.Event()

    def renew():
        while not stopped.wait(10):
            try:
                if not store.renew_job_lease(claimed, ident):
                    return
            except Exception:
                return

    renewal = threading.Thread(target=renew, daemon=True)
    renewal.start()
    try:
        try:
            result, outcome = execute_precheck(store, claimed.payload), "succeeded"
        except Exception:
            result, outcome = {"error": "precheck_execution_failed", "files": [], "input_fingerprint": config_digest(claimed.payload)}, "failed"
        store.complete_job(claimed, ident, result, outcome)
        return result
    finally:
        stopped.set()
        renewal.join(timeout=1)
