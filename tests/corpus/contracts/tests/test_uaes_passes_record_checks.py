"""An installed UAES clause that passes a real sample has checked something: an audit shows a PASS without checks as a technical error."""

import hashlib
import json
from pathlib import Path

import pytest
from agent.execution.audit_runtime import run_audit
from agent.mapping import unproven_pass
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import library_revision, predefined_directory_suites
from documents.model_access import model_access_scope

SAMPLES = Path(__file__).resolve().parents[4] / "assets/uaes/samples"
MAGNET_MSA = "F01R20V040_磁性罩/测试文件/06 测量系统分析评价"


@pytest.mark.parametrize(
    ("leaf", "samples", "clause"),
    [
        # A Kappa study filed beside the suite's main MSA file: only its own report is its to judge.
        ("U05", (f"{MAGNET_MSA}/kappa-MSA-检具 (1).xlsx", f"{MAGNET_MSA}/MSA.xls"), "MSA:2"),
        ("U05", ("F01RD0YA40_机加工壳体/测试文件/06 测量系统分析评价/MSA/A40-粗糙度仪-Cgk.xls",), "MSA:5"),
        ("U06", ("F01R10Y776_机加壳体/测试文件/07 过程能力研究/776-机加CPK.xlsx",), "PCE:3"),
    ],
)
def test_a_passing_clause_has_checked_the_sample(leaf, samples, clause):
    """The first sample is judged; the others are the same round's files of the same type."""
    suite = next(suite for suite in predefined_directory_suites() if suite.source_path == f"uaes/{leaf}")
    rules, binding = json.loads(suite.files["rules.json"]), RuleBinding(suite.suite_key, checker_digest(suite.program_files), suite.entrypoints)
    blobs, inputs, targets = {}, [], []
    for index, sample in enumerate(samples, 1):
        identity, path = f"f{index:02d}", SAMPLES / sample
        blobs[identity] = path.read_bytes()
        inputs.append(FrozenInput(identity, hashlib.sha256(blobs[identity]).hexdigest(), "target", path.name, document_type=suite.document_type))
        targets.append(FrozenTarget(identity, "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, rules, binding))
    snapshot = ExecutionSnapshot("corpus", "passes-record-checks", tuple(inputs), tuple(targets), "corpus-source", library_revision(), {}, round_id="passes-record-checks")
    with model_access_scope(None):
        findings = run_audit(snapshot, blobs, {binding.checker_digest: suite.program_files})["findings"]
    finding = next(row for row in findings if row["file_version_id"] == "f01" and str(row["clause_id"]) == clause)
    assert finding["status"] == "PASS", finding.get("suggestions")
    assert unproven_pass(finding["status"], finding.get("checks_performed"), finding.get("empty_scope_basis"), finding.get("suggestions")) is None, finding
