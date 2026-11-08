"""Positive release bridge checks using executed synthetic, human-owned fixtures.

These prove the runner protocol, never real-customer correctness or permission
to publish. No paid model calls, imported proof documents, or production writes.
"""

import csv
import hashlib
import io
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from audit.standard_suites import StandardSuite
from corpus_contracts import official_suites, oracle
from corpus_contracts.official_models import LiveModels
from documents.model_access import ModelAccess
from openpyxl import Workbook
from product.suites import ResolvedSuite, SuiteRevision, content_digest, resolved_digest
from pydantic import SecretStr

PROGRAM = b"""import json
from openpyxl import load_workbook
from audit.contracts import AuditResultItem
RESOURCES = {"carriers": {".xlsx": "fixture"}, "methods": {"R1": "positive"}}
def check(context):
    assert not context.runtime.allow_model_tools
    book = load_workbook(context.paths[context.target.file_version_id], read_only=True)
    value = book.active['A1'].value
    book.close()
    return AuditResultItem(classification='fixture', item='positive', priority='P0',
        clause_id=context.clause_id, status='PASS' if value > 0 else 'NG',
        suggestions='', checks_performed=1,
        evidence={"required": "structure", "used": "structure", "source_format": "xlsx", "prepared_format": "xlsx",
                  "sources": [{"kind": "structure", "locator": json.dumps({"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"})}]})
"""


def corpus_fixture(tmp_path, monkeypatch, *, explain_pass=True):
    rules = {"type": "fixture", "rules": [{"id": "R1", "rule_to_check": "A1 must be positive"}], "checker_binding": {"R1": "checker:check"}}
    files = {"rules.json": json.dumps(rules).encode(), "checker.py": PROGRAM, "appendix.json": b"[]"}
    source = StandardSuite("fixture", "fixture", "", "", (), files, {"R1": "checker:check"}, "test fixture")
    selector = "fixture@v4"
    monkeypatch.setattr(oracle, "suite_sources", lambda: {selector: source})
    monkeypatch.setattr(oracle, "reviewed_rules", lambda: {selector: oracle.reviewed_contract(source)})
    paths = {"cases": tmp_path / "cases.csv", "oracle": tmp_path / "oracle.csv", "root": tmp_path}
    source_rows, labels = [], []
    for number in range(1, 5):
        workbook = Workbook()
        workbook.active["A1"] = number
        raw = io.BytesIO()
        workbook.save(raw)
        workbook.close()
        filename = f"original-{number}.xlsx"
        (tmp_path / filename).write_bytes(raw.getvalue())
        source_rows.append(["fixture", str(number), "target", filename, filename, "target", selector, "fixture", "R1"])
        reason = f"Synthetic fixture A1 is {number}" if explain_pass else ""
        labels.append(["fixture", str(number), "target", "R1", "PASS", "A1 must be positive", reason, "test author", "2026-11-07", ""])
    for key, columns, rows in (("cases", oracle.FILE_COLUMNS, source_rows), ("oracle", oracle.EXPECTED_COLUMNS, labels)):
        with paths[key].open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(rows)
    files = {**files, "checker.py": PROGRAM + b"\n# Candidate implementation under test.\n"}
    objects = {name: content_digest(raw) for name, raw in files.items()}
    revision = SuiteRevision(str(uuid4()), None, "fixture", (), None, objects, resolved_digest("fixture", (), objects), None)
    context = {
        "suite": ResolvedSuite(revision, files, objects),
        "head": {"revision": 0, "current_revision_id": None},
        "validation": {"write_revision": 0},
        "report": None,
        "related_suites": {},
        "related_suite_digests": {},
        "related_revision_ids": (),
    }
    return context, paths


