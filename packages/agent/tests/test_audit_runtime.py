"""Controlled native/process validation; no test invokes a paid provider."""

import hashlib
import json
import os
import signal

import pytest
from agent.execution.audit_runtime import AuditRunError, run_audit, run_audit_process
from agent.execution.checker_runtime import CheckerRuntime
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest

PROGRAM = b"""import os
from audit.contracts import AuditResultItem

def check(context):
    context.cache['count'] = context.cache.get('count', 0) + 1
    return AuditResultItem(classification='test', item=context.clause_id, priority='P0',
        status='PASS', suggestions=str(os.getpid()) + ':' + str(context.cache['count']), clause_id=context.clause_id)
"""


def request(program=PROGRAM, clauses=("a", "b"), *, targets=2, unbound=False):
    files = {"checker.py": program}
    digest = checker_digest(files)
    sources = {f"target-{index}": f"original-{index}".encode() for index in range(targets)}
    sources["reference"] = b"reference original"
    inputs = tuple(FrozenInput(key, hashlib.sha256(data).hexdigest(), "reference" if key == "reference" else "target", key + ".txt") for key, data in sources.items())
    rules = {"rules": [{"id": clause, "rule_to_check": clause} for clause in clauses] + ([{"id": "unbound"}] if unbound else [])}
    bound = tuple(FrozenTarget(key, "revision", "suite", "a" * 64, rules, RuleBinding("suite", digest, {clause: "checker:check" for clause in clauses})) for key in sources if key != "reference")
    snapshot = ExecutionSnapshot("tenant", "initiator", inputs, bound, "runtime", "library", {}, round_id="round")
    return snapshot, sources, {digest: files}


def test_one_guest_many_targets_clauses_shared_cache():
    args = request(unbound=True)
    result = run_audit_process(*args)
    assert result["process_id"] != os.getpid()
    assert [row["suggestions"] for row in result["findings"]] == [f"{result['process_id']}:{index}" for index in range(1, 5)]
    assert len([row for row in result["coverage"] if row["coverage"] == "unsupported"]) == 2
    assert {row["file_version_id"] for row in result["findings"]} == {"target-0", "target-1"}


def test_failure_isolated_without_unknown_becoming_pass():
    program = PROGRAM.replace(b"context.cache['count'] =", b"if context.clause_id == 'a': raise RuntimeError('a secret must not escape')\n    context.cache['count'] =")
    result = run_audit(*request(program, targets=1))
    assert [row["status"] for row in result["findings"]] == ["ERR", "PASS"]
    assert "secret" not in json.dumps(result)


def test_clause_timeout_isolated_and_signal_restored():
    program = PROGRAM.replace(b"context.cache['count'] =", b"if context.clause_id == 'a':\n        import time; time.sleep(1)\n    context.cache['count'] =")
    old = signal.getsignal(signal.SIGALRM)
    result = run_audit(*request(program, targets=1), clause_timeout=0.02)
    assert [row["status"] for row in result["findings"]] == ["ERR", "PASS"]
    assert signal.getsignal(signal.SIGALRM) == old


@pytest.mark.parametrize("mutation", ["digest", "missing", "extra"])
def test_input_integrity_fails_before_execution(mutation):
    snapshot, sources, programs = request()
    if mutation == "digest":
        sources["target-0"] = b"changed"
    elif mutation == "missing":
        sources.pop("reference")
    else:
        sources["unauthorized"] = b"other tenant"
    with pytest.raises(ValueError):
        run_audit(snapshot, sources, programs)


def test_wrong_clause_and_wrong_source_fail_whole_contract():
    program = PROGRAM.replace(b"clause_id=context.clause_id", b"clause_id='foreign-clause'")
    with pytest.raises(ValueError, match="result_unknown_clause"):
        run_audit(*request(program))
    program = b"""def check(context):
        return dict(classification='x',item='x',priority='P0',status='PASS',suggestions='',clause_id=context.clause_id,evidence_file_version_ids=['unauthorized'])
"""
    with pytest.raises(ValueError, match="result_invalid_file_reference"):
        run_audit(*request(program))


