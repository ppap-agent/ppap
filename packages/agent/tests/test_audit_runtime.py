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


@pytest.mark.parametrize("shared_entrypoint", [False, True])
@pytest.mark.parametrize("result_type", ["model", "dict"])
def test_direct_entrypoint_cannot_return_another_bound_clause(shared_entrypoint, result_type):
    from dataclasses import replace

    program = b"""from audit.contracts import AuditResultItem

def check(context):
    context.cache.setdefault('calls', []).append(context.clause_id)
    item = AuditResultItem(classification='rule', item=context.clause_id, priority='P0',
        clause_id={'a': 'b', 'b': 'a', 'c': 'c'}[context.clause_id],
        status='NG' if context.clause_id == 'a' else 'PASS',
        suggestions=','.join(context.cache['calls']), checks_performed=1)
    return item

def check_a(context): return check(context)
def check_b(context): return check(context)
def check_c(context): return check(context)
"""
    if result_type == "dict":
        program = program.replace(b"return item", b"return item.model_dump()")
    snapshot, sources, programs = request(program, clauses=("a", "b", "c"), targets=1)
    if not shared_entrypoint:
        target = snapshot.targets[0]
        target = replace(target, binding=replace(target.binding, entrypoints={clause: "checker:check_" + clause for clause in ("a", "b", "c")}))
        snapshot = replace(snapshot, targets=(target,))
    result = run_audit(snapshot, sources, programs)
    assert [(row["clause_id"], row["status"]) for row in result["findings"]] == [("a", "ERR"), ("b", "ERR"), ("c", "PASS")]
    assert [row["suggestions"] for row in result["findings"]] == ["checker_result_clause_mismatch", "checker_result_clause_mismatch", "a,b,c"]
    assert all(row["coverage"] == "mapped" for row in result["coverage"])


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


@pytest.mark.parametrize("runner", [run_audit, run_audit_process])
def test_empty_binding_never_imports_placeholder_code(runner):
    from dataclasses import replace

    snapshot, sources, programs = request(b"raise AssertionError('Placeholder code must not execute')\n", targets=1)
    snapshot = replace(snapshot, targets=tuple(replace(target, binding=replace(target.binding, entrypoints={})) for target in snapshot.targets))
    result = runner(snapshot, sources, programs)
    assert result["findings"] == []
    assert [(row["clause_id"], row["coverage"]) for row in result["coverage"]] == [("a", "unsupported"), ("b", "unsupported")]
    assert snapshot.targets[0].verification == "unverified"


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
    assert all(row["status"] == "UNABLE" and row["suggestions"] == "personal_model_unavailable_or_uncertain" for row in result["findings"])


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


def test_reference_type_is_visible_without_a_reference_rule():
    from dataclasses import replace

    program = PROGRAM.replace(
        b"context.cache['count'] =",
        b"assert [item.audit_id for item in context.group_context.by_doc_type['control-plan']] == ['reference']\n"
        b"    assert 'reference' not in context.group_context.rules_by_id\n"
        b"    context.cache['count'] =",
    )
    snapshot, sources, programs = request(program, targets=1)
    snapshot = replace(snapshot, inputs=tuple(replace(item, document_type="control-plan") if item.role == "reference" else item for item in snapshot.inputs))
    assert all(item["status"] == "PASS" for item in run_audit_process(snapshot, sources, programs)["findings"])


def test_reference_rule_and_appendix_are_frozen_and_survive_guest_serialization():
    from dataclasses import replace

    program = PROGRAM.replace(
        b"context.cache['count'] =",
        b"related = context.group_context.rules_by_id['reference']\n"
        b"    assert related.rule_id == 'related-revision'\n"
        b"    assert related.rules[0]['rule_to_check'] == 'confirmed reference rule'\n"
        b"    assert related.appendix == [{'instruction': 'confirmed attachment'}]\n"
        b"    assert context.group_context.rules_by_id[context.audit_file.audit_id].rule_id == 'revision'\n"
        b"    context.cache['count'] =",
    )
    snapshot, sources, programs = request(program, targets=1)
    body = {"type": "control-plan", "rules": [{"id": "r", "rule_to_check": "confirmed reference rule"}]}
    appendix = ({"instruction": "confirmed attachment"},)
    reference = replace(snapshot.inputs[-1], rule_revision_id="related-revision", rules=body, appendix=appendix)
    frozen = replace(snapshot, inputs=(*snapshot.inputs[:-1], reference))
    digest = frozen.input_digest
    body["rules"][0]["rule_to_check"] = "changed later"
    appendix[0]["instruction"] = "changed later"
    assert frozen.input_digest == digest
    assert digest != snapshot.input_digest
    assert ExecutionSnapshot.from_dict(frozen.to_dict()).input_digest == digest
    changed = replace(frozen, inputs=(*frozen.inputs[:-1], replace(reference, rule_revision_id="later-revision")))
    assert changed.input_digest != digest
    assert all(item["status"] == "PASS" for item in run_audit_process(frozen, sources, programs)["findings"])


def test_shared_program_module_is_loaded_once_per_audit():
    program = PROGRAM.replace(b"def check(context):", b"calls = 0\n\ndef check(context):\n    global calls\n    calls += 1").replace(b"str(context.cache['count'])", b"str(calls)")
    result = run_audit_process(*request(program))
    assert [item["suggestions"] for item in result["findings"]] == [f"{result['process_id']}:{index}" for index in range(1, 5)]


