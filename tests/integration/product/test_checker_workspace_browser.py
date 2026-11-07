"""#254: real browser/HTTP/PG/queue/Controller/Core optimization journey.

Model metadata and authorship are deterministic. The guest runs in fresh local processes,
not a container, so this does not certify OS isolation or real BYOK model quality.
Default storage is the real Silo fixture. PPAP_CHECKER_BROWSER_LOCAL_OBJECTS=1 is
an explicit local-only persistent object substitute, never an S3 acceptance claim.
PPAP_CHECKER_BROWSER_LOCAL_TRANSPORT=1 substitutes only JSON RPC transport when
Unix sockets are unavailable; neither substitute is permitted in CI.
"""

import hashlib
import io
import json
import os
import re
import runpy
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import uvicorn
from agent.core_program import CoreRuntime
from agent.run import Controller, UnixControllerServer
from agent.source import authorize_execution, object_file_source
from api import create_app
from audit.checker_core import runtime_revision
from audit.standard_suites import library_revision
from cryptography.fernet import Fernet
from openpyxl import Workbook
from product import build_product_store
from product.db import connect
from product.files import ObjectExists, ObjectMissing, ObjectTooLarge
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from worker import process_claimed
from worker.checker_optimizer import development_handler
from worker.precheck_controller import precheck_handler

ROOT = Path(__file__).resolve().parents[3]
GUEST = ROOT / "tests/integration/product/_checker_workspace_guest.py"
BASELINE = runpy.run_path(str(GUEST))["BASELINE"]
RULES = {
    "kind": "engine",
    "type": "控制计划",
    "rule_profile": "v3",
    "suite_key": "browser:threshold",
    "customers": ["测试客户"],
    "models": [],
    "parts": [],
    "rules": [{"id": "R1", "category": "阈值", "priority": "P0", "rule_to_check": "A1 必须大于等于 10", "type_of_rule": "table"}],
    "checker_binding": {"R1": "checker:create_audit"},
}


class LocalObjects:
    """Explicit opt-in persistence substitute; no business fact lives here."""

    def __init__(self, root):
        self.root = root
        root.mkdir()

    def path(self, key):
        return self.root / hashlib.sha256(key.encode()).hexdigest()

    def put_exclusive(self, key, body, content_type):
        try:
            with self.path(key).open("xb") as stream:
                stream.write(body)
        except FileExistsError:
            raise ObjectExists() from None

    def get(self, key, *, max_bytes=None):
        try:
            raw = self.path(key).read_bytes()
        except FileNotFoundError:
            raise ObjectMissing() from None
        if max_bytes is not None and len(raw) > max_bytes:
            raise ObjectTooLarge()
        return raw

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)


class DeterministicMetadata:
    """The model metadata boundary only; extraction, matching and receipts stay real."""

    def structured_completion(self, *, operation, **kwargs):
        if operation == "document_metadata_extraction":
            return json.dumps({"customer": "测试客户", "part_number": "P-1"})
        if operation == "document_classification":
            return "控制计划"
        raise AssertionError("Unexpected model capability in a native XLSX journey")


class NativeGuest:
    usage_source = "controlled"

    def __init__(self, settings, model_identity=None, *, jobs=None):
        self.settings = settings
        self.model_identity = dict(model_identity) if model_identity is not None else None
        self.jobs = jobs if jobs is not None else []

    @property
    def adaptations(self):
        return self.jobs.count("adapt-checker")

    def for_identity(self, identity):
        return NativeGuest(self.settings, identity, jobs=self.jobs)

    def engine_revision(self):
        return self.settings.deep_execution_revision(self.model_identity)

    def ready(self, operation="audit"):
        return True

    def run(self, job, inputs, work, deadline, cancelled, **kwargs):
        assert not cancelled.is_set()
        self.jobs.append(job.operation)
        grants = work / "grants"
        grants.mkdir()
        if job.operation in {"execute-audit", "adapt-checker"}:
            assert self.model_identity is not None
            manifest = json.loads((inputs / "manifest.json").read_text())
            if job.operation == "execute-audit":
                assert manifest["snapshot"]["model_identity"] == self.model_identity
            (grants / "model.json").write_text(
                json.dumps(
                    {
                        **self.model_identity,
                        "base_url": "http://127.0.0.1:1/v1",
                        "token": "synthetic-unused-workspace-grant-1234567890",
                        "wall_seconds": 60,
                        "max_turns": 1,
                    }
                )
            )
        # Do not inherit any credentials or PYTHONSTARTUP. Run production guest
        # on its own main thread so real clause deadlines stay active.
        env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL"}}
        env["PYTHONPATH"] = os.pathsep.join(sys.path)
        result = subprocess.run(
            [sys.executable, str(GUEST), str(inputs), str(work), str(work / "grants")],
            env=env,
            capture_output=True,
            timeout=min(30, max(0.001, deadline - time.monotonic())),
        )
        if result.returncode:
            print("Guest failure:", result.stderr.decode()[-3000:], flush=True)
        assert result.returncode == 0, result.stderr.decode()[-3000:]

    def execute_precheck(self, payload, snapshot, sources, programs, context):
        from agent.precheck_program import execute_precheck

        return execute_precheck(self, payload, snapshot, sources, programs, context)


def workbook(value, index):
    book = Workbook()
    book.active.append([value, "holdout-secret" if index == 4 else f"training-{index}"])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


