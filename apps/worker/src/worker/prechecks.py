"""Queue fencing and identity-only dispatch for free Suite preparation."""

from __future__ import annotations

import threading

from agent.client import DeepClient
from audit.precheck_contract import complete_coverage, customer_conflict, empty_result, fact, input_digest, result_row, validate_result


def unavailable_result(payload):
    """No executable target or conflicting customer: no original/code is read."""
    result = empty_result(payload)
    conflict = customer_conflict(payload)
    for source in payload["files"]:
        row = result_row(source)
        result["files"].append(row)
        if source["selection"] != "selected":
            row["facts"].append(fact("selection", "not_checked", "用户未选择此文件；未执行预检。", code="S01"))
        elif conflict:
            row["facts"].append(fact("routing", "unknown", "任务识别到多个客户，关联范围尚不能确定；请按客户拆分任务后重试。未对本文件作缺陷判断。", code="B03"))
        elif source["role"] == "reference":
            row["facts"].append(fact("reference", "handoff", "此文件是关联参考资料；不创建收费审核目标。", code="S03"))
        else:
            row["facts"].append(fact("routing", "not_checked", "尚未取得可执行的冻结 Suite；未作文件缺陷判断。"))
    return validate_result(payload, complete_coverage(payload, result))


def execute_precheck(store, payload, *, claimed=None, ident=None, cancelled=None):
    """Worker never loads a Suite program or imports a customer checker."""
    if payload["execution_policy"] != store.settings.standard_execution_policy().model_dump(mode="json"):
        raise ValueError("precheck_execution_policy_changed")
    if customer_conflict(payload) or not any(row.get("suite_target") for row in payload["files"]):
        return unavailable_result(payload)
    if claimed is None or not ident:
        raise ValueError("precheck_lease_required")
    result = DeepClient(store.settings.deep_controller_socket).precheck(
        {"op": "precheck", "job_id": claimed.id, "tenant_id": claimed.tenant_id, "attempt": claimed.attempt, "lease_owner": ident}, cancelled=cancelled
    )
    return validate_result(payload, result)


def process_task_precheck(store, claimed, ident):
    stopped, cancelled = threading.Event(), threading.Event()

    def renew():
        while not stopped.wait(10):
            try:
                if store.renew_job_lease(claimed, ident):
                    continue
            except Exception:
                pass
            cancelled.set()
            return

    renewal = threading.Thread(target=renew, daemon=True)
    renewal.start()
    try:
        try:
            result, outcome = execute_precheck(store, claimed.payload, claimed=claimed, ident=ident, cancelled=cancelled), "succeeded"
        except Exception:
            result, outcome = {"error": "precheck_execution_failed", "files": [], "input_fingerprint": input_digest(claimed.payload)}, "failed"
        store.complete_job(claimed, ident, result, outcome)
        return result
    finally:
        stopped.set()
        renewal.join(timeout=1)
