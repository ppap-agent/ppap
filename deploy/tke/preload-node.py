"""Short-lived node helper. No application credentials or production jobs."""

from __future__ import annotations

import hashlib
import hmac
import http.server
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path("/transfer")
TOKEN_FILE = Path("/credentials/transfer-token")
COMPONENTS = {"api", "worker", "deep", "web"}


def component(value: str) -> str:
    if value not in COMPONENTS:
        raise ValueError("unexpected image component")
    return value


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args: list[str], **kwargs) -> str:
    result = subprocess.run(args, capture_output=True, timeout=1800, **kwargs)
    if result.returncode:
        # Registry errors can contain temporary signed URLs. Do not emit them.
        raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode})")
    return result.stdout.decode()


def import_image(path: Path, ref: str) -> dict:
    repository = ref.split("@", 1)[0].split(":", 1)[0]
    with path.open("rb") as stream:
        run(["chroot", "/host", "/usr/local/bin/ctr", "--namespace", "k8s.io", "images", "import", "--digests", "--base-name", repository, "-"], stdin=stream)
    run(["chroot", "/host", "/usr/local/bin/ctr", "--namespace", "k8s.io", "images", "label", ref, "io.cri-containerd.image=managed"])
    return inspect_image(ref)


def inspect_image(ref: str) -> dict:
    repository = ref.split("@", 1)[0]
    for _ in range(30):
        result = subprocess.run(["chroot", "/host", "/usr/local/bin/crictl", "--runtime-endpoint", "unix:///run/containerd/containerd.sock", "inspecti", ref], capture_output=True, timeout=15)
        if result.returncode == 0:
            status = json.loads(result.stdout)["status"]
            expected = repository + "@" + ref.split("@", 1)[1]
            if expected in status.get("repoDigests", []):
                return {"image": ref, "config_digest": status["id"], "registered": True}
        time.sleep(1)
    raise RuntimeError("imported image was not registered with the expected digest")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("internal image transfer cannot redirect")


def receive(name: str, ref: str, address: str, expected: str) -> dict:
    component(name)
    if not ipaddress.ip_address(address).is_private:
        raise ValueError("image sender must have a private address")
    token = TOKEN_FILE.read_text().strip()
    request = urllib.request.Request(f"http://{address}:18081/{name}.tar", headers={"Authorization": f"Bearer {token}"})
    path = ROOT / f"{name}.tar"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=60) as response, path.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if checksum(path) != expected:
            raise ValueError("image archive checksum mismatch")
        return import_image(path, ref)
    finally:
        path.unlink(missing_ok=True)


def serve() -> None:
    token = TOKEN_FILE.read_text().strip()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                self.send_error(403)
                return
            if not re.fullmatch(r"/(api|worker|deep|web)\.tar", self.path):
                self.send_error(404)
                return
            path = ROOT / self.path.removeprefix("/")
            if not path.is_file():
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            with path.open("rb") as source:
                shutil.copyfileobj(source, self.wfile, length=1024 * 1024)

    http.server.ThreadingHTTPServer(("0.0.0.0", 18081), Handler).serve_forever()


def export(name: str, ref: str) -> dict:
    component(name)
    path = ROOT / f"{name}.tar"
    partial = path.with_suffix(".partial")
    try:
        status = inspect_image(ref)
        with partial.open("wb") as output:
            result = subprocess.run(
                ["chroot", "/host", "/usr/local/bin/ctr", "--namespace", "k8s.io", "images", "export", "--platform", "linux/amd64", "-", ref], stdout=output, stderr=subprocess.PIPE, timeout=1800
            )
        if result.returncode:
            raise RuntimeError("native image export failed")
        digest = checksum(partial)
        partial.rename(path)
        return {**status, "archive_sha256": digest, "archive_bytes": path.stat().st_size}
    finally:
        partial.unlink(missing_ok=True)


def main() -> None:
    action, *args = sys.argv[1:]
    if action == "serve":
        serve()
    elif action == "export":
        print(json.dumps(export(*args)), flush=True)
    elif action == "receive":
        print(json.dumps(receive(*args)), flush=True)
    elif action == "clean":
        (ROOT / f"{component(args[0])}.tar").unlink(missing_ok=True)
    else:
        raise ValueError("unexpected preload action")


if __name__ == "__main__":
    main()
