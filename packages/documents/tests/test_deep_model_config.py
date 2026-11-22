"""Deep uses its own model only when fully configured; half a configuration is refused, never silently replaced."""

import pytest
from documents.model_access import ModelUnavailable, platform_model_access

DEEP = ("PPAP_MODEL_CODING_BASE_URL", "PPAP_MODEL_CODING_MODEL", "PPAP_MODEL_CODING_API_KEY")


@pytest.fixture(autouse=True)
def text_model(monkeypatch):
    monkeypatch.setenv("PPAP_MODEL_TEXT_BASE_URL", "https://text.example.test/v1")
    monkeypatch.setenv("PPAP_MODEL_TEXT_MODEL", "text-model")
    monkeypatch.setenv("PPAP_MODEL_TEXT_API_KEY", "text-key")
    for name in DEEP:
        monkeypatch.delenv(name, raising=False)


def configure(monkeypatch, *values):
    for name, value in zip(DEEP, values):
        if value:
            monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    "values",
    [
        ("https://deep.example.test/v1", "", ""),
        ("", "deep-model", ""),
        ("https://deep.example.test/v1", "", "deep-key"),
        ("", "deep-model", "deep-key"),
        ("", "", "deep-key"),  # a Deep key without a Deep endpoint is not quietly dropped
    ],
)
def test_incomplete_deep_configuration_is_refused(monkeypatch, values):
    configure(monkeypatch, *values)
    with pytest.raises(ModelUnavailable, match="deep_model_config_incomplete"):
        platform_model_access("coding")
    assert platform_model_access("text").model == "text-model"


def test_without_deep_configuration_deep_uses_the_text_model():
    access = platform_model_access("coding")
    assert (access.base_url, access.model, access.api_key.get_secret_value()) == ("https://text.example.test/v1", "text-model", "text-key")


def test_a_complete_deep_configuration_is_used_and_its_key_may_live_elsewhere(monkeypatch):
    configure(monkeypatch, "https://deep.example.test/v1", "deep-model", "deep-key")
    access = platform_model_access("coding")
    assert (access.base_url, access.model, access.api_key.get_secret_value()) == ("https://deep.example.test/v1", "deep-model", "deep-key")
    # API and Worker hold the endpoint and model only; the identity is the same, the call is what needs the key.
    monkeypatch.delenv("PPAP_MODEL_CODING_API_KEY")
    keyless = platform_model_access("coding")
    assert keyless.identity() == access.identity() and keyless.api_key.get_secret_value() == ""
