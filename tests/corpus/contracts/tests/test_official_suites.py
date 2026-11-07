"""Positive release bridge checks using executed synthetic, human-owned fixtures.

These prove the runner protocol, never real-customer correctness or permission
to publish. No model calls, imported proof documents, or production writes.
"""

import csv
import io
import json
from dataclasses import replace
from uuid import uuid4

from audit.standard_suites import StandardSuite
from corpus_contracts import official_suites, oracle
from openpyxl import Workbook
from product.suites import ResolvedSuite, SuiteRevision, content_digest, resolved_digest

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


def corpus_fixture(tmp_path, monkeypatch):
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
        labels.append(["fixture", str(number), "target", "R1", "PASS", "A1 must be positive", f"Synthetic fixture A1 is {number}", "test author", "2026-11-07", ""])
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


def test_actual_fixed_execution_then_record_readback_and_explicit_publish(tmp_path, monkeypatch):
    context, paths = corpus_fixture(tmp_path, monkeypatch)
    release = ReleaseBoundary(context)
    revision_id = context["suite"].revision.id
    observed = []
    result = official_suites.validate(release, revision_id, record=True, expected_validation_revision=0, before_record=observed.append, **paths)
    report = result["report"]
    assert report["verified"] and report["passed_samples"] == 4
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