@pytest.fixture
def workspace_product(auth_store_factory, tmp_path, request, monkeypatch):
    if os.environ.get("CI") and any(os.environ.get(name) == "1" for name in ("PPAP_CHECKER_BROWSER_LOCAL_OBJECTS", "PPAP_CHECKER_BROWSER_LOCAL_TRANSPORT")):
        pytest.fail("Local workspace substitutes must not replace the CI S3/UDS boundaries")
    objects = LocalObjects(tmp_path / "objects") if os.environ.get("PPAP_CHECKER_BROWSER_LOCAL_OBJECTS") == "1" else request.getfixturevalue("v4_objects")
    settings = Settings(
        usage_charge_enabled=False, feishu_notifications_enabled=False, feishu_app_id="checker-browser", git_sha="a" * 40, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode())
    )
    admin = auth_store_factory(settings, objects=objects)
    alice = admin.upsert_feishu_identity("checker-browser", "alice", "Alice")
    bob = admin.upsert_feishu_identity("checker-browser", "bob", "Bob")
    settings.deep_controller_socket = str(tmp_path / "controller.sock")
    suites = SuiteStore(settings, objects, role="migrator")
    rules = {**RULES, "rules": [{**RULES["rules"][0], "id": f"R{index}"} for index in range(1, getattr(request, "param", 1) + 1)]}
    rules["checker_binding"] = {row["id"]: "checker:create_audit" for row in rules["rules"]}
    base = suites.create_revision(tenant_id=None, suite_key=RULES["suite_key"], files={"rules.json": json.dumps(rules).encode(), "checker.py": BASELINE.encode()}, created_by=None)
    releases = SuiteReleaseStore(settings, objects, role="api")
    releases.publish(alice, RULES["suite_key"], base.id, 0, str(uuid4()), force_unverified=True)
    rule_id = str(uuid4())
    with admin._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES (%s,%s,'阈值检查',%s)", (rule_id, alice.tenant_id, Jsonb({**rules, "suite_revision_id": base.id})))
    api = build_product_store(settings, objects=objects, role="api")
    worker = build_product_store(settings, objects=objects, role="worker")
    worker_suites = SuiteStore(settings, objects, role="worker")
    guest = NativeGuest(settings)

    class Runtime:
        def core(self, spec):
            scoped = CoreRuntime(guest.for_identity(spec.model_identity), lambda spec: worker_suites.read_snapshot(spec.round_id, spec.tenant_id), worker_suites.resolve_suite, objects=objects)
            assert scoped.execution_revision() == spec.executor_revision
            return scoped

        def execute(self, spec, files, *, context):
            return self.core(spec).execute(spec, files, context=context)

        def develop_and_run(self, request, *, spec, context):
            assert request.executor_revision == spec.executor_revision
            return self.core(spec).develop_and_run(request, spec=spec, context=context)

    runtime = Runtime()
    controller = Controller(
        runtime,
        object_file_source(settings, connect=connect, objects=objects),
        executor_revision=runtime_revision(settings.deep_execution_revision(), library_revision()),
        executor_revision_for_request=lambda spec: runtime_revision(settings.deep_execution_revision(spec.model_identity), library_revision()),
        authorize=authorize_execution(settings, connect=connect, open_store=lambda _: worker),
        readiness=lambda: True,
        configuration=settings.deep_execution_policy().runtime_identity(),
        development_handler=development_handler(worker, runtime),
        precheck_handler=precheck_handler(worker, guest),
    )
    rpc = None
    if os.environ.get("PPAP_CHECKER_BROWSER_LOCAL_TRANSPORT") == "1":
        from agent.client import DeepClient, DeepError
        from agent.run import ExecutionContext

        def request_locally(client, payload, *, cancelled=None, on_progress=None):
            context = ExecutionContext()
            if cancelled is not None:
                context.cancelled = cancelled
            if on_progress is not None:
                context.on_progress = on_progress
            raw = controller.handle_raw(json.loads(json.dumps(payload)), context)
            if raw.get("op") == "error":
                raise DeepError(raw["code"])
            return json.loads(json.dumps(raw))

        monkeypatch.setattr(DeepClient, "_request", request_locally)
    else:
        rpc = UnixControllerServer(settings.deep_controller_socket, controller, request_timeout=180)
        rpc.start()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    api_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(settings, auth_store=api), log_level="warning"))
    api_thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    api_thread.start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        web_port = probe.getsockname()[1]
    web = f"http://127.0.0.1:{web_port}"
    log = (tmp_path / "web.log").open("w+")
    request.node.web_log = tmp_path / "web.log"
    vite = subprocess.Popen(
        ["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(web_port), "--strictPort"],
        cwd=ROOT / "apps/web",
        env={**os.environ, "PPAP_PREVIEW": "0", "PPAP_PUBLIC_API_URL": api_url},
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    stop, pause, paused, errors, jobs = threading.Event(), threading.Event(), threading.Event(), [], []

    def work():
        while not stop.wait(0.03):
            if pause.is_set():
                paused.set()
                continue
            paused.clear()
            try:
                job = worker.claim_job("checker-browser")
                if job:
                    jobs.append(job.kind)
                    result = process_claimed(worker, job, "checker-browser", rule_ai=DeterministicMetadata())
                    if isinstance(result, dict) and result.get("error_code"):
                        print("Worker failure:", result, flush=True)
            except Exception as error:
                errors.append(repr(error))
                return

    worker_thread = threading.Thread(target=work, daemon=True)
    try:
        until = time.monotonic() + 45
        while True:
            try:
                if httpx.get(web + "/runtime-config.json", timeout=1).status_code == 200 and server.started:
                    break
            except httpx.HTTPError:
                pass
            assert vite.poll() is None, "Native Web did not start: " + request.node.web_log.read_text(errors="replace")[-2000:]
            assert time.monotonic() < until, "Native product startup timed out"
            time.sleep(0.1)
        worker_thread.start()
        yield SimpleNamespace(
            web=web,
            admin=admin,
            alice=alice,
            bob=bob,
            base=base,
            rule_id=rule_id,
            rules=rules,
            guest=guest,
            jobs=jobs,
            pause=pause,
            paused=paused,
            errors=errors,
            session=admin.create_session(alice),
            bob_session=admin.create_session(bob),
            suites=suites,
        )
    finally:
        stop.set()
        if worker_thread.ident:
            worker_thread.join(timeout=180)
        server.should_exit = True
        api_thread.join(timeout=15)
        if rpc is not None:
            rpc.close()
        try:
            os.killpg(vite.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            vite.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(vite.pid, signal.SIGKILL)
            vite.wait(timeout=5)
        listener.close()
        log.close()
        assert not errors, errors


def response_json(response, status=200):
    assert response.status == status, response.text()
    return response.json()


def read_until(page, url, predicate, timeout=40):
    until = time.monotonic() + timeout
    while True:
        result = response_json(page.request.get(url))
        assert result.get("execution") != "failed", json.dumps(result, ensure_ascii=False)
        if predicate(result):
            return result
        assert time.monotonic() < until, result
        page.wait_for_timeout(100)


def create_round(page, f, value, index):
    raw = workbook(value, index)
    uploaded = response_json(
        page.request.post(
            f.web + "/api/files", data={"original_filename": f"阈值-{index}.xlsx", "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "byte_size": len(raw)}
        ),
        201,
    )
    path = f"/api/files/{uploaded['file_id']}/versions/{uploaded['id']}"
    response_json(page.request.put(f.web + path + "/blob", data=raw, headers={"Content-Type": "application/octet-stream"}))
    uploaded = response_json(page.request.post(f.web + path + "/finalize"))
    validation = response_json(
        page.request.post(f.web + "/api/file-validations", data={"request_id": str(uuid4()), "source_file_id": uploaded["file_id"], "source_file_version_id": uploaded["id"]}), 202
    )
    validated = read_until(page, f.web + "/api/file-validations/" + validation["id"], lambda r: r["status"] in {"succeeded", "failed"})
    assert validated["status"] == "succeeded", validated
    task = response_json(
        page.request.post(
            f.web + "/api/audit-tasks",
            data={
                "request_id": str(uuid4()),
                "analysis_mode": "standard",
                "customer_preference": None,
                "files": [{"file_version_id": uploaded["id"], "validation_id": validation["id"], "selection": "selected", "removal_reason": None}],
            },
        ),
        202,
    )
    task = read_until(page, f.web + "/api/audit-tasks/" + task["id"], lambda r: r["state"] == "pending_confirm")
    assert task["files"][0]["match"]["state"] == "matched", task
    accepted = response_json(
        page.request.post(
            f.web + f"/api/audit-tasks/{task['id']}/confirm",
            data={"request_id": str(uuid4()), "expected_revision": task["input_revision"], "expected_confirmation_basis_digest": task["confirmation_basis_digest"]},
        ),
        201,
    )
    completed = read_until(page, f.web + f"/api/rounds/{accepted['round_id']}", lambda r: r["execution"] == "completed")
    assert len(completed["findings"]) == len(f.rules["rules"]), completed
    return completed, uploaded


def answer(page, f, round_, expected):
    from playwright.sync_api import expect

    page.goto(f.web + f"/audit?round={round_['id']}")
    page.get_by_role("button", name="这条判错了 R1", exact=True).focus()
    page.keyboard.press("Enter")
    page.get_by_label("正确结论", exact=True).select_option(expected)
    page.get_by_text("添加我在原件中找到的依据", exact=True).click()
    page.get_by_label("工作表名称", exact=True).fill("Sheet")
    page.get_by_label("单元格（例如 B12）", exact=True).fill("A1")
    page.get_by_role("button", name="加入这处依据", exact=True).click()
    page.get_by_label("判断理由类别（用于建议分组）", exact=True).select_option("rule_application")
    page.get_by_label("为什么应当这样判断", exact=True).fill("我已核对原件 A1 与大于等于 10 的要求。")
    page.get_by_role("button", name="加入待修复", exact=True).click()
    expect(page.get_by_text("已加入待修复。尚未调用模型，也没有改写本轮结果。", exact=True)).to_be_visible()


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 1000}, {"width": 390, "height": 844}], ids=["desktop", "mobile"])
def test_feedback_optimize_verify_publish_reaudit_and_rollback(workspace_product, viewport, observe_browser_assets):
    from playwright.sync_api import expect, sync_playwright

    f = workspace_product

    with sync_playwright() as playwright:
        executable = os.environ.get("PPAP_BROWSER_EXECUTABLE")
        browser = playwright.chromium.launch(**({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport=viewport)
        context.add_cookies([{"name": SESSION_COOKIE, "value": f.session, "url": f.web, "httpOnly": True}])
        page = context.new_page()
        startup_diagnostics = observe_browser_assets(page, f.web)
        page.set_default_timeout(20000)
        writes = []
        page.on(
            "request",
            lambda request: writes.append((request.method, request.url, request.post_data_json))
            if request.method in {"POST", "PUT"} and any(p in request.url for p in ("checker-feedback", "/optimizations", "/active"))
            else None,
        )
        try:
            rounds = []
            for index, (value, actual, expected) in enumerate([(5, "PASS", "NG"), (15, "NG", "PASS"), (16, "UNABLE", "PASS"), (6, "NG", "NG")], 1):
                round_, uploaded = create_round(page, f, value, index)
                assert round_["findings"][0]["status"] == actual
                rounds.append((round_, uploaded))
                answer(page, f, round_, expected)
            original, uploaded = rounds[0]
            page.goto(f.web + f"/audit?round={original['id']}&file={uploaded['id']}&clause=R1&basis=1&checkerFeedbackClause=R1")
            expect(page.get_by_role("button", name="让 AI 修复并验证", exact=True)).to_be_enabled()
            page.get_by_role("button", name="选择全部已确认样本", exact=True).click()
            # Save-only neither executes nor manufactures a validation proof.
            page.get_by_role("button", name="仅保存待发布修改", exact=True).click()
            expect(page.get_by_role("heading", name="已保存，尚未验证", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="发布此修改", exact=True)).to_be_enabled()
            assert f.guest.adaptations == 0
            page.get_by_role("button", name="让 AI 修复并验证", exact=True).click()
            expect(page.get_by_text("尚未配置。普通审核已有的检查不需要生成代码。", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="确认开始修复", exact=True)).to_be_disabled()
            page.get_by_label("Base URL", exact=True).fill("https://models.example.test")
            page.get_by_label("模型名称", exact=True).fill("test-model")
            page.get_by_label("API Key", exact=True).fill("synthetic-workspace-key-never-sent-to-provider")
            page.get_by_role("button", name="保存模型配置", exact=True).click()
            expect(page.get_by_text("已配置，仅供你发起的模型操作使用。", exact=True)).to_be_visible()
            consent = page.get_by_label("同意将本次资料发送至 https://models.example.test，使用 test-model 修复并验证这 4 条反馈", exact=True)
            consent.check()
            f.pause.set()
            assert f.paused.wait(5), "Worker did not pause before admission"
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/optimizations")) as started:
                page.get_by_role("button", name="确认开始修复", exact=True).click()
            operation = response_json(started.value)
            assert operation["state"] == "queued"
            operation_id = operation["operation_id"]
            page.reload()
            expect(page.get_by_role("heading", name="正在修复与验证", exact=True)).to_be_visible()
            f.pause.clear()
            completed = read_until(page, f.web + "/api/checker-optimizations/" + operation_id, lambda r: r["state"] not in {"queued", "running"}, timeout=90)
            assert completed["state"] == "completed", completed
            assert completed["report"]["validation"]["verified"], completed
            assert completed["report"]["validation"]["total_samples"] == 4
            assert completed["usage"]["source"] == "controlled"
            assert f.guest.adaptations == 1
            expect(page.get_by_role("heading", name="修复结果", exact=True)).to_be_visible(timeout=15000)
            expect(page.get_by_text("修好 3 条 · 新增错误 0 条 · 无法判断 0 条 · 技术失败 0 条", exact=True)).to_be_visible()
            candidate = completed["candidate_revision_id"]
            page.get_by_text("查看修改摘要与高级内容", exact=True).click()
            if viewport["width"] < 640:
                expect(page.get_by_text("手机上仅展示摘要，可在电脑查看完整规则和代码。", exact=True)).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth") <= viewport["width"]
            # Re-login in a clean browser storage reads the same durable receipt.
            address = page.url
            context.close()
            context = browser.new_context(viewport=viewport)
            context.add_cookies([{"name": SESSION_COOKIE, "value": f.admin.create_session(f.alice), "url": f.web, "httpOnly": True}])
            page = context.new_page()
            startup_diagnostics = observe_browser_assets(page, f.web)
            page.set_default_timeout(20000)
            page.goto(f.web + f"/audit?round={original['id']}&file={uploaded['id']}&clause=R1&basis=1&checkerFeedbackClause=R1")
            expect(page.get_by_role("heading", name="修复结果", exact=True)).to_be_visible()
            assert len([r for r in writes if r[1].endswith("/optimizations")]) == 2
            assert f.guest.adaptations == 1
            # Authorized replay returns one candidate, not a second model job.
            start_write = [r for r in writes if r[1].endswith("/optimizations")][-1]
            assert response_json(page.request.post(start_write[1], data=start_write[2]))["candidate_revision_id"] == candidate
            page.get_by_role("button", name="发布此修改", exact=True).click()
            expect(page.get_by_role("group", name="确认采用检查版本")).to_contain_text("已有报告不变")
            expect(page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True)).to_have_count(0)
            page.get_by_role("button", name="确认发布", exact=True).click()
            expect(page.get_by_text("发布操作已完成", exact=True)).to_be_visible()
            active = response_json(page.request.get(f.web + "/api/suites/browser:threshold/active"))
            assert active["current_revision_id"] == candidate and not active["force_unverified"]
            page.get_by_role("button", name="用新版本重审这份文件", exact=True).click()
            expect(page.get_by_text("新建关联重审", exact=True)).to_be_visible()
            page.get_by_role("button", name="开始识别", exact=True).click()
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_enabled(timeout=30000)
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/confirm")) as confirmed:
                page.get_by_role("button", name="开始审核", exact=True).click()
            task = response_json(confirmed.value, 201)
            newer = read_until(page, f.web + "/api/rounds/" + task["round_id"], lambda r: r["execution"] == "completed")
            assert newer["id"] != original["id"] and newer["predecessor_id"] == original["id"]
            assert newer["findings"][0]["status"] == "NG"
            assert f.suites.read_snapshot(newer["id"], f.alice.tenant_id).targets[0].suite_revision_id == candidate
            unchanged = response_json(page.request.get(f.web + "/api/rounds/" + original["id"]))
            assert unchanged["findings"] == original["findings"]
            assert f.suites.read_snapshot(original["id"], f.alice.tenant_id).targets[0].suite_revision_id == f.base.id
            page.goto(address)
            expect(page.get_by_role("button", name="用新版本重审这份文件", exact=True)).to_be_visible()
            page.get_by_text("版本记录与受影响审核", exact=True).click()
            page.get_by_role("button", name="查看此版本／回滚", exact=True).last.click()
            page.get_by_role("button", name="回滚到此版本", exact=True).click()
            expect(page.get_by_role("button", name="确认回滚", exact=True)).to_be_disabled()
            page.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True).check()
            page.get_by_role("button", name="确认回滚", exact=True).click()
            expect(page.get_by_text("发布操作已完成", exact=True)).to_be_visible()
            assert response_json(page.request.get(f.web + "/api/suites/browser:threshold/active"))["current_revision_id"] == f.base.id
            assert response_json(page.request.get(f.web + "/api/rounds/" + newer["id"]))["findings"] == newer["findings"]
            assert f.jobs.count("checker_optimization") == 1
            assert f.guest.adaptations == 1
            assert not f.errors
        except BaseException:
            try:
                print("Browser asset diagnostics:", json.dumps(startup_diagnostics()), flush=True)
            except Exception:
                print("Browser asset diagnostics unavailable", flush=True)
            try:
                document = page.evaluate(
                    "() => ({readyState: document.readyState, appChildren: document.getElementById('app')?.childElementCount ?? null, bodyTextLength: document.body.innerText.length})"
                )
                print("Browser document diagnostics:", json.dumps(document), flush=True)
            except Exception:
                print("Browser document diagnostics unavailable", flush=True)
            try:
                print(page.locator("body").aria_snapshot(timeout=1000)[:8000], flush=True)
            except Exception:
                print("Browser ARIA diagnostics unavailable", flush=True)
            raise
        finally:
            f.pause.clear()
            context.close()
            browser.close()


def test_workspace_http_receipts_use_real_trials_and_frozen_reaudit(workspace_product):
    """The same stack also runs without a browser process, never seeded findings."""
    from playwright.sync_api import sync_playwright

    f = workspace_product
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=f.web, extra_http_headers={"Cookie": SESSION_COOKIE + "=" + f.session})
        page = SimpleNamespace(request=request, wait_for_timeout=lambda ms: time.sleep(ms / 1000))
        try:
            feedback, rounds = [], []
            for index, (value, actual, expected) in enumerate([(5, "PASS", "NG"), (15, "NG", "PASS"), (16, "UNABLE", "PASS"), (6, "NG", "NG")], 1):
                round_, uploaded = create_round(page, f, value, index)
                assert round_["findings"][0]["status"] == actual, round_
                if index == 1:
                    round_ = response_json(
                        request.put(
                            f.web + f"/api/rounds/{round_['id']}/reviews",
                            data={
                                "item_key": round_["findings"][0]["item_key"],
                                "judgment": "需修改",
                                "rationale": "人工原件复核独立保存",
                                "next_step": "修复检查",
                                "version": 0,
                            },
                        )
                    )
                    assert round_["findings"][0]["review"] is not None
                rounds.append((round_, uploaded))
                payload = {
                    "operation_id": str(uuid4()),
                    "file_version_id": uploaded["id"],
                    "rule_version_id": f.rule_id,
                    "rule_clause_id": "R1",
                    "result_attempt": round_["result_attempt"],
                    "original_status": actual,
                    "expected_status": expected,
                    "rationale": "用户已核对 A1 与阈值要求。",
                    "reason_category": "rule_application",
                    "evidence": [{"file_version_id": uploaded["id"], "locator": {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}, "excerpt": str(value)}],
                }
                url = f.web + f"/api/audit-rounds/{round_['id']}/checker-feedback"
                saved = response_json(request.post(url, data=payload))
                assert response_json(request.post(url, data=payload)) == saved
                feedback.append(saved)
            sample_path = f.web + f"/api/suite-revisions/{f.base.id}/checker-feedback"
            grouped = response_json(request.get(sample_path))
            assert grouped["groups"][0]["feedback_ids"] == [feedback[1]["id"], feedback[3]["id"]]
            first_page = response_json(request.get(sample_path + "?limit=2"))
            second_page = response_json(request.get(sample_path + f"?limit=2&after={first_page['next_cursor']}"))
            assert [row["id"] for row in first_page["feedback"] + second_page["feedback"]] == [row["id"] for row in feedback]
            history_path = f.web + f"/api/suite-revisions/{f.base.id}/affected-rounds?clause_id=R1&limit=1&exclude_round_id={rounds[0][0]['id']}"
            affected, cursor, scanned = [], None, 0
            while True:
                history = response_json(request.get(history_path + (f"&after={cursor}" if cursor else "")))
                affected.extend(history["items"])
                scanned += history["scanned_rounds"]
                cursor = history["next_cursor"]
                if cursor is None:
                    break
            assert scanned == 4 and {row["round_id"] for row in affected} == {item[0]["id"] for item in rounds[1:]}
            assert f.guest.adaptations == 0
            path = f.web + f"/api/suite-revisions/{f.base.id}/optimizations"
            saved = response_json(request.post(path, data={"operation_id": str(uuid4()), "feedback_ids": [row["id"] for row in feedback], "action": "save_only", "consume_model": False}))
            assert saved["state"] == "saved" and saved["report"] == {"verified": False, "reasons": ["tests_not_run"]}
            assert f.guest.adaptations == 0
            path = f.web + f"/api/suite-revisions/{saved['candidate_revision_id']}/optimizations"
            command = {"operation_id": str(uuid4()), "feedback_ids": [row["id"] for row in feedback], "action": "run_tests", "consume_model": True}
            missing_key = request.post(path, data=command)
            assert missing_key.status == 409 and missing_key.json() == {"error": "personal_model_unavailable"}
            assert f.guest.adaptations == 0 and "checker_optimization" not in f.jobs
            response_json(
                request.put(f.web + "/api/me/model-config", data={"api_key": "synthetic-workspace-key-never-sent-to-provider", "base_url": "https://models.example.test", "model": "test-model"})
            )
            f.pause.set()
            assert f.paused.wait(5), "Worker did not pause before admission"
            accepted = response_json(request.post(path, data=command))
            assert accepted["state"] == "queued"
            assert response_json(request.post(path, data=command))["operation_id"] == command["operation_id"]
            f.pause.clear()
            completed = read_until(page, f.web + "/api/checker-optimizations/" + command["operation_id"], lambda r: r["state"] not in {"queued", "running"}, timeout=90)
            assert completed["state"] == "completed", completed
            report = completed["report"]["validation"]
            assert report["verified"] and report["total_samples"] == report["passed_samples"] == 4, completed
            assert [case["split"] for case in report["cases"]].count("holdout") == 1
            assert f.guest.adaptations == 1 and f.jobs.count("checker_optimization") == 1
            candidate = completed["candidate_revision_id"]
            assert response_json(request.post(path, data=command))["candidate_revision_id"] == candidate
            active_path = f.web + "/api/suites/browser:threshold/active"
            active = response_json(request.get(active_path))
            assert active["current_revision_id"] == f.base.id
            adoption = {"revision_id": candidate, "expected_revision": active["revision"], "operation_id": str(uuid4()), "force_unverified": False}
            published = response_json(request.put(active_path, data=adoption))
            assert response_json(request.put(active_path, data=adoption)) == published
            original, uploaded = rounds[0]
            # A published wording update is a new development basis, not adoption.
            updated = response_json(
                request.post(
                    f.web + "/api/rules",
                    data={
                        "request_id": str(uuid4()),
                        "title": "新版阈值规则",
                        "predecessor_id": f.rule_id,
                        "source_file_version_id": uploaded["id"],
                        "body": {**RULES, "rules": [{**RULES["rules"][0], "rule_to_check": "A1 的数值不得小于 10"}]},
                    },
                ),
                201,
            )
            active = response_json(request.get(active_path))
            assert active["current_revision_id"] == candidate and active["verification_status"] == "stale"
            assert f.guest.adaptations == 1 and f.jobs.count("checker_optimization") == 1
            new_basis = updated["body"]["suite_revision_id"]
            confirmed = {
                "operation_id": str(uuid4()),
                "feedback_ids": [row["id"] for row in feedback],
                "action": "run_tests",
                "consume_model": False,
                "confirmed_rule_revision_id": new_basis,
                "expected_feedback_revisions": {row["id"]: row["revision"] for row in feedback},
            }
            response_json(request.post(f.web + f"/api/suite-revisions/{new_basis}/optimizations", data=confirmed))
            wording = read_until(page, f.web + "/api/checker-optimizations/" + confirmed["operation_id"], lambda r: r["state"] not in {"queued", "running"}, timeout=90)
            assert wording["state"] == "completed" and wording["report"]["validation"]["verified"], wording
            assert wording["report"]["code_changed"] is False and wording["report"]["validation"]["total_samples"] == 4
            assert f.guest.adaptations == 1
            assert response_json(request.get(active_path))["current_revision_id"] == candidate
            candidate = wording["candidate_revision_id"]
            published = response_json(request.put(active_path, data={"revision_id": candidate, "expected_revision": active["revision"], "operation_id": str(uuid4()), "force_unverified": False}))
            assert response_json(request.get(active_path))["rule_update"] is None
            prepared = response_json(request.get(f.web + f"/api/rounds/{original['id']}/reaudit-inputs"))
            source = prepared["files"][0]
            task = response_json(
                request.post(
                    f.web + "/api/audit-tasks",
                    data={
                        "request_id": str(uuid4()),
                        "predecessor_id": original["id"],
                        "analysis_mode": "standard",
                        "customer_preference": prepared["customer_preference"],
                        "files": [
                            {
                                "file_version_id": uploaded["id"],
                                "validation_id": source["validation_id"],
                                "selection": "selected",
                                "removal_reason": None,
                                "preferred_rule_version_id": source["preferred_rule_version_id"],
                            }
                        ],
                    },
                ),
                202,
            )
            task = read_until(page, f.web + "/api/audit-tasks/" + task["id"], lambda r: r["state"] == "pending_confirm")
            accepted = response_json(
                request.post(
                    f.web + f"/api/audit-tasks/{task['id']}/confirm",
                    data={
                        "request_id": str(uuid4()),
                        "expected_revision": task["input_revision"],
                        "expected_confirmation_basis_digest": task["confirmation_basis_digest"],
                    },
                ),
                201,
            )
            newer = read_until(page, f.web + "/api/rounds/" + accepted["round_id"], lambda r: r["execution"] == "completed")
            assert newer["predecessor_id"] == original["id"] and newer["findings"][0]["status"] == "NG"
            assert f.suites.read_snapshot(newer["id"], f.alice.tenant_id).targets[0].suite_revision_id == candidate
            assert response_json(request.get(f.web + "/api/rounds/" + original["id"]))["findings"] == original["findings"]
            rollback = {"revision_id": f.base.id, "expected_revision": published["revision"], "operation_id": str(uuid4()), "force_unverified": False}
            assert request.put(active_path, data=rollback).status == 409
            rollback["force_unverified"] = True
            response_json(request.put(active_path, data=rollback))
            assert response_json(request.get(active_path))["current_revision_id"] == f.base.id
            assert response_json(request.get(f.web + "/api/rounds/" + newer["id"]))["findings"] == newer["findings"]
            assert newer["findings"][0]["review"] is None
            # Editing a confirmed answer invalidates the old proof, never history.
            sample = feedback[0]
            edit_url = f.web + f"/api/audit-rounds/{sample['round_id']}/checker-feedback/{sample['id']}"
            edited = response_json(
                request.put(edit_url, data={"expected_revision": sample["revision"], "expected_status": "PASS", "rationale": "用户重新核对后修改答案", "evidence": sample["evidence"]})
            )
            assert edited["revision"] == sample["revision"] + 1
            assert request.put(edit_url, data={"expected_revision": sample["revision"], "expected_status": "NG", "rationale": "过期写入必须拒绝", "evidence": sample["evidence"]}).status == 409
            detail = response_json(request.get(f.web + f"/api/suite-revisions/{candidate}"))
            assert detail["validation"]["status"] == "stale"
            latest = response_json(request.get(active_path))
            assert request.put(active_path, data={"revision_id": candidate, "expected_revision": latest["revision"], "operation_id": str(uuid4()), "force_unverified": False}).status == 409
            assert response_json(request.get(f.web + "/api/rounds/" + original["id"]))["findings"] == original["findings"]
            stranger = playwright.request.new_context(base_url=f.web, extra_http_headers={"Cookie": SESSION_COOKIE + "=" + f.bob_session})
            try:
                assert stranger.get(f.web + "/api/checker-optimizations/" + command["operation_id"]).status == 404
                assert stranger.get(f.web + "/api/rounds/" + original["id"]).status == 403
                assert not response_json(stranger.get(f.web + "/api/me/model-config"))["configured"]
            finally:
                stranger.dispose()
        finally:
            f.pause.clear()
            request.dispose()


