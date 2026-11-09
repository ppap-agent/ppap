"""Prepare a manual live test, or summarize its bounded evidence without network IO."""

import base64
import json
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit


def validate_invocation(environment):
    """Each explicit dispatch/rerun is independent; never resume an old operation."""
    if environment.get("PPAP_CHECKER_LIVE_MODE") == "local":
        run_id = environment.get("PPAP_CHECKER_LIVE_LOCAL_ID", "")
        if environment.get("GITHUB_ACTIONS") or environment.get("CI") or not re.fullmatch(r"[a-f0-9]{32}", run_id) or environment.get("COMPOSE_PROJECT_NAME") != "ppap-checker-local-" + run_id:
            raise ValueError("isolated_local_test_required")
    else:
        if environment.get("GITHUB_ACTIONS") != "true" or environment.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
            raise ValueError("manual_github_test_required")
        run_id, attempt = environment.get("GITHUB_RUN_ID", ""), environment.get("GITHUB_RUN_ATTEMPT", "")
        if not all(re.fullmatch(r"[1-9][0-9]*", value) for value in (run_id, attempt)) or environment.get("COMPOSE_PROJECT_NAME") != f"ppap-checker-live-{run_id}-{attempt}":
            raise ValueError("isolated_test_attempt_required")
    if environment.get("PPAP_BUILD_SHA") != environment.get("PPAP_APPROVED_SHA") or not re.fullmatch(r"[a-f0-9]{40}", environment.get("PPAP_BUILD_SHA", "")):
        raise ValueError("reviewed_source_revision_mismatch")
    for name, ceiling in (("REQUESTS", 32), ("INPUT_TOKENS", 200000), ("OUTPUT_TOKENS", 32000)):
        value = int(environment.get("PPAP_PERSONAL_MODEL_MAX_" + name, "0"))
        if not 1 <= value <= ceiling:
            raise ValueError("test_budget_out_of_range")


def prepare(environment, output):
    validate_invocation(environment)
    # Early shape check before dependency installation. The product's canonical
    # endpoint/model validators still own admission and execution security.
    base = environment.get("CI_LLM_BASE_URL", "")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(char.isspace() for char in base):
        raise ValueError("invalid_configured_endpoint")
    if not environment.get("CI_LLM_MODEL"):
        raise ValueError("missing_configured_model")
    names = ("LIVE_PG_BOOTSTRAP_PASSWORD", "LIVE_PG_MIGRATOR_PASSWORD", "LIVE_PG_API_PASSWORD", "LIVE_PG_WORKER_PASSWORD", "LIVE_S3_ACCESS_KEY", "LIVE_S3_SECRET_KEY")
    values = {name: secrets.token_hex(24) for name in names}
    values["LIVE_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    if environment.get("PPAP_CHECKER_LIVE_MODE") == "local":
        # Desktop host bind mounts can ignore Unix permission checks. Keep the
        # actual private boundary on Linux storage; the runner copies only its
        # existing Oracle and Controller receipt through explicit root execs.
        values["LIVE_EVIDENCE_SOURCE"] = "checker-evidence"
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        stream.write("".join(f"{name}={value}\n" for name, value in values.items()))


def summarize(environment):
    """No inference, product calls, retries, or raw service output in the summary."""
    path = Path(environment["RUNNER_TEMP"]) / "checker-live-output/report.json"
    report = json.loads(path.read_text()) if path.is_file() else {}
    proven = report.get("accepted") is True and environment.get("CHECKER_JOB_STATUS") == "success"
    facts = {
        "outcome": "PROVEN" if proven else "NOT_PROVEN",
        "source_sha": environment["GITHUB_SHA"],
        "run_id": environment["GITHUB_RUN_ID"],
        "run_attempt": environment["GITHUB_RUN_ATTEMPT"],
        "stage": report.get("stage", "setup_or_build"),
        "verified_steps": [name for name, passed in report.get("journey", {}).items() if passed is True],
        "requested_model": report.get("requested_model", "unknown"),
        "provider_returned_model": report.get("provider_returned_model") or "unknown (not retained by the production gateway)",
        "usage": report.get("usage", "unknown"),
        "cost": "unknown (no provider billing receipt)",
    }
    if not proven:
        facts["failure_attribution"] = "inconclusive"
        facts["failure"] = report.get("gate_failure", "See the failed workflow step; test completion is not proven.")
    # All values are fixed codes, validated configuration, IDs or numeric usage.
    # Escape markdown delimiters so even unexpected metadata cannot add content.
    payload = json.dumps(facts, indent=2, ensure_ascii=True).replace("`", "\\u0060").replace("<", "\\u003c")
    with Path(environment["GITHUB_STEP_SUMMARY"]).open("a") as stream:
        stream.write("## Checker optimization live test\n\n```json\n" + payload + "\n```\n\n")
        stream.write("This result covers this candidate and synthetic scenario, not all production behavior.\n\n")
        stream.write("A manual rerun or new dispatch starts a separate paid test. It does not recover or erase this attempt; reconcile unknown prior usage with the provider.\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--summary"]:
        summarize(os.environ)
    elif not sys.argv[1:]:
        prepare(os.environ, Path(os.environ["PPAP_CHECKER_LIVE_ENV"]))
        print("Manual test identity and budget checked; ephemeral stack credentials prepared.")
    else:
        raise SystemExit("Unsupported arguments")
