"""Preload transport and the boundary before any production apply."""

import hashlib
import http.server
import json
import runpy
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRELOAD = runpy.run_path(str(ROOT / "deploy/tke/preload-current.py"))
NODE = runpy.run_path(str(ROOT / "deploy/tke/preload-node.py"))
IMAGES = {name: f"ghcr.io/ppap-agent/ppap/{name}@sha256:{'1' * 64}" for name in PRELOAD["OWNERS"]}


def objects():
    return [
        {"kind": "Deployment", "metadata": {"name": owner}, "spec": {"template": {"spec": {"containers": [{"name": name, "image": IMAGES[name], "imagePullPolicy": "IfNotPresent"}]}}}}
        for name, owner in PRELOAD["OWNERS"].items()
    ]


def test_requires_complete_immutable_release():
    read = PRELOAD["release_images"]
    assert read(objects()) == IMAGES
    for field, value in (("image", "ghcr.io/ppap-agent/ppap/api:main"), ("imagePullPolicy", "Always"), ("image", "unregistry.xixifusi.online/ppap-api:old")):
        invalid = objects()
        invalid[0]["spec"]["template"]["spec"]["containers"][0][field] = value
        with pytest.raises(ValueError):
            read(invalid)
    with pytest.raises(ValueError):
        read(objects()[:-1])


def test_full_process_smoke_is_isolated_and_proves_cache():
    pod = PRELOAD["smoke_pod"]("probe", "node", IMAGES, "scripts", {"postgres": "postgres:16", "silo": "silo:fixture"})
    spec = pod["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert not any("persistentVolumeClaim" in volume or "hostPath" in volume for volume in spec["volumes"])
    for container in spec["containers"]:
        assert "envFrom" not in container
        assert not any("valueFrom" in entry for entry in container["env"])
        env = {entry["name"]: entry["value"] for entry in container["env"]}
        if container["name"] not in IMAGES:
            continue
        assert container["imagePullPolicy"] == "Never"
        assert "PPAP_BUILD_SHA" not in env  # Verify the baked SHA, do not fake it.
        assert env["PPAP_PG_HOST"] == "127.0.0.1"
        assert env["PPAP_PG_DATABASE"] == "ppap_preload"
        assert env["PPAP_S3_ENDPOINT"] == "http://127.0.0.1:9000"
        assert env["PPAP_FEISHU_NOTIFICATIONS_ENABLED"] == "false"
        assert env["PPAP_USAGE_CHARGE_ENABLED"] == "false"
        assert env["LLM_BASE_URL"] == "https://example.invalid/v1"
        assert "preload-ready" in container["command"][-1]


def test_only_source_kubelet_receives_private_registry_credentials():
    for source in (True, False):
        pod = PRELOAD["transfer_pod"]("probe", "node", "cached-bootstrap", source, "scripts", "credentials", IMAGES)
        spec = pod["spec"]
        assert spec.get("imagePullSecrets", []) == ([{"name": "credentials-ghcr"}] if source else [])
        secret = next(volume["secret"] for volume in spec["volumes"] if volume["name"] == "credentials")
        assert secret["items"] == [{"key": "transfer-token", "path": "transfer-token"}]
        pulls = [container for container in spec.get("initContainers", []) if container["name"].startswith("pull-")]
        assert {container["image"] for container in pulls} == (set(IMAGES.values()) if source else set())
        assert all(container["command"] == ["true"] for container in pulls)


def test_receiver_checks_archive_before_native_import(tmp_path):
    payload = b"bounded image archive fixture"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers["Authorization"] == "Bearer fixture"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = tmp_path / "token"
    token.write_text("fixture")
    receive = NODE["receive"]
    # Same production receiver; only network address, filesystem and native CRI
    # boundary differ. A bad archive must never reach that CRI boundary.
    original_request = NODE["urllib"].request.Request

    def request(url, **kwargs):
        return original_request(url.replace(":18081/", f":{server.server_port}/"), **kwargs)

    try:
        with (
            patch.dict(receive.__globals__, {"ROOT": tmp_path, "TOKEN_FILE": token}),
            patch.object(NODE["urllib"].request, "Request", side_effect=request),
            patch.dict(receive.__globals__, {"import_image": lambda path, ref: {"image": ref, "digest": NODE["checksum"](path)}}),
        ):
            with pytest.raises(ValueError, match="checksum mismatch"):
                receive("api", IMAGES["api"], "127.0.0.1", "wrong")
            result = receive("api", IMAGES["api"], "127.0.0.1", hashlib.sha256(payload).hexdigest())
            assert result["image"] == IMAGES["api"]
            assert result["digest"] == hashlib.sha256(payload).hexdigest()
            assert not (tmp_path / "api.tar").exists()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_export_failure_cleans_resources_without_starting_smoke():
    preload = PRELOAD["preload"]
    created, deleted, executed = [], [], []

    def kube(*args, **kwargs):
        if args[:2] == ("get", "nodes"):
            return json.dumps(
                {
                    "items": [
                        {"metadata": {"name": node}, "status": {"allocatable": {"pods": "61"}, "nodeInfo": {"architecture": "amd64"}, "conditions": [{"type": "Ready", "status": "True"}]}}
                        for node in PRELOAD["NODES"]
                    ]
                }
            ).encode()
        if args[:2] == ("get", "pods"):
            return b'{"items":[]}'
        if args[:2] == ("get", "deployment"):
            return b'{"spec":{"template":{"spec":{"containers":[{"image":"cached-fixture"}]}}}}'
        if args[0] == "create":
            created.append(json.loads(kwargs["data"]))
        if args[0] == "delete":
            deleted.append((args[1], args[2]))
        if args[0] == "exec":
            executed.append(args)
            if "export" in args:
                raise RuntimeError("native export unavailable")
        if args[:2] == ("get", "pod"):
            return b'{"status":{"podIP":"10.0.0.1"}}'
        return b""

    config = {"kind": "ConfigMap", "metadata": {"name": "ppap-current"}, "data": {"PPAP_BUILD_SHA": "2" * 40}}
    with patch.dict(preload.__globals__, {"kube": kube}), patch.dict(PRELOAD["os"].environ, {"GHCR_USERNAME": "fixture", "GHCR_TOKEN": "fixture"}):
        with pytest.raises(RuntimeError, match="native export unavailable"):
            preload([*objects(), config])
    assert len(created) == 5
    assert {(obj["kind"].lower(), obj["metadata"]["name"]) for obj in created} == set(deleted)
    assert not any("preload-smoke.py" in args for args in executed)
