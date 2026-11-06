"""Validate explicit non-secret approval and create ephemeral isolated credentials.

Never print any generated credential or secret endpoint/model value. This is
setup only: no API, provider request, container start or workflow dispatch.
"""

import base64
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit


def prepare(environment, output):
    if environment.get("PPAP_CHECKER_LIVE_APPROVED") != "true" or environment.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("Explicit approval and a first workflow attempt are required")
    if environment.get("PPAP_BUILD_SHA") != environment.get("PPAP_APPROVED_SHA") or not re.fullmatch(r"[a-f0-9]{40}", environment.get("PPAP_BUILD_SHA", "")):
        raise ValueError("Approved source revision does not match")
    base = environment.get("PPAP_APPROVED_BASE_URL", "").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(char.isspace() for char in base):
        raise ValueError("Approval must name a clean HTTPS base URL")
    if base != environment.get("CI_LLM_BASE_URL", "").rstrip("/") or not environment.get("CI_LLM_MODEL") or environment.get("PPAP_APPROVED_MODEL") != environment.get("CI_LLM_MODEL"):
        raise ValueError("Configured recipient/model does not match approval")
    for name, ceiling in (("REQUESTS", 32), ("INPUT_TOKENS", 200000), ("OUTPUT_TOKENS", 32000)):
        value = int(environment.get("PPAP_PERSONAL_MODEL_MAX_" + name, "0"))
        if not 1 <= value <= ceiling:
            raise ValueError("Budget outside this acceptance's maximum")
    names = ("LIVE_PG_BOOTSTRAP_PASSWORD", "LIVE_PG_MIGRATOR_PASSWORD", "LIVE_PG_API_PASSWORD", "LIVE_PG_WORKER_PASSWORD", "LIVE_S3_ACCESS_KEY", "LIVE_S3_SECRET_KEY")
    values = {name: secrets.token_hex(24) for name in names}
    values["LIVE_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        stream.write("".join(f"{name}={value}\n" for name, value in values.items()))


if __name__ == "__main__":
    prepare(os.environ, Path(os.environ["PPAP_CHECKER_LIVE_ENV"]))
    print("Approved recipient, model and budget match; ephemeral stack credentials prepared.")
