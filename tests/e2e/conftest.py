"""Browser integration setup through public HTTP interfaces."""

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
