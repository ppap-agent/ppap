"""Preload the canonical release on both TKE nodes before an application rollout."""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMESPACE = "ppap-test"
NODES = ("10.3.4.3", "10.3.4.7")
OWNERS = {"api": "ppap-current-api", "worker": "ppap-current-worker", "deep": "ppap-current-worker", "web": "ppap-current-web"}


def command(args: list[str], *, data: bytes | None = None, timeout: int = 1800) -> bytes:
    result = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        # stderr may include credentials, signed registry URLs or application logs.
        raise RuntimeError(f"{Path(args[0]).name} {args[1]} failed (exit {result.returncode})")
    return result.stdout


def kube(*args: str, data: bytes | None = None, timeout: int = 1800) -> bytes:
    return command(["kubectl", "-n", NAMESPACE, *args], data=data, timeout=timeout)


def release_images(objects: list[dict]) -> dict[str, str]:
    images = {}
    policies = {}
    for obj in objects:
        if obj.get("kind") != "Deployment":
            continue
        for item in obj["spec"]["template"]["spec"].get("containers", []):
            name = item["name"]
            if OWNERS.get(name) == obj["metadata"]["name"]:
                if name in images:
                    raise ValueError("duplicate application image")
                images[name] = item["image"]
                policies[name] = item.get("imagePullPolicy")
    if not images:
        return {}
    if set(images) != set(OWNERS):
        raise ValueError("release must contain all four application images")
    prefixes = ("icoastline.tencentcloudcr.com/ppap-feishu/current-", "unregistry.xixifusi.online/ppap-")
    immutable = any(all(re.fullmatch(re.escape(prefix + name) + r"@sha256:[0-9a-f]{64}", ref) for name, ref in images.items()) for prefix in prefixes)
    rollback_tags = all(re.fullmatch(rf"unregistry\.xixifusi\.online/ppap-{name}:[0-9a-f]{{40}}", ref) for name, ref in images.items())
    if rollback_tags and len({ref.rsplit(":", 1)[1] for ref in images.values()}) != 1:
        raise ValueError("rollback images must use one source SHA")
    if not immutable and not rollback_tags:
        raise ValueError("release images must be immutable PPAP digests from one owned registry")
    if any(policy != "IfNotPresent" for policy in policies.values()):
        raise ValueError("preloaded images require IfNotPresent")
    return images


def tcr_pull_secret() -> str:
    """Reuse the legacy Web pull binding without copying registry credentials."""
    deployment = json.loads(kube("get", "deployment", "web-base", "-o", "json"))
    refs = deployment["spec"]["template"]["spec"].get("imagePullSecrets", [])
    for ref in refs:
        secret = json.loads(kube("get", "secret", ref["name"], "-o", "json"))
        if secret.get("type") != "kubernetes.io/dockerconfigjson":
            continue
        registry = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
        if "icoastline.tencentcloudcr.com" in registry.get("auths", {}):
            return ref["name"]
    raise ValueError("web-base must bind an existing pull Secret for the owned TCR host")


def native_objects(output: bytes) -> list[dict]:
    """kubectl create prints a JSON object per YAML document, not one array."""
    remaining = output.decode().strip()
    objects = []
    decoder = json.JSONDecoder()
    while remaining:
        obj, end = decoder.raw_decode(remaining)
        if not isinstance(obj, dict):
            raise ValueError("native render must contain Kubernetes objects")
        objects.extend(obj["items"] if obj.get("kind") == "List" else [obj])
        remaining = remaining[end:].lstrip()
    if not objects:
        raise ValueError("native render returned no objects")
    return objects


