"""Shared browser test setup through public HTTP interfaces."""

import os
import time

from playwright.sync_api import expect

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")
expect.set_options(timeout=20_000)


def route_api_address(page) -> None:
    """Point the Web at the controlled API under the same origin."""
    page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))


def pick_audit_files(page, files) -> None:
    """A disabled input still accepts set_input_files, and the page then ignores the files."""
    picker = page.get_by_label("选择审核文件", exact=True)
    expect(picker).to_be_enabled()
    picker.set_input_files(files)


def wait_until(page, predicate, message: str, timeout: float = 5.0):
    """Bounded poll for state Playwright's expect cannot observe, such as the clipboard or a route callback."""
    deadline = time.monotonic() + timeout
    while not (value := predicate()):
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        page.wait_for_timeout(20)
    return value
