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


REQUIRED_PROMISED_FORMATS = ("xlsx", "pdf", "docx", "doc", "scanned", "cross_file")
DIFFERENCE_KINDS = (
    "equivalent",
    "conversion_loss",
    "rule_logic_defect",
    "approved_format_difference",
)
CORPUS_CATALOG = ROOT / "tests" / "live" / "corpus" / "catalog.json"
PUBLIC_FIXTURE_PREFIX = "tests/live/corpus/public/"


def human_oracle_authority(authority: str) -> bool:
    text = (authority or "").strip()
    if not text or text.startswith("EXAMPLE_ONLY"):
        return False
    lowered = text.lower()
    if any(token in lowered for token in ("legacy_engine", "engine_output", "generated_oracle")):
        return False
    if lowered in {"auto", "generated", "not_proven"}:
        return False
    if text in {"NOT_A_BUSINESS_ORACLE", "NOT_PROVEN"}:
        return False
    return True


def classify_difference(reference: dict, observed: dict) -> str:
    """Label a comparison. Never writes or updates an oracle."""
    ref_fields = dict(reference.get("key_fields") or {})
    obs_fields = dict(observed.get("key_fields") or {})
    field_mismatches = [key for key in sorted(set(ref_fields) | set(obs_fields)) if ref_fields.get(key) != obs_fields.get(key)]
    ref_status = list(reference.get("statuses") or [])
    obs_status = list(observed.get("statuses") or [])
    status_mismatches = tuple(index + 1 for index, (left, right) in enumerate(zip(ref_status, obs_status)) if left != right)
    coverage_changed = len(ref_status) != len(obs_status)
    diagnostics = list(observed.get("conversion_diagnostics") or [])
    allowed = set(observed.get("allowed_format_rule_differences") or reference.get("allowed_format_rule_differences") or [])
    if not field_mismatches and not status_mismatches and not coverage_changed:
        return "equivalent"
    if diagnostics or field_mismatches:
        return "conversion_loss"
    if not coverage_changed and status_mismatches and set(status_mismatches) <= allowed:
        return "approved_format_difference"
    return "rule_logic_defect"


def public_fixture_format_matches(path: Path, fmt: str) -> bool:
    header = path.read_bytes()[:8]
    if fmt == "pdf":
        return header.startswith(b"%PDF")
    if fmt in {"xlsx", "docx"}:
        return header.startswith(b"PK\x03\x04")
    return False


def load_corpus_catalog(path: Path | None = None) -> dict:
    catalog_path = path or CORPUS_CATALOG
    try:
        data = json.loads(catalog_path.read_text())
    except (OSError, ValueError) as exc:
        raise NotProven("corpus_catalog_unavailable") from exc
    if not isinstance(data, dict):
        raise NotProven("invalid_corpus_catalog")
    if not re.fullmatch(r"[0-9a-f]{40}", str(data.get("source_commit") or "")):
        raise NotProven("invalid_corpus_catalog")
    rights = data.get("rights_policy")
    if not isinstance(rights, dict) or not rights.get("public_fixtures") or not rights.get("controlled_business"):
        raise NotProven("invalid_corpus_catalog")
    promised = data.get("promised_formats")
    if list(promised or []) != list(REQUIRED_PROMISED_FORMATS):
        raise NotProven("corpus_promise_changed")
    kinds = data.get("difference_kinds")
    if list(kinds or []) != list(DIFFERENCE_KINDS):
        raise NotProven("invalid_difference_kinds")
    fixtures = data.get("public_fixtures")
    suites = data.get("controlled_suites")
    if not isinstance(fixtures, list) or not fixtures or not isinstance(suites, list) or not suites:
        raise NotProven("invalid_corpus_catalog")
    revisions = data.get("oracle_revisions", [])
    if not isinstance(revisions, list):
        raise NotProven("invalid_corpus_catalog")
    for row in revisions:
        if not isinstance(row, dict) or not str(row.get("reason") or "").strip():
            raise NotProven("oracle_revision_missing_reason")
    return data


def _format_row(fmt: str) -> dict[str, str]:
    return {
        "format": fmt,
        "promised": "yes",
        "public_fixture": "absent",
        "controlled_sample": "NOT_PROVEN",
        "human_oracle": "NOT_PROVEN",
        "replay": "NOT_PROVEN",
    }


