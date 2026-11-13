"""A source digest is checked against its frozen file before public projection."""

import json

import pytest
from agent.mapping import checker_response
from agent.protocol import ExecuteRequest, FrozenFile
from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource


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
    request = ExecuteRequest(protocol_revision="test", executor="ppap-checker-core", executor_revision="runtime", round_id="round", job_id="job", files=[target, peer])
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