def test_program_without_resources_cannot_import_another_suite_resource(monkeypatch):
    import sys
    from types import ModuleType

    foreign = ModuleType("resources")
    monkeypatch.setitem(sys.modules, "resources", foreign)
    program = PROGRAM.replace(b"context.cache['count'] =", b"import sys\n    assert 'resources' not in sys.modules\n    context.cache['count'] =")
    assert all(item["status"] == "PASS" for item in run_audit(*request(program))["findings"])
    assert sys.modules["resources"] is foreign


def test_authored_unknown_checks_are_diagnostics_not_authoritative_findings():
    program = b"""from types import SimpleNamespace
from audit.contracts import AuditResultItem

def create_audit(audit_file, rule, group_context):
    def check():
        return AuditResultItem(classification='x', item='a', priority='P0', status='NG', suggestions='native finding', clause_id='a')
    def forbidden():
        raise AssertionError('Unbound code must not run')
    return SimpleNamespace(checks=[SimpleNamespace(rule_item=SimpleNamespace(id='a'), check=check), SimpleNamespace(rule_item=SimpleNamespace(id='foreign'), check=forbidden)])
"""
    from dataclasses import replace

    snapshot, sources, programs = request(program, targets=1)
    snapshot = replace(snapshot, targets=tuple(replace(target, binding=replace(target.binding, entrypoints={"a": "checker:create_audit", "b": "checker:create_audit"})) for target in snapshot.targets))
    result = run_audit(snapshot, sources, programs)
    assert [(row["clause_id"], row["status"]) for row in result["findings"]] == [("a", "NG"), ("b", "ERR")]
    assert result["diagnostics"] == [{"file_version_id": "target-0", "clause_id": "foreign", "code": "authored_checker_extra_clause"}]
    assert "Unbound code must not run" not in json.dumps(result)


@pytest.mark.parametrize("customer,count", [("UAES", 19), ("理想", 17), ("蔚来", 5)])
def test_all_standard_suites_execute_explicit_clauses_without_claiming_correctness(customer, count):
    """Synthetic source smoke, separate from independently labelled corpus results."""
    import io

    from audit.standard_suites import standard_suites
    from openpyxl import Workbook
    from PIL import Image

    suites = [suite for suite in standard_suites() if suite.customer == customer]
    assert len(suites) == count
    inputs, targets, sources, programs = [], [], {}, {}
    for suite in suites:
        stream = io.BytesIO()
        if suite.source_entry == "U13":
            Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
            suffix = ".png"
        else:
            book = Workbook()
            book.active.title = {"N01": "CoverSheet", "N02": "PFMEA", "N03": "控制计划", "N04": "特殊特性清单", "N05": "过程流程图"}.get(suite.source_entry, "报告")
            rules = json.loads(suite.files["rules.json"])
            for row in [[suite.source_entry, rules["title"]], ["客户", customer], ["零件号", "P-1"], ["版本", "A"]]:
                book.active.append(row)
            book.save(stream)
            book.close()
            suffix = ".xlsx"
        data = stream.getvalue()
        version = suite.source_entry
        sources[version] = data
        digest = checker_digest(suite.program_files)
        programs[digest] = suite.program_files
        inputs.append(FrozenInput(version, hashlib.sha256(data).hexdigest(), "target", version + suffix, document_type=suite.document_type, customer=customer))
        targets.append(
            FrozenTarget(version, suite.revision_digest, suite.suite_key, suite.revision_digest, json.loads(suite.files["rules.json"]), RuleBinding(suite.suite_key, digest, suite.entrypoints))
        )
    snapshot = ExecutionSnapshot("tenant", "initiator", tuple(inputs), tuple(targets), "runtime", "library", {})
    result = run_audit_process(snapshot, sources, programs)
    expected = {(target.file_version_id, clause) for target in targets for clause in target.binding.entrypoints}
    assert {(row["file_version_id"], row["clause_id"]) for row in result["findings"]} == expected
    assert not [row for row in result["findings"] if row["status"] == "ERR"]
    assert all(row["coverage"] == "mapped" for row in result["coverage"])
    assert all(target.verification == "unverified" for target in targets)


def test_unavailable_target_survives_real_guest_without_losing_other_targets():
    program = PROGRAM.replace(
        b"context.cache['count'] =",
        b"assert context.audit_file.audit_id != 'target-0'\n    assert 'target-0' not in context.paths\n    assert 'target-0' in context.group_context.unavailable_files\n    context.cache['count'] =",
    )
    snapshot, sources, programs = request(program)
    del sources["target-0"]
    result = run_audit_process(snapshot, sources, programs, unavailable_sources=frozenset({"target-0"}))
    assert result["process_id"] != os.getpid()
    assert [(row["file_version_id"], row["status"]) for row in result["findings"]] == [("target-0", "ERR"), ("target-0", "ERR"), ("target-1", "PASS"), ("target-1", "PASS")]
    assert result["input_digest"] == snapshot.input_digest
    assert result["unavailable_sources"] == ["target-0"]


@pytest.mark.parametrize("mutation", ["availability", "pass", "evidence"])
def test_host_rejects_guest_fabrication_for_unavailable_sources(mutation):
    from agent.execution.audit_runtime import _validate_output

    snapshot, sources, programs = request()
    del sources["target-0"]
    unavailable = frozenset({"target-0"})
    result = run_audit(snapshot, sources, programs, unavailable_sources=unavailable)
    if mutation == "availability":
        result["unavailable_sources"] = []
    elif mutation == "pass":
        result["findings"][0]["status"] = "PASS"
    else:
        result["findings"][-1]["evidence_file_version_ids"] = ["target-1", "target-0"]
    with pytest.raises(AuditRunError, match="audit_output_(availability_mismatch|unavailable_source)"):
        _validate_output(snapshot, result, unavailable_sources=unavailable)