class ReleaseBoundary:
    """Only persistence is controlled; samples execute actual Core subprocesses."""

    def __init__(self, context):
        self.context = context
        self.events = []

    def official_context(self, revision_id):
        assert revision_id == self.context["suite"].revision.id
        self.events.append("read")
        return self.context

    def record_official_validation(self, revision_id, proof, *, expected_revision):
        assert expected_revision == self.context["validation"]["write_revision"]
        assert revision_id == proof.suite_revision_id
        self.events.append("record")
        self.context = {**self.context, "report": proof.model_dump(mode="json"), "validation": {"write_revision": expected_revision + 1, "proof_digest": proof.proof_digest}}
        return self.context["validation"]

    def publish_official(self, revision_id, *, expected_revision, expected_validation_revision, expected_proof_digest):
        assert expected_revision == self.context["head"]["revision"]
        assert expected_validation_revision == self.context["validation"]["write_revision"]
        assert expected_proof_digest == self.context["validation"]["proof_digest"]
        self.events.append("publish")
        self.context["head"] = {"revision": expected_revision + 1, "current_revision_id": revision_id}
        return self.context["head"]


@pytest.mark.parametrize("explain_pass", [True, False])
def test_actual_fixed_execution_then_record_readback_and_explicit_publish(tmp_path, monkeypatch, explain_pass):
    context, paths = corpus_fixture(tmp_path, monkeypatch, explain_pass=explain_pass)
    plan = official_suites.prepare(context, **paths)
    assert not plan.reasons
    for sample in plan.samples:
        answer = sample.oracle.expected["R1"]
        assert (answer.confirmed_by, answer.confirmed_on) == ("test author", "2026-11-07")
        assert bool(answer.manual_reason) is explain_pass
        assert sample.snapshot.targets[0].file_version_id == sample.oracle.file_id
        assert set(sample.snapshot.targets[0].binding.entrypoints) == set(sample.oracle.expected)
    release = ReleaseBoundary(context)
    revision_id = context["suite"].revision.id
    observed = []
    result = official_suites.validate(release, revision_id, record=True, expected_validation_revision=0, before_record=observed.append, **paths)
    report = result["report"]
    assert report["verified"] and report["passed_samples"] == 4
    assert report["oracle_revision"] == plan.oracle_revision
    assert len({row["input_digest"] for row in report["cases"]}) == 4
    assert [row["split"] for row in report["cases"]] == ["training", "training", "training", "holdout"]
    assert all(row["clauses"][0]["checks_performed"] == 1 and row["clauses"][0]["evidence_locatable"] for row in report["cases"])
    assert observed[0]["report"]["proof_digest"] == report["proof_digest"]
    assert release.events == ["read", "record"]
    receipt = official_suites.readback(release, revision_id)
    assert receipt["validation"]["proof_digest"] == report["proof_digest"]
    assert receipt["head"]["current_revision_id"] is None
    head = official_suites.publish(release, revision_id, expected_revision=0, expected_validation_revision=1, expected_proof_digest=report["proof_digest"], **paths)
    assert head == {"revision": 1, "current_revision_id": revision_id}
    assert release.events.count("publish") == 1


def test_plan_binds_direct_dependency_revision_content_and_all_originals(tmp_path, monkeypatch):
    context, paths = corpus_fixture(tmp_path, monkeypatch)
    suite = context["suite"]
    revision = replace(suite.revision, related_suite_keys=("fixture:dependency",))
    context = {**context, "suite": replace(suite, revision=revision), "related_suite_digests": {"fixture:dependency": "d" * 64}, "related_revision_ids": (str(uuid4()),)}
    plan = official_suites.prepare(context, **paths)
    assert not plan.reasons
    assert len(plan.originals) == 6
    assert all(sample.snapshot.targets[0].related_suite_digests == context["related_suite_digests"] for sample in plan.samples)
    assert plan.oracle_revision and all(sample.snapshot.library_revision for sample in plan.samples)
    assert len({item.sha256 for sample in plan.samples for item in sample.snapshot.inputs}) == 4


