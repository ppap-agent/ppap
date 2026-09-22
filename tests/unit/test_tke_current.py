"""TKE current PPAP manifests stay generic and do not steal the engine host."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TKE = ROOT / "deploy" / "tke"


class TkeCurrentDeployTests(unittest.TestCase):
    def test_current_stack_has_five_workloads_without_node_pins(self):
        text = (TKE / "current.yaml").read_text()
        for name in (
            "ppap-current-postgres",
            "ppap-current-silo",
            "ppap-current-api",
            "ppap-current-worker",
            "ppap-current-web",
        ):
            self.assertIn(f"name: {name}", text)
        self.assertNotIn("nodeSelector", text)
        self.assertNotIn("kubernetes.io/hostname", text)
        self.assertNotIn("ppap.api.xixifusi.online", text)
        self.assertNotIn("/v2", text)
        self.assertIn("unregistry.xixifusi.online/ppap-api:", text)
        self.assertIn("unregistry.xixifusi.online/ppap-worker:", text)
        self.assertIn("unregistry.xixifusi.online/ppap-web:", text)
        self.assertIn("docker.m.daocloud.io/library/postgres:", text)
        self.assertIn("docker.io/pgsty/silo:", text)
        self.assertIn("storageClassName: cbs", text)
        self.assertIn('PPAP_USAGE_CHARGE_ENABLED: "false"', text)
        self.assertIn("namespace: ppap-test", text)

    def test_edition_proxy_uses_in_cluster_current_services(self):
        conf = (TKE / "edition-proxy.conf").read_text()
        yaml = (TKE / "edition-proxy.yaml").read_text()
        for line in (
            "proxy_pass http://ppap-current-api:8080/enter-current;",
            "proxy_pass http://ppap-current-api:8080/enter-legacy;",
            "proxy_pass http://ppap-current-api:8080/;",
            "proxy_pass http://ppap-current-web:4173;",
            "web-base.ppap-test.svc.cluster.local",
        ):
            self.assertIn(line, conf)
            self.assertIn(line, yaml)
        self.assertNotIn("ppap-agent.xixifusi.online", conf)
        self.assertNotIn("ppap-agent.xixifusi.online", yaml)
        self.assertNotIn("ppap.api.xixifusi.online", conf)
        self.assertNotIn("/v2", conf)
        self.assertIn("type: NodePort", yaml)
