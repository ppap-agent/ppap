"""Real browser, Product API and PostgreSQL for one saved clause.

The model is a script. This does not accept model semantic quality.
"""

import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import uvicorn
from agent.checker_development import DevelopmentOutcome
from agent.checker_spec import CheckerArtifact
from agent.protocol import DevelopmentEnvelope
from agent.run import ExecutionContext
from api import create_app
from audit.checker_validation import SuiteValidationReport
from cryptography.fernet import Fernet
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.model_credentials import TenantModelStore
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from worker import process_claimed
from worker.checker_optimizer import development_handler


def back_to_templates(page):
    from playwright.sync_api import expect

    page.get_by_role("navigation", name="位置", exact=True).get_by_role("link", name="规则模板", exact=True).click()
    expect(page.get_by_role("searchbox", name="搜索规则模板", exact=True)).to_be_visible()


def open_rule(page, title):
    """Template list, the template that holds the rule, then the rule's file type in it."""
    from playwright.sync_api import expect

    if page.get_by_role("navigation", name="位置", exact=True).count():
        back_to_templates(page)
    page.get_by_role("searchbox", name="搜索规则模板", exact=True).fill(title)
    template = page.get_by_role("main").get_by_role("listitem").get_by_role("link").first
    expect(template).to_be_visible()
    template.click()
    row = page.get_by_role("row").filter(has_text=title).get_by_role("link")
    item = page.get_by_role("link").filter(has_text=title).filter(visible=True)
    row.or_(item).first.click()


ROOT = Path(__file__).resolve().parents[3]
# Rule tidying returns the clause as plain check steps: sections, open questions and a suggested wording.
_READINGS = {
    "文档要有编号": {"sections": [{"title": "检查", "items": ["文档有编号 → 通过；没有编号 → 不通过"]}], "review": [], "suggested_clause": ""},
    "键和名称要对应": {
        "sections": [{"title": "比对", "items": ["键和名称都对应 → 通过；任一不对应 → 不通过"]}],
        "review": ["名称是和键一起匹配，还是先按键再对名称？"],
        "suggested_clause": "键和名称要同时对应",
    },
    "長度至少 10℃": {"sections": [{"title": "比对", "items": ["長度大于或等于 10℃ → 通过；小于 10℃ → 不通过"]}], "review": [], "suggested_clause": ""},
    "日期必须填写": {"sections": [{"title": "检查", "items": ["日期已填写 → 通过；未填写 → 不通过"]}], "review": [], "suggested_clause": ""},
}
_REVISED = {"sections": [{"title": "检查", "items": ["文档编号至少关联一条记录 → 通过；没有 → 不通过"]}], "review": [], "suggested_clause": ""}


class ControlledRuntime:
    """Writes a fixed program for the example; the program author is not under test here."""

    def __init__(self):
        self.calls = 0

    def develop_and_run(self, request, *, spec, context):
        self.calls += 1
        program = b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"
        artifact = CheckerArtifact(input_digest=request.target.digest, executor_revision=request.executor_revision, entrypoint="checker:create_audit", files={"checker.py": program})
        proof = SuiteValidationReport(
            suite_revision_id=request.suite_revision_id,
            suite_digest=request.suite_digest,
            library_revision=request.library_revision,
            program_digest=artifact.program_digest,
            reasons=("no_independent_oracle",),
        )
        return DevelopmentOutcome(artifact, [], ["controlled fixture"], {"model_requests": 1}, validation=proof, usage_source="controlled")


class ScriptedFormalizer:
    def __init__(self, rule_id: str):
        self.rule_id = rule_id
        self.calls = 0
        self.fail_next = False

    def __call__(self, task, **kwargs):
        from documents.ai import AIError, AIResult

        if self.fail_next:
            self.fail_next = False
            raise AIError("timeout")
        self.calls += 1
        return AIResult(json.loads(self._answer(kwargs.get("prompt") or "")))

    def _answer(self, prompt: str) -> str:
        payload = json.loads(prompt[prompt.index("{") :])
        reading = _REVISED if payload.get("instruction") else _READINGS[payload["clause_text"]]
        return json.dumps(reading, ensure_ascii=False)


