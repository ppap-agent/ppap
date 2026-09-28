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
        rendered = _run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{TKE}:/src:ro",
                "-w",
                "/src",
                KUSTOMIZE,
                "build",
                ".",
            ]
        )
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
        self.assertEqual(config["data"]["PPAP_DEEP_CUSTOMER_ENABLED"], "true")
        images = [
            container["image"]
            for doc in docs
            for container in _containers(doc)
            if container.get("image")
        ]
        self.assertTrue(any(image.startswith("unregistry.xixifusi.online/ppap-api:") for image in images))
        self.assertTrue(any(image.startswith("unregistry.xixifusi.online/ppap-worker:") for image in images))
        self.assertTrue(any(image.startswith("unregistry.xixifusi.online/ppap-deep:") for image in images))
        self.assertTrue(any(image.startswith("unregistry.xixifusi.online/ppap-web:") for image in images))
        self.assertTrue(any("postgres:" in image for image in images))
        self.assertTrue(any(image.startswith("docker.io/pgsty/silo:") for image in images))
        worker = next(
            container
            for doc in docs
            if doc.get("metadata", {}).get("name") == "ppap-current-worker"
            for container in _containers(doc)
            if container.get("name") == "worker"
        )
        command = "\n".join(worker.get("args") or [])
        self.assertIn('until [ -S "$PPAP_DEEP_CONTROLLER_SOCKET" ]', command)
        self.assertIn("warning: deep controller socket unavailable after 60s; starting Worker so Standard remains available", command)
        self.assertIn("exec python -m ppap_worker.main", command)
        self.assertNotIn("deep controller socket missing", command)
        deep = next(
            container
            for doc in docs
            if doc.get("metadata", {}).get("name") == "ppap-current-worker"
            for container in _containers(doc)
            if container.get("name") == "deep"
        )
        self.assertEqual(next(item["value"] for item in deep["env"] if item["name"] == "PPAP_SERVICE"), "deep")
        self.assertTrue(any("emptyDir" in json.dumps(doc.get("spec", {})) for doc in docs if doc.get("metadata", {}).get("name") == "ppap-current-worker"))
        proxy_conf = next(doc for doc in docs if doc.get("kind") == "ConfigMap" and doc["metadata"]["name"] == "edition-proxy")
        self.assertEqual(proxy_conf["data"]["default.conf"].strip(), (TKE / "edition-proxy.conf").read_text().strip())
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
                "web-base.ppap-test.svc.cluster.local:127.0.0.1",
                "-v",
                f"{TKE / 'edition-proxy.conf'}:/etc/nginx/conf.d/default.conf:ro",
                "nginx:1.27-alpine",
                "nginx",
                "-t",
            ]
        )
        self.assertEqual(nginx.returncode, 0, nginx.stderr)
