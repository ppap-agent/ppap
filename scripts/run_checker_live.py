"""Explicit paid Checker optimization acceptance, never collected by default pytest.

Only synthetic inputs are used. Expected answers are authored here, never by the
provider. This file is mounted into the private client only, not the Deep image.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path
from re import fullmatch
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from openpyxl import Workbook

SAMPLES = ((3, "NG"), (10, "PASS"), (20, "PASS"), (7, "NG"), (-5, "NG"), (9, "NG"), (12, "PASS"), (30, "PASS"))
HOLDOUT_MARKER = "checker-live-holdout-"
BASELINE = """import json
from types import SimpleNamespace
from openpyxl import load_workbook
from audit.contracts import AuditResultItem, AuditEvidence, EvidenceSource

def create_audit(audit_file, rule, group_context=None):
    book = load_workbook(audit_file.file_url, data_only=True)
    try:
        value = book.active["A1"].value
    finally:
        book.close()
    def check(clause):
        locator = json.dumps({"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1",
                              "file_version_id": audit_file.file_metadata["file_version_id"]})
        return AuditResultItem(classification="规则审核", item="阈值", priority="P0",
            status="PASS" if value >= 0 else "NG", suggestions="已核对 A1。", clause_id=clause, checks_performed=1,
            evidence=AuditEvidence(required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
                sources=(EvidenceSource(kind="structure", locator=locator),)))
    return SimpleNamespace(checks=[SimpleNamespace(rule_item=SimpleNamespace(id=row["id"]),
        check=lambda clause=row["id"]: check(clause)) for row in rule.rules])