@pytest.fixture
def formalization_browser(auth_store_factory, tmp_path, request):
    settings = Settings(
        usage_charge_enabled=False,
        feishu_notifications_enabled=False,
        feishu_app_id="formalization-browser",
        git_sha="a" * 40,
        model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()),
    )
    admin = auth_store_factory(settings)
    alice = admin.upsert_feishu_identity("formalization-browser", "alice", "Alice")
    models = TenantModelStore(admin)
    models.put(alice.tenant_id, models.candidate(alice.tenant_id, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", text_model="test-model"), operator="test")
    rule_id, file_id, version_id = str(uuid4()), str(uuid4()), str(uuid4())
    rules = [
        ("c1", "文档要有编号", True),
        ("c2", "键和名称要对应", True),
        ("c3", "長度至少 10℃", False),
        ("c4", "日期必须填写", True),
    ]
    body = {
        "kind": "engine",
        "type": "自定义",
        "rules": [{"id": clause_id, "category": "条款", "priority": "P0", "rule_to_check": text, "type_of_rule": "text"} for clause_id, text, _checked in rules],
    }
    from product.store.suite_releases import SuiteReleaseStore
    from product.store.suites import SuiteStore
    from product.suites import UNIMPLEMENTED_CHECKER_SOURCE

    suite_key = "custom:" + rule_id
    program = b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"
    # An uploaded rule starts with the never-audited starter as its adopted program.
    suite = SuiteStore(settings, admin.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key=suite_key,
        files={
            "rules.json": json.dumps({"type": "自定义", "title": "编号规则", "suite_key": suite_key, "rules": body["rules"], "checker_binding": {}}).encode(),
            "appendix.json": b"[]",
            "checker.py": UNIMPLEMENTED_CHECKER_SOURCE,
        },
        created_by=alice.user_id,
    )
    SuiteReleaseStore(settings, admin.objects).publish(alice, suite_key, suite.id, 0, str(uuid4()))
    body["suite_key"] = suite_key
    body["suite_revision_id"] = suite.id
    SuiteStore(settings, admin.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:browser-catalog",
        files={"rules.json": json.dumps({"title": "编号检查", "type": "自定义", "rules": body["rules"]}).encode(), "checker.py": program},
        created_by=alice.user_id,
    )

    def cited(clause_id: str, text: str, checked: bool) -> dict:
        location = f"第 1 页 · {clause_id}"
        clause = {"id": clause_id, "title": text, "text": text, "original": text, "source": "编号.txt", "location": location, "applicability": "apply", "reason": "", "checked": checked}
        if checked:
            clause["provenance"] = {"id": f"unit-{clause_id}", "location": location, "excerpt": text, "fileVersionId": version_id}
        return clause

    registration = {
        "registration": json.dumps(
            {
                "basis": {
                    "source": {"id": rule_id, "title": "编号规则", "filename": "编号.txt", "version": "v1", "origin": "local", "state": "ready"},
                    "clauses": [cited(*rule) for rule in rules],
                    "conflict": {"active": False, "choice": "pending", "reason": ""},
                    "confirmed": None,
                },
                "scope": {"customer": "", "part": "", "revision": "", "allCustomers": True, "allParts": True, "allRevisions": True},
                "reason": "已核对",
            },
            ensure_ascii=False,
        )
    }
    with admin._conn() as conn:
        conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES (%s,%s,'file')", (file_id, alice.tenant_id))
        conn.execute("INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES (%s,%s,'read')", (file_id, alice.user_id))
        conn.execute("INSERT INTO product.files(id,tenant_id,created_by,original_filename) VALUES (%s,%s,%s,'编号.txt')", (file_id, alice.tenant_id, alice.user_id))
        conn.execute(
            """INSERT INTO product.file_versions(id,file_id,tenant_id,original_filename,content_type,byte_size,sha256,object_key,status)
               VALUES (%s,%s,%s,'编号.txt','text/plain',4,%s,%s,'ready')""",
            (version_id, file_id, alice.tenant_id, "a" * 64, version_id),
        )
        conn.execute(
            """INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,source_file_version_id,case_context)
               VALUES (%s,%s,'编号规则',%s,%s,%s,%s)""",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, version_id, Jsonb(registration)),
        )
        bare_id = str(uuid4())
        bare_body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "条款", "priority": "P0", "rule_to_check": "要有名称", "type_of_rule": "text"}]}
        bare_registration = {
            "registration": json.dumps(
                {
                    "basis": {
                        "source": {"id": bare_id, "title": "无检查规则", "filename": "编号.txt", "version": "v1", "origin": "local", "state": "ready"},
                        "clauses": [cited("c1", "要有名称", True)],
                        "conflict": {"active": False, "choice": "pending", "reason": ""},
                        "confirmed": None,
                    },
                    "scope": {"customer": "", "part": "", "revision": "", "allCustomers": True, "allParts": True, "allRevisions": True},
                    "reason": "已核对",
                },
                ensure_ascii=False,
            )
        }
        conn.execute(
            """INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,source_file_version_id,case_context)
               VALUES (%s,%s,'无检查规则',%s,%s,%s,%s)""",
            (bare_id, alice.tenant_id, Jsonb(bare_body), alice.user_id, version_id, Jsonb(bare_registration)),
        )
    api = build_product_store(settings, objects=admin.objects, role="api")
    worker = build_product_store(settings, objects=admin.objects, role="worker")
    script = ScriptedFormalizer(rule_id)
    runtime = ControlledRuntime()
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
    vite = subprocess.Popen(
        ["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(web_port), "--strictPort"],
        cwd=ROOT / "apps/web",
        env={**os.environ, "PPAP_PREVIEW": "0", "PPAP_PUBLIC_API_URL": api_url},
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    stop, errors = threading.Event(), []

    def work():
        while not stop.wait(0.05):
            try:
                job = worker.claim_job("formalization-browser")
                if job and job.kind == "rule_formalization":
                    process_claimed(worker, job, "formalization-browser", rule_ai=script)
                elif job and job.kind == "checker_optimization":
                    envelope = DevelopmentEnvelope(
                        op="develop", operation_id=job.payload["operation_id"], job_id=job.id, tenant_id=job.tenant_id, attempt=job.attempt, lease_owner="formalization-browser"
                    )
                    development_handler(worker, runtime)(envelope, ExecutionContext())
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
            assert vite.poll() is None, "Web did not start: " + log.read()[-2000:]
            assert time.monotonic() < until, "Formalization browser startup timed out"
            time.sleep(0.1)
        worker_thread.start()
        yield SimpleNamespace(
            web=web,
            api=api_url,
            session=admin.create_session(alice),
            renew_session=lambda: admin.create_session(alice),
            rule_id=rule_id,
            suite_key=suite_key,
            tenant_id=alice.tenant_id,
            store=admin,
            script=script,
            runtime=runtime,
            starter_id=suite.id,
            model_key=lambda: models.put(
                alice.tenant_id, models.candidate(alice.tenant_id, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", text_model="test-model"), operator="test"
            ),
            errors=errors,
        )
    finally:
        stop.set()
        if worker_thread.ident:
            worker_thread.join(timeout=10)
        server.should_exit = True
        api_thread.join(timeout=15)
        try:
            os.killpg(vite.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        vite.wait(timeout=10)
        listener.close()
        log.close()
        assert not errors, errors


def test_rule_program_is_generated_from_confirmed_check_points_against_the_real_api(formalization_browser):
    pytest.importorskip("playwright")
    from playwright.sync_api import expect, sync_playwright

    f = formalization_browser
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        context.add_cookies([{"name": SESSION_COOKIE, "value": f.session, "url": f.web, "httpOnly": True}])
        page = context.new_page()
        page.set_default_timeout(20000)
        generations = []
        page.on("request", lambda item: generations.append(item.url) if item.method == "POST" and "/formalizations/" in item.url and item.url.endswith("/generations") else None)
        page.goto(f.web + "/requirements")
        expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
        open_rule(page, "编号规则")
        expect(page).to_have_url(re.compile(rf"[?&]requirement={f.rule_id}"))
        deep_link = page.url
        page.goto(f.web + "/requirements")
        expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
        page.goto(deep_link)
        page.go_back()
        expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
        page.go_forward()
        expect(page.get_by_text("文档要有编号").first).to_be_visible()
        # The rule page shows the rule itself; nothing about programs or check points is generated by reading it.
        for removed in ("系统是这样理解的", "还不能自动审核，审核时由人工逐条判断。", "AI 模型", "让系统解释"):
            expect(page.get_by_text(removed)).to_have_count(0)
        program_card = page.get_by_role("region", name="检查程序", exact=True)
        expect(program_card).to_contain_text("还不能用于审核")
        wizard = page.get_by_role("region", name="逐条确认", exact=True)
        nav = wizard.get_by_role("navigation", name="条款", exact=True)
        heading = page.get_by_role("heading", name="生成检查程序", exact=True)
        shown = {"c1": "文档要有编号", "c2": "键和名称", "c3": "長度至少", "c4": "日期必须"}

        def open_points():
            program_card.get_by_role("button", name="生成检查程序", exact=True).click()
            expect(heading).to_be_visible()

        def clause(clause_id: str):
            # One clause is shown at a time; the clause list beside it switches between them.
            nav.get_by_role("button").filter(has_text=shown[clause_id]).click()
            return wizard.get_by_role("article", name=f"条款 {clause_id}", exact=True)

        def latest(clause_id: str) -> dict:
            detail = httpx.get(f"{f.api}/rules/{f.rule_id}/formalizations/{clause_id}", cookies={SESSION_COOKIE: f.session}, timeout=10)
            assert detail.status_code == 200, detail.text
            return detail.json()["candidates"][-1]

        open_points()
        expect(nav.get_by_text("已确认 0 / 4", exact=True)).to_be_visible()
        expect(wizard.get_by_text("4 条还没整理。", exact=True)).to_be_visible()
        expect(clause("c1")).to_contain_text("还没整理。")
        assert generations == [] and f.script.calls == 0
        wizard.get_by_role("button", name="开始整理", exact=True).click()
        expect(clause("c1")).to_contain_text("文档有编号 → 通过；没有编号 → 不通过")
        c2 = clause("c2")
        expect(c2.get_by_role("heading", name="原文有 1 处没说清", exact=True)).to_be_visible()
        expect(c2).to_contain_text("名称是和键一起匹配，还是先按键再对名称？")
        expect(clause("c3")).to_contain_text("長度大于或等于 10℃")
        expect(clause("c4")).to_contain_text("日期已填写")
        # One explicit click prepares each clause once.
        assert len(generations) == 4 and f.script.calls == 4
        assert latest("c1")["formal_profile"] == "rule-text-v1"
        # A clause whose source was never checked cannot be confirmed.
        c3 = clause("c3")
        c3.get_by_role("button", name="确认", exact=True).click()
        expect(c3.get_by_role("alert")).to_contain_text("这条的出处还没核对。先在规则页核对出处。")
        # Someone else confirms c1 first: this page's confirmation is refused, not merged.
        detail = httpx.get(f"{f.api}/rules/{f.rule_id}/formalizations/c1", cookies={SESSION_COOKIE: f.session}, timeout=10).json()
        candidate = detail["candidates"][-1]
        raced = httpx.post(
            f"{f.api}/rules/{f.rule_id}/formalizations/c1/confirmations",
            cookies={SESSION_COOKIE: f.session},
            timeout=10,
            json={
                "operation_id": str(uuid4()),
                "candidate_id": candidate["id"],
                "expected_candidate_revision": candidate["formalization_revision"],
                "expected_source_digest": detail["source_rule_digest"],
                "expected_display_digest": candidate["display_digest"],
            },
        )
        assert raced.status_code == 201, raced.text
        c1 = clause("c1")
        c1.get_by_role("button", name="确认", exact=True).click()
        expect(c1.get_by_role("alert")).to_be_visible()
        c1.get_by_role("button", name="重新读取", exact=True).click()
        expect(c1.get_by_role("button", name="撤销确认", exact=True)).to_be_visible()
        # An open question can be kept as written: the person confirms the original wording.
        clause("c2").get_by_role("button", name="保持原文，确认", exact=True).click()
        expect(nav.get_by_text("已确认 2 / 4", exact=True)).to_be_visible()
        # A confirmed clause can be withdrawn, corrected and confirmed again; only one confirmation stays current.
        c1 = clause("c1")
        c1.get_by_role("button", name="撤销确认", exact=True).click()
        c1.get_by_role("button", name="写得不对", exact=True).click()
        c1.get_by_role("textbox", name="哪里写得不对？", exact=True).fill("改成至少一条")
        c1.get_by_role("button", name="重新整理", exact=True).click()
        expect(c1).to_contain_text("至少关联")
        c1.get_by_role("button", name="确认", exact=True).click()
        expect(nav.get_by_text("已确认 2 / 4", exact=True)).to_be_visible()
        revised = httpx.get(f"{f.api}/rules/{f.rule_id}/formalizations/c1", cookies={SESSION_COOKIE: f.session}, timeout=10).json()
        assert len(revised["confirmations"]) == 2
        assert sum(item["current"] for item in revised["confirmations"]) == 1
        assert next(item for item in revised["confirmations"] if item["current"])["candidate_id"] == revised["candidates"][-1]["id"]
        assert "至少关联" in revised["candidates"][-1]["cnl_text"]
        assert len(generations) == 5 and f.script.calls == 5
        # An unknown provider outcome is read back, never posted again by reloading or refreshing.
        f.script.fail_next = True
        digest = httpx.get(f"{f.api}/rules/{f.rule_id}/formalizations/c3", cookies={SESSION_COOKIE: f.session}, timeout=10).json()["source_rule_digest"]
        unknown = httpx.post(
            f"{f.api}/rules/{f.rule_id}/formalizations/c3/generations",
            cookies={SESSION_COOKIE: f.session},
            timeout=10,
            json={"consume_model": True, "operation_id": str(uuid4()), "expected_source_digest": digest},
        )
        assert unknown.status_code == 202, unknown.text
        state = ""
        for _ in range(50):
            state = httpx.get(f"{f.api}/rule-formalization-generations/{unknown.json()['operation_id']}", cookies={SESSION_COOKIE: f.session}, timeout=10).json().get("state", "")
            if state == "unknown":
                break
            time.sleep(0.1)
        assert state == "unknown", state
        for _ in range(2):
            page.reload()
            # The wizard is part of the address; reloading reopens it.
            expect(heading).to_be_visible()
            expect(clause("c3")).to_contain_text("还不知道整理结果。刷新看看，不要重新开始。")
        clause("c3").get_by_role("button", name="刷新", exact=True).click()
        expect(clause("c3")).to_contain_text("还不知道整理结果。刷新看看，不要重新开始。")
        assert len(generations) == 5 and f.script.calls == 5
        # Without a usable model the request is refused and said plainly.
        TenantModelStore(f.store).delete(f.tenant_id)
        page.reload()
        expect(heading).to_be_visible()
        c4 = clause("c4")
        c4.get_by_role("button", name="写得不对", exact=True).click()
        c4.get_by_role("textbox", name="哪里写得不对？", exact=True).fill("写清楚日期格式")
        c4.get_by_role("button", name="重新整理", exact=True).click()
        expect(c4.get_by_role("alert")).to_contain_text("服务暂时不可用，请稍后重试；如持续出现，请联系管理员。")
        assert len(generations) == 6 and f.script.calls == 5
        f.model_key()
        # A changed clause loses its check points until someone prepares it again.
        with f.store._conn() as conn:
            conn.execute("UPDATE product.rule_versions SET body = jsonb_set(body, '{rules,3,rule_to_check}', '\"日期必须完整填写\"') WHERE id=%s", (f.rule_id,))
        page.reload()
        expect(heading).to_be_visible()
        expect(wizard.get_by_text("1 条还没整理。", exact=True)).to_be_visible()
        expect(clause("c4").get_by_role("button", name="确认", exact=True)).to_have_count(0)
        page.set_viewport_size({"width": 320, "height": 720})
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, overflow
        page.set_viewport_size({"width": 1280, "height": 900})
        # Two confirmed clauses go into the program; the example file is not labeled.
        generate = wizard.get_by_role("button", name="生成检查程序", exact=True)
        expect(wizard.get_by_role("status").filter(has_text="还要一份示例文件。")).to_be_visible()
        expect(generate).to_be_disabled()
        page.get_by_label("选择示例文件").set_input_files({"name": "示例.txt", "mimeType": "text/plain", "buffer": "编号 A-001".encode()})
        expect(wizard.get_by_text("示例.txt", exact=True)).to_be_visible()
        expect(wizard.get_by_role("status").filter(has_text="还有 2 条没确认，审核时这几条交给人判断。")).to_be_visible()
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/checker-generations")) as started:
            generate.click()
        assert started.value.status == 200, started.value.text()
        expect(program_card.get_by_text("可用于审核", exact=True)).to_be_visible(timeout=60000)
        expect(program_card.get_by_text(re.compile(r"^第 \d+ 版 · 未验证$"))).to_be_visible()
        assert f.runtime.calls == 1
        with f.store._conn() as conn:
            head, forced = conn.execute("SELECT current_revision_id::text, force_unverified FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key=%s", (f.tenant_id, f.suite_key)).fetchone()
        assert head != f.starter_id and forced is True
        from product.store.suites import SuiteStore

        adopted = SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(head, f.tenant_id)
        rules = json.loads(adopted.files["rules.json"])
        assert rules["checker_binding"] == {"c1": "checker:create_audit", "c2": "checker:create_audit"}
        assert "至少关联" in json.dumps(rules["formalizations"]["c1"], ensure_ascii=False)
        page.reload()
        expect(program_card.get_by_text("可用于审核", exact=True)).to_be_visible()
        assert f.runtime.calls == 1
        # A rule without its own program has no program card.
        open_rule(page, "无检查规则")
        expect(page.get_by_text("要有名称").first).to_be_visible()
        expect(program_card).to_have_count(0)
        browser.close()
