#!/usr/bin/env python3
"""Isolated-dev static web with baseline customer paths."""
from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PAGE_ALIASES = {
    "/audit-files": "/audit-files.html",
    "/addon": "/addon.html",
}
DIRECTORY_REDIRECTS = {
    "/audit-files/": "/audit-files",
    "/addon/": "/addon",
}


def aliased_path(path: str) -> str:
    parsed = urlparse(path)
    route = parsed.path or "/"
    if len(route) > 1 and route.endswith("/"):
        route = route.rstrip("/")
    return PAGE_ALIASES.get(route, parsed.path or "/")


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _redirect_directory_alias(self) -> bool:
        parsed = urlparse(self.path)
        dest = DIRECTORY_REDIRECTS.get(parsed.path or "/")
        if not dest:
            return False
        if parsed.query:
            dest = dest + "?" + parsed.query
        self.send_response(301)
        self.send_header("Location", dest)
        self.end_headers()
        return True

    def do_GET(self) -> None:
        if self._redirect_directory_alias():
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if self._redirect_directory_alias():
            return
        super().do_HEAD()

    def translate_path(self, path: str) -> str:
        return super().translate_path(aliased_path(path))


def write_runtime_config(directory: str | Path | None = None) -> None:
    api = os.environ.get("PPAP_PUBLIC_API_URL", "").strip()
    if not api:
        return
    dest = Path(directory or os.getcwd()) / "runtime-config.json"
    dest.write_text(json.dumps({"api_url": api.rstrip("/")}) + "\n")


def serve(bind: str | None = None, port: int | None = None) -> None:
    write_runtime_config()
    host = bind if bind is not None else os.environ.get("PPAP_WEB_BIND", "127.0.0.1")
    listen = int(port if port is not None else os.environ.get("PPAP_WEB_PORT", "4173"))
    ThreadingHTTPServer((host, listen), Handler).serve_forever()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