def test_grouped_feedback_selection_and_read_only_history(workspace_product):
    """Two real results can be grouped, split, and opened without paid dispatch."""
    from playwright.sync_api import expect, sync_playwright

    f = workspace_product
    with sync_playwright() as playwright:
        executable = os.environ.get("PPAP_BROWSER_EXECUTABLE")
        browser = playwright.chromium.launch(**({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_cookies([{"name": SESSION_COOKIE, "value": f.session, "url": f.web, "httpOnly": True}])
        page = context.new_page()
        page.set_default_timeout(20000)
        try:
            rounds = []
            for index in (1, 2):
                round_, uploaded = create_round(page, f, 5, index)
                rounds.append((round_, uploaded))
                answer(page, f, round_, "NG")
            first, uploaded = rounds[0]
            page.goto(f.web + f"/audit?round={first['id']}&file={uploaded['id']}&clause=R1&basis=1&checkerFeedbackClause=R1")
            expect(page.get_by_text("这 2 条可能是同一个原因：条款 R1 · Sheet · A", exact=True)).to_be_visible()
            page.get_by_role("button", name="选择这组一起修复", exact=True).click()
            page.get_by_label("本次修复条款 R1 的样本 2", exact=True).uncheck()
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/optimizations")) as started:
                page.get_by_role("button", name="仅保存待发布修改", exact=True).click()
            saved = response_json(started.value)
            selected = started.value.request.post_data_json["feedback_ids"]
            assert len(selected) == 1 and saved["state"] == "saved"
            samples = response_json(page.request.get(f.web + f"/api/suite-revisions/{f.base.id}/checker-feedback"))
            assert selected == [next(row["id"] for row in samples["feedback"] if row["ordinal"] == 1)]
            with f.admin._conn() as conn:
                frozen = conn.execute("SELECT frozen FROM product.checker_optimizations WHERE operation_id=%s", (saved["operation_id"],)).fetchone()[0]
                assert frozen["selected_feedback_ids"] == selected and len(frozen["samples"]) == 2
            page.get_by_role("button", name="查看同一条款的历史结果", exact=True).click()
            expect(page.get_by_text("本页找到 1 轮可能受影响；已检查 2 轮历史，不代表全部历史。", exact=True)).to_be_visible()
            history = page.get_by_role("region", name="可能受影响的历史结果")
            expect(history.get_by_role("link", name="查看结果并选择重审", exact=False)).to_have_attribute("href", f"/audit?round={rounds[1][0]['id']}&file={rounds[1][1]['id']}&clause=R1&basis=1")
            assert f.guest.adaptations == 0
            with f.admin._conn() as conn:
                assert conn.execute("SELECT count(*) FROM product.audit_rounds WHERE tenant_id=%s", (f.alice.tenant_id,)).fetchone()[0] == 2
        finally:
            context.close()
            browser.close()


def test_rule_wording_update_preserves_adoption_until_confirmed_browser_publication(workspace_product):
    """A wording-only update runs all real samples, then explicitly adopts unchanged code."""
    from playwright.sync_api import expect, sync_playwright

    f = workspace_product
    releases = SuiteReleaseStore(f.admin.settings, f.admin.objects, role="api")
    corrected = releases.save_candidate(f.alice, f.base.id, {"checker.py": runpy.run_path(str(GUEST))["CORRECTED"]})
    releases.publish(f.alice, RULES["suite_key"], corrected["id"], 1, str(uuid4()), force_unverified=True)
    with sync_playwright() as playwright:
        executable = os.environ.get("PPAP_BROWSER_EXECUTABLE")
        browser = playwright.chromium.launch(**({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_cookies([{"name": SESSION_COOKIE, "value": f.session, "url": f.web, "httpOnly": True}])
        page = context.new_page()
        page.set_default_timeout(20000)
        try:
            rounds = []
            for index, (value, expected) in enumerate([(5, "NG"), (15, "PASS"), (16, "PASS"), (6, "NG")], 1):
                round_, uploaded = create_round(page, f, value, index)
                rounds.append((round_, uploaded))
                response_json(
                    page.request.post(
                        f.web + f"/api/audit-rounds/{round_['id']}/checker-feedback",
                        data={
                            "operation_id": str(uuid4()),
                            "file_version_id": uploaded["id"],
                            "rule_version_id": f.rule_id,
                            "rule_clause_id": "R1",
                            "result_attempt": round_["result_attempt"],
                            "original_status": expected,
                            "expected_status": expected,
                            "rationale": "用户核对 A1 与阈值要求。",
                            "evidence": [{"file_version_id": uploaded["id"], "locator": {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}, "excerpt": str(value)}],
                        },
                    )
                )
            original, uploaded = rounds[0]
            body = {**RULES, "rules": [{**RULES["rules"][0], "rule_to_check": "A1 的数值不得小于 10"}]}
            updated = response_json(
                page.request.post(
                    f.web + "/api/rules",
                    data={
                        "request_id": str(uuid4()),
                        "title": "新版阈值规则",
                        "body": body,
                        "predecessor_id": f.rule_id,
                        "source_file_version_id": uploaded["id"],
                    },
                ),
                201,
            )
            active_path = f.web + "/api/suites/browser:threshold/active"
            active = response_json(page.request.get(active_path))
            assert active["current_revision_id"] == corrected["id"]
            assert active["verification_status"] == "stale" and active["rule_update"]["changed"]
            assert f.guest.adaptations == 0 and "checker_optimization" not in f.jobs
            page.goto(f.web + f"/audit?round={original['id']}&file={uploaded['id']}&clause=R1&basis=1&checkerFeedbackClause=R1")
            expect(page.get_by_text("规则已更新，需要重新验证", exact=True)).to_be_visible()
            page.get_by_role("button", name="按新版规则起草修改", exact=True).click()
            expect(page.get_by_role("button", name="确认开始修复", exact=True)).to_be_disabled()
            assert f.guest.adaptations == 0
            page.get_by_label("我已核对当前所选规则及全部样本，确认以上正确答案仍适用于当前规则", exact=True).check()
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/optimizations")) as started:
                page.get_by_role("button", name="只运行现有检查验证", exact=True).click()
            operation = response_json(started.value)
            completed = read_until(page, f.web + "/api/checker-optimizations/" + operation["operation_id"], lambda r: r["state"] not in {"queued", "running"}, timeout=90)
            assert completed["state"] == "completed", completed
            assert completed["report"]["validation"]["verified"]
            assert completed["report"]["validation"]["total_samples"] == 4
            assert completed["report"]["code_changed"] is False and f.guest.adaptations == 0
            expect(page.get_by_text("代码无需修改，样本已通过。请核对后确认发布，才能采用新规则。", exact=True)).to_be_visible()
            assert response_json(page.request.get(active_path))["current_revision_id"] == corrected["id"]
            page.get_by_role("button", name="发布此修改", exact=True).click()
            page.get_by_role("button", name="确认发布", exact=True).click()
            expect(page.get_by_text("发布操作已完成", exact=True)).to_be_visible()
            adopted = response_json(page.request.get(active_path))
            assert adopted["current_revision_id"] == completed["candidate_revision_id"] and adopted["rule_update"] is None
            assert adopted["verification_status"] == "verified"
            assert response_json(page.request.get(f.web + "/api/rounds/" + original["id"])) == original
            assert updated["body"]["suite_revision_id"] != adopted["current_revision_id"]
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("workspace_product", [2], indirect=True)
def test_multi_clause_wording_reconfirmation_verifies_four_independent_documents(workspace_product, monkeypatch):
    """Two labels per document are one case, including a stable fourth-document holdout."""
    from playwright.sync_api import sync_playwright

    f = workspace_product
    from worker import checker_optimizer

    requests = []
    materialize = checker_optimizer.materialize_request

    def observed_materialize(*args):
        result = materialize(*args)
        requests.append(result[0])
        return result

    monkeypatch.setattr(checker_optimizer, "materialize_request", observed_materialize)
    releases = SuiteReleaseStore(f.admin.settings, f.admin.objects, role="api")
    corrected = releases.save_candidate(f.alice, f.base.id, {"checker.py": runpy.run_path(str(GUEST))["CORRECTED"]})
    releases.publish(f.alice, RULES["suite_key"], corrected["id"], 1, str(uuid4()), force_unverified=True)
    with sync_playwright() as playwright:
        request = playwright.request.new_context(base_url=f.web, extra_http_headers={"Cookie": SESSION_COOKIE + "=" + f.session})
        page = SimpleNamespace(request=request, wait_for_timeout=lambda ms: time.sleep(ms / 1000))
        try:
            feedback, originals = [], []
            for index, (value, expected) in enumerate([(5, "NG"), (15, "PASS"), (16, "PASS"), (6, "NG")], 1):
                round_, uploaded = create_round(page, f, value, index)
                originals.append(round_)
                for clause in f.rules["rules"]:
                    saved = response_json(
                        request.post(
                            f.web + f"/api/audit-rounds/{round_['id']}/checker-feedback",
                            data={
                                "operation_id": str(uuid4()),
                                "file_version_id": uploaded["id"],
                                "rule_version_id": f.rule_id,
                                "rule_clause_id": clause["id"],
                                "result_attempt": round_["result_attempt"],
                                "original_status": expected,
                                "expected_status": expected,
                                "rationale": f"用户独立核对 {clause['id']} 与 A1。",
                                "evidence": [{"file_version_id": uploaded["id"], "locator": {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}, "excerpt": str(value)}],
                            },
                        )
                    )
                    assert saved["case_ordinal"] == index
                    feedback.append(saved)
            updated = response_json(
                request.post(
                    f.web + "/api/rules",
                    data={
                        "request_id": str(uuid4()),
                        "title": "仅 R2 措辞更新",
                        "predecessor_id": f.rule_id,
                        "source_file_version_id": uploaded["id"],
                        "body": {**f.rules, "rules": [f.rules["rules"][0], {**f.rules["rules"][1], "rule_to_check": "A1 的数值不得小于 10"}]},
                    },
                ),
                201,
            )
            revision = updated["body"]["suite_revision_id"]
            command = {
                "operation_id": str(uuid4()),
                "feedback_ids": [feedback[1]["id"]],
                "action": "run_tests",
                "consume_model": False,
                "confirmed_rule_revision_id": revision,
                "expected_feedback_revisions": {row["id"]: row["revision"] for row in feedback},
            }
            response_json(request.post(f.web + f"/api/suite-revisions/{revision}/optimizations", data=command))
            result = read_until(page, f.web + "/api/checker-optimizations/" + command["operation_id"], lambda row: row["state"] not in {"queued", "running"}, timeout=90)
            assert result["state"] == "completed" and result["report"]["validation"]["verified"], result
            report = result["report"]["validation"]
            assert report["total_samples"] == report["passed_samples"] == 4
            assert [row["split"] for row in report["cases"]] == ["training", "training", "training", "holdout"]
            assert all({clause["clause_id"] for clause in row["clauses"]} == {"R1", "R2"} and len(row["feedback_ids"]) == 2 for row in report["cases"])
            assert result["report"]["code_changed"] is False and f.guest.adaptations == 0
            assert len(requests) == 1
            assert requests[0].feedback == "用户独立核对 R2 与 A1。"
            assert requests[0].oracle[0].repair_clause_ids == frozenset({"R2"})
            assert all(not sample.repair_clause_ids for sample in requests[0].oracle[1:])
            assert all(set(sample.expected) == {"R1", "R2"} for sample in requests[0].oracle)
            active_path = f.web + "/api/suites/browser:threshold/active"
            active = response_json(request.get(active_path))
            assert active["current_revision_id"] == corrected["id"]
            response_json(
                request.put(
                    active_path,
                    data={
                        "revision_id": result["candidate_revision_id"],
                        "expected_revision": active["revision"],
                        "operation_id": str(uuid4()),
                        "force_unverified": False,
                    },
                )
            )
            assert response_json(request.get(active_path))["verification_status"] == "verified"
            assert all(response_json(request.get(f.web + "/api/rounds/" + original["id"])) == original for original in originals)
        finally:
            request.dispose()


def test_first_uaes_adoption_from_rule_library_freezes_dependencies(workspace_product):
    """A fresh UAES tenant adopts real source through Web before its first Round."""
    from audit.standard_suites import standard_suites
    from playwright.sync_api import expect, sync_playwright

    f = workspace_product
    ordered = ("UAES:U12", "UAES:U16", "UAES:U07", "UAES:U08")
    baselines = {suite.suite_key: suite for suite in standard_suites() if suite.suite_key in ordered}
    revisions = {
        key: f.suites.create_revision(tenant_id=None, suite_key=key, files=dict(baselines[key].files), created_by=None, related_suite_keys=baselines[key].related_suite_keys) for key in ordered
    }
    # Keep the first accepted job queued: this test proves adoption and freezing,
    # not a model-backed business verdict or S3/container isolation.
    f.pause.set()
    assert f.paused.wait(5)
    with sync_playwright() as playwright:
        executable = os.environ.get("PPAP_BROWSER_EXECUTABLE")
        browser = playwright.chromium.launch(**({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_cookies([{"name": SESSION_COOKIE, "value": f.session, "url": f.web, "httpOnly": True}])
        page = context.new_page()
        page.set_default_timeout(20000)
        try:
            page.goto(f.web + "/requirements?checkerSuite=UAES%3AU08")
            catalog = page.get_by_role("region", name="企业检查版本", exact=True)
            release = catalog.get_by_role("region", name="检查发布与版本记录", exact=True)
            expect(release.get_by_text("先采用关联检查版本", exact=True)).to_be_visible()
            release.get_by_role("button", name="查看 UAES:U07", exact=True).click()
            expect(page).to_have_url(re.compile(r"checkerSuite=UAES%3AU07"))
            release.get_by_role("button", name="查看 UAES:U12", exact=True).click()
            expect(page).to_have_url(re.compile(r"checkerSuite=UAES%3AU12"))
            publications = []
            for key in ordered:
                catalog.get_by_label("选择检查", exact=True).select_option(key)
                expect(release.get_by_text("尚未采用检查版本。发布后，本企业的新审核才会使用此版本。", exact=True)).to_be_visible()
                expect(release.get_by_text(re.compile("官方版本 v1"))).to_be_visible()
                release.get_by_role("button", name="采用此版本", exact=True).click()
                confirmation = release.get_by_role("group", name="确认采用检查版本", exact=True)
                expect(confirmation).to_contain_text("需要重新验证")
                expect(confirmation).to_contain_text("这个版本还没有用样本验证过。")
                confirmation.get_by_label("我已核对上述未验证项和新增错误，仍要启用", exact=True).check()
                with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/active")) as submitted:
                    confirmation.get_by_role("button", name="确认采用", exact=True).click()
                receipt = response_json(submitted.value)
                assert receipt["suite_key"] == key and receipt["revision_id"] == revisions[key].id
                assert receipt["expected_revision"] == 0 and receipt["revision"] == 1
                assert receipt["previous_revision_id"] is None and receipt["force_unverified"] is True
                publications.append(receipt)
                expect(release.get_by_text("发布操作已完成", exact=True)).to_be_visible()
                expect(release.get_by_text("当前已固定采用一个版本，不会自动升级。", exact=True)).to_be_visible()
                page.reload()
                catalog = page.get_by_role("region", name="企业检查版本", exact=True)
                release = catalog.get_by_role("region", name="检查发布与版本记录", exact=True)
                expect(release.get_by_text(re.compile("当前使用"))).to_be_visible()
                active = response_json(page.request.get(f.web + f"/api/suites/{key}/active"))
                assert active["history"] == [receipt]
                assert active["current_revision_id"] == revisions[key].id

            # Another tenant can read official candidates, but this adoption is
            # not an official publication or that tenant's active selection.
            other = f.admin.upsert_feishu_identity("checker-browser-other", "reader")
            other_active = SuiteReleaseStore(f.admin.settings, f.admin.objects, role="api").active(other, "UAES:U08")
            assert other_active["selection_mode"] == "candidate_only" and other_active["current_revision_id"] is None
            with f.admin._conn() as conn:
                assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM product.official_suite_heads").fetchone()[0] == 0
                rule_id = str(uuid4())
                conn.execute(
                    "INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES (%s,%s,'UAES 控制计划',%s)",
                    (rule_id, f.alice.tenant_id, Jsonb({**json.loads(baselines["UAES:U08"].files["rules.json"]), "suite_key": "UAES:U08"})),
                )
            raw = workbook(12, 1)
            staged = f.admin.create_staging(f.alice, "控制计划.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", len(raw))
            uploaded = f.admin.finalize_version(f.admin.put_blob(staged, raw))
            draft = response_json(page.request.post(f.web + "/api/drafts", data={"items": [{"file_version_id": uploaded.id, "role": "target", "rule_version_id": rule_id}]}), 201)
            accepted = response_json(page.request.post(f.web + f"/api/drafts/{draft['id']}/accept", data={"idempotency_key": "first-uaes-adoption"}), 201)
            frozen = f.suites.read_snapshot(accepted["id"], f.alice.tenant_id)
            target = frozen.targets[0]
            assert target.suite_revision_id == revisions["UAES:U08"].id and target.verification == "unverified"
            assert target.related_suite_digests == {key: revisions[key].resolved_digest for key in ("UAES:U07", "UAES:U16")}
            assert len(publications) == 4 and not f.guest.jobs
            page.reload()
            expect(page.get_by_role("region", name="检查发布与版本记录").get_by_text(re.compile("当前使用"))).to_be_visible()
            assert f.suites.read_snapshot(accepted["id"], f.alice.tenant_id).to_dict() == frozen.to_dict()
        finally:
            context.close()
            browser.close()