def pull_pod(name: str, node: str, bootstrap: str, cm: str, pull_secret: str | None, images: dict[str, str]) -> dict:
    security = {"runAsUser": 0, "readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
    host = {"name": "host", "mountPath": "/host", "readOnly": True}
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": NAMESPACE, "labels": {"ppap-image-preload": cm}},
        "spec": {
            "nodeName": node,
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 1200,
            "terminationGracePeriodSeconds": 5,
            **({"imagePullSecrets": [{"name": pull_secret}]} if pull_secret else {}),
            "initContainers": [
                {"name": "pull-" + key, "image": ref, "imagePullPolicy": "IfNotPresent", "command": ["true"], "resources": {"requests": {"cpu": "10m", "memory": "16Mi"}}}
                for key, ref in images.items()
            ],
            "containers": [
                {
                    "name": "inspect",
                    "image": bootstrap,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sleep", "1200"],
                    "securityContext": {**security, "capabilities": {"drop": ["ALL"], "add": ["SYS_CHROOT"]}},
                    "resources": {"requests": {"cpu": "10m", "memory": "32Mi"}},
                    "volumeMounts": [host, {"name": "scripts", "mountPath": "/scripts", "readOnly": True}],
                }
            ],
            "volumes": [{"name": "host", "hostPath": {"path": "/", "type": "Directory"}}, {"name": "scripts", "configMap": {"name": cm}}],
        },
    }


def smoke_pod(name: str, node: str, images: dict[str, str], cm: str, fixtures: dict[str, str]) -> dict:
    # This database and bucket exist only in the temporary Pod's network/emptyDir.
    password = "preload-isolated-fixture"
    env = {
        "PPAP_PROFILE": "isolated-dev",
        "PPAP_PG_HOST": "127.0.0.1",
        "PPAP_PG_DATABASE": "ppap_preload",
        "PPAP_PG_BOOTSTRAP_USER": "postgres",
        "PPAP_PG_MIGRATOR_USER": "ppap_migrator",
        "PPAP_PG_API_USER": "ppap_api",
        "PPAP_PG_WORKER_USER": "ppap_worker",
        "PPAP_S3_ENDPOINT": "http://127.0.0.1:9000",
        "PPAP_S3_BUCKET": "preload-smoke",
        "PPAP_S3_ACCESS_KEY": "preload-smoke",
        "PPAP_S3_SECRET_KEY": password,
        "PPAP_FEISHU_NOTIFICATIONS_ENABLED": "false",
        "PPAP_USAGE_CHARGE_ENABLED": "false",
        "PPAP_DEEP_CONTROLLER_SOCKET": "/run/ppap-deep/controller.sock",
        "PPAP_DEEP_RUNTIME": "container-only",
        "PPAP_DEEP_SANDBOX_PROFILE": "off",
        "LLM_API_KEY": "not-a-provider-key",
        "LLM_BASE_URL": "https://example.invalid/v1",
        "LLM_MODEL": "preload-startup-only",
        "PPAP_WORKER_ID": name,
    }
    env.update({f"PPAP_PG_{role}_PASSWORD": password for role in ("API", "WORKER", "MIGRATOR", "BOOTSTRAP")})
    containers = []
    for component, ref in images.items():
        variables = {**env, "PPAP_SERVICE": component}
        variables.update({"PPAP_PUBLIC_API_URL": "http://127.0.0.1:8080"} if component == "web" else {})
        containers.append(
            {
                "name": component,
                "image": ref,
                "imagePullPolicy": "Never",
                "command": ["sh", "-ec", "until [ -f /run/ppap-deep/preload-ready ]; do sleep 1; done; exec " + ("/entrypoint.sh" if component == "web" else "/app/entrypoint.sh")],
                "env": [{"name": key, "value": value} for key, value in variables.items()],
                "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}},
                "volumeMounts": [{"name": "socket", "mountPath": "/run/ppap-deep"}, {"name": "scripts", "mountPath": "/scripts", "readOnly": True}],
            }
        )
    pg_env = {**env, "POSTGRES_PASSWORD": password, "PGHOST": "127.0.0.1", "PGPASSWORD": password}
    containers.extend(
        [
            {
                "name": "postgres",
                "image": fixtures["postgres"],
                "imagePullPolicy": "IfNotPresent",
                "env": [{"name": key, "value": value} for key, value in pg_env.items()],
                "volumeMounts": [{"name": "pg", "mountPath": "/var/lib/postgresql/data"}, {"name": "migrations", "mountPath": "/migrations", "readOnly": True}],
                "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}},
            },
            {
                "name": "silo",
                "image": fixtures["silo"],
                "imagePullPolicy": "IfNotPresent",
                "args": ["server", "/data", "--console-address", ":9001"],
                "env": [{"name": "MINIO_ROOT_USER", "value": "preload-smoke"}, {"name": "MINIO_ROOT_PASSWORD", "value": password}],
                "volumeMounts": [{"name": "silo", "mountPath": "/data"}],
                "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}},
            },
        ]
    )
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": NAMESPACE, "labels": {"ppap-preload-smoke": cm}},
        "spec": {
            "nodeName": node,
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 600,
            "terminationGracePeriodSeconds": 10,
            "containers": containers,
            "volumes": [{"name": key, "emptyDir": {}} for key in ("pg", "silo", "socket")] + [{"name": "scripts", "configMap": {"name": cm}}, {"name": "migrations", "configMap": {"name": cm}}],
        },
    }


