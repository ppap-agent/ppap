"""TKE manifests are rendered by Kustomize and checked by kubeconform.

Kubernetes schema does not encode PPAP deploy policy: no node pin, no Docker
socket, usage charge stays off, the worker still starts when Deep is late,
and the edition proxy ConfigMap is the nginx conf file. Those checks read the
rendered object model. They do not scan raw manifest text.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TKE = ROOT / "deploy" / "tke"
KUSTOMIZE = "registry.k8s.io/kustomize/kustomize:v5.6.0"
KUBECONFORM = "ghcr.io/yannh/kubeconform:v0.6.7"
YQ = "mikefarah/yq:4.44.6"
WORKLOADS = (
    "ppap-current-postgres",
    "ppap-current-silo",
    "ppap-current-api",
    "ppap-current-worker",
    "ppap-current-web",
)


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, capture_output=True, text=True, **kwargs)


def _find(node, key):
    if isinstance(node, dict):
        if key in node:
            yield node[key]
        for value in node.values():
            yield from _find(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _find(item, key)


def _containers(doc: dict):
    spec = ((doc.get("spec") or {}).get("template") or {}).get("spec") or {}
    yield from spec.get("containers") or []
    yield from spec.get("initContainers") or []


class TkeCurrentDeployTests(unittest.TestCase):
    def test_kustomize_render_matches_schema_and_ppap_policy(self):
        rendered = _run([str(TKE / "render-current.sh")])
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        schema = _run(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                KUBECONFORM,
                "-strict",
                "-summary",
                "-kubernetes-version",
                "1.28.0",
                "-",
            ],
            input=rendered.stdout,
        )
        self.assertEqual(schema.returncode, 0, schema.stdout + schema.stderr)
        converted = _run(["docker", "run", "--rm", "-i", YQ, "eval-all", "-o=json", "[.]"], input=rendered.stdout)
        self.assertEqual(converted.returncode, 0, converted.stderr)
        docs = json.loads(converted.stdout)
        names = [doc.get("metadata", {}).get("name") for doc in docs]
        self.assertFalse(any(doc.get("kind") == "Secret" for doc in docs))
        for name in WORKLOADS:
            self.assertIn(name, names)
        blob = json.dumps(docs)
        self.assertNotIn("ppap.api.xixifusi.online", blob)
        self.assertNotIn("ppap-agent.xixifusi.online", blob)
        self.assertNotIn("docker.sock", blob)
        self.assertNotIn("/var/run/docker", blob)
        self.assertNotIn('"/v2"', blob)
        self.assertEqual(list(_find(docs, "nodeSelector")), [])
        self.assertIn("cbs", list(_find(docs, "storageClassName")))
        config = next(doc for doc in docs if doc.get("kind") == "ConfigMap" and doc["metadata"]["name"] == "ppap-current")
        self.assertEqual(config["metadata"]["namespace"], "ppap-test")
        self.assertEqual(config["data"]["PPAP_USAGE_CHARGE_ENABLED"], "false")
        self.assertEqual(config["data"]["PPAP_DEEP_EXECUTOR_REVISION"], "ppap-deep-v2")
        self.assertEqual(config["data"]["PPAP_DEEP_RUNTIME"], "container-only")
        self.assertEqual(config["data"]["PPAP_DEEP_SANDBOX_PROFILE"], "off")
        self.assertNotIn("PPAP_DEEP_CUSTOMER_ENABLED", config["data"])
        images = [container["image"] for doc in docs for container in _containers(doc) if container.get("image")]
        app_containers = [container for doc in docs for container in _containers(doc) if container.get("name") in {"api", "worker", "deep", "web"}]
        app_images = [container["image"] for container in app_containers]
        self.assertEqual(len(app_images), 4)
        self.assertEqual({container["name"] for container in app_containers}, {"api", "worker", "deep", "web"})
        self.assertRegex(config["data"]["PPAP_BUILD_SHA"], r"^[0-9a-f]{40}$")
        for container in app_containers:
            registry_path = r"(?:icoastline\.tencentcloudcr\.com/ppap-feishu/current-|unregistry\.xixifusi\.online/ppap-)"
            self.assertRegex(container["image"], registry_path + container["name"] + r"@sha256:[0-9a-f]{64}$")
            self.assertEqual(container["imagePullPolicy"], "IfNotPresent")
        self.assertTrue(any("postgres:" in image for image in images))
        self.assertTrue(any(image.startswith("docker.io/pgsty/silo:") for image in images))
        database_keys = {"PPAP_PG_API_PASSWORD", "PPAP_PG_WORKER_PASSWORD", "PPAP_PG_MIGRATOR_PASSWORD", "PPAP_PG_BOOTSTRAP_PASSWORD"}
        for doc in docs:
            for container in _containers(doc):
                name = container.get("name")
                if name not in {"api", "worker", "deep", "init-bucket"}:
                    continue
                self.assertFalse(any("secretRef" in entry for entry in container.get("envFrom", [])), name)
                keys = {entry.get("valueFrom", {}).get("secretKeyRef", {}).get("key") for entry in container.get("env", [])}
                expected = {"PPAP_PG_API_PASSWORD"} if name == "api" else {"PPAP_PG_WORKER_PASSWORD"} if name in {"worker", "deep"} else set()
                self.assertEqual(keys & database_keys, expected, name)
        worker = next(container for doc in docs if doc.get("metadata", {}).get("name") == "ppap-current-worker" for container in _containers(doc) if container.get("name") == "worker")
        init_db = next(container for doc in docs if doc.get("metadata", {}).get("name") == "ppap-current-init-db" for container in _containers(doc) if container.get("name") == "init-db")
        init_command = "\n".join(init_db.get("args") or [])
        self.assertIn("sh /migrations/apply.sh", init_command)
        self.assertNotIn("apply.sql", init_command)

        command = "\n".join(worker.get("args") or [])
        self.assertIn('until [ -S "$PPAP_DEEP_CONTROLLER_SOCKET" ]', command)
        self.assertIn("warning: deep controller socket unavailable after 60s; starting Worker so Standard remains available", command)
        self.assertIn("exec python -m worker.main", command)
        self.assertNotIn("deep controller socket missing", command)
        deep = next(container for doc in docs if doc.get("metadata", {}).get("name") == "ppap-current-worker" for container in _containers(doc) if container.get("name") == "deep")
        self.assertEqual(next(item["value"] for item in deep["env"] if item["name"] == "PPAP_SERVICE"), "deep")
        self.assertEqual(deep.get("readinessProbe", {}).get("exec", {}).get("command"), ["python", "-m", "agent.readiness"])
        self.assertTrue(any("emptyDir" in json.dumps(doc.get("spec", {})) for doc in docs if doc.get("metadata", {}).get("name") == "ppap-current-worker"))
        proxy_conf = next(doc for doc in docs if doc.get("kind") == "ConfigMap" and doc["metadata"]["name"].startswith("edition-proxy-"))
        self.assertEqual(proxy_conf["data"]["default.conf"].strip(), (TKE / "edition-proxy.conf").read_text().strip())
        proxy_deployment = next(doc for doc in docs if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "edition-proxy")
        proxy_volume = next(volume for volume in proxy_deployment["spec"]["template"]["spec"]["volumes"] if volume["name"] == "conf")
        self.assertEqual(proxy_volume["configMap"]["name"], proxy_conf["metadata"]["name"])
        service = next(doc for doc in docs if doc.get("kind") == "Service" and doc["metadata"]["name"] == "edition-proxy")
        self.assertEqual(service["spec"]["type"], "NodePort")
        nginx = _run(
            [
                "docker",
                "run",
                "--rm",
                "--add-host",
                "ppap-current-api:127.0.0.1",
                "--add-host",
                "ppap-current-web:127.0.0.1",
                "--add-host",
                "ppap-current-office:127.0.0.1",
                "--add-host",
                "web-base.ppap-test.svc.cluster.local:127.0.0.1",
                "-v",
                f"{TKE / 'edition-proxy.conf'}:/etc/nginx/conf.d/default.conf:ro",
                "nginx:1.27-alpine",
                "nginx",
                "-t",
            ]
        )
        self.assertEqual(nginx.returncode, 0, nginx.stderr)
