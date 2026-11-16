"""Customer path aliases are nginx behavior, checked by nginx itself."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONF = ROOT / "deploy" / "web" / "nginx.conf"
IMAGE = "nginx:1.27-alpine"
CURL = "curlimages/curl:8.10.1"


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, capture_output=True, text=True, **kwargs)


class WebAliasTests(unittest.TestCase):
    def test_nginx_accepts_the_config_and_serves_customer_aliases(self):
        name = "ppap-nginx-contract-" + uuid.uuid4().hex[:12]
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            html = Path(tmp)
            html.chmod(0o755)
            (html / "index.html").write_text("native-audit")
            (html / "addon.html").write_text("addon-page")
            (html / "pdf.worker.mjs").write_text("export const worker = true;")
            (html / "runtime-config.json").write_text('{"api_url": "/api"}')
            (html / "assets").mkdir()
            (html / "assets").chmod(0o755)
            (html / "assets" / "addon-1.js").write_text("addon-script")
            started = _run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "-v",
                    f"{CONF}:/etc/nginx/conf.d/default.conf:ro",
                    "-v",
                    f"{html}:/usr/share/nginx/html:ro",
                    IMAGE,
                ]
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            try:
                syntax = _run(["docker", "exec", name, "nginx", "-t"])
                self.assertEqual(syntax.returncode, 0, syntax.stderr)
                self.assertEqual(_fetch(name, "/addon"), (200, "addon-page"))
                for path in ("/", "/?round=frozen-round", "/audit?task=frozen-task&report=frozen-file", "/audit?view=archive&packageRequest=p"):
                    self.assertEqual(_fetch(name, path), (200, "native-audit"))
                # Retired aliases have no current generator.
                for path in ("/audit-files", "/rules", "/dev", "/__audit-native", "/audit.html", "/workbench"):
                    self.assertEqual(_fetch(name, path)[0], 404)
                for path in ("/files", "/requirements", "/templates", "/usage", "/tasks/new", "/tasks/frozen-task?file=frozen-file&clause=c1"):
                    self.assertEqual(_fetch(name, path), (200, "native-audit"))
                    address, _, query = path.partition("?")
                    self.assertEqual(_fetch(name, address + "/" + ("?" + query if query else "")), (200, "native-audit"))
                status, body, headers = _request(name, "/pdf.worker.mjs")
                self.assertEqual((status, body), (200, "export const worker = true;"))
                self.assertEqual(headers.get("content-type"), "application/javascript")
                self.assertEqual(_fetch(name, "/missing-worker.mjs")[0], 404)
                self.assertEqual(_redirect(name, "/audit/?task=frozen-task"), "/audit?task=frozen-task")
                self.assertEqual(_redirect(name, "/addon/"), "/addon")
                # Inside Feishu no edition cookie is sent, so everything the add-on page loads is reachable under /addon.
                self.assertEqual(_fetch(name, "/addon/assets/addon-1.js"), (200, "addon-script"))
                self.assertEqual(_fetch(name, "/addon/assets/missing.js")[0], 404)
                status, body, headers = _request(name, "/addon/runtime-config.json")
                self.assertEqual((status, body), (200, '{"api_url": "/api"}'))
                self.assertEqual(headers.get("cache-control"), "no-store")
                self.assertEqual(_fetch(name, "/missing-customer-path")[0], 404)
            finally:
                _run(["docker", "rm", "-f", name])


def _request(name: str, path: str) -> tuple[int, str, dict[str, str]]:
    completed = _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            f"container:{name}",
            CURL,
            "-sS",
            "--max-redirs",
            "0",
            "-D",
            "-",
            "-o",
            "-",
            f"http://127.0.0.1:4173{path}",
        ]
    )
    header, _, body = completed.stdout.partition("\n\n")
    status = 0
    headers = {}
    for line in header.splitlines():
        if line.startswith("HTTP/"):
            status = int(line.split()[1])
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    if status == 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return status, body, headers


def _fetch(name: str, path: str) -> tuple[int, str]:
    status, body, _headers = _request(name, path)
    return status, body


def _redirect(name: str, path: str) -> str:
    status, _body, headers = _request(name, path)
    location = headers.get("location", "")
    if status != 301 or not location:
        raise AssertionError((status, location))
    return location