def test_real_execution_preserves_selected_dependency_rules_when_demoting_target(tmp_path, monkeypatch):
    context, paths = corpus_fixture(tmp_path, monkeypatch)
    source = oracle.suite_sources()["fixture@v4"]
    dependency_rules = {"type": "dependency", "rules": [{"id": "R1", "rule_to_check": "A1 must be positive"}], "checker_binding": {"R1": "checker:check"}}
    dependency_files = {**source.files, "rules.json": json.dumps(dependency_rules).encode(), "appendix.json": b'[{"reviewed":"context"}]'}
    dependency_source = replace(source, suite_key="fixture:dependency", document_type="dependency", files=dependency_files)
    selected_files = {**dependency_files, "checker.py": PROGRAM + b"\n# Actual selected dependency implementation.\n"}
    objects = {name: content_digest(raw) for name, raw in selected_files.items()}
    dependency_revision = SuiteRevision(str(uuid4()), None, "fixture:dependency", (), None, objects, resolved_digest("fixture:dependency", (), objects), None)
    dependency = ResolvedSuite(dependency_revision, selected_files, objects)
    sources = {"fixture@v4": source, "fixture:dependency@v4": dependency_source}
    monkeypatch.setattr(oracle, "suite_sources", lambda: sources)
    monkeypatch.setattr(oracle, "reviewed_rules", lambda: {selector: oracle.reviewed_contract(value) for selector, value in sources.items()})
    for number in range(1, 5):
        filename = f"dependency-{number}.xlsx"
        workbook = Workbook()
        workbook.active["A1"] = number + 10
        workbook.save(tmp_path / filename)
        workbook.close()
        with paths["cases"].open("a", newline="") as stream:
            csv.writer(stream).writerow(["fixture", str(number), "dependency", filename, filename, "target", "fixture:dependency@v4", "fixture:dependency", "R1"])
        with paths["oracle"].open("a", newline="") as stream:
            csv.writer(stream).writerow(["fixture", str(number), "dependency", "R1", "PASS", "A1 must be positive", "Positive synthetic source", "test author", "2026-11-07", ""])
    # The candidate actually consumes the demoted target's selected rule context.
    program = PROGRAM.replace(
        b"    assert not context.runtime.allow_model_tools",
        (
            "    assert not context.runtime.allow_model_tools\n"
            f"    assert context.group_context.rules_by_id['dependency'].rule_id == {dependency_revision.id!r}\n"
            "    assert context.group_context.rules_by_id['dependency'].appendix == [{'reviewed': 'context'}]"
        ).encode(),
    )
    files = {**context["suite"].files, "checker.py": program}
    objects = {name: content_digest(raw) for name, raw in files.items()}
    revision = replace(context["suite"].revision, related_suite_keys=("fixture:dependency",), overrides=objects, resolved_digest=resolved_digest("fixture", ("fixture:dependency",), objects))
    context = {
        **context,
        "suite": ResolvedSuite(revision, files, objects),
        "related_suites": {"fixture:dependency": dependency},
        "related_suite_digests": {"fixture:dependency": dependency_revision.resolved_digest},
        "related_revision_ids": (dependency_revision.id,),
    }
    plan = official_suites.prepare(context, **paths)
    assert not plan.reasons
    for sample in plan.samples:
        reference = next(item for item in sample.snapshot.inputs if item.file_version_id == "dependency")
        assert reference.role == "reference"
        assert reference.rule_revision_id == dependency_revision.id
        assert reference.to_dict()["rules"] == dependency_rules
        assert reference.to_dict()["appendix"] == [{"reviewed": "context"}]
    proof = official_suites.evaluate(plan)
    assert proof.verified and proof.passed_samples == 4
    assert proof.related_revision_ids == (dependency_revision.id,)
    assert proof.related_suite_digests == {"fixture:dependency": dependency_revision.resolved_digest}


