"""Cross-package policy identity. Product distribution tests cannot import the agent runtime."""

from pathlib import Path

from agent.execution.isolation import RuntimeConfig
from product.settings import Settings


def test_acceptance_and_runtime_use_the_same_effective_policy():
    settings = Settings(git_sha="a" * 40, model_text_base_url="https://model.example/v1", model_text_model="model-a", deep_executor_revision="release-a", deep_sandbox_profile="off")
    config = RuntimeConfig(state=Path("/unused"), gateway_socket=Path("/unused/gateway.sock"), gateway_host="127.0.0.1", **settings.deep_execution_policy().model_dump())
    accepted = settings.deep_execution_revision()
    assert config.engine_revision() == accepted
    for field, value in (("app_revision", "b" * 40), ("model", "model-b"), ("model_revision", "b" * 64), ("sandbox_profile", "ppap"), ("memory_mib", 2048), ("max_processes", 128)):
        changed = config.model_copy(update={field: value})
        assert changed.engine_revision() != accepted
