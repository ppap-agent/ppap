"""Free Suite preparation inside the same low-privilege guest as formal audit."""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path
from types import MappingProxyType

from audit.checker_core import checker_digest
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.engine.runtime import bind_runtime
from audit.precheck.contract import STATUSES, complete_coverage, customer_conflict, empty_result, fact, result_row, validate_result
from audit.precheck.sources import l1_spec, structure_source
from documents.model_access import model_access_scope

from agent.execution.audit_runtime import CheckerContext, ClauseTimeout, _clause_deadline, _load_program, _restore_modules
from agent.execution.checker_runtime import CheckerRuntime


def run_precheck(payload, snapshot, sources, programs):
    """No formal finding is produced and no paid/model capability is installed."""
    if payload.get("precheck_source") != structure_source():
        raise ValueError("precheck_source_changed")
    result = empty_result(payload)
    conflict = customer_conflict(payload)
    if snapshot is not None and not conflict:
        snapshot.validate_sources(sources)
        expected = {target.binding.checker_digest for target in snapshot.targets}
        if set(programs) != expected or any(checker_digest(code) != digest for digest, code in programs.items()):
            raise ValueError("checker_program_digest_mismatch")
    targets = {target.file_version_id: target for target in snapshot.targets} if snapshot is not None and not conflict else {}
    with tempfile.TemporaryDirectory(prefix="ppap-precheck-guest-") as directory:
        root, audits, paths, closeables = Path(directory), {}, {}, []
        runtime = CheckerRuntime(allow_model_tools=False)
        group = GroupContextInput(task_group_id=payload["task_id"])
        try:
            if targets:
                for index, source in enumerate(snapshot.inputs):
                    path = root / f"source-{index}{Path(source.filename).suffix.lower()}"
                    path.write_bytes(sources[source.file_version_id])
                    path.chmod(0o400)
                    paths[source.file_version_id] = path
                    audit = AuditInput(
                        audit_id=source.file_version_id,
                        file_url=str(path),
                        customer=source.customer,
                        model=source.model,
                        part=source.part,
                        file_metadata={"file_version_id": source.file_version_id, "role": source.role, "original_filename": source.filename},
                    )
                    audits[source.file_version_id] = audit
                    group.by_audit_id[source.file_version_id] = audit
                    if source.document_type:
                        group.by_doc_type.setdefault(source.document_type, []).append(audit)
            with model_access_scope(None), bind_runtime(runtime):
                for index, source in enumerate(payload["files"]):
                    row = result_row(source)
                    result["files"].append(row)
                    facts = row["facts"]
                    if source["selection"] != "selected":
                        facts.append(fact("selection", "not_checked", "用户未选择此文件；未执行预检。", code="S01"))
                    elif conflict:
                        facts.append(fact("routing", "unknown", "任务识别到多个客户，关联范围尚不能确定；请按客户拆分任务后重试。未对本文件作缺陷判断。", code="B03"))
                    elif source["role"] == "reference":
                        facts.append(fact("reference", "handoff", "此文件是关联参考资料；不创建收费审核目标。", code="S03"))
                    elif source["file"]["id"] not in targets:
                        facts.append(fact("routing", "error" if source.get("matching_state") == "failed" else "not_checked", "尚未取得可执行的冻结 Suite；未作文件缺陷判断。"))
                    else:
                        target = targets[source["file"]["id"]]
                        if source.get("suite_target") != target.to_dict():
                            raise ValueError("precheck_target_mismatch")
                        if not target.binding.entrypoints:
                            facts.append(fact("coverage", "not_checked", "此 Suite 尚无可执行条款 Binding；不能视为通过。"))
                            continue
                        body = target.to_dict()["rules"]
                        if source.get("precheck_spec") != l1_spec(body.get("preset_key") or ""):
                            raise ValueError("precheck_l1_source_changed")
                        try:
                            modules, saved = _load_program(programs[target.binding.checker_digest], root / f"program-{index}")
                            try:
                                hook = getattr(modules["checker"], "precheck", None)
                                if hook is not None and not callable(hook):
                                    raise ValueError("invalid_precheck_hook")
                                if hook is None:
                                    facts.append(fact("coverage", "not_checked", "此 Suite 尚未实现免费准备检查；不能视为通过。"))
                                else:
                                    rule = RuleInput.model_validate({**body, "rule_id": target.suite_revision_id, "appendix": target.to_dict()["appendix"]})
                                    context = CheckerContext(
                                        snapshot, target, "", audits[target.file_version_id], rule, group, MappingProxyType(sources), MappingProxyType(paths), runtime, {}, closeables.append
                                    )
                                    with _clause_deadline(120):
                                        observed = hook(context)
                                    if not isinstance(observed, list) or len(observed) > 500:
                                        raise ValueError("invalid_precheck_fact")
                                    for item in observed:
                                        if not isinstance(item, dict) or item.get("status") not in STATUSES:
                                            raise ValueError("invalid_precheck_fact")
                                        # A criterion may have several distinct element facts.
                                        # Keep their unique IDs separate from their shared source.
                                        identity = item.get("id", item.get("source_id"))
                                        source_id = item.get("source_id", identity)
                                        if not isinstance(identity, str) or not identity or not isinstance(source_id, str) or not source_id:
                                            raise ValueError("invalid_precheck_fact")
                                        facts.append({**fact(identity, item["status"], item.get("message") or ""), **item, "id": identity, "source_id": source_id, "certified": False})
                                facts.append(fact("engineering", "handoff", "正式工程结论由正式审核处理；预检未声明覆盖全部规则。", code="S03"))
                            finally:
                                _restore_modules(saved)
                        except (Exception, ClauseTimeout):
                            facts.clear()
                            facts.append(fact("execution", "error", "内容检查执行失败；未取得业务结论。"))
            if targets:
                snapshot.validate_sources({identity: path.read_bytes() for identity, path in paths.items()})
        finally:
            for item in reversed(closeables):
                close = getattr(item, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()
            runtime.close()
    return validate_result(payload, complete_coverage(payload, result))
