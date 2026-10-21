"""Preload transport and the boundary before any production apply."""

import hashlib
import http.server
import json
import runpy
import threading
from pathlib import Path
from types import SimpleNamespace
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


def test_tcr_release_cannot_skip_preload_or_mix_registries():
    read = PRELOAD["release_images"]
    docs = objects()
    expected = {name: f"icoastline.tencentcloudcr.com/ppap-feishu/current-{name}@sha256:{'2' * 64}" for name in IMAGES}
    for doc in docs:
        container = doc["spec"]["template"]["spec"]["containers"][0]
        container["image"] = expected[container["name"]]
    assert read(docs) == expected
    for image in (IMAGES["api"], expected["api"].replace("ppap-feishu", "foreign"), expected["api"].split("@")[0] + ":main"):
        docs[0]["spec"]["template"]["spec"]["containers"][0]["image"] = image
        with pytest.raises(ValueError, match="one owned registry"):
            read(docs)
    docs[0]["spec"]["template"]["spec"]["containers"][0]["image"] = expected["api"]
    docs[0]["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] = "Always"
    with pytest.raises(ValueError, match="IfNotPresent"):
        read(docs)
    with pytest.raises(ValueError, match="all four"):
        read(docs[:-1])


def test_only_complete_legacy_release_retains_rollback_path():
    docs = objects()
    for doc in docs:
        container = doc["spec"]["template"]["spec"]["containers"][0]
        container["image"] = f"unregistry.xixifusi.online/ppap-{container['name']}:{'3' * 40}"
    assert PRELOAD["release_images"](docs) == {}
    docs[0]["spec"]["template"]["spec"]["containers"][0]["image"] = "foreign.example/api:old"
    with pytest.raises(ValueError):
        PRELOAD["release_images"](docs)


def test_tcr_binding_uses_existing_host_and_never_copies_credentials():
    find = PRELOAD["tcr_pull_secret"]
    calls = []

    def kube(*args):
        calls.append(args)
        if args[1] == "deployment":
            return b'{"spec":{"template":{"spec":{"imagePullSecrets":[{"name":"existing-tcr"}]}}}}'
        config = PRELOAD["base64"].b64encode(b'{"auths":{"icoastline.tencentcloudcr.com":{"auth":"private"}}}').decode()
        return json.dumps({"type": "kubernetes.io/dockerconfigjson", "data": {".dockerconfigjson": config}}).encode()

    with patch.dict(find.__globals__, {"kube": kube}):
        assert find() == "existing-tcr"
    assert all(call[0] == "get" for call in calls)
    with patch.dict(find.__globals__, {"kube": lambda *args: b'{"spec":{"template":{"spec":{"imagePullSecrets":[]}}}}'}):
        with pytest.raises(ValueError, match="existing pull Secret"):
            find()


def test_native_multi_document_json_keeps_every_deployment():
    docs = objects()
    stream = "\n".join(json.dumps(obj) for obj in docs).encode()
    assert PRELOAD["native_objects"](stream) == docs
    assert PRELOAD["native_objects"](json.dumps({"kind": "List", "items": docs}).encode()) == docs
    assert PRELOAD["release_images"](PRELOAD["native_objects"](stream)) == IMAGES
    with pytest.raises(ValueError, match="no objects"):
        PRELOAD["native_objects"](b"")


def test_disk_pressure_aborts_before_allocating_resources():
    nodes = [
        {"metadata": {"name": node}, "status": {"nodeInfo": {"architecture": "amd64"}, "conditions": [{"type": "Ready", "status": "True"}, {"type": "DiskPressure", "status": "True"}]}}
        for node in PRELOAD["NODES"]
    ]
    preload = PRELOAD["preload"]
    with patch.dict(preload.__globals__, {"kube": lambda *args, **kwargs: json.dumps({"items": nodes}).encode()}):
        with pytest.raises(ValueError, match="disk pressure"):
            preload(objects())


def test_emitted_disk_check_reserves_space_for_full_images():
    for gib in (9, 12):
        with patch.object(PRELOAD["os"], "statvfs", return_value=SimpleNamespace(f_bavail=gib * 1024**3, f_frsize=1)):
            if gib < 12:
                with pytest.raises(AssertionError, match="12 GiB"):
                    exec(PRELOAD["DISK_CHECK"])
            else:
                exec(PRELOAD["DISK_CHECK"])


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


@pytest.mark.parametrize("failed_inspection", [False, True])
def test_tcr_pulls_each_node_directly_and_requires_both_startup_checks(failed_inspection):
    preload = PRELOAD["preload"]
    created, deleted, executed = [], [], []
    images = {name: f"icoastline.tencentcloudcr.com/ppap-feishu/current-{name}@sha256:{'2' * 64}" for name in IMAGES}
    docs = objects()
    for doc in docs:
        container = doc["spec"]["template"]["spec"]["containers"][0]
        container["image"] = images[container["name"]]

    def kube(*args, **kwargs):
        if args[:2] == ("get", "nodes"):
            return json.dumps(
                {
                    "items": [
                        {
                            "metadata": {"name": node},
                            "status": {
                                "allocatable": {"pods": "61"},
                                "nodeInfo": {"architecture": "amd64"},
                                "conditions": [{"type": "Ready", "status": "True"}, {"type": "DiskPressure", "status": "False"}],
                            },
                        }
                        for node in PRELOAD["NODES"]
                    ]
                }
            ).encode()
        if args[:2] == ("get", "pods"):
            return b'{"items":[]}'
        if args[:2] == ("get", "pod"):
            return b'{"status":{"phase":"Running"}}'
        if args[:2] == ("get", "deployment"):
            return b'{"spec":{"template":{"spec":{"containers":[{"image":"cached-fixture"}]}}}}'
        if args[0] == "create":
            created.append(json.loads(kwargs["data"]))
        if args[0] == "delete":
            deleted.append((args[1], args[2]))
        if args[0] == "exec":
            executed.append(args)
            if "inspect" in args:
                return json.dumps({"image": args[-1], "registered": not failed_inspection}).encode()
            if "/scripts/preload-smoke.py" in args:
                return json.dumps({"git_sha": "2" * 40, "api_ready": True, "web_ready": True, "worker_heartbeat": True, "deep_socket_response": True}).encode()
        return b""

    config = {"kind": "ConfigMap", "metadata": {"name": "ppap-current"}, "data": {"PPAP_BUILD_SHA": "2" * 40}}
    with patch.dict(preload.__globals__, {"kube": kube, "tcr_pull_secret": lambda: "existing-tcr"}), patch.dict(PRELOAD["os"].environ, {}, clear=True):
        if failed_inspection:
            with pytest.raises(RuntimeError, match="invalid image registration"):
                preload([*docs, config])
        else:
            preload([*docs, config])
    assert {(obj["kind"].lower(), obj["metadata"]["name"]) for obj in created} == set(deleted)
    assert not any(obj["kind"] == "Secret" for obj in created)
    assert not any(action in args for args in executed for action in ("export", "receive", "serve", "clean"))
    pulls = [obj for obj in created if obj["kind"] == "Pod" and "imagePullSecrets" in obj["spec"]]
    assert {obj["spec"]["nodeName"] for obj in pulls} == ({PRELOAD["NODES"][0]} if failed_inspection else set(PRELOAD["NODES"]))
    for obj in pulls:
        spec = obj["spec"]
        assert spec["imagePullSecrets"] == [{"name": "existing-tcr"}]
        assert not any("secret" in volume or "emptyDir" in volume for volume in spec["volumes"])
        assert {c["image"] for c in spec["initContainers"] if c["name"].startswith("pull-")} == set(images.values())
        assert all(c["command"] == ["true"] for c in spec["initContainers"] if c["name"].startswith("pull-"))
    smokes = [obj for obj in created if obj["kind"] == "Pod" and "smoke" in obj["metadata"]["name"]]
    assert len(smokes) == (0 if failed_inspection else 2)
    assert sum("/scripts/preload-smoke.py" in args for args in executed) == len(smokes)