"""


class AcceptanceFailure(Exception):
    """Only fixed, non-secret failure codes may be emitted."""


def require(condition, code):
    if not condition:
        raise AcceptanceFailure(code)


def approved_configuration(environment):
    """Fail closed before opening product state or sending a credential."""
    require(environment.get("PPAP_CHECKER_LIVE_APPROVED") == "true", "paid_approval_required")
    require(environment.get("GITHUB_RUN_ATTEMPT", "1") == "1", "automatic_or_manual_rerun_forbidden")
    base = environment.get("CI_LLM_BASE_URL", "").rstrip("/")
    model = environment.get("CI_LLM_MODEL", "")
    require(base == environment.get("PPAP_APPROVED_BASE_URL", "").rstrip("/"), "approved_endpoint_mismatch")
    require(bool(model) and model == environment.get("PPAP_APPROVED_MODEL"), "approved_model_mismatch")
    parsed = urlsplit(base)
    require(parsed.scheme == "https" and bool(parsed.hostname) and not (parsed.username or parsed.password or parsed.query or parsed.fragment), "invalid_provider_endpoint")
    require(bool(environment.get("CI_LLM_API_KEY")), "missing_provider_credential")
    run_id = environment.get("GITHUB_RUN_ID", "")
    require(bool(fullmatch(r"[0-9]+", run_id)) and environment.get("COMPOSE_PROJECT_NAME") == "ppap-checker-live-" + run_id, "isolated_stack_required")
    require(environment.get("GITHUB_ACTIONS") == "true", "disposable_github_runner_required")
    return base, model


def workbook(value, ordinal):
    book = Workbook()
    book.active.append([value, HOLDOUT_MARKER + uuid4().hex if ordinal % 4 == 0 else "synthetic-training-" + uuid4().hex])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


class Client:
    def __init__(self, session):
        from product.identity import SESSION_COOKIE

        self.http = httpx.Client(base_url="http://127.0.0.1:18080", timeout=30, follow_redirects=False, headers={"Cookie": SESSION_COOKIE + "=" + session}, trust_env=False)

    def request(self, method, path, *, payload=None, content=None, status=200):
        # Never retry a write, including an uncertain optimization admission.
        response = self.http.request(method, path, json=payload, content=content)
        require(response.status_code == status, "unexpected_product_http_status")
        return response.json()

    def poll(self, path, terminal, *, seconds=2400, on_read=lambda _: None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            result = self.request("GET", path)
            on_read(result)
            if terminal(result):
                return result
            time.sleep(1)
        raise AcceptanceFailure("receipt_pending_no_retry")

    def upload(self, name, content_type, raw):
        uploaded = self.request("POST", "/files", payload={"original_filename": name, "content_type": content_type, "byte_size": len(raw)}, status=201)
        path = f"/files/{uploaded['file_id']}/versions/{uploaded['id']}"
        self.request("PUT", path + "/blob", content=raw)
        return self.request("POST", path + "/finalize")


def validate_outcome(receipt, samples):
    """Check each independently frozen answer, never just a server 'verified' bit."""
    require(receipt.get("state") == "completed", "optimization_not_completed")
    require(bool(receipt.get("candidate_revision_id")), "candidate_missing")
    usage = receipt.get("usage", {})
    require(usage.get("source") == "provider" and usage.get("uncertain") is False, "provider_usage_unconfirmed")
    require(all(type(usage.get(name)) is int and usage[name] > 0 for name in ("input_tokens", "output_tokens", "model_requests")), "provider_usage_missing")
    report = (receipt.get("report") or {}).get("validation") or {}
    require(report.get("verified") is True and report.get("total_samples") == report.get("passed_samples") == len(samples), "independent_validation_failed")
    cases = report.get("cases", [])
    require(len(cases) == len(samples) and {case.get("case_id") for case in cases} == set(samples), "sample_identity_mismatch")
    repaired_misses = 0
    holdout_expected = set()
    for case in cases:
        expected = samples[case["case_id"]]
        require(case.get("ordinal") == expected["ordinal"], "sample_ordinal_mismatch")
        split = "holdout" if expected["ordinal"] % 4 == 0 else "training"
        require(case.get("split") == split and case.get("coverage") is True and case.get("error_code") is None, "sample_coverage_failed")
        clauses = case.get("clauses", [])
        require(len(clauses) == 1, "clause_coverage_failed")
        clause = clauses[0]
        require(clause.get("clause_id") == "R1" and clause.get("expected") == expected["expected"] and clause.get("actual") == expected["expected"], "independent_answer_mismatch")
        require(clause.get("baseline_actual") == expected["baseline"], "baseline_answer_mismatch")
        require(clause.get("evidence_locatable") is True and clause.get("checks_performed", 0) > 0 and clause.get("regression") is False, "evidence_or_regression_failed")
        repaired_misses += expected["expected"] == "NG" and expected["baseline"] == "PASS"
        if split == "holdout":
            holdout_expected.add(expected["expected"])
    require(repaired_misses > 0 and holdout_expected == {"PASS", "NG"}, "repair_or_holdout_coverage_missing")
    return report


def receipt_snapshot(receipt):
    """Legal unknown receipts may have report:null; preserve confirmed facts first."""
    nested = receipt.get("report") or {}
    usage = receipt.get("usage") or {}
    return {
        **{name: receipt.get(name) for name in ("state", "suite_revision_id", "candidate_revision_id")},
        "returned_operation_id": receipt.get("operation_id"),
        "product_failure": receipt.get("failure"),
        "usage": {name: usage[name] for name in ("source", "uncertain", "model_requests", "input_tokens", "output_tokens") if name in usage},
        "validation": nested.get("validation") or {},
    }


def retain_receipt(report, receipt):
    snapshot = receipt_snapshot(receipt)
    snapshot["usage"] = {**report.get("usage", {}), **snapshot["usage"]}
    report.update(snapshot)


def final_readback(report, receipt):
    """A read must not erase gate diagnostics or keep a changed proof accepted."""
    mismatch = receipt.get("operation_id") != report.get("operation_id")
    if report.get("accepted"):
        previous = report.get("validation") or {}
        current = (receipt.get("report") or {}).get("validation") or {}
        mismatch = mismatch or receipt.get("state") != "completed" or any(receipt.get(name) != report.get(name) for name in ("suite_revision_id", "candidate_revision_id")) or current != previous
        mismatch = mismatch or receipt_snapshot(receipt)["usage"] != report.get("usage", {})
        try:
            validate_outcome(receipt, report["samples"])
        except (AcceptanceFailure, KeyError, TypeError):
            mismatch = True
    if mismatch:
        report["accepted"] = False
        report.setdefault("gate_failure", "final_receipt_identity_or_proof_changed")
    retain_receipt(report, receipt)


def validate_rendered_round(rendered, expected):
    require(
        rendered == {"round_id": expected["id"], "attempt": str(expected["result_attempt"]), "status": {"PASS": "符合", "NG": "不符合"}[expected["findings"][0]["status"]]},
        "browser_round_identity_or_result_mismatch",
    )


def validate_bindings(receipt, *, operation_id, base_revision_id, detail, frozen, input_digests):
    report = (receipt.get("report") or {}).get("validation") or {}
    require(receipt.get("operation_id") == operation_id and receipt.get("suite_revision_id") == base_revision_id, "operation_identity_mismatch")
    require(receipt.get("candidate_revision_id") == detail["id"] == report.get("suite_revision_id"), "candidate_identity_mismatch")
    require(report.get("suite_digest") == detail["resolved_digest"] and report.get("oracle_revision") == frozen["oracle_revision"], "candidate_or_oracle_digest_mismatch")
    require(report.get("library_revision") == frozen["library_revision"], "library_identity_mismatch")
    require({case["case_id"]: case.get("input_digest") for case in report.get("cases", [])} == input_digests, "validation_input_identity_mismatch")


def history_fingerprint(result, snapshot):
    frozen = {"result_attempt": result["result_attempt"], "findings": result["findings"], "snapshot": snapshot}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class BrowserJourney:
    """Real Chromium against the built Web. No mocked routes, responses or traces."""

    def __init__(self, session):
        from playwright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch()
        self.context = None
        self.new_session(session)

    def new_session(self, session):
        from product.identity import SESSION_COOKIE

        if self.context:
            self.context.close()
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.context.add_cookies([{"name": SESSION_COOKIE, "value": session, "url": "http://127.0.0.1:14173", "httpOnly": True}])
        self.page = self.context.new_page()
        self.page.set_default_timeout(20000)

    def read_round(self, result):
        from playwright.sync_api import expect

        page = self.page
        page.goto("http://127.0.0.1:14173/audit?round=" + result["id"])
        view = page.get_by_role("region", name="历史审核", exact=True)
        expect(view).to_have_attribute("data-round-id", result["id"])
        expect(view).to_have_attribute("data-result-attempt", str(result["result_attempt"]))
        row = view.get_by_role("row").filter(has=page.get_by_role("button", name="这条判错了 R1", exact=True))
        expect(row).to_have_count(1)
        status = row.get_by_role("cell").nth(2)
        expect(status).to_have_text({"PASS": "符合", "NG": "不符合"}[result["findings"][0]["status"]])
        validate_rendered_round({"round_id": view.get_attribute("data-round-id"), "attempt": view.get_attribute("data-result-attempt"), "status": status.inner_text()}, result)

    def read_optimization(self, receipt, samples):
        from playwright.sync_api import expect

        self.page.goto(self.address)
        view = self.page.get_by_role("region", name="修复与回归结果", exact=True)
        expect(view).to_have_attribute("data-operation-id", receipt["operation_id"])
        expect(view).to_have_attribute("data-candidate-revision", receipt["candidate_revision_id"])
        repaired = sum(row["expected"] != row["baseline"] for row in samples.values())
        expect(view.get_by_text(f"修好 {repaired} 条 · 新增错误 0 条 · 无法判断 0 条 · 技术失败 0 条", exact=True)).to_be_visible()
        view.get_by_text("逐项验证结果与正确答案", exact=True).click()
        expect(view.locator("article[data-case-id]")).to_have_count(len(samples))
        labels = {"PASS": "符合", "NG": "不符合"}
        for identity, sample in samples.items():
            case = view.locator(f'article[data-case-id="{identity}"]')
            split = "独立验证样本" if sample["ordinal"] % 4 == 0 else "修复样本"
            expect(case.get_by_text(split + " · 符合已确认答案", exact=True)).to_be_visible()
            expect(case.get_by_text(f"条款 R1 · 正确答案 {labels[sample['expected']]} · 此前 {labels[sample['baseline']]} → 当前 {labels[sample['expected']]}", exact=True)).to_be_visible()

    def feedback(self, result, uploaded, expected):
        page = self.page
        self.read_round(result)
        page.get_by_role("button", name="这条判错了 R1", exact=True).click()
        page.get_by_label("正确结论", exact=True).select_option(expected)
        page.get_by_text("添加我在原件中找到的依据", exact=True).click()
        page.get_by_label("工作表名称", exact=True).fill("Sheet")
        page.get_by_label("单元格（例如 B12）", exact=True).fill("A1")
        page.get_by_role("button", name="加入这处依据", exact=True).click()
        page.get_by_label("为什么应当这样判断", exact=True).fill("人工合成样本独立答案：核对 A1 与大于等于 10 的要求。")
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/checker-feedback")) as changed:
            page.get_by_role("button", name="加入待修复", exact=True).click()
        require(changed.value.status == 200, "browser_feedback_failed")
        return changed.value.json()

    def configure_and_start(self, original, base_url, model, report, report_path):
        from playwright.sync_api import expect

        page = self.page
        self.address = "http://127.0.0.1:14173/audit?round=" + original["round_id"] + "&file=" + original["file_version_id"] + "&clause=R1&basis=1&checkerFeedbackClause=R1"
        page.goto(self.address)
        page.get_by_role("button", name="让 AI 修复并验证", exact=True).click()
        page.get_by_label("Base URL", exact=True).fill(base_url)
        page.get_by_label("模型名称", exact=True).fill(model)
        page.get_by_label("API Key", exact=True).fill(os.environ["CI_LLM_API_KEY"])
        with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/me/model-config")) as saved:
            page.get_by_role("button", name="保存模型配置", exact=True).click()
        require(saved.value.status == 200, "browser_configuration_failed")
        configured = saved.value.json()
        require(configured.get("configured") is True and configured.get("base_url") == base_url and configured.get("model") == model and "api_key" not in configured, "personal_configuration_mismatch")
        expect(page.get_by_label("API Key", exact=True)).to_have_value("")
        page.get_by_label(f"同意将本次资料发送至 {base_url}，使用 {model} 修复并验证这 8 条反馈", exact=True).check()

        def capture(route):
            # Persist the real UI-generated identity before forwarding its sole
            # mutation. The request and real response are otherwise unchanged.
            if route.request.method != "POST":
                route.continue_()
                return
            body = route.request.post_data_json
            require(not report.get("operation_id") and body.get("expected_model_revision") == configured["revision"], "optimization_identity_not_frozen")
            report.update(stage="admitting", operation_id=body["operation_id"])
            write_report(report_path, report)
            route.continue_()

        page.route("**/suite-revisions/*/optimizations", capture)
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/optimizations")) as started:
            page.get_by_role("button", name="确认开始修复", exact=True).click()
        require(started.value.status == 200, "browser_optimization_admission_failed")
        receipt = started.value.json()
        page.reload()
        return configured, receipt

    def publish(self):
        from playwright.sync_api import expect

        page = self.page
        page.goto(self.address)
        expect(page.get_by_role("heading", name="修复结果", exact=True)).to_be_visible()
        page.get_by_role("button", name="发布此修改", exact=True).click()
        expect(page.get_by_role("group", name="确认采用检查版本")).to_contain_text("已有报告不变")
        expect(page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True)).to_have_count(0)
        page.get_by_role("button", name="确认发布", exact=True).click()
        expect(page.get_by_text("发布操作已完成", exact=True)).to_be_visible()

    def rollback(self):
        from playwright.sync_api import expect

        page = self.page
        page.goto(self.address)
        page.get_by_text("版本记录与受影响审核", exact=True).click()
        page.get_by_role("button", name="查看此版本／回滚", exact=True).last.click()
        page.get_by_role("button", name="回滚到此版本", exact=True).click()
        expect(page.get_by_role("button", name="确认回滚", exact=True)).to_be_disabled()
        page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True).check()
        page.get_by_role("button", name="确认回滚", exact=True).click()
        expect(page.get_by_text("发布操作已完成", exact=True)).to_be_visible()

    def stale(self):
        from playwright.sync_api import expect

        self.page.goto(self.address)
        self.page.get_by_role("button", name="发布此修改", exact=True).click()
        expect(self.page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True)).not_to_be_checked()
        expect(self.page.get_by_role("button", name="确认发布", exact=True)).to_be_disabled()

    def close(self):
        self.browser.close()
        self.playwright.stop()


def local_settings(environment):
    from product.settings import Settings

    return Settings(
        profile="remote-dev",
        git_sha=environment["PPAP_BUILD_SHA"],
        personal_model_max_requests=int(environment["PPAP_PERSONAL_MODEL_MAX_REQUESTS"]),
        personal_model_max_input_tokens=int(environment["PPAP_PERSONAL_MODEL_MAX_INPUT_TOKENS"]),
        personal_model_max_output_tokens=int(environment["PPAP_PERSONAL_MODEL_MAX_OUTPUT_TOKENS"]),
        pg_host="127.0.0.1",
        pg_port=15432,
        pg_database="ppap",
        pg_migrator_password=environment["LIVE_PG_MIGRATOR_PASSWORD"],
        s3_endpoint="http://127.0.0.1:19000",
        s3_bucket="checker-live",
        s3_access_key=environment["LIVE_S3_ACCESS_KEY"],
        s3_secret_key=environment["LIVE_S3_SECRET_KEY"],
        web_url="http://127.0.0.1:14173",
        llm_base_url=environment["PPAP_APPROVED_BASE_URL"],
        llm_model=environment["PPAP_APPROVED_MODEL"],
        usage_charge_enabled=False,
        feishu_notifications_enabled=False,
        deep_executor_revision="checker-live",
        feishu_app_id="checker-live",
        personal_model_allowed_base_urls=environment["PPAP_APPROVED_BASE_URL"],
        model_credential_encryption_key=environment["LIVE_ENCRYPTION_KEY"],
    )


def run(report_path):
    base_url, model = approved_configuration(os.environ)
    report = {"accepted": False, "stage": "initializing", "candidate_sha": os.environ["PPAP_BUILD_SHA"], "provider_endpoint": base_url, "model": model, "cost": "unknown", "automatic_retry": False}
    report["limits"] = {name: int(os.environ["PPAP_PERSONAL_MODEL_MAX_" + name.upper()]) for name in ("requests", "input_tokens", "output_tokens")}
    write_report(report_path, report)
    # Only initial identity, rule and immutable faulty Suite use trusted stores.
    # All files, real baseline rounds, personal settings, feedback, optimization
    # admission and result readback use the authenticated production HTTP routes.
    from product import build_product_store
    from product.storage import S3Objects
    from product.store.suites import SuiteStore
    from psycopg.types.json import Jsonb

    settings = local_settings(os.environ)
    require(not settings.usage_charge_enabled and not settings.feishu_notifications_enabled, "business_side_effects_must_be_disabled")
    store = build_product_store(settings, objects=S3Objects(settings), role="migrator")
    principal = store.upsert_feishu_identity("checker-live-" + uuid4().hex, "acceptance", "Synthetic Checker acceptance")
    session = store.create_session(principal)
    client = Client(session)
    recovery = report_path.parent / "private-client.json"
    recovery.write_text(json.dumps({"session": session}))
    recovery.chmod(0o600)
    browser = None
    suite_key = "live:threshold:" + uuid4().hex
    rules = {
        "kind": "engine",
        "type": "控制计划",
        "rule_profile": "v3",
        "suite_key": suite_key,
        "customers": [],
        "models": [],
        "parts": [],
        "rules": [{"id": "R1", "category": "阈值", "priority": "P0", "rule_to_check": "A1 必须大于等于 10。数值小于 10 为 NG，大于等于 10 为 PASS。", "type_of_rule": "table"}],
        "checker_binding": {"R1": "checker:create_audit"},
    }
    suites = SuiteStore(settings, store.objects, role="migrator")
    base = suites.create_revision(tenant_id=None, suite_key=suite_key, files={"rules.json": json.dumps(rules).encode(), "checker.py": BASELINE.encode()}, created_by=None)
    rule_id = str(uuid4())
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES (%s,%s,'Synthetic threshold',%s)", (rule_id, principal.tenant_id, Jsonb(rules)))
    try:
        require(client.http.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 404, "fixture_login_exposed_to_guest")
        browser = BrowserJourney(session)
        client.request("PUT", f"/suites/{suite_key}/active", payload={"revision_id": base.id, "expected_revision": 0, "operation_id": str(uuid4()), "force_unverified": True})
        samples = {}
        rounds = []
        for ordinal, (value, expected) in enumerate(SAMPLES, 1):
            raw = workbook(value, ordinal)
            uploaded = client.upload(f"sample-{ordinal}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", raw)
            require(uploaded["sha256"] == hashlib.sha256(raw).hexdigest(), "uploaded_source_identity_mismatch")
            draft = client.request("POST", "/drafts", status=201)
            client.request("PUT", f"/drafts/{draft['id']}", payload={"analysis_mode": "standard", "items": [{"file_version_id": uploaded["id"], "role": "target", "rule_version_id": rule_id}]})
            accepted = client.request("POST", f"/drafts/{draft['id']}/accept", payload={"idempotency_key": str(uuid4())}, status=201)
            result = client.poll(f"/rounds/{accepted['id']}", lambda row: row.get("execution") in {"completed", "failed"})
            actual = "PASS" if value >= 0 else "NG"
            require(result.get("execution") == "completed" and len(result.get("findings", [])) == 1 and result["findings"][0]["status"] == actual, "baseline_not_executed")
            snapshot = suites.read_snapshot(result["id"], principal.tenant_id).to_dict()
            rounds.append({"round_id": result["id"], "file_version_id": uploaded["id"], "history_sha256": history_fingerprint(result, snapshot)})
            saved = browser.feedback(result, uploaded, expected)
            require(saved["ordinal"] == ordinal, "oracle_ordinal_mismatch")
            samples[saved["id"]] = {"ordinal": ordinal, "expected": expected, "baseline": actual, "file_version_id": uploaded["id"], "sha256": uploaded["sha256"], "feedback": saved}
        report.update(stage="configuring", base_revision_id=base.id, samples={ident: {key: value for key, value in row.items() if key != "feedback"} for ident, row in samples.items()})
        write_report(report_path, report)
        private = report_path.parent / "private-oracle.json"
        private.write_text(
            json.dumps(
                {
                    "hidden_case_ids": [ident for ident, row in samples.items() if row["ordinal"] % 4 == 0],
                    "hidden_source_sha256": [row["sha256"] for row in samples.values() if row["ordinal"] % 4 == 0],
                }
            )
        )
        private.chmod(0o600)
        configured, receipt = browser.configure_and_start(rounds[0], base_url, model, report, report_path)
        require(client.request("GET", "/me/model-config") == configured, "personal_configuration_readback_mismatch")
        path = f"/checker-optimizations/{report['operation_id']}"

        def record(current):
            retain_receipt(report, current)
            write_report(report_path, report)

        record(receipt)
        report["stage"] = "waiting"
        receipt = client.poll(path, lambda row: row.get("state") not in {"queued", "running"}, on_read=record)
        validate_outcome(receipt, samples)
        require(client.request("GET", path) == receipt, "optimization_readback_changed")
        detail = client.request("GET", "/suite-revisions/" + receipt["candidate_revision_id"])
        require(detail["files"]["checker.py"] != BASELINE, "checker_not_changed")
        report["candidate_checker_sha256"] = hashlib.sha256(detail["files"]["checker.py"].encode()).hexdigest()
        from audit.checker_core import checker_digest

        program = {name: content.encode() for name, content in detail["files"].items() if name in {"checker.py", "resources.py"}}
        require(receipt["report"]["validation"]["program_digest"] == checker_digest(program), "validated_candidate_identity_mismatch")
        require(client.request("GET", f"/suites/{suite_key}/active")["current_revision_id"] == base.id, "candidate_published_without_request")
        browser.new_session(store.create_session(principal))
        browser.read_optimization(receipt, samples)
        require(client.request("GET", path) == receipt, "reconnected_receipt_changed")
        browser.publish()
        require(client.request("GET", f"/suites/{suite_key}/active")["current_revision_id"] == receipt["candidate_revision_id"], "publication_not_applied")
        newer = client.request("POST", f"/rounds/{rounds[0]['round_id']}/resubmit", payload={"idempotency_key": str(uuid4())}, status=201)
        newer = client.poll("/rounds/" + newer["id"], lambda row: row.get("execution") in {"completed", "failed"})
        require(newer.get("execution") == "completed" and newer["predecessor_id"] == rounds[0]["round_id"] and newer["findings"][0]["status"] == "NG", "explicit_reaudit_failed")
        newer_snapshot = suites.read_snapshot(newer["id"], principal.tenant_id)
        require(newer_snapshot.targets[0].suite_revision_id == receipt["candidate_revision_id"], "reaudit_used_wrong_suite")
        browser.read_round(newer)
        rounds.append({"round_id": newer["id"], "history_sha256": history_fingerprint(newer, newer_snapshot.to_dict())})
        report["reaudit_round_id"] = newer["id"]
        browser.rollback()
        active = client.request("GET", f"/suites/{suite_key}/active")
        require(active["current_revision_id"] == base.id, "rollback_not_applied")
        first = next(iter(samples.values()))["feedback"]
        client.request(
            "PUT",
            f"/audit-rounds/{first['round_id']}/checker-feedback/{first['id']}",
            payload={
                "expected_revision": first["revision"],
                "expected_status": first["expected_status"],
                "rationale": first["rationale"] + " 再次人工确认。",
                "evidence": first["evidence"],
            },
        )
        stale = client.request("GET", "/suite-revisions/" + receipt["candidate_revision_id"])
        require(stale["validation"]["status"] == "stale", "changed_oracle_not_stale")
        rejected = client.http.put(
            f"/suites/{suite_key}/active", json={"revision_id": receipt["candidate_revision_id"], "expected_revision": active["revision"], "operation_id": str(uuid4()), "force_unverified": False}
        )
        require(rejected.status_code == 409, "stale_publication_not_rejected")
        browser.stale()
        for old in rounds:
            current = client.request("GET", "/rounds/" + old["round_id"])
            require(history_fingerprint(current, suites.read_snapshot(old["round_id"], principal.tenant_id).to_dict()) == old["history_sha256"], "historical_result_changed")
        report["final_candidate_validation_status"] = stale["validation"]["status"]
        report["final_active_revision_id"] = base.id
        report["journey"] = {
            "browser_personal_configuration": True,
            "browser_feedback": True,
            "browser_optimize": True,
            "reconnect_readback": True,
            "browser_publish": True,
            "api_explicit_reaudit_and_browser_readback": True,
            "browser_rollback": True,
            "stale_blocked": True,
            "history_unchanged": True,
        }
        with store._conn() as conn:
            from psycopg.rows import dict_row

            with conn.cursor(row_factory=dict_row) as cursor:
                operation = cursor.execute("SELECT * FROM product.checker_optimizations WHERE operation_id=%s AND tenant_id=%s", (report["operation_id"], principal.tenant_id)).fetchone()
            frozen = operation["frozen"]
            require({sample["id"] for sample in frozen["samples"]} == set(samples), "frozen_sample_set_mismatch")
            for sample in frozen["samples"]:
                expected = samples[sample["id"]]
                target = next(item for item in sample["files"] if item["role"] == "target")
                require(target["file_version_id"] == expected["file_version_id"] and target["sha256"] == expected["sha256"], "frozen_source_identity_mismatch")
                require(sample["expected_status"] == expected["expected"] and sample["ordinal"] == expected["ordinal"], "frozen_oracle_identity_mismatch")
            row = conn.execute("SELECT count(*), max(attempt) FROM engine.jobs WHERE kind='checker_optimization' AND payload->>'operation_id'=%s", (report["operation_id"],)).fetchone()
            require(row[0] == 1, "optimization_operation_duplicated")
            report["technical_attempts"] = row[1]
            require(conn.execute("SELECT count(*) FROM product.usage_events WHERE tenant_id=%s", (principal.tenant_id,)).fetchone()[0] == 0, "formal_audit_points_charged")
        from product.store.checker_development import CheckerDevelopmentStore
        from worker.checker_optimizer import materialize_request

        frozen_request, _ = materialize_request(CheckerDevelopmentStore(store), operation)
        validate_bindings(
            receipt,
            operation_id=report["operation_id"],
            base_revision_id=base.id,
            detail=detail,
            frozen=frozen,
            input_digests={sample.case_id: sample.inputs.digest for sample in frozen_request.oracle},
        )
        probe = json.loads((report_path.parent / "controller.json").read_text())
        require(
            probe.get("adaptations") == 1
            and set(probe.get("holdout_sources", [])) == {row["sha256"] for row in samples.values() if row["ordinal"] % 4 == 0}
            and probe.get("provider_requests") == receipt["usage"]["model_requests"]
            and 0 < probe["provider_requests"] <= report["limits"]["requests"]
            and probe.get("violation") is False
            and probe.get("guest_cannot_read_evidence") is True,
            "holdout_boundary_not_proven",
        )
        report.update(accepted=True, stage="complete", isolation_probe=probe)
    finally:
        # Do not revoke the configuration while a timed-out operation may still
        # own it. The entire disposable stack is stopped by workflow cleanup.
        if report.get("operation_id"):
            try:
                final_readback(report, client.request("GET", "/checker-optimizations/" + report["operation_id"]))
                report["final_readback"] = "read_only_completed"
            except Exception:
                report["final_readback"] = "unavailable_known_usage_preserved"
        report["recovery"] = "Ephemeral stack; after cleanup, reconcile unknown consumption with provider billing. Never redispatch automatically."
        write_report(report_path, report)
        if browser is not None:
            browser.close()
        client.http.close()


def final_snapshot(path):
    """Read-only reconciliation before disposable containers stop, even on timeout."""
    if not path.is_file():
        return
    report = json.loads(path.read_text())
    recovery = path.parent / "private-client.json"
    if not report.get("operation_id") or not recovery.is_file():
        return
    client = Client(json.loads(recovery.read_text())["session"])
    client.http.timeout = httpx.Timeout(5)
    try:
        final_readback(report, client.request("GET", "/checker-optimizations/" + report["operation_id"]))
        report["final_readback"] = "read_only_completed"
    except Exception:
        report["final_readback"] = "unavailable_known_usage_preserved"
    finally:
        report["recovery"] = "Ephemeral stack; after cleanup, reconcile unknown consumption with provider billing. Never redispatch automatically."
        write_report(path, report)
        client.http.close()


def main():
    path = Path(os.environ["PPAP_CHECKER_LIVE_OUTPUT"]) / "report.json"
    try:
        require(sys.argv[1:] in ([], ["--snapshot"]), "invalid_arguments")
        if sys.argv[1:] == ["--snapshot"]:
            final_snapshot(path)
        else:
            run(path)
        # The final read can revoke an earlier proof. Both the live process and
        # the workflow's later read-only snapshot must propagate that verdict.
        report = json.loads(path.read_text()) if path.is_file() else {}
        require(report.get("accepted") is True, report.get("gate_failure") or "final_acceptance_not_proven")
        print("Checker live acceptance passed; see the bounded report.")
        return 0
    except Exception as exc:
        report = json.loads(path.read_text()) if path.exists() else {"accepted": False}
        report.update(accepted=False, gate_failure=str(exc) if isinstance(exc, AcceptanceFailure) else type(exc).__name__, automatic_retry=False, cost="unknown")
        write_report(path, report)
        print("Checker live acceptance did not pass; inspect report.json. Do not rerun uncertain provider work.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