def test_modified_original_rejected_after_execution():
    program = PROGRAM.replace(b"context.cache['count'] =", b"path = context.paths[context.audit_file.audit_id]\n    path.chmod(0o600); path.write_bytes(b'tampered')\n    context.cache['count'] =")
    with pytest.raises(ValueError, match="snapshot_source_digest_mismatch"):
        run_audit(*request(program, targets=1))


def test_program_hash_checked_and_entrypoint_is_callable_only():
    snapshot, sources, programs = request()
    programs[next(iter(programs))]["checker.py"] += b"\n# changed"
    with pytest.raises(AuditRunError, match="checker_program_digest_mismatch"):
        run_audit(snapshot, sources, programs)
    with pytest.raises(AuditRunError, match="checker_entrypoint_missing"):
        run_audit(*request(b"check = None\n"))


def test_crash_timeout_cancel_never_return_partial_success():
    with pytest.raises(AuditRunError, match="guest_failed"):
        run_audit_process(*request(b"import os\ndef check(context): os._exit(7)\n"))
    with pytest.raises(AuditRunError, match="timeout"):
        run_audit_process(*request(b"import time\ndef check(context): time.sleep(60)\n"), timeout=0.2)
    with pytest.raises(AuditRunError, match="cancelled"):
        run_audit_process(*request(), cancelled=lambda: True)


def test_provider_credentials_not_inherited(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "host-provider-secret")
    program = PROGRAM.replace(b"context.cache['count'] =", b"assert 'OPENAI_API_KEY' not in os.environ\n    context.cache['count'] =")
    assert all(row["status"] == "PASS" for row in run_audit_process(*request(program))["findings"])


def test_tools_reuse_success_and_failed_receipts(monkeypatch):
    from audit.engine.checkers import ocr_visual_facts
    from documents.ocr.paddle import reader

    calls = []

    def ocr(**kwargs):
        calls.append("ocr")
        return {"observed": True}

    def paddle(*args):
        calls.append("paddle")
        raise RuntimeError("unknown external receipt")

    monkeypatch.setattr(ocr_visual_facts, "request_ocr_visual_facts", ocr)
    monkeypatch.setattr(reader, "read_pdf_page_evidence", paddle)
    runtime = CheckerRuntime(allow_model_tools=True)
    for _ in range(2):
        assert runtime.read_ocr_visual_facts(images=[b"original page"], prompt="read", response_schema={}) == {"observed": True}
        with pytest.raises(RuntimeError):
            runtime.read_paddle_page("/tmp/frozen.pdf", 1)
    assert calls == ["ocr", "paddle"]
    runtime.close()


def test_plain_function_can_use_dataclasses_and_original_multicarrier_sources():
    import io

    import fitz
    from docx import Document
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active["A1"] = "native Excel"
    excel = io.BytesIO()
    workbook.save(excel)
    workbook.close()
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "native PDF")
    pdf_bytes = pdf.tobytes()
    pdf.close()
    docx = Document()
    docx.add_paragraph("native DOCX")
    word = io.BytesIO()
    docx.save(word)
    program = b"""from dataclasses import dataclass
from audit.contracts import AuditResultItem
from documents.contracts import FileRef
@dataclass
class Marker:
    value: str

def check(context):
    import fitz
    from docx import Document
    path = context.paths[context.audit_file.audit_id]
    if path.suffix == '.xlsx':
        book = context.runtime.load_workbook_auto(FileRef(location=str(path)))
        value = book.active['A1'].value
    elif path.suffix == '.pdf':
        with fitz.open(path) as pdf:
            value = pdf[0].get_text().strip()
    else:
        value = Document(path).paragraphs[0].text
    assert len(context.sources) == 3
    return AuditResultItem(classification='native', item='fact', priority='P0', status='PASS', suggestions=Marker(value).value, clause_id=context.clause_id)
"""
    files = {"checker.py": program}
    digest = checker_digest(files)
    sources = {"excel": excel.getvalue(), "pdf": pdf_bytes, "docx": word.getvalue()}
    inputs = tuple(FrozenInput(key, hashlib.sha256(value).hexdigest(), "target", key + extension) for (key, value), extension in zip(sources.items(), (".xlsx", ".pdf", ".docx")))
    targets = tuple(FrozenTarget(key, "revision", "suite", "a" * 64, {"rules": [{"id": "read"}]}, RuleBinding("suite", digest, {"read": "checker:check"})) for key in sources)
    snapshot = ExecutionSnapshot("tenant", "initiator", inputs, targets, "runtime", "library", {})
    result = run_audit_process(snapshot, sources, {digest: files})
    assert [row["suggestions"] for row in result["findings"]] == ["native Excel", "native PDF", "native DOCX"]


