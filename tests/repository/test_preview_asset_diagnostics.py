"""Failure-only browser diagnostics never keep queries, other origins or business requests."""

import json


class _Request:
    """Playwright requests are hashable identities; namespaces are not."""

    def __init__(self, url):
        self.url, self.failure, self.status = url, f"net::ERR_FAILED at {url}", 500


class _Page:
    def __init__(self):
        self.handlers = {}

    def on(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    def emit(self, name, event):
        for handler in self.handlers.get(name, []):
            handler(event)


def test_asset_paths_exclude_queries_cross_origin_and_business_requests(observe_browser_assets):
    page = _Page()
    snapshot = observe_browser_assets(page, "http://127.0.0.1:4185")
    for url in (
        "http://127.0.0.1:4185/src/main.tsx?input=private#fragment",
        "https://other.example/src/main.tsx?secret=hidden",
        "http://127.0.0.1:4185/api/rules?token=hidden",
        "http://127.0.0.1:4185/files/private",
    ):
        event = _Request(url)
        page.emit("request", event)
        page.emit("response", event)
        page.emit("requestfailed", event)
    result = snapshot()
    assert result["first_asset_failure"] == {"asset": "/src/main.tsx", "status": 500}
    assert result["events"] == [{"asset": "/src/main.tsx", "status": 500}, {"failed_asset": "/src/main.tsx", "error_code": "net::ERR_FAILED"}]
    assert result["assets"]["started"] == result["assets"]["finished"] == 1
    serialized = json.dumps(result)
    assert not any(secret in serialized for secret in ("private", "hidden", "other.example", "/api/", "/files/", "?", "#"))
