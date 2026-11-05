"""Production composition binds both audited artifacts and optimization RPC."""

from contextlib import contextmanager
from types import SimpleNamespace

from worker import deep_controller


def test_controller_wires_development_and_round_artifact_persistence(monkeypatch):
    policy = SimpleNamespace(runtime_identity=lambda: {"model": "deepseek-flash"})
    settings = SimpleNamespace(deep_controller_socket="/not-opened-in-this-controlled-test", deep_execution_revision=lambda: "runtime", deep_execution_policy=lambda: policy)
    store = SimpleNamespace(settings=settings, objects=object(), role="worker")
    runtime = SimpleNamespace(ready=lambda: True)
    captured = {}

    def handler(identity, context):
        return {"operation_id": identity.operation_id}

    @contextmanager
    def from_env(settings, objects, **kwargs):
        captured.update(kwargs)
        yield runtime

    class Server:
        def __init__(self, path, controller, **kwargs):
            captured["controller"] = controller

        def start(self):
            captured["started"] = True

        def close(self):
            captured["closed"] = True

    def compose(passed_store, passed_runtime):
        assert passed_store is store and passed_runtime is runtime
        return handler

    monkeypatch.setattr(deep_controller, "load_settings", lambda: settings)
    monkeypatch.setattr(deep_controller, "open_store", lambda settings: store)
    monkeypatch.setattr(deep_controller, "S3Objects", lambda settings: store.objects)
    monkeypatch.setattr(deep_controller, "runtime_from_env", from_env)
    monkeypatch.setattr(deep_controller, "runtime_revision", lambda *args: "runtime")
    monkeypatch.setattr(deep_controller, "object_file_source", lambda *args, **kwargs: lambda *args: b"")
    monkeypatch.setattr(deep_controller, "authorize_execution", lambda *args, **kwargs: lambda *args: None)
    monkeypatch.setattr(deep_controller, "development_handler", compose)
    monkeypatch.setattr(deep_controller, "UnixControllerServer", Server)
    monkeypatch.setattr(deep_controller.STOP, "wait", lambda: None)
    monkeypatch.setattr(deep_controller.signal, "signal", lambda *args: None)
    assert deep_controller.main() == 0
    assert captured["controller"].development_handler is handler
    persist = captured["save_round_artifacts"]
    assert persist.__self__.store is store
    assert persist.__name__ == "save_round_artifacts"
    assert captured["started"] and captured["closed"]
