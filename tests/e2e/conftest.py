"""Browser integration setup through public HTTP interfaces."""

import time
from urllib.parse import urljoin


def isolated_dev_login(page, web: str) -> str:
    config = page.request.get(f"{web}/runtime-config.json")
    if config.status != 200:
        raise AssertionError(f"Web runtime configuration was not read: {config.status}")
    api = urljoin(web + "/", config.json()["api_url"]).rstrip("/")
    response = page.request.post(f"{api}/isolated-dev/sessions", data={"subject": "alice"})
    if response.status != 200:
        raise AssertionError(f"isolated-dev session was not created: {response.status}")
    page.goto(web)
    return api


def pick_audit_files(page, files) -> None:
    """A disabled input still accepts set_input_files, and the page then ignores the files."""
    from playwright.sync_api import expect

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
