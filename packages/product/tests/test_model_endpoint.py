"""User input cannot turn a personal credential into an arbitrary URL proxy."""

from types import SimpleNamespace

import pytest
from product.model_endpoint import ModelEndpointError, approved_base_url, normalize_base_url, valid_model


@pytest.mark.parametrize(
    "url",
    [
        "http://models.company.test/v1",
        "file:///etc/passwd",
        "https://user:secret@models.company.test/v1",
        "https://models.company.test/v1?token=secret",
        "https://models.company.test/v1#secret",
        "https://models.company.test/v1?",
        "https://127.0.0.1/v1",
        "https://127.1/v1",
        "https://2130706433/v1",
        "https://0x7f000001/v1",
        "https://localhost/v1",
        "https://sub.localhost/v1",
        "https://169.254.169.254/v1",
        "https://[::1]/v1",
        "https://[::ffff:127.0.0.1]/v1",
        "https://0.0.0.0/v1",
        "https://224.0.0.1/v1",
        "https://metadata.google.internal/v1",
        "https://models.company.test./v1",
        "https://models.company.test/%2e%2e/admin",
        "https://models.company.test/v1/../admin",
        "https://models.company.test/v1//responses",
        "https://models.company.test\\@other.test/v1",
        "https://models.company.test:0/v1",
        "https://models.company.test:65536/v1",
        "https://models.company.test/v1\n",
        " https://models.company.test/v1",
    ],
)
def test_invalid_destinations_rejected_even_if_operator_lists_them(url):
    with pytest.raises(ModelEndpointError, match="invalid_model_endpoint"):
        approved_base_url(SimpleNamespace(tenant_model_allowed_base_urls=url), url)


def test_exact_https_prefix_allowlist_supports_trusted_internal_provider():
    settings = SimpleNamespace(tenant_model_allowed_base_urls="https://models.company.test/v1,https://10.25.2.10:8443/inference,https://inference.internal/v1")
    assert approved_base_url(settings, "https://MODELS.company.test:443/v1/") == "https://models.company.test/v1"
    assert approved_base_url(settings, "https://10.25.2.10:8443/inference") == "https://10.25.2.10:8443/inference"
    assert approved_base_url(settings, "https://inference.internal/v1") == "https://inference.internal/v1"
    for url in [
        "https://models.company.test/admin",
        "https://models.company.test/v1/responses",
        "https://models.company.test:444/v1",
        "https://evil.models.company.test/v1",
        "https://models.company.test.evil.test/v1",
    ]:
        with pytest.raises(ModelEndpointError, match="model_endpoint_not_allowed"):
            approved_base_url(settings, url)


def test_empty_or_broken_allowlist_does_not_grant_custom_destinations():
    settings = SimpleNamespace(tenant_model_allowed_base_urls="https://,*.company.test,http://10.0.0.1/v1")
    with pytest.raises(ModelEndpointError, match="model_endpoint_not_allowed"):
        approved_base_url(settings, "https://models.example.test/")
    with pytest.raises(ModelEndpointError, match="model_endpoint_not_allowed"):
        approved_base_url(settings, "https://models.company.test/v1")


def test_model_is_bounded_plain_identifier_and_endpoint_has_one_canonical_form():
    assert valid_model("x" * 128) == "x" * 128
    assert valid_model("gpt-6.1-sol") == "gpt-6.1-sol"
    assert valid_model("company/model:v2") == "company/model:v2"
    assert normalize_base_url("https://models.company.test:443/v1/") == "https://models.company.test/v1"
    for model in ["", " model", "model\n", "x" * 129, "model?key=secret", None]:
        with pytest.raises(ModelEndpointError, match="invalid_model_model"):
            valid_model(model)
