"""A source digest is checked against its frozen file before public projection."""

import hashlib
import json

import pytest
from agent.checker_development import FrozenDevelopmentInput, OracleSample, _evaluate
from agent.mapping import checker_response
from agent.protocol import ExecuteRequest, FrozenFile
from audit.contracts import AuditEvidence, AuditInput, AuditResultItem, EvidenceSource, GroupContextInput, RuleInput


def observation(version, digest, **extra):
    evidence = AuditEvidence(
        required="structure",
        used="structure",
        source_format="xlsx",
        prepared_format="xlsx",
        sources=(
            EvidenceSource(kind="structure", locator=json.dumps({"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1", "file_version_id": version, "source_sha256": digest, **extra}), excerpt="5"),
        ),
    )
    return AuditResultItem(classification="rule", item="Required", clause_id="R1", priority="P0", status="PASS", suggestions="Read original", checks_performed=1, evidence=evidence)


def project(version, digest, **extra):
    target = FrozenFile(file_version_id="target", role="target", filename="target.xlsx", sha256="a" * 64, rule_version_id="rule", rule_body={"rules": [{"id": "R1", "rule_to_check": "Required"}]})
    peer = FrozenFile(file_version_id="peer", role="reference", filename="peer.xlsx", sha256="b" * 64)
    request = ExecuteRequest(protocol_revision="test", executor="ppap-deep", executor_revision="runtime", round_id="round", job_id="job", files=[target, peer])
    return checker_response(request, [(target, [observation(version, digest, **extra)])]).findings[0]


@pytest.mark.parametrize(("version", "digest"), [("target", "a" * 64), ("peer", "b" * 64)])
def test_matching_frozen_target_or_peer_digest_preserves_pass(version, digest):
    finding = project(version, digest)
    assert finding.status == "PASS"
    sources = finding.locator["semantic"]["sources"]
    assert len(sources) == 1 and sources[0]["file_version_id"] == version
    assert sources[0]["locator"] == {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}


@pytest.mark.parametrize(
    ("version", "digest"),
    [
        ("target", "b" * 64),
        ("peer", "a" * 64),
        ("outside", "a" * 64),
        ("target", None),
        ("target", 0),
        ("target", []),
        ("target", ""),
    ],
)
def test_wrong_or_untyped_digest_and_unfrozen_files_do_not_pass(version, digest):
    finding = project(version, digest)
    assert finding.status == "UNABLE"
    assert finding.locator["semantic"]["sources"] == []


def test_a_matching_digest_does_not_authorize_unknown_locator_fields():
    finding = project("target", "a" * 64, unexpected="not a public locator field")
    assert finding.status == "UNABLE"
    assert finding.locator["semantic"]["sources"] == []


@pytest.mark.parametrize("frozen_sha256", [None, {}, {"peer": "b" * 64}], ids=["no-context", "empty-context", "other-version-only"])
def test_declared_digest_without_frozen_hash_context_is_not_accepted(frozen_sha256):
    from agent.mapping import _checker_finding

    status, _, _, sources = _checker_finding(observation("target", "a" * 64), file_version_id="target", file_facts={"target": ("target.xlsx", "target")}, frozen_sha256=frozen_sha256)
    assert status == "UNABLE" and not sources


@pytest.mark.parametrize(
    ("version", "wrong_digest", "include_peer"),
    [
        ("target", False, True),
        ("peer", False, True),
        ("target", True, True),
        ("peer", True, True),
        ("peer", False, False),
    ],
)
def test_pi_evaluation_uses_frozen_bytes_for_target_and_peer(version, wrong_digest, include_peer):
    target = AuditInput(audit_id="target", file_url="/not-read/target.xlsx", file_metadata={"file_version_id": "target", "sha256": "f" * 64})
    peer = AuditInput(audit_id="peer-alias", file_url="/not-read/peer.xlsx", file_metadata={"file_version_id": "peer", "sha256": "f" * 64, "role": "reference"})
    rule = RuleInput(rule_id="rule", type="自定义", rules=[{"id": "R1", "category": "value", "rule_to_check": "Required"}])
    group = GroupContextInput(task_group_id="test-group", by_audit_id={"target": target, "peer-alias": peer})
    inputs = FrozenDevelopmentInput(target, rule, b"target original", group_context=group, related_bytes={"peer-alias": b"peer original"} if include_peer else {})
    sample = OracleSample("sample", 1, inputs, {"R1": "PASS"}, "reviewer")
    digest = "f" * 64 if wrong_digest else hashlib.sha256(b"target original" if version == "target" else b"peer original").hexdigest()
    result = observation(version, digest)
    evaluated = _evaluate(sample, [result], [result], None).clauses[0]
    expected = "PASS" if not wrong_digest and (version == "target" or include_peer) else "UNABLE"
    assert evaluated.actual == evaluated.baseline_actual == expected
    assert evaluated.evidence_locatable is (expected == "PASS")
