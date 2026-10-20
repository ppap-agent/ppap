"""Preload the canonical release on both TKE nodes before an application rollout."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import signal
import subprocess
import sys
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
    for obj in objects:
        if obj.get("kind") != "Deployment":
            continue
        for item in obj["spec"]["template"]["spec"].get("containers", []):
            name = item["name"]
            if OWNERS.get(name) == obj["metadata"]["name"]:
                images[name] = item["image"]
                if item["image"].startswith("ghcr.io/") and item.get("imagePullPolicy") != "IfNotPresent":
                    raise ValueError("preloaded images require IfNotPresent")
    if not any(ref.startswith("ghcr.io/") for ref in images.values()):
        return {}
    if set(images) != set(OWNERS):
        raise ValueError("release must contain all four application images")
    for name, ref in images.items():
        if not re.fullmatch(rf"ghcr\.io/ppap-agent/ppap/{name}@sha256:[0-9a-f]{{64}}", ref):
            raise ValueError("release images must be immutable PPAP GHCR digests")
    return images


def transfer_pod(name: str, node: str, bootstrap: str, source: bool, cm: str, credentials: str, images: dict[str, str]) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": NAMESPACE, "labels": {"ppap-image-transfer": cm, "transfer-role": "source" if source else "receiver"}},
        "spec": {
            "nodeName": node,
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 7200,
            "terminationGracePeriodSeconds": 5,
            **(
                {
                    "imagePullSecrets": [{"name": credentials + "-ghcr"}],
                    "initContainers": [
                        {
                            "name": "check-disk",
                            "image": bootstrap,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["python", "-c", "import os; s=os.statvfs('/host/var/lib/containerd'); assert s.f_bavail*s.f_frsize >= 4*1024**3, 'at least 4 GiB free required'"],
                            "volumeMounts": [{"name": "host", "mountPath": "/host", "readOnly": True}],
                            "securityContext": {"runAsUser": 0, "readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                        }
                    ]
                    + [
                        {"name": "pull-" + key, "image": ref, "imagePullPolicy": "IfNotPresent", "command": ["true"], "resources": {"requests": {"cpu": "10m", "memory": "16Mi"}}}
                        for key, ref in images.items()
                    ],
                }
                if source
                else {}
            ),
            "containers": [
                {
                    "name": "transfer",
                    "image": bootstrap,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["python", "/scripts/preload-node.py", "serve"] if source else ["sleep", "7200"],
                    "securityContext": {"runAsUser": 0, "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"], "add": ["SYS_CHROOT"]}},
                    "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}},
                    "volumeMounts": [
                        {"name": "host", "mountPath": "/host", "readOnly": True},
                        {"name": "transfer", "mountPath": "/transfer"},
                        {"name": "scripts", "mountPath": "/scripts", "readOnly": True},
                        {"name": "credentials", "mountPath": "/credentials", "readOnly": True},
                    ],
                }
            ],
            "volumes": [
                {"name": "host", "hostPath": {"path": "/", "type": "Directory"}},
                {"name": "transfer", "emptyDir": {}},
                {"name": "scripts", "configMap": {"name": cm}},
                {
                    "name": "credentials",
                    "secret": {
                        "secretName": credentials,
                        "defaultMode": 0o400,
                        "items": [{"key": "transfer-token", "path": "transfer-token"}],
                    },
                },
            ],
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


def preload(objects: list[dict]) -> None:
    images = release_images(objects)
    if not images:
        print("No GHCR application images; pre-distribution is not required.", flush=True)
        return
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
    run_id = "ppap-preload-" + secrets.token_hex(4)
    created: list[tuple[str, str]] = []

    def create(obj: dict) -> None:
        created.append((obj["kind"].lower(), obj["metadata"]["name"]))
        kube("create", "-f", "-", data=json.dumps(obj).encode())

    def delete(kind: str, name: str) -> None:
        kube("delete", kind, name, "--ignore-not-found", "--wait=true", "--timeout=60s")
        created.remove((kind, name))

    def execute(pod: str, *args: str, container: str = "transfer", data: bytes | None = None) -> bytes:
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
        username, token = os.getenv("GHCR_USERNAME", ""), os.getenv("GHCR_TOKEN", "")
        if not username or not token:
            raise ValueError("set GHCR_USERNAME and GHCR_TOKEN for private images")
        create(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": run_id, "namespace": NAMESPACE},
                "type": "Opaque",
                "data": {"transfer-token": base64.b64encode(secrets.token_bytes(32).hex().encode()).decode()},
            }
        )
        registry = {"auths": {"ghcr.io": {"auth": base64.b64encode(f"{username}:{token}".encode()).decode()}}}
        create(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": run_id + "-ghcr", "namespace": NAMESPACE},
                "type": "kubernetes.io/dockerconfigjson",
                "data": {".dockerconfigjson": base64.b64encode(json.dumps(registry).encode()).decode()},
            }
        )
        sender, receiver = run_id + "-source", run_id + "-receiver"
        for pod, node, source in ((sender, NODES[0], True), (receiver, NODES[1], False)):
            create(transfer_pod(pod, node, bootstrap, source, run_id, run_id, images))
            kube("wait", "--for=condition=Ready", "pod/" + pod, "--timeout=900s")
            execute(pod, "python", "-c", "import os; s=os.statvfs('/host/var/lib/containerd'); assert s.f_bavail*s.f_frsize >= 4*1024**3, 'at least 4 GiB free required'")
        address = json.loads(kube("get", "pod", sender, "-o", "json"))["status"]["podIP"]
        for name, ref in images.items():
            print(f"Exporting complete {name} image from the GHCR cache on {NODES[0]}", flush=True)
            status = json.loads(execute(sender, "python", "/scripts/preload-node.py", "export", name, ref))
            print(json.dumps({"node": NODES[0], **status}), flush=True)
            received = json.loads(execute(receiver, "python", "/scripts/preload-node.py", "receive", name, ref, address, status["archive_sha256"]))
            if any(item.get("image") != ref or item.get("registered") is not True for item in (status, received)):
                raise RuntimeError("pre-distribution returned an invalid image registration")
            print(json.dumps({"node": NODES[1], **received, "transport": "private-pod-network"}), flush=True)
            execute(sender, "python", "/scripts/preload-node.py", "clean", name)
        delete("pod", sender)
        delete("pod", receiver)
        for node in NODES:
            name = run_id + "-smoke-" + node.rsplit(".", 1)[1]
            create(smoke_pod(name, node, images, run_id, fixtures))
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
            if startup.get("git_sha") != sha or not all(startup.get(key) is True for key in ("api_ready", "web_ready", "worker_heartbeat", "deep_socket_response")):
                raise RuntimeError("full-image startup returned an incomplete verification")
            print(json.dumps({"node": node, "startup": startup}), flush=True)
            delete("pod", name)
        print("Both nodes passed full-image startup; application rollout is now permitted.", flush=True)
    finally:
        for kind, name in reversed(created.copy()):
            try:
                delete(kind, name)
            except (RuntimeError, subprocess.TimeoutExpired):
                print(f"Cleanup required: {kind}/{name}", file=sys.stderr)


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objects", type=Path, required=True, help="Canonical render-current.sh output")
    args = parser.parse_args()
    rendered = json.loads(kube("create", "--dry-run=client", "-f", str(args.objects), "-o", "json"))
    preload(rendered.get("items", [rendered]))


if __name__ == "__main__":
    main()
