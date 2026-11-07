"""Explicit process controls must win over environment and dotenv aliases."""

import pytest
from product.settings import Settings


@pytest.mark.parametrize("source", ["dotenv", "environment"])
@pytest.mark.parametrize("enabled", [False, True])
def test_explicit_effect_controls_win_over_lower_priority_aliases(tmp_path, monkeypatch, source, enabled):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    aliases = ("PPAP_USAGE_CHARGE_ENABLED", "PPAP_FEISHU_NOTIFICATIONS_ENABLED")
    for alias in aliases:
        monkeypatch.delenv(alias, raising=False)
    lower = str(not enabled).lower()
    config = None
    if source == "dotenv":
        config = tmp_path / "runtime.env"
        config.write_text("".join(f"{alias}={lower}\n" for alias in aliases))
    else:
        for alias in aliases:
            monkeypatch.setenv(alias, lower)

    settings = Settings(_env_file=config, usage_charge_enabled=enabled, feishu_notifications_enabled=enabled)

    observed = (settings.usage_charge_enabled, settings.feishu_notifications_enabled)
    assert observed == (enabled, enabled)
    assert settings.public_config()["usage_charge_enabled"] is enabled


def test_explicit_empty_identity_and_port_win_over_environment_aliases(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    monkeypatch.setenv("FEISHU_APP_ID", "environment-app")
    monkeypatch.setenv("PPAP_PG_PORT", "5432")
    settings = Settings(feishu_app_id="", pg_port=15432)

    observed = (settings.feishu_app_id, settings.pg_port)
    assert observed == ("", 15432)


def test_environment_still_wins_over_dotenv_when_no_explicit_value_is_given(tmp_path, monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    monkeypatch.setenv("PPAP_USAGE_CHARGE_ENABLED", "false")
    config = tmp_path / "runtime.env"
    config.write_text("PPAP_USAGE_CHARGE_ENABLED=true\n")

    settings = Settings(_env_file=config)

    observed = settings.usage_charge_enabled
    assert observed is False


def test_ocr_configuration_is_independent_from_text_model(monkeypatch):
    monkeypatch.setenv("PPAP_PROFILE", "isolated-dev")
    for name in ("OCR_MODEL_NAME", "OCR_OPENAI_API_KEY", "OCR_OPENAI_BASE_URL", "MODEL_NAME", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    inherited = Settings(llm_model="text-model", llm_api_key="text-key", llm_base_url="https://text.invalid/v1")
    dedicated = Settings(llm_model="text-model", OCR_MODEL_NAME="dedicated-ocr-model", OCR_OPENAI_API_KEY="ocr-key", OCR_OPENAI_BASE_URL="https://ocr.invalid/v1")

    with pytest.raises(ValueError, match="OCR_MODEL_NAME"):
        inherited.get_ocr_model_name()
    assert inherited.get_ocr_openai_api_key() == ""
    assert inherited.get_ocr_openai_base_url() == ""
    assert inherited.standard_execution_policy().model == "unconfigured"
    assert dedicated.get_ocr_model_name() == "dedicated-ocr-model"
    assert dedicated.get_ocr_openai_api_key() == "ocr-key"
    assert dedicated.get_ocr_openai_base_url() == "https://ocr.invalid/v1"