def preload(objects: list[dict], expected_configs: dict[str, str]) -> None:
    images = release_images(objects)
    if not images:
        raise ValueError("release must contain all four application images")
    nodes = json.loads(kube("get", "nodes", "-o", "json"))["items"]
    if {node["metadata"]["name"] for node in nodes} != set(NODES):
        raise ValueError("node membership changed; review pre-distribution targets before rollout")
    if any(node["status"]["nodeInfo"]["architecture"] != "amd64" or not any(c["type"] == "Ready" and c["status"] == "True" for c in node["status"]["conditions"]) for node in nodes):
        raise ValueError("both amd64 nodes must be Ready")
    pods = json.loads(kube("get", "pods", "--all-namespaces", "-o", "json"))["items"]
    for node in nodes:
        active = sum(pod.get("spec", {}).get("nodeName") == node["metadata"]["name"] and pod.get("status", {}).get("phase") not in {"Succeeded", "Failed"} for pod in pods)
        if active >= int(node["status"]["allocatable"]["pods"]):
            raise ValueError("a free Pod slot is required on each node; no production Pod will be evicted")
    config = next(obj for obj in objects if obj.get("kind") == "ConfigMap" and obj["metadata"]["name"] == "ppap-current")["data"]
    sha = config["PPAP_BUILD_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("release Git SHA is invalid")
    bootstrap = json.loads(kube("get", "deployment", "ppap-current-api", "-o", "json"))["spec"]["template"]["spec"]["containers"][0]["image"]
    fixtures = {key: json.loads(kube("get", "deployment", f"ppap-current-{key}", "-o", "json"))["spec"]["template"]["spec"]["containers"][0]["image"] for key in ("postgres", "silo")}
    if set(expected_configs) != set(images):
        raise ValueError("all four registry-verified config digests are required")
    pull_secret = tcr_pull_secret() if next(iter(images.values())).startswith("icoastline.tencentcloudcr.com/") else None
    run_id = "ppap-preload-" + secrets.token_hex(4)
    created: list[tuple[str, str]] = []

    def create(obj: dict) -> None:
        created.append((obj["kind"].lower(), obj["metadata"]["name"]))
        kube("create", "-f", "-", data=json.dumps(obj).encode())

    def delete(kind: str, name: str) -> None:
        kube("delete", kind, name, "--ignore-not-found", "--wait=true", "--timeout=60s")
        created.remove((kind, name))

    def execute(pod: str, *args: str, container: str = "inspect", data: bytes | None = None) -> bytes:
        return kube("exec", "-i", pod, "-c", container, "--", *args, data=data)

    try:
        create(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": run_id, "namespace": NAMESPACE},
                "data": {name: (HERE / name).read_text() for name in ("preload-node.py", "preload-smoke.py")}
                | {path.name: path.read_text() for path in (HERE.parents[1] / "migrations").iterdir() if path.suffix == ".sql" or path.name == "apply.sh"},
            }
        )
        # Allocate one Pod per node. Waits/inspections overlap; no node-to-node transfer.
        pulls = {node: run_id + "-pull-" + node.rsplit(".", 1)[1] for node in NODES}
        for node, name in pulls.items():
            create(pull_pod(name, node, bootstrap, run_id, pull_secret, images))

        def inspect(node: str) -> None:
            name = pulls[node]
            kube("wait", "--for=condition=Ready", "pod/" + name, "--timeout=900s")
            for component, ref in images.items():
                status = json.loads(execute(name, "python", "/scripts/preload-node.py", "inspect", ref, container="inspect"))
                if status.get("image") != ref or status.get("registered") is not True or status.get("config_digest") != expected_configs[component]:
                    raise RuntimeError("direct pull returned an invalid image registration")
                print(json.dumps({"node": node, **status, "transport": "direct-registry-pull"}), flush=True)

        with ThreadPoolExecutor(max_workers=len(NODES)) as pool:
            list(pool.map(inspect, NODES))
        for name in pulls.values():
            delete("pod", name)

        def startup(node: str) -> None:
            name = run_id + "-smoke-" + node.rsplit(".", 1)[1]
            kube("wait", "--for=condition=Ready", "pod/" + name, "--timeout=180s")
            execute(
                name,
                "sh",
                "-ec",
                'test "$PGHOST" = 127.0.0.1; '
                "until pg_isready -h 127.0.0.1 -U postgres; do sleep 1; done; "
                "psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f /migrations/bootstrap.sql; "
                "sh /migrations/apply.sh",
                container="postgres",
            )
            execute(name, "sh", "-ec", "mcli alias set smoke http://127.0.0.1:9000 preload-smoke preload-isolated-fixture >/dev/null; mcli mb --ignore-existing smoke/preload-smoke", container="silo")
            execute(name, "touch", "/run/ppap-deep/preload-ready", container="api")
            result = execute(name, "python", "/scripts/preload-smoke.py", sha, name, container="api")
            startup = json.loads(result)
            if (
                startup.get("git_sha") != sha
                or not all(startup.get(key) is True for key in ("api_ready", "web_ready", "worker_heartbeat", "deep_socket_response"))
                or startup.get("model_called") is not False
                or startup.get("production_data_accessed") is not False
            ):
                raise RuntimeError("full-image startup returned an incomplete verification")
            print(json.dumps({"node": node, "startup": startup}), flush=True)

        for node in NODES:
            create(smoke_pod(run_id + "-smoke-" + node.rsplit(".", 1)[1], node, images, run_id, fixtures))
        with ThreadPoolExecutor(max_workers=len(NODES)) as pool:
            list(pool.map(startup, NODES))
        for node in NODES:
            delete("pod", run_id + "-smoke-" + node.rsplit(".", 1)[1])
        print("Both nodes passed full-image startup; application rollout is now permitted.", flush=True)
    except Exception:
        for kind, name in created:
            if kind != "pod":
                continue
            try:
                status = json.loads(kube("get", "pod", name, "-o", "json"))["status"]
                states = [
                    {"name": item["name"], "reasons": [state.get("reason") for state in item.get("state", {}).values()]}
                    for item in status.get("initContainerStatuses", []) + status.get("containerStatuses", [])
                ]
                print(json.dumps({"pod": name, "phase": status.get("phase"), "reason": status.get("reason"), "containers": states}), file=sys.stderr)
            except (RuntimeError, KeyError, subprocess.TimeoutExpired):
                pass
        raise
    finally:
        for kind, name in reversed(created.copy()):
            try:
                delete(kind, name)
            except (RuntimeError, subprocess.TimeoutExpired):
                print(f"Cleanup required: {kind}/{name}", file=sys.stderr)
        if created:
            raise RuntimeError("temporary resource cleanup incomplete; rollout is forbidden")


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", type=Path, required=True, help="Registry-verified component to config digest JSON")
    parser.add_argument("--objects", type=Path, required=True, help="Canonical render-current.sh output")
    args = parser.parse_args()
    preload(native_objects(kube("create", "--dry-run=client", "-f", str(args.objects), "-o", "json")), json.loads(args.configs.read_text()))


if __name__ == "__main__":
    main()