def evaluate_corpus(catalog: dict | None = None, corpus_root: str | None = None) -> dict:
    data = catalog if catalog is not None else load_corpus_catalog()
    raw_root = corpus_root if corpus_root is not None else os.getenv("PPAP_CORPUS_ROOT", "")
    root = Path(raw_root.strip()) if str(raw_root or "").strip() else None
    matrix = {fmt: _format_row(fmt) for fmt in REQUIRED_PROMISED_FORMATS}
    public_ok = True
    covered_public: set[str] = set()
    for fixture in data["public_fixtures"]:
        if not isinstance(fixture, dict):
            raise NotProven("invalid_corpus_catalog")
        fmt = str(fixture.get("format") or "")
        relative = str(fixture.get("path") or "")
        if ".." in Path(relative).parts or not relative.startswith(PUBLIC_FIXTURE_PREFIX):
            raise NotProven("invalid_corpus_catalog")
        path = ROOT / relative
        expected = str(fixture.get("source_sha256") or "")
        if fmt not in matrix or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise NotProven("invalid_corpus_catalog")
        if fixture.get("oracle_kind") != "format_identity" or human_oracle_authority(str(fixture.get("oracle_authority") or "")):
            raise NotProven("public_fixture_cannot_be_business_oracle")
        if not path.is_file() or digest(path) != expected or not public_fixture_format_matches(path, fmt):
            public_ok = False
            matrix[fmt]["public_fixture"] = "mismatch"
            continue
        matrix[fmt]["public_fixture"] = "present"
        covered_public.add(fmt)
    for suite in data["controlled_suites"]:
        if not isinstance(suite, dict):
            raise NotProven("invalid_corpus_catalog")
        formats = suite.get("formats")
        if not isinstance(formats, list) or not formats or any(fmt not in matrix for fmt in formats):
            raise NotProven("invalid_corpus_catalog")
        if suite.get("oracle_kind") != "human":
            raise NotProven("controlled_suite_oracle_must_be_human")
        if not str(suite.get("rule_version") or "").strip() or not str(suite.get("role") or "").strip():
            raise NotProven("invalid_corpus_catalog")
        relative = str(suite.get("dataset_root") or "")
        if not relative or ".." in Path(relative).parts:
            raise NotProven("invalid_corpus_catalog")
        local = (root / relative) if root is not None else None
        present = bool(local and local.exists())
        oracle_ok = human_oracle_authority(str(suite.get("oracle_authority") or ""))
        for fmt in formats:
            if root is None:
                pass
            elif present:
                matrix[fmt]["controlled_sample"] = "present"
            else:
                matrix[fmt]["controlled_sample"] = "absent"
            if oracle_ok:
                matrix[fmt]["human_oracle"] = "reviewed"
            if present and oracle_ok:
                matrix[fmt]["replay"] = "ready"
    business = all(row["human_oracle"] == "reviewed" for row in matrix.values())
    candidates = data.get("document_candidates") if isinstance(data.get("document_candidates"), list) else []
    docling = "NOT_PROVEN"
    for row in candidates:
        if isinstance(row, dict) and row.get("id") == "docling":
            docling = str(row.get("status") or "NOT_PROVEN")
    return {
        "catalog_sha256": digest(CORPUS_CATALOG),
        "source_commit": data.get("source_commit"),
        "public_fixtures": "PROVEN" if public_ok and {"xlsx", "pdf", "docx"} <= covered_public else "NOT_PROVEN",
        "controlled_corpus": "PROVEN" if root and root.exists() and all(row["controlled_sample"] == "present" for row in matrix.values()) else "NOT_PROVEN",
        "business_oracle": "PROVEN" if business else "NOT_PROVEN",
        "docling_comparison": docling if docling in {"PROVEN", "NOT_PROVEN"} else "NOT_PROVEN",
        "legacy_engine_replay": (data.get("legacy_engine_replay") or {}).get("command"),
        "product_replay": (data.get("product_replay") or {}).get("command"),
        "difference_kinds": list(DIFFERENCE_KINDS),
        "matrix": [matrix[fmt] for fmt in REQUIRED_PROMISED_FORMATS],
    }


def corpus_check(evidence: dict) -> None:
    report = evaluate_corpus()
    evidence.update(report)
    if report["public_fixtures"] != "PROVEN":
        raise NotProven("public_fixture_mismatch")
    if report["business_oracle"] != "PROVEN":
        raise NotProven("business_oracle_not_proven")


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
    parser.add_argument("mode", choices=["probe", "audit", "history", "review", "isolation", "corpus"])
    parser.add_argument("--url", default="")
    parser.add_argument("--expected-build", default="")
    parser.add_argument("--output-dir", default=str(ROOT / ".artifacts" / "live"))
    args = parser.parse_args()
    gates = {"audit": "G01", "isolation": "G02-ui", "history": "G03", "review": "G05-review", "probe": "P00", "corpus": "P07"}
    report = {
        "gate": gates[args.mode], "kind": "prerequisite" if args.mode in {"probe", "corpus"} else "product_child_proof",
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
        elif args.mode == "corpus":
            corpus_check(report["evidence"])
        else:
            ui_check(args.mode, report["evidence"])
        report.update(outcome="PROVEN", attribution=None, cleanup="test_records_retained" if args.mode not in {"probe", "corpus"} else "not_applicable")
    except NotProven as exc:
        report.update(reason=exc.code, attribution=exc.attribution)
    except Exception as exc:
        # Never emit raw Playwright/request/config exceptions: they can contain tokens or content.
        report["reason"] = "execution_not_proven:" + type(exc).__name__
    if report["evidence"].get("submit_invocations") or report["evidence"].get("review_invocations"):
        report["cleanup"] = "test_records_may_exist"
    if args.mode in {"probe", "corpus"}:
        report["cleanup"] = "not_applicable"
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
