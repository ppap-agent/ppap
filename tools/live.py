"""Bounded live checks. A prerequisite result is never a product acceptance result."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import time
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


class NotProven(Exception):
    def __init__(self, code: str, attribution: str = "inconclusive") -> None:
        super().__init__(code)
        self.code, self.attribution = code, attribution


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise NotProven("missing_configuration:" + name)
    return value


def origin(url: str) -> tuple[str, str, int]:
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise NotProven("invalid_endpoint")
    return p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80)


def same_origin(url: str, base: str) -> None:
    if origin(url) != origin(base):
        raise NotProven("unexpected_product_origin")


def check_findings(actual: list[dict], oracle: dict) -> None:
    expected = oracle.get("findings")
    if not isinstance(expected, dict) or not expected or not actual:
        raise NotProven("missing_rule_coverage")
    ids = [row.get("id") for row in actual]
    if len(set(ids)) != len(ids) or set(ids) != set(expected):
        raise NotProven("rule_coverage_mismatch")
    for row in actual:
        target = expected[row["id"]]
        if row.get("status") not in ("PASS", "NG", "ERR"):
            raise NotProven("invalid_machine_status")
        if row["status"] != target.get("status"):
            raise NotProven("oracle_result_mismatch")
        if "locator" not in target:
            raise NotProven("oracle_evidence_unspecified")
        if target["locator"] is None:
            if row.get("evidence_state") != "missing":
                raise NotProven("missing_evidence_not_disclosed")
        else:
            if row.get("evidence_state") != "available":
                raise NotProven("expected_evidence_unavailable")
            if row.get("locator") != target["locator"]:
                raise NotProven("wrong_evidence_location")
            if row.get("source_sha256") != oracle.get("source_sha256"):
                raise NotProven("evidence_from_wrong_source")


def normalized_findings(rows: list[dict]) -> list[dict]:
    """List order is presentation, not a change to a rule result."""
    return sorted(rows, key=lambda row: row["id"])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise NotProven("probe_redirect_not_allowed")


def probe(url: str, expected: str) -> dict:
    origin(url)
    try:
        with build_opener(NoRedirect).open(Request(url, headers={"Accept": "application/json"}), timeout=15) as response:
            if response.status != 200 or response.geturl() != url:
                raise NotProven("unexpected_probe_response")
            raw = response.read(65537)
        if len(raw) > 65536:
            raise NotProven("probe_response_too_large")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise NotProven("invalid_probe_contract")
        observed = body.get("git_sha") or body.get("sha") or body.get("version")
        if not expected or observed != expected:
            raise NotProven("build_identity_not_verified")
        return {"build_matches": True}
    except URLError as exc:
        if isinstance(exc.reason, socket.gaierror):
            raise NotProven("dns_failed", "external_environment") from None
        raise NotProven("endpoint_not_proven") from None
    except (ValueError, OSError):
        raise NotProven("probe_not_proven") from None


def ui_check(mode: str, evidence: dict) -> None:
    # Driver hooks are replaceable representation mappings, not product policy.
    base = required("PPAP_WEB_URL")
    origin(base)
    environment = required("PPAP_ENVIRONMENT")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", environment):
        raise NotProven("invalid_environment_label")
    evidence["environment"] = environment
    evidence["origin_sha256"] = hashlib.sha256(repr(origin(base)).encode()).hexdigest()
    build = required("PPAP_BUILD_SHA")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", build):
        raise NotProven("invalid_build_identity")
    state = Path(required("PPAP_AUTH_STATE"))
    oracle_path = Path(required("PPAP_ORACLE"))
    oracle = json.loads(oracle_path.read_text())
    if not oracle.get("authority") or str(oracle["authority"]).startswith("EXAMPLE_ONLY") or not re.fullmatch(r"[0-9a-f]{64}", oracle.get("source_sha256", "")):
        raise NotProven("unreviewed_oracle")
    evidence["oracle_sha256"] = digest(oracle_path)
    timeout = int(os.getenv("PPAP_LIVE_TIMEOUT_SECONDS", "300"))
    if not 1 <= timeout <= 1800:
        raise NotProven("invalid_wait_budget")

    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        contexts = []

        def open_page(url: str, auth: Path = state):
            same_origin(url, base)
            ctx = browser.new_context(storage_state=str(auth), accept_downloads=True)
            contexts.append(ctx)
            page = ctx.new_page()
            page.set_default_timeout(timeout * 1000)
            page.goto(url, wait_until="domcontentloaded")
            same_origin(page.url, base)
            expect(page.get_by_test_id("build-revision")).to_have_text(build)
            evidence["build_matches"] = True
            return page

        def text(page, hook: str) -> str:
            value = page.get_by_test_id(hook).inner_text().strip()
            if not value:
                raise NotProven("missing_visible_fact:" + hook)
            return value

        def download_hash(page) -> str:
            with page.expect_download() as event:
                page.get_by_test_id("download-original").click()
            download = event.value
            if download.failure():
                raise NotProven("original_download_failed")
            return digest(Path(download.path()))

        def findings(page) -> list[dict]:
            expect(page.get_by_test_id("execution-state")).to_have_text("completed")
            expect(page.get_by_test_id("rule-version")).to_have_text(oracle["rule_version_label"])
            rows = page.get_by_test_id("finding")
            data = []
            for i in range(rows.count()):
                row = rows.nth(i)
                entry = {
                    "id": row.get_by_test_id("finding-id").inner_text().strip(),
                    "status": row.get_by_test_id("finding-status").inner_text().strip(),
                    "evidence_state": row.get_by_test_id("evidence-state").inner_text().strip(),
                }
                if entry["evidence_state"] == "available":
                    row.get_by_test_id("view-evidence").click()
                    expect(page.get_by_test_id("evidence-panel")).to_be_visible()
                    entry["locator"] = text(page, "evidence-locator")
                    entry["source_sha256"] = text(page, "evidence-source-sha256")
                if not row.get_by_test_id("next-action").inner_text().strip():
                    raise NotProven("next_action_absent")
                data.append(entry)
            check_findings(data, oracle)
            return normalized_findings(data)

        try:
            if mode == "audit":
                sample = Path(required("PPAP_SAMPLE"))
                if digest(sample) != oracle["source_sha256"]:
                    raise NotProven("input_does_not_match_oracle")
                page = open_page(base)
                page.get_by_test_id("source-upload").set_input_files(str(sample))
                page.get_by_test_id("prepare-audit").click()
                page.get_by_test_id("rule-selector").select_option(label=oracle["rule_version_label"])
                evidence["submit_invocations"] = 1  # A failure after this point must not re-submit.
                page.get_by_test_id("submit-audit").click()
                expect(page.get_by_test_id("round-id")).not_to_have_text("")
                round_id = text(page, "round-id")
                url = page.url
                same_origin(url, base)
                page.context.close()
                page = open_page(url)  # New browser storage context, not a component remount.
                expect(page.get_by_test_id("round-id")).to_have_text(round_id)
                result = findings(page)
                if download_hash(page) != oracle["source_sha256"]:
                    raise NotProven("wrong_historical_bytes")
                evidence.update(returned_to_same_round=True, original_matches=True, finding_count=len(result))
            else:
                url = required("PPAP_EXISTING_ROUND_URL")
                page = open_page(url)
                before = findings(page)
                if download_hash(page) != oracle["source_sha256"]:
                    raise NotProven("baseline_original_mismatch")
                if mode == "isolation":
                    foreign = Path(required("PPAP_OTHER_TENANT_STATE"))
                    other = open_page(url, foreign)
                    expect(other.get_by_test_id("access-denied")).to_be_visible()
                    expect(other.get_by_test_id("finding")).to_have_count(0)
                    expect(other.get_by_test_id("download-original")).to_have_count(0)
                    evidence["foreign_ui_denied"] = True
                elif mode == "history":
                    sample = Path(required("PPAP_SAMPLE"))
                    alternate = Path(required("PPAP_REPLACEMENT_SAMPLE"))
                    if sample.name != alternate.name or digest(alternate) == oracle["source_sha256"]:
                        raise NotProven("replacement_fixture_invalid")
                    upload = open_page(base)
                    upload.get_by_test_id("source-upload").set_input_files(str(alternate))
                    upload.get_by_test_id("prepare-audit").click()
                    expect(upload.get_by_test_id("document-version-id")).not_to_have_text("")
                    page = open_page(url)
                    if download_hash(page) != oracle["source_sha256"] or findings(page) != before:
                        raise NotProven("history_changed_after_new_upload")
                    evidence["historical_version_preserved"] = True
                elif mode == "review":
                    note = "隔离验收复核-" + uuid.uuid4().hex
                    page.get_by_test_id("review-reason").fill(note)
                    evidence["review_invocations"] = 1
                    page.get_by_test_id("save-review").click()
                    expect(page.get_by_test_id("review-entry").last).to_contain_text(note)
                    page = open_page(url)
                    expect(page.get_by_test_id("review-entry").last).to_contain_text(note)
                    if findings(page) != before or download_hash(page) != oracle["source_sha256"]:
                        raise NotProven("review_mutated_machine_fact")
                    evidence.update(review_persisted=True, machine_fact_unchanged=True)
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["probe", "audit", "history", "review", "isolation"])
    parser.add_argument("--url", default="")
    parser.add_argument("--expected-build", default="")
    parser.add_argument("--output-dir", default=str(ROOT / ".artifacts" / "live"))
    args = parser.parse_args()
    gates = {"audit": "G01", "isolation": "G02-ui", "history": "G03", "review": "G05-review", "probe": "P00"}
    report = {
        "gate": gates[args.mode], "kind": "prerequisite" if args.mode == "probe" else "product_child_proof",
        "run_id": uuid.uuid4().hex, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidate_sha": os.getenv("PPAP_BUILD_SHA", "") if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", os.getenv("PPAP_BUILD_SHA", "")) else None, "gate_sha256": digest(Path(__file__)),
        "story_sha256": digest(ROOT / "docs" / "STORIES.md"),
        "outcome": "NOT_PROVEN", "attribution": "inconclusive", "evidence": {},
        "cleanup": "not_started",
    }
    try:
        if args.mode == "probe":
            url = args.url or required("PPAP_VERSION_URL")
            origin(url)
            if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", args.expected_build):
                raise NotProven("invalid_expected_build")
            report["candidate_sha"] = args.expected_build
            report["evidence"]["endpoint_sha256"] = hashlib.sha256(url.encode()).hexdigest()
            report["evidence"]["http_invocations"] = 1
            report["evidence"].update(probe(url, args.expected_build))
        else:
            ui_check(args.mode, report["evidence"])
        report.update(outcome="PROVEN", attribution=None, cleanup="test_records_retained" if args.mode != "probe" else "not_applicable")
    except NotProven as exc:
        report.update(reason=exc.code, attribution=exc.attribution)
    except Exception as exc:
        # Never emit raw Playwright/request/config exceptions: they can contain tokens or content.
        report["reason"] = "execution_not_proven:" + type(exc).__name__
    if report["evidence"].get("submit_invocations") or report["evidence"].get("review_invocations"):
        report["cleanup"] = "test_records_may_exist"
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    destination = Path(args.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / (report["run_id"] + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    print(json.dumps({"gate": report["gate"], "outcome": report["outcome"], "reason": report.get("reason"), "report": str(path)}, ensure_ascii=False))
    return 0 if report["outcome"] == "PROVEN" else 1


if __name__ == "__main__":
    sys.exit(main())