def test_live_text_and_visual_clients_use_one_bounded_gateway_then_record_and_publish(tmp_path, monkeypatch):
    """Actual SDKs/process/gateway, controlled upstream, never paid quality proof."""
    context, paths = corpus_fixture(tmp_path, monkeypatch)
    program = PROGRAM.replace(b"assert not context.runtime.allow_model_tools", b"assert context.runtime.allow_model_tools")
    program = program.replace(
        b"    book.close()",
        b"""    book.close()
    import io
    from PIL import Image
    from audit.engine.checkers.client import AIClient
    from documents.model_access import current_model_access
    assert current_model_access().api_key.get_secret_value() != 'host-only-test-secret'
    assert current_model_access().base_url.startswith('http://127.0.0.1:')
    raw = io.BytesIO()
    Image.new('RGB', (2, 2), (value, 0, 0)).save(raw, format='PNG')
    facts = context.runtime.read_ocr_visual_facts(
        images=[raw.getvalue()], prompt=f'Inspect synthetic fixture {value}',
        response_schema={'name': 'fixture', 'strict': True, 'schema': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'positive': {'type': 'boolean'}}, 'required': ['positive']}})
    text = AIClient().response_text([{'role': 'user', 'content': f'Assess synthetic fixture {value}'}], max_tokens=32)
    value = int(facts['positive'] and text == 'positive')""",
    )
    files = {**context["suite"].files, "checker.py": program}
    objects = {name: content_digest(raw) for name, raw in files.items()}
    revision = replace(context["suite"].revision, overrides=objects, resolved_digest=resolved_digest("fixture", (), objects))
    context = {**context, "suite": ResolvedSuite(revision, files, objects)}
    observations = []

    class ControlledProvider:
        sock = None

        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, body, headers):
            request = json.loads(body)
            visual = "input_image" in body
            assert method == "POST" and path == "/v1/responses"
            assert headers["Authorization"] == "Bearer host-only-test-secret"
            assert request["model"] == "controlled-vision" and request["store"] is False
            observations.append("visual" if visual else "text")
            response = {
                "id": "controlled-response",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "controlled-vision",
                "output": [
                    {
                        "type": "message",
                        "id": "controlled-message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": '{"positive":true}' if visual else "positive", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 17, "output_tokens": 4, "total_tokens": 21},
            }
            self.body = io.BytesIO(json.dumps(response).encode())

        def getresponse(self):
            return SimpleNamespace(status=200, read1=self.body.read1)

        def close(self):
            pass

    monkeypatch.setattr("agent.execution.gateway.http.client.HTTPSConnection", ControlledProvider)
    access = ModelAccess(SecretStr("host-only-test-secret"), provider="controlled", base_url="https://provider.example/v1", model="controlled-vision")
    models = LiveModels(access, max_requests=8, max_input_tokens=1000, max_output_tokens=1000)
    release = ReleaseBoundary(context)
    progress = []
    result = official_suites.validate(release, revision.id, models=models, on_progress=progress.append, record=True, expected_validation_revision=0, **paths)
    assert result["report"]["verified"] and result["report"]["passed_samples"] == 4
    assert result["report"]["model_identity"] == access.identity()
    assert observations == ["visual", "text"] * 4
    assert result["models"]["usage"] == {"completed_requests": 8, "input_tokens": 136, "output_tokens": 32, "uncertain": False, "in_flight": False, "budget_exhausted": False}
    assert result["models"]["monetary_cost"] == "unknown"
    assert [row["completed_samples"] for row in progress if row["phase"] == "sample_completed"] == [1, 2, 3, 4]
    assert "host-only-test-secret" not in json.dumps([result, progress])
    assert "provider.example" not in json.dumps([result, progress])
    disabled = official_suites.prepare(context, **paths)
    assert all(a["input_digest"] != b.snapshot.input_digest for a, b in zip(result["report"]["cases"], disabled.samples, strict=True))
    receipt = official_suites.readback(release, revision.id)
    assert receipt["report"]["model_identity"] == access.identity()
    official_suites.publish(release, revision.id, expected_revision=0, expected_validation_revision=1, expected_proof_digest=result["report"]["proof_digest"], **paths)
    assert observations == ["visual", "text"] * 4


def test_no_model_proof_keeps_its_preexisting_digest_and_serialization(tmp_path, monkeypatch):
    context, paths = corpus_fixture(tmp_path, monkeypatch)
    proof = official_suites.evaluate(official_suites.prepare(context, **paths))
    payload = proof.model_dump(mode="json")
    assert "model_identity" not in payload
    old_digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert proof.proof_digest == old_digest


def test_selected_revision_consumes_only_samples_judged_under_its_own_rules(tmp_path, monkeypatch):
    context, paths = corpus_fixture(tmp_path, monkeypatch)
    current = oracle.suite_sources()["fixture@v4"]
    earlier_rules = {**json.loads(current.files["rules.json"]), "rules": [{"id": "R1", "rule_to_check": "A1 must be negative"}]}
    earlier = replace(current, files={**current.files, "rules.json": json.dumps(earlier_rules).encode()})
    monkeypatch.setattr(oracle, "suite_sources", lambda: {"fixture@v4": current, "fixture@v2": earlier})
    monkeypatch.setattr(oracle, "reviewed_rules", lambda: {key: oracle.reviewed_contract(value) for key, value in oracle.suite_sources().items()})
    workbook = Workbook()
    workbook.active["A1"] = -1
    workbook.save(tmp_path / "earlier.xlsx")
    workbook.close()
    with paths["cases"].open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow(["fixture", "earlier", "target", "earlier.xlsx", "earlier.xlsx", "target", "fixture@v2", "fixture", "R1"])
    with paths["oracle"].open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow(["fixture", "earlier", "target", "R1", "NG", "A1 must be negative", "Synthetic earlier rule", "test author", "2026-11-07", ""])

    plan = official_suites.prepare(context, **paths)
    assert not plan.reasons
    assert [sample.identity for sample in plan.samples] == [f"fixture/{number}/target" for number in range(1, 5)]

    unlabeled_rules = {**earlier_rules, "rules": [{"id": "R1", "rule_to_check": "A1 must be even"}]}
    unlabeled = replace(context["suite"], files={**context["suite"].files, "rules.json": json.dumps(unlabeled_rules).encode()})
    plan = official_suites.prepare({**context, "suite": unlabeled}, **paths)
    assert not plan.samples
    assert {"oracle_rules_changed", "insufficient_independent_samples"} <= set(plan.reasons)


@pytest.mark.parametrize(("selector", "samples"), [("PPAP:潜在过程失效模式及后果分析@v2", 10), ("PPAP:潜在过程失效模式及后果分析@v3", 9)])
def test_real_generations_of_one_document_type_do_not_block_each_other(selector, samples):
    source = oracle.suite_sources()[selector]
    objects = {name: content_digest(raw) for name, raw in source.files.items()}
    revision = SuiteRevision(str(uuid4()), None, source.suite_key, (), None, objects, resolved_digest(source.suite_key, (), objects), None)
    plan = official_suites.prepare({"suite": ResolvedSuite(revision, dict(source.files), objects), "related_suites": {}, "related_suite_digests": {}, "related_revision_ids": ()})
    assert len(plan.samples) == samples
    assert "oracle_rules_changed" not in plan.reasons


def test_live_validation_gives_paddle_only_through_the_run_local_bridge(tmp_path, monkeypatch):
    context, paths = corpus_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("PADDLE_OCR_URL", "http://paddle.invalid/ocr")
    monkeypatch.setenv("PADDLE_TOKEN", "host-only-paddle-token")
    plan = official_suites.prepare(context, **paths)
    sample = replace(plan.samples[0], sources={key: b"%PDF-1.7 fixture" for key in plan.samples[0].sources})
    with official_suites._paddle(sample, live=True) as env:
        assert set(env) == {"PPAP_PADDLE_BRIDGE_URL", "PPAP_PADDLE_BRIDGE_TOKEN"}
        assert env["PPAP_PADDLE_BRIDGE_URL"].startswith("http://127.0.0.1:")
        assert "host-only-paddle-token" not in env.values()

    seen = []

    def stop(*_args, tool_env, **_kwargs):
        seen.append(tool_env)
        raise official_suites.AuditRunError("stop")

    monkeypatch.setattr(official_suites, "run_audit_process", stop)
    assert official_suites.evaluate(plan).reasons == ("official_execution_incomplete",)
    assert seen == [{}]


def test_v4_oracle_selector_stays_on_the_legacy_u08_carrier():
    from audit.standard_suites import predefined_directory_suites

    legacy = oracle.suite_sources()["UAES:U08@v4"]
    directory = next(suite for suite in predefined_directory_suites() if suite.source_path == "v5/UAES/U08")
    assert legacy.source_path != directory.source_path
    assert legacy.revision_digest != directory.revision_digest
    assert b"check_clause" in legacy.files["checker.py"]
    assert b"audit.checkers.v4" not in directory.files["checker.py"]
    assert b"audit.checkers.v3" not in directory.files["checker.py"]
    assert b"audit.checkers.v2" not in directory.files["checker.py"]


def test_guest_runs_frozen_v5_u08_bytes_and_leaves_unbound_clauses_unpassed(tmp_path):
    import hashlib

    from agent.execution.audit_runtime import run_audit_process
    from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
    from audit.standard_suites import predefined_directory_suites
    from openpyxl import Workbook

    suite = next(item for item in predefined_directory_suites() if item.source_path == "v5/UAES/U08")
    book = Workbook()
    sheet = book.active
    sheet.title = "控制计划"
    sheet.append(["控制计划"])
    sheet.append(["工序名称/操作描述", "机器/装置/夹具/工装", "特性类型"])
    sheet.append(["装配", "压机", "CC"])
    path = tmp_path / "control-plan.xlsx"
    book.save(path)
    book.close()
    original = path.read_bytes()
    rules = json.loads(suite.files["rules.json"])
    program = {name: suite.files[name] for name in ("checker.py", "resources.py")}
    digest = checker_digest(program)
    binding = RuleBinding(suite.suite_key, digest, rules["checker_binding"])
    source = FrozenInput("target", hashlib.sha256(original).hexdigest(), "target", "control-plan.xlsx")
    target = FrozenTarget("target", "revision", suite.suite_key, "a" * 64, rules, binding)
    snapshot = ExecutionSnapshot("tenant", "initiator", (source,), (target,), "runtime", "library", {})
    result = run_audit_process(snapshot, {"target": original}, {digest: program})
    by_clause = {row["clause_id"]: row for row in result["findings"]}
    assert set(by_clause) == {"U08:1", "U08:4"}
    assert by_clause["U08:1"]["status"] == by_clause["U08:4"]["status"] == "PASS"
    assert all(row["evidence"]["sources"] for row in by_clause.values())
    coverage = {row["clause_id"]: row["coverage"] for row in result["coverage"]}
    assert coverage["U08:1"] == coverage["U08:4"] == "mapped"
    unbound = [clause for clause, state in coverage.items() if state == "unsupported"]
    assert set(unbound) == {row["id"] for row in rules["rules"]} - {"U08:1", "U08:4"}
    assert not any(row["status"] == "PASS" and row["clause_id"] in unbound for row in result["findings"])
    changed = dict(program)
    changed["checker.py"] = program["checker.py"].replace("已读取各工序对应的生产资源。".encode(), b"LIVE-DIRECTORY")
    (tmp_path / "checker.py").write_bytes(changed["checker.py"])
    rerun = run_audit_process(snapshot, {"target": original}, {digest: program})
    assert rerun["findings"] == result["findings"]
