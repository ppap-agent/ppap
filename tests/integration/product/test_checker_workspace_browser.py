"""#254: real browser/HTTP/PG/queue/Controller/Core program adoption journey.

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
from agent.execution.guest_model import ModelGrant
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

ROOT = Path(__file__).resolve().parents[3]
GUEST = ROOT / "tests/integration/product/_checker_workspace_guest.py"
BASELINE = runpy.run_path(str(GUEST))["BASELINE"]
RULES = {
    "kind": "engine",
    "type": "控制计划",
    "rule_profile": "v3",
    "suite_key": "custom:browser-threshold",
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

    def __call__(self, operation, **kwargs):
        from documents.ai import AIResult

        if operation == "document_metadata_extraction":
            return AIResult({"customer": "测试客户", "part_number": "P-1"})
        if operation == "document_classification":
            return AIResult({"document_type": "控制计划"})
        raise AssertionError("Unexpected model capability in a native XLSX journey")


class NativeGuest:
    usage_source = "controlled"

    def __init__(self, settings, model_identity=None, *, jobs=None):
        self.settings = settings
        self.model_identity = dict(model_identity) if model_identity is not None else None
        self.jobs = jobs if jobs is not None else []

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
        if job.operation == "execute-audit":
            assert self.model_identity is not None
            manifest = json.loads((inputs / "manifest.json").read_text())
            assert manifest["snapshot"]["model_identity"] == self.model_identity
            (grants / "model.json").write_text(
                json.dumps(
                    {
                        **ModelGrant.fields_for(self.model_identity),
                        "base_url": "http://127.0.0.1:1/v1",
                        "token": "synthetic-unused-workspace-grant-1234567890",
                        "wall_seconds": 60,
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


def workbook(value, index):
    book = Workbook()
    book.active.append([value, "holdout-secret" if index == 4 else f"training-{index}"])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


@pytest.fixture
def workspace_product(auth_store_factory, tmp_path, request, monkeypatch):
    # Only a tenant key set by the journey may serve a model.
    for name in (
        "PPAP_MODEL_TEXT_API_KEY",
        "PPAP_MODEL_TEXT_BASE_URL",
        "PPAP_MODEL_TEXT_MODEL",
        "PPAP_MODEL_VISION_API_KEY",
        "PPAP_MODEL_VISION_BASE_URL",
        "PPAP_MODEL_VISION_MODEL",
        "PPAP_MODEL_CODING_API_KEY",
        "PPAP_MODEL_CODING_BASE_URL",
        "PPAP_MODEL_CODING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("documents.model_access.ISOLATED_DEV_LLM_BASE_URL", "")
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
    # An uploaded rule's program is the tenant's own custom Suite.
    base = suites.create_revision(tenant_id=alice.tenant_id, suite_key=RULES["suite_key"], files={"rules.json": json.dumps(rules).encode(), "checker.py": BASELINE.encode()}, created_by=alice.user_id)
    releases = SuiteReleaseStore(settings, objects, role="api")
    releases.publish(alice, RULES["suite_key"], base.id, 0, str(uuid4()), force_unverified=True)
    rule_id = str(uuid4())
    # An uploaded rule keeps its source file; the rule library lists it by that file.
    staged = admin.create_staging(alice, "阈值规则.json", "application/json", 2)
    source = admin.finalize_version(admin.put_blob(staged, b"{}"))
    with admin._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,source_file_version_id) VALUES (%s,%s,'阈值检查',%s,%s)",
            (rule_id, alice.tenant_id, Jsonb({**rules, "suite_revision_id": base.id}), source.id),
        )
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

    runtime = Runtime()
    controller = Controller(
        runtime,
        object_file_source(settings, connect=connect, objects=objects),
        executor_revision=runtime_revision(settings.deep_execution_revision(), library_revision()),
        executor_revision_for_request=lambda spec: runtime_revision(settings.deep_execution_revision(spec.model_identity), library_revision()),
        authorize=authorize_execution(settings, connect=connect, open_store=lambda _: worker),
        readiness=lambda: True,
        configuration=settings.deep_execution_policy().runtime_identity(),
    )
    rpc = None
    if os.environ.get("PPAP_CHECKER_BROWSER_LOCAL_TRANSPORT") == "1":
        from agent.client import DeepClient, DeepError
        from agent.run import ExecutionContext

        def request_locally(client, payload, *, cancelled=None, on_progress=None, on_activity=None):
            context = ExecutionContext()
            if cancelled is not None:
                context.cancelled = cancelled
            if on_progress is not None:
                context.on_progress = on_progress
            if on_activity is not None:
                context.on_activity = on_activity
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


def test_first_uaes_adoption_from_rule_library_freezes_dependencies(workspace_product):
    """A fresh UAES tenant adopts real source through Web before its first Round."""
    from audit.standard_suites import predefined_directory_suites
    from playwright.sync_api import expect, sync_playwright

    f = workspace_product
    ordered = ("UAES:U12", "UAES:U16", "UAES:U07", "UAES:U08")
    baselines = {suite.suite_key: suite for suite in predefined_directory_suites() if suite.suite_key in ordered}
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
            # A platform program is adopted on its rule's page; each preset rule names its program.
            presets = {key: str(uuid4()) for key in ordered}
            with f.admin._conn() as conn:
                for key, preset_id in presets.items():
                    body = {**json.loads(baselines[key].files["rules.json"]), "kind": "engine", "rule_profile": "v4", "customers": ["UAES"], "preset_key": key.split(":")[1], "suite_key": key}
                    conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES (%s,%s,%s,%s)", (preset_id, f.alice.tenant_id, key, Jsonb(body)))
            page.goto(f.web + "/requirements?systemRule=" + presets["UAES:U08"])
            program = page.get_by_role("main").get_by_role("region", name="检查程序", exact=True)
            # Its dependencies come first; each opens the rule page that owns that program.
            expect(program.get_by_text("这个版本依赖下面这些规则的检查程序，先采用它们：", exact=True)).to_be_visible()
            expect(program.get_by_role("button", name="采用", exact=True)).to_be_disabled()
            program.get_by_role("button", name=baselines["UAES:U07"].document_type, exact=True).click()
            expect(page).to_have_url(re.compile("systemRule=" + presets["UAES:U07"]))
            program.get_by_role("button", name=baselines["UAES:U12"].document_type, exact=True).click()
            expect(page).to_have_url(re.compile("systemRule=" + presets["UAES:U12"]))
            publications = []
            for key in ordered:
                page.goto(f.web + "/requirements?systemRule=" + presets[key])
                program = page.get_by_role("main").get_by_role("region", name="检查程序", exact=True)
                expect(program.get_by_text("还没采用", exact=True)).to_be_visible()
                expect(program).to_contain_text("可以采用：第 1 版")
                program.get_by_role("button", name="采用", exact=True).click()
                confirmation = page.get_by_role("dialog", name="采用第 1 版？", exact=True)
                expect(confirmation).to_contain_text("它还没验证过，结果要人工确认。")
                with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/active")) as submitted:
                    confirmation.get_by_role("button", name="采用", exact=True).click()
                receipt = response_json(submitted.value)
                assert receipt["suite_key"] == key and receipt["revision_id"] == revisions[key].id
                assert receipt["expected_revision"] == 0 and receipt["revision"] == 1
                assert receipt["previous_revision_id"] is None and receipt["force_unverified"] is True
                publications.append(receipt)
                expect(program.get_by_text("可用于审核", exact=True)).to_be_visible()
                page.reload()
                program = page.get_by_role("main").get_by_role("region", name="检查程序", exact=True)
                expect(program.get_by_text("第 1 版 · 未验证", exact=True)).to_be_visible()
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
            expect(page.get_by_role("region", name="检查程序").get_by_text("可用于审核", exact=True)).to_be_visible()
            assert f.suites.read_snapshot(accepted["id"], f.alice.tenant_id).to_dict() == frozen.to_dict()
        finally:
            context.close()
            browser.close()