def test_no_personal_model_access_never_uses_environment_key(monkeypatch):
    from documents.model_access import model_access_scope

    program = PROGRAM.replace(b"context.cache['count'] =", b"from documents.model_access import current_model_access\n    current_model_access()\n    context.cache['count'] =")
    monkeypatch.setenv("OPENAI_API_KEY", "host-key")
    with model_access_scope(None):
        result = run_audit(*request(program))
    assert all(row["status"] == "ERR" and row["suggestions"] == "checker_failure:ModelUnavailable" for row in result["findings"])


def test_clause_deadline_cannot_be_swallowed_by_legacy_fallback():
    program = PROGRAM.replace(
        b"context.cache['count'] =",
        b"if context.clause_id == 'a':\n        try:\n            import time; time.sleep(1)\n        except Exception:\n            pass\n    context.cache['count'] =",
    )
    result = run_audit(*request(program, targets=1), clause_timeout=0.01)
    assert [row["status"] for row in result["findings"]] == ["ERR", "PASS"]
    assert result["findings"][0]["suggestions"] == "checker_clause_timeout_external_usage_unknown"


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), 0, -1])
def test_invalid_deadline_fails_before_guest(seconds):
    with pytest.raises(ValueError, match="invalid_clause_timeout"):
        run_audit(*request(), clause_timeout=seconds)


def test_usage_receipts_are_returned_and_parent_scope_restored():
    from audit.usage import get_token_tracker

    tracker = get_token_tracker()
    parent = tracker.start_request_tracking()
    program = PROGRAM.replace(b"context.cache['count'] =", b"from audit.usage import record_token_usage\n    record_token_usage(3, 2, model_name='controlled')\n    context.cache['count'] =")
    try:
        result = run_audit(*request(program, targets=1))
        assert result["usage"]["total_tokens"] == 10
        assert result["usage"]["llm_calls_count"] == 2
        assert result["usage"]["usage_complete"]
        assert tracker.get_current_request_id() == parent
        assert tracker.get_request_summary(result["usage"]["request_id"]) is None
    finally:
        tracker.cleanup_request(parent)
        tracker.set_current_request_id(None)


def test_unknown_usage_never_becomes_zero():
    program = PROGRAM.replace(b"context.cache['count'] =", b"from audit.usage import record_unreported_usage\n    record_unreported_usage()\n    context.cache['count'] =")
    usage = run_audit(*request(program, targets=1))["usage"]
    assert usage["total_tokens"] is None
    assert not usage["usage_complete"]


def test_original_filename_survives_safe_local_materialization():
    program = PROGRAM.replace(b"context.cache['count'] =", b"assert context.audit_file.file_metadata['original_filename'] == context.audit_file.audit_id + '.txt'\n    context.cache['count'] =")
    assert all(row["status"] == "PASS" for row in run_audit(*request(program))["findings"])
