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
NS = "https://example.test/ns#"
_VOCAB = {
    "prefixes": {"ex": NS},
    "entities": [
        {"iri": NS + "Source", "kind": "class", "label": "源记录"},
        {"iri": NS + "Target", "kind": "class", "label": "目标记录"},
        {"iri": NS + "key", "kind": "dataProperty", "label": "键"},
        {"iri": NS + "name", "kind": "dataProperty", "label": "名称"},
    ],
}
_OWL = f"Prefix(ex:=<{NS}>)\nPrefix(ppap:=<https://ppap.example/formal/v1#>)\nOntology(\nDeclaration(Class(ex:Source))\nDeclaration(Class(ex:Target))\nDeclaration(DataProperty(ex:key))\nDeclaration(DataProperty(ex:name))\n)\n"
_AT_LEAST = "(sentence s (forall (s) (if (ex:Source s) (exists (t) (and (ex:Target t) (= (ex:key s) (ex:key t)))))))"
_EXACT = "(sentence s (forall (s) (if (ex:Source s) (exists (t) (and (ex:Target t) (= (ex:key s) (ex:key t)) (forall (t2) (if (and (ex:Target t2) (= (ex:key s) (ex:key t2))) (= t t2))))))))"
_COMBINED = (
    "(sentence s (forall (s) (if (ex:Source s) (exists (t) (and (ex:Target t) "
    "(= (ex:key s) (ex:key t)) (= (ex:name s) (ex:name t)) (forall (t2) (if (and (ex:Target t2) "
    "(= (ex:key s) (ex:key t2)) (= (ex:name s) (ex:name t2))) (= t t2))))))))"
)
_KEY_THEN_NAME = (
    "(sentence s (forall (s) (if (ex:Source s) (and (exists (t) (and (ex:Target t) "
    "(= (ex:key s) (ex:key t)) (forall (t2) (if (and (ex:Target t2) (= (ex:key s) (ex:key t2))) "
    "(= t t2))))) (forall (u) (if (and (ex:Target u) (= (ex:key s) (ex:key u))) (= (ex:name s) (ex:name u))))))))"
)
_QUANTIFIER_QUESTION = {
    "id": "quantity",
    "reason": "原文没有写明是至少一条还是恰好一条",
    "choices": [
        {"id": "at-least", "summary": "至少一个目标", "counterexample": "一个源对应两个目标时，至少成立而恰好不成立"},
        {"id": "exact", "summary": "恰好一个目标", "counterexample": "一个源对应两个目标时，至少成立而恰好不成立"},
    ],
}
_MATCH_QUESTION = {
    "id": "match",
    "reason": "原文没有写明是只按键还是按键和名称一起",
    "choices": [
        {"id": "key-only", "summary": "先按键再对名称", "counterexample": "键唯一但名称不同时，两种理解结果不同"},
        {"id": "combined", "summary": "键和名称一起匹配", "counterexample": "键唯一但名称不同时，两种理解结果不同"},
    ],
}


_THRESHOLD = "(sentence s (forall (x) (if (ex:Row x) (>= (ex:length x) 10))))"
_THRESHOLD_VOCAB = {
    "prefixes": {"ex": NS},
    "entities": [
        {"iri": NS + "Row", "kind": "class", "label": "行"},
        {"iri": NS + "length", "kind": "function", "label": "长度"},
    ],
}
_THRESHOLD_OWL = f"Prefix(ex:=<{NS}>)\nPrefix(ppap:=<https://ppap.example/formal/v1#>)\nOntology(\nDeclaration(Class(ex:Row))\nDeclaration(DataProperty(ex:length))\n)\n"


