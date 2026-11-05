"""Publication acknowledgement is not a Checker verdict or regression proof."""

import pytest
from product.suite_releases import empty_validation, publication_fingerprint


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
