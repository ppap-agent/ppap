"""One-shot readiness proof for PPAP's Responses-compatible LLM dependency."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
DEV_DEFAULT_BASE_URL = "https://api.trustedtunnel.app/v1"
DEV_DEFAULT_MODEL = "gpt-5.6-luna"
MAX_RESPONSE_BYTES = 256 * 1024


class NotProven(Exception):
    def __init__(self, code: str, attribution: str = "inconclusive") -> None:
        super().__init__(code)
        self.code = code
        self.attribution = attribution


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise NotProven("redirect_not_allowed")


def responses_url(base_url: str) -> str:
    value = base_url.strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise NotProven("invalid_base_url")
    path = parsed.path.rstrip("/")
    if path.endswith("/responses"):
        raise NotProven("base_url_must_be_api_root")
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/responses", "", ""))


def load_config(
    environ: dict[str, str] | None = None,
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> tuple[str, str, str]:
    env = os.environ if environ is None else environ
    api_key = env.get("LLM_API_KEY", "").strip()
    if not api_key:
        raise NotProven("missing_configuration:LLM_API_KEY")

    resolved_base = (base_url or env.get("LLM_BASE_URL") or DEV_DEFAULT_BASE_URL).strip()
    resolved_model = (model or env.get("LLM_MODEL") or DEV_DEFAULT_MODEL).strip()
    if not resolved_model or len(resolved_model) > 200:
        raise NotProven("invalid_model")
    responses_url(resolved_base)
    return api_key, resolved_base, resolved_model


def validate_response(body: object) -> dict[str, object]:
    if not isinstance(body, dict):
        raise NotProven("invalid_response_envelope")
    if body.get("object") != "response":
        raise NotProven("not_responses_protocol")
    if not isinstance(body.get("id"), str) or not body["id"].strip():
        raise NotProven("missing_response_identity")

    status = body.get("status")
    if status is not None and status != "completed":
        raise NotProven("response_not_completed")
    output = body.get("output")
    if not isinstance(output, list):
        raise NotProven("missing_response_output")

    observed_model = body.get("model")
    if observed_model is not None and not isinstance(observed_model, str):
        raise NotProven("invalid_response_model")

    return {
        "response_identity_present": True,
        "response_completed": status in (None, "completed"),
        "output_items": len(output),
        "observed_model": observed_model,
    }


def probe(
    api_key: str,
    base_url: str,
    model: str,
    *,
    timeout: int = 30,
) -> dict[str, object]:
    if not 1 <= timeout <= 120:
        raise NotProven("invalid_timeout")

    url = responses_url(base_url)
    payload = json.dumps(
        {
            "model": model,
            "input": "Reply with exactly PPAP_READY.",
        }
    ).encode("utf-8")
    request = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            if response.status < 200 or response.status >= 300 or response.geturl() != url:
                raise NotProven("unexpected_http_response")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise NotProven("response_too_large")
        body = json.loads(raw)
    except HTTPError as exc:
        raise NotProven(f"http_status:{exc.code}") from None
    except URLError as exc:
        if isinstance(exc.reason, socket.gaierror):
            raise NotProven("dns_failed", "external_environment") from None
        raise NotProven("endpoint_not_proven") from None
    except (OSError, ValueError, json.JSONDecodeError):
        raise NotProven("response_not_proven") from None

    evidence = validate_response(body)
    evidence.update(
        {
            "protocol": "responses",
            "configured_model": model,
            "endpoint_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
            "http_invocations": 1,
        }
    )
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output-dir", default=str(ROOT / ".artifacts" / "llm"))
    args = parser.parse_args()

    report: dict[str, object] = {
        "gate": "P01",
        "kind": "dependency_readiness",
        "protocol": "responses",
        "run_id": uuid.uuid4().hex,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "outcome": "NOT_PROVEN",
        "attribution": "inconclusive",
        "evidence": {},
    }

    try:
        api_key, base_url, model = load_config(
            base_url=args.base_url or None,
            model=args.model or None,
        )
        report["evidence"] = probe(
            api_key,
            base_url,
            model,
            timeout=args.timeout,
        )
        report["outcome"] = "PROVEN"
        report["attribution"] = None
    except NotProven as exc:
        report["reason"] = exc.code
        report["attribution"] = exc.attribution
    except Exception as exc:
        # Never emit raw provider/client exceptions. They can contain endpoint or credential material.
        report["reason"] = "execution_not_proven:" + type(exc).__name__

    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    destination = Path(args.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / (str(report["run_id"]) + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    print(
        json.dumps(
            {
                "gate": report["gate"],
                "outcome": report["outcome"],
                "reason": report.get("reason"),
                "report": str(path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["outcome"] == "PROVEN" else 1


if __name__ == "__main__":
    sys.exit(main())
