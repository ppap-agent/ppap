"""Self-hosted InsForge substrate for PPAP API/Worker. No live InsForge required."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from tests.unit.test_dev_tooling import (
    SILO_IMAGE,
    _compose_services,
    _interpolated_environment,
    _parse_dotenv,
)

INSFORGE = ROOT / "deploy" / "insforge"


class InsForgeRuntimeTests(unittest.TestCase):
    def test_insforge_release_is_pinned(self):
        self.assertEqual((INSFORGE / "VERSION").read_text().strip(), "v2.3.2")
        readme = (INSFORGE / "README.md").read_text()
        self.assertIn("v2.3.2", readme)
        self.assertIn("docker.sock", readme)
        self.assertIn("host-root-equivalent", readme)
        self.assertIn("Custom Compute", readme)
        self.assertIn("Edge Function", readme)
        self.assertIn("Do not import `ppap-host` schemas", readme)

    def test_overlay_joins_insforge_network_without_bundled_postgres(self):
        compose = (INSFORGE / "compose.yaml").read_text()
        self.assertIn("insforge-network", compose)
        self.assertIn("external: true", compose)
        self.assertNotIn("\n  postgres:\n", compose)
        self.assertNotIn("ghcr.io/insforge/postgres", compose)
        self.assertNotIn("denoland/deno", compose)
        self.assertNotIn("postgrest/postgrest", compose)
        self.assertNotIn("edge function", compose.lower())
        self.assertIn("bootstrap.sql", compose)
        self.assertIn("apply.sql", compose)
        self.assertNotIn("python tools/", compose)
        services = _compose_services(compose)
        self.assertEqual(set(services), {"silo", "init-db", "init-bucket", "api", "worker", "web"})
        self.assertNotIn("depends_on:", services["init-db"])
        self.assertNotIn("env_file:", services["api"])
        self.assertNotIn("env_file:", services["worker"])
        self.assertIn("PPAP_PG_HOST: ${PPAP_PG_HOST}", services["api"])
        self.assertIn("PPAP_PG_HOST: ${PPAP_PG_HOST}", services["worker"])
        self.assertNotIn("PPAP_PG_HOST: postgres", services["api"])
        self.assertIn("FEISHU_APP_ID", services["api"])
        self.assertNotIn("FEISHU_APP_ID", services["worker"])
        self.assertIn("PPAP_PG_API_PASSWORD", services["api"])
        self.assertNotIn("PPAP_PG_BOOTSTRAP_PASSWORD", services["api"])
        self.assertNotIn("PPAP_PG_MIGRATOR_PASSWORD", services["api"])
        self.assertNotIn("PPAP_PG_WORKER_PASSWORD", services["api"])
        self.assertIn("PPAP_PG_WORKER_PASSWORD", services["worker"])
        self.assertNotIn("PPAP_PG_API_PASSWORD", services["worker"])
        self.assertNotIn("PADDLE_TOKEN", services["api"])
        self.assertIn("PPAP_PG_BOOTSTRAP_PASSWORD", services["init-db"])
        self.assertIn("PGCONNECT_TIMEOUT", services["init-db"])
        self.assertIn("image: postgres:16.10-bookworm", services["init-db"])
        self.assertIn("minio/health/live", services["silo"])
        self.assertIn("condition: service_healthy", services["init-bucket"])

    def test_selected_env_reaches_insforge_postgres_host(self):
        compose = (INSFORGE / "compose.yaml").read_text()
        services = _compose_services(compose)
        selected = {
            "PPAP_PG_HOST": "postgres",
            "PPAP_PG_PORT": "5432",
            "PPAP_PG_DATABASE": "ppap",
            "PPAP_PG_API_USER": "ppap_api",
            "PPAP_PG_API_PASSWORD": "api-secret",
            "PPAP_PG_WORKER_USER": "ppap_worker",
            "PPAP_PG_WORKER_PASSWORD": "worker-secret",
            "PPAP_S3_ENDPOINT": "http://silo:9000",
            "PPAP_S3_REGION": "us-east-1",
            "PPAP_S3_BUCKET": "ppap-objects",
            "PPAP_S3_ACCESS_KEY": "objects",
            "PPAP_S3_SECRET_KEY": "objects-secret",
            "PPAP_WEB_URL": "https://app.example",
            "PPAP_PUBLIC_API_URL": "https://api.example",
            "LLM_BASE_URL": "http://insforge:7130/v1",
            "LLM_MODEL": "gateway-model",
            "FEISHU_APP_ID": "cli_prod",
            "FEISHU_APP_SECRET": "feishu-secret",
        }
        api = _interpolated_environment(services["api"], selected)
        worker = _interpolated_environment(services["worker"], selected)
        init_db = _interpolated_environment(services["init-db"], selected)
        self.assertEqual(api["PPAP_PG_HOST"], "postgres")
        self.assertEqual(worker["PPAP_PG_HOST"], "postgres")
        self.assertEqual(api["PPAP_PG_API_PASSWORD"], "api-secret")
        self.assertEqual(worker["PPAP_PG_WORKER_PASSWORD"], "worker-secret")
        self.assertEqual(api["FEISHU_APP_ID"], "cli_prod")
        self.assertEqual(init_db["PGHOST"], "postgres")
        self.assertNotIn("PPAP_PG_BOOTSTRAP_PASSWORD", api)
        self.assertNotIn("PPAP_PG_MIGRATOR_PASSWORD", api)
        self.assertNotIn("PPAP_PG_WORKER_PASSWORD", api)
        self.assertNotIn("PPAP_PG_API_PASSWORD", worker)
        catalog = _parse_dotenv(INSFORGE / "env.example")
        self.assertEqual(catalog["PPAP_PG_HOST"], "postgres")
        self.assertEqual(catalog["PPAP_PG_BOOTSTRAP_USER"], "postgres")
        self.assertEqual(catalog["PPAP_PG_DATABASE"], "ppap")
        self.assertEqual(catalog["PPAP_S3_ENDPOINT"], "http://silo:9000")
        self.assertNotIn("isolated-dev", catalog["PPAP_S3_ENDPOINT"])
        self.assertIn(SILO_IMAGE, compose)
        self.assertIn('command: ["server", "/data", "--console-address", ":9001"]', compose)
        self.assertIn("MINIO_ROOT_USER", compose)
        self.assertNotIn("minio/minio:", compose)
        self.assertNotIn("ppap-minio:", compose)
        self.assertNotIn("distroless", compose)

    def test_compute_specs_are_custom_compute_not_edge_functions(self):
        api = json.loads((INSFORGE / "compute" / "api.json").read_text())
        worker = json.loads((INSFORGE / "compute" / "worker.json").read_text())
        self.assertEqual(api["kind"], "custom-compute")
        self.assertEqual(worker["kind"], "custom-compute")
        self.assertEqual(api["ingress"], "port")
        self.assertEqual(worker["ingress"], "none")
        self.assertEqual(api["port"], 8080)
        self.assertNotIn("port", worker)
        self.assertIn("ppap-api:", api["image"])
        self.assertIn("ppap-worker:", worker["image"])
        self.assertEqual(api["environment"]["PPAP_SERVICE"], "api")
        self.assertEqual(worker["environment"]["PPAP_SERVICE"], "worker")
        self.assertNotIn("PPAP_PG_BOOTSTRAP_PASSWORD", api["environment"])
        self.assertNotIn("PPAP_PG_MIGRATOR_PASSWORD", api["environment"])
        self.assertNotIn("PPAP_PG_WORKER_PASSWORD", api["environment"])
        self.assertIn("FEISHU_APP_SECRET", api["environment"])
        self.assertNotIn("FEISHU_APP_SECRET", worker["environment"])
        self.assertNotIn("PPAP_PG_API_PASSWORD", worker["environment"])
        self.assertIn("PADDLE_TOKEN", worker["environment"])
        self.assertNotIn("PADDLE_TOKEN", api["environment"])
        for spec in (api, worker):
            blob = json.dumps(spec)
            self.assertNotIn("edge-function", blob)
            self.assertNotIn("deno", blob.lower())

    def test_object_store_is_durable_and_insforge_down_keeps_the_volume(self):
        compose = (INSFORGE / "compose.yaml").read_text()
        services = _compose_services(compose)
        self.assertIn("- silo-data:/data", services["silo"])
        self.assertIn("name: ppap-silo-data", compose)
        self.assertNotIn("bind:", services["silo"])
        readme = (INSFORGE / "README.md").read_text()
        self.assertIn("compose.yaml down", readme)
        self.assertIn("without `-v`", readme)
        verify = (ROOT / "deploy" / "README.md").read_text()
        self.assertIn("down -v", verify)


if __name__ == "__main__":
    unittest.main()
