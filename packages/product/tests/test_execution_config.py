import pytest
from product.execution_config import verify_process_config


def environment(**updates):
    return {
        "PPAP_PROFILE": "isolated-dev",
        "PPAP_BUILD_SHA": "a" * 40,
        "PPAP_SERVICE": "api",
        "PPAP_DEEP_LLM_BASE_URL": "https://deep.example/v1",
        "PPAP_DEEP_LLM_MODEL": "model",
        "PPAP_DEEP_REASONING_EFFORT": "high",
        **updates,
    }


def test_release_reads_the_container_start_configuration_without_credentials():
    actual = environment()
    identity = verify_process_config({k: v for k, v in actual.items() if k != "PPAP_SERVICE"}, process_env=actual)
    assert identity["model"] == "unconfigured" and identity["reasoning_effort"] == "high"
    assert identity["agent"] == "pi" and "API_KEY" not in str(identity)


def test_old_process_configuration_cannot_pass_after_configmap_changes():
    with pytest.raises(ValueError, match="differs"):
        verify_process_config({"PPAP_DEEP_REASONING_EFFORT": "low"}, process_env=environment())


def test_api_cannot_hold_the_deep_provider_credential():
    with pytest.raises(ValueError, match="only to the Controller"):
        verify_process_config({}, process_env=environment(PPAP_DEEP_LLM_API_KEY="private"))
