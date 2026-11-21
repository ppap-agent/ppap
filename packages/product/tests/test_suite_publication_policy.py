"""Publication acknowledgement is not a Checker verdict or regression proof."""

import json

import pytest
from product.suite_releases import adoption_block, empty_validation, publication_fingerprint
from product.suites import UNIMPLEMENTED_CHECKER_SOURCE, ResolvedSuite, SuiteRevision, content_digest, resolved_digest


def test_missing_proof_is_never_verified():
    result = empty_validation()
    assert result["status"] == "unverified"
    assert result["verified"] is False
    assert result["total_samples"] == result["passed_samples"] == 0


@pytest.mark.parametrize("value", [True, -1, "0", 1.5, None])
def test_head_revision_is_a_real_nonnegative_integer(value):
    with pytest.raises(ValueError, match="invalid_suite_publication"):
        publication_fingerprint(suite_key="suite", revision_id="rev", expected_revision=value, force_unverified=False, user_id="user")


@pytest.mark.parametrize("value", [1, "true", None])
def test_risk_acknowledgement_requires_boolean(value):
    with pytest.raises(ValueError, match="invalid_suite_publication"):
        publication_fingerprint(suite_key="suite", revision_id="rev", expected_revision=0, force_unverified=value, user_id="user")


def test_idempotency_identity_binds_all_consequential_inputs_and_actual_actor():
    values = dict(suite_key="suite", revision_id="rev", expected_revision=0, force_unverified=False, user_id="user")
    original = publication_fingerprint(**values)
    for field, changed in (("suite_key", "other"), ("revision_id", "other"), ("expected_revision", 1), ("force_unverified", True), ("user_id", "another")):
        assert publication_fingerprint(**{**values, field: changed}) != original
    assert publication_fingerprint(**values) == original


def _resolved(files: dict[str, bytes], *, tenant_id: str | None, suite_key: str = "UAES:U08", related: tuple[str, ...] = ()):
    objects = {name: content_digest(body) for name, body in files.items()}
    revision = SuiteRevision("rev", tenant_id, suite_key, related, None, objects, resolved_digest(suite_key, related, objects), None, 1)
    return ResolvedSuite(revision, files, objects)


def test_a_partial_official_directory_is_adopted_like_a_complete_one():
    from audit.standard_suites import predefined_directory_suites

    directory = next(suite for suite in predefined_directory_suites() if suite.source_path == "uaes/U08")
    files = dict(directory.files)
    # The leaf binds only the clauses its program is aligned with; the rest of the revision stays unchecked.
    rules = json.loads(files["rules.json"])
    assert rules["directory_coverage"] == "partial" and set(rules["checker_binding"]) < {row["id"] for row in rules["rules"]}
    for tenant_id in (None, "tenant-1"):
        partial = _resolved(files, tenant_id=tenant_id, related=directory.related_suite_keys)
        assert adoption_block(partial, verified=True, force_unverified=False) is None
        assert adoption_block(partial, verified=False, force_unverified=True) is None
        assert adoption_block(partial, verified=False, force_unverified=False) == "suite_confirmation_required"

    starter = {"rules.json": json.dumps({"rules": [{"id": "C1"}], "checker_binding": {}}).encode(), "checker.py": UNIMPLEMENTED_CHECKER_SOURCE}
    assert adoption_block(_resolved(starter, tenant_id="tenant-1", suite_key="custom:X"), verified=False, force_unverified=False) is None
