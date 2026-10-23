"""Fault injection at the real checker boundary protects engine error handling."""

import pytest
from openpyxl import Workbook
from ppap_core.contracts import AuditInput, RuleInput
from ppap_core.types import PpapDocumentType
from ppap_excel_audit_engine import run_audit
from ppap_excel_audit_engine.app_runtime import _AppAuditRuntime
from ppap_excel_audit_engine.routing import resolve_engine_agent_class
from ppap_excel_audit_engine.runtime import bind_runtime, get_runtime


@pytest.mark.parametrize("profile", ["v2", "v3"])
@pytest.mark.parametrize("with_runtime", [False, True], ids=["without-runtime", "with-runtime"])
@pytest.mark.parametrize("failure", [RuntimeError, TypeError], ids=["runtime-error", "type-error"])
def test_public_engine_checker_exception_returns_err_without_retry(tmp_path, monkeypatch, profile, with_runtime, failure):
    path = tmp_path / "suppliers.xlsx"
    workbook = Workbook()
    workbook.active.title = "材料产品及分供方清单"
    workbook.active.append(["零件号", "单件重量"])
    workbook.active.append(["PART-001", 3.1897])
    workbook.save(path)
    workbook.close()
    original_bytes = path.read_bytes()
    audit = AuditInput(audit_id="checker-boundary-failure", file_url=str(path))
    rules = RuleInput(rule_id="confirmed-supplier-rules", rule_profile=profile, type="材料分供方")
    checker_class = resolve_engine_agent_class(PpapDocumentType.SUB_SUPPLIER_LIST, str(path), profile)
    assert checker_class is not None
    runtime = _AppAuditRuntime() if with_runtime else None
    calls = []

    def fail_check(agent, index):
        assert type(agent) is checker_class
        assert agent.audit_file is audit
        assert agent.rule is rules
        assert get_runtime() is runtime
        loaded = agent._load_workbook()
        assert loaded.active["B2"].value == 3.1897
        calls.append((agent, index, loaded))
        raise failure("injected checker boundary failure")

    # Only this call boundary is replaced; routing, construction, IO and close run.
    monkeypatch.setattr(checker_class, "check", fail_check)
    previous_runtime = get_runtime()
    outer_runtime = _AppAuditRuntime()
    with bind_runtime(outer_runtime):
        results = run_audit(audit, rules, runtime=runtime)
        assert get_runtime() is outer_runtime

    assert get_runtime() is previous_runtime
    assert len(calls) == 1
    agent, index, loaded = calls[0]
    assert index == 0
    assert loaded.active["B2"].value == 3.1897
    assert agent._workbook_cache is None
    assert len(results) == 1
    assert results[0].status == "ERR"
    assert "injected checker boundary failure" in results[0].suggestions
    assert path.read_bytes() == original_bytes