def _reading(rule_id: str, clause_id: str, clif: str, kind: str, unresolved: list | None = None, vocabulary: dict | None = None, owl: str | None = None) -> str:
    return json.dumps(
        {
            "format_version": "1",
            "formal_profile": "owl2-plus-clif-v1",
            "owl_content": owl or _OWL,
            "clif_content": f"(cl-text {clif})",
            "vocabulary": vocabulary or _VOCAB,
            "source_rule_version_id": rule_id,
            "clause_id": clause_id,
            "obligations": [{"id": "o1", "source_span": "0:2", "semantic_kind": kind, "formal_ref": "s"}],
            "unresolved": unresolved or [],
            "completeness": "partial" if unresolved else "complete",
            "assumptions": [],
            "cnl_text": "规则解释已确认，全部符合",
        },
        ensure_ascii=False,
    )


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
        clause_id = payload["clause_id"]
        choice = (payload.get("clarification") or {}).get("choice_id")
        if clause_id == "c3":
            return _reading(self.rule_id, clause_id, _THRESHOLD, "threshold", vocabulary=_THRESHOLD_VOCAB, owl=_THRESHOLD_OWL)
        if clause_id == "c2":
            if choice == "combined":
                return _reading(self.rule_id, clause_id, _COMBINED, "unique")
            if choice == "key-only":
                return _reading(self.rule_id, clause_id, _KEY_THEN_NAME, "unique")
            return _reading(self.rule_id, clause_id, _KEY_THEN_NAME, "unique", [_MATCH_QUESTION])
        if choice == "exact":
            return _reading(self.rule_id, clause_id, _EXACT, "unique")
        if choice == "at-least":
            return _reading(self.rule_id, clause_id, _AT_LEAST, "existential")
        if choice == "undecided":
            return _reading(self.rule_id, clause_id, _AT_LEAST, "existential")
        if payload.get("instruction"):
            return _reading(self.rule_id, clause_id, _AT_LEAST, "existential")
        return _reading(self.rule_id, clause_id, _AT_LEAST, "existential", [_QUANTIFIER_QUESTION])


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
    shots = Path("/private/tmp/claude-501/-Users-bef0rewind-Projects-work-ppap/1fe405b6-a54a-4e2e-a578-ee1705025761/scratchpad")
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
        page.get_by_role("tab", name="检查程序", exact=True).click()
        expect(page).to_have_url(re.compile(r"[?&]reqTab=program"))
        expect(page.get_by_text("这条规则还没有检查程序，不能用于审核。", exact=True)).to_be_visible()
        page.screenshot(path=str(shots / "rp-missing.png"), full_page=True)

        def open_points():
            page.get_by_role("button", name="生成检查程序", exact=True).click()
            expect(page.get_by_role("heading", name="确认检查要点", exact=True)).to_be_visible()

        def clause(clause_id: str):
            return page.get_by_role("article", name=f"条款 {clause_id}", exact=True)

        def choose(article, answer: str):
            # A choice belongs to the candidate on screen; a re-read candidate clears it, so wait for a settled one.
            confirm = article.get_by_role("button", name="确认选择", exact=True)
            for _ in range(10):
                article.get_by_role("radio", name=answer).check()
                try:
                    expect(confirm).to_be_enabled(timeout=1000)
                    break
                except AssertionError:
                    continue
            confirm.click()

        def latest(clause_id: str) -> dict:
            detail = httpx.get(f"{f.api}/rules/{f.rule_id}/formalizations/{clause_id}", cookies={SESSION_COOKIE: f.session}, timeout=10)
            assert detail.status_code == 200, detail.text
            return detail.json()["candidates"][-1]

        open_points()
        expect(page.get_by_text("已确认 0 / 4", exact=True)).to_be_visible()
        expect(page.get_by_text("4 条还没有整理检查要点。", exact=True)).to_be_visible()
        expect(clause("c1")).to_contain_text("尚未整理。")
        assert generations == [] and f.script.calls == 0
        page.get_by_role("button", name="整理检查要点", exact=True).click()
        c1 = clause("c1")
        expect(c1.get_by_text("需要你选择", exact=True)).to_be_visible()
        expect(c1).to_contain_text("至少一个目标")
        expect(c1).to_contain_text("恰好一个目标")
        expect(clause("c2")).to_contain_text("先按键再对名称")
        expect(clause("c3").get_by_role("button", name="确认", exact=True)).to_be_visible()
        expect(clause("c4").get_by_text("需要你选择", exact=True)).to_be_visible()
        # One explicit click prepares each clause once.
        assert len(generations) == 4 and f.script.calls == 4
        opened = latest("c1")
        assert "forall (t2)" not in opened["clif_content"]
        c1.get_by_role("button", name="暂不确定", exact=True).click()
        undecided = opened
        for _ in range(100):
            undecided = latest("c1")
            if undecided["id"] != opened["id"]:
                break
            time.sleep(0.1)
        # Not deciding keeps the question open; nothing becomes confirmable.
        assert undecided["id"] != opened["id"] and undecided["unresolved"]
        expect(c1.get_by_text("需要你选择", exact=True)).to_be_visible()
        expect(c1.get_by_role("button", name="确认选择", exact=True)).to_be_disabled()
        choose(c1, "恰好一个目标")
        expect(c1).to_contain_text("恰好关联一条")
        chosen = latest("c1")
        assert chosen["id"] != opened["id"]
        assert chosen["formal_digest"] != opened["formal_digest"]
        assert "forall (t2)" in chosen["clif_content"]
        assert "恰好" in chosen["cnl_text"]
        c2 = clause("c2")
        open_match = latest("c2")
        assert "forall (u)" in open_match["clif_content"]
        choose(c2, "键和名称一起匹配")
        expect(c2).to_contain_text("同时满足")
        matched = latest("c2")
        assert "(= (ex:name s) (ex:name t))" in matched["clif_content"]
        assert "forall (u)" not in matched["clif_content"]
        assert matched["completeness"] == "complete" and matched["unresolved"] == []
        assert matched["formal_digest"] != open_match["formal_digest"]
        assert len(generations) == 7 and f.script.calls == 7
        c3 = clause("c3")
        expect(page.get_by_text("長度至少 10℃").first).to_be_visible()
        expect(c3).to_contain_text("大于或等于 10")
        expect(c3).not_to_contain_text("mm")
        expect(c3.get_by_role("radio")).to_have_count(0)
        # A clause whose source was never checked cannot be confirmed.
        c3.get_by_role("button", name="确认", exact=True).click()
        expect(c3).to_contain_text("原文出处未核对。请先在规则页核对出处，再确认。")
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
        c1.get_by_role("button", name="确认", exact=True).click()
        expect(c1.get_by_role("alert")).to_be_visible()
        c1.get_by_role("button", name="重新读取", exact=True).click()
        expect(c1.get_by_text("已确认", exact=True)).to_be_visible()
        expect(c1.get_by_role("button", name="重新核对", exact=True)).to_be_visible()
        c2.get_by_role("button", name="确认", exact=True).click()
        expect(c2.get_by_role("button", name="重新核对", exact=True)).to_be_visible()
        expect(page.get_by_text("已确认 2 / 4", exact=True)).to_be_visible()
        page.screenshot(path=str(shots / "rp-points.png"), full_page=True)
        # A confirmed clause can be corrected and confirmed again; only one confirmation stays current.
        c1.get_by_role("button", name="重新核对", exact=True).click()
        c1.get_by_role("button", name="需要修改", exact=True).click()
        c1.get_by_role("textbox", name="需要怎样修改", exact=True).fill("改成至少一条")
        c1.get_by_role("button", name="提交修改", exact=True).click()
        expect(c1).to_contain_text("至少关联")
        c1.get_by_role("button", name="确认", exact=True).click()
        expect(c1.get_by_role("button", name="重新核对", exact=True)).to_be_visible()
        revised = httpx.get(f"{f.api}/rules/{f.rule_id}/formalizations/c1", cookies={SESSION_COOKIE: f.session}, timeout=10).json()
        assert len(revised["confirmations"]) == 2
        assert sum(item["current"] for item in revised["confirmations"]) == 1
        assert next(item for item in revised["confirmations"] if item["current"])["candidate_id"] == revised["candidates"][-1]["id"]
        assert "至少关联" in revised["candidates"][-1]["cnl_text"]
        assert len(generations) == 8 and f.script.calls == 8
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
            open_points()
            expect(clause("c3")).to_contain_text("整理结果尚未确定。请刷新，不要重复开始。")
        clause("c3").get_by_role("button", name="刷新", exact=True).click()
        expect(clause("c3")).to_contain_text("整理结果尚未确定。请刷新，不要重复开始。")
        assert len(generations) == 8 and f.script.calls == 8
        # Without a usable model the request is refused and said plainly.
        from product.store.model_credentials import TenantModelStore

        TenantModelStore(f.store).delete(f.tenant_id)
        page.reload()
        open_points()
        c4 = clause("c4")
        choose(c4, "至少一个目标")
        expect(c4).to_contain_text("服务暂时不可用，请稍后重试；如持续出现，请联系管理员。")
        assert len(generations) == 9 and f.script.calls == 8
        f.model_key()
        # A changed clause loses its check points until someone prepares it again.
        with f.store._conn() as conn:
            conn.execute("UPDATE product.rule_versions SET body = jsonb_set(body, '{rules,3,rule_to_check}', '\"日期必须完整填写\"') WHERE id=%s", (f.rule_id,))
        page.reload()
        open_points()
        expect(page.get_by_text("1 条还没有整理检查要点。", exact=True)).to_be_visible()
        expect(clause("c4").get_by_role("button", name="确认", exact=True)).to_have_count(0)
        page.set_viewport_size({"width": 320, "height": 720})
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, overflow
        page.set_viewport_size({"width": 1280, "height": 900})
        # Two confirmed clauses go into the program; the example file is not labeled.
        expect(page.get_by_text("未确认的 2 条不进入程序，审核时由人判断。", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="生成检查程序", exact=True).last).to_be_disabled()
        page.get_by_label("选择示例文件").set_input_files({"name": "示例.txt", "mimeType": "text/plain", "buffer": "编号 A-001".encode()})
        expect(page.get_by_text("示例.txt", exact=True)).to_be_visible()
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/checker-generations")) as started:
            page.get_by_role("button", name="生成检查程序", exact=True).last.click()
        assert started.value.status == 200, started.value.text()
        expect(page.get_by_text("未经验证", exact=True)).to_be_visible(timeout=60000)
        expect(page.get_by_text("这条规则已有检查程序，可以用于审核。")).to_be_visible()
        page.screenshot(path=str(shots / "rp-done.png"), full_page=True)
        assert f.runtime.calls == 1
        with f.store._conn() as conn:
            head, forced = conn.execute("SELECT current_revision_id::text, force_unverified FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key=%s", (f.tenant_id, f.suite_key)).fetchone()
        assert head != f.starter_id and forced is True
        from product.store.suites import SuiteStore

        adopted = SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(head, f.tenant_id)
        rules = json.loads(adopted.files["rules.json"])
        assert rules["checker_binding"] == {"c1": "checker:create_audit", "c2": "checker:create_audit"}
        assert "至少关联" in rules["formalizations"]["c1"]["cnl_text"]
        page.reload()
        expect(page.get_by_text("这条规则已有检查程序，可以用于审核。")).to_be_visible()
        assert f.runtime.calls == 1
        # A rule without its own program has no program tab.
        open_rule(page, "无检查规则")
        expect(page.get_by_text("要有名称").first).to_be_visible()
        expect(page.get_by_role("tab", name="检查程序", exact=True)).to_have_count(0)
        browser.close()
