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
from api import create_app
from cryptography.fernet import Fernet
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from worker import process_claimed

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


class ScriptedFormalizer:
    def __init__(self, rule_id: str):
        self.rule_id = rule_id
        self.calls = 0
        self.fail_next = False

    def structured_completion(self, **kwargs) -> str:
        if self.fail_next:
            self.fail_next = False
            raise TimeoutError("formalization outcome unknown")
        self.calls += 1
        prompt = kwargs.get("user_prompt") or ""
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
    admin.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
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
    from product.store.suites import SuiteStore

    suite_key = "custom:" + rule_id
    program = b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"
    suite = SuiteStore(settings, admin.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key=suite_key,
        files={"rules.json": json.dumps({"type": "自定义", "rules": body["rules"]}).encode(), "checker.py": program},
        created_by=alice.user_id,
    )
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


def test_saved_clause_confirms_against_the_real_api(formalization_browser):
    pytest.importorskip("playwright")
    from playwright.sync_api import expect, sync_playwright

    f = formalization_browser
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        context.add_cookies([{"name": SESSION_COOKIE, "value": f.session, "url": f.web, "httpOnly": True}])
        page = context.new_page()
        page.set_default_timeout(20000)
        generations = []
        page.on("request", lambda item: generations.append(item.url) if item.method == "POST" and "/formalizations/" in item.url and item.url.endswith("/generations") else None)
        page.goto(f.web + "/requirements")
        expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
        for label in ("客户要求", "企业检查版本", "预定义检查"):
            expect(page.get_by_role("tab", name=label, exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="查看系统提供的检查", exact=True)).to_be_visible()
        page.get_by_role("searchbox", name="搜索审核规则", exact=True).fill("编号")
        rule_link = page.get_by_role("link", name="编号规则", exact=True).first
        numbered = page.get_by_role("row").filter(has_text="编号规则")
        expect(numbered).to_contain_text("继续确认")
        expect(numbered.get_by_role("link", name="编号规则", exact=True)).to_have_attribute("aria-describedby", re.compile(r"^rule-action-.+-wide$"))
        catalog_row = page.get_by_role("button", name="编号检查").first
        expect(rule_link).to_be_visible()
        expect(catalog_row).to_be_visible()
        expect(catalog_row).to_contain_text("企业上传")
        expect(rule_link).not_to_contain_text("企业上传")
        page.get_by_role("searchbox", name="搜索审核规则", exact=True).fill("规则")
        official = page.get_by_role("button").filter(has_text="系统提供").filter(has_text="查看规则").first
        expect(rule_link).to_be_visible()
        expect(official).to_be_visible()
        expect(official).to_contain_text("系统提供")
        expect(official).not_to_contain_text("查看系统提供的检查")
        expect(rule_link).not_to_contain_text("系统提供")
        official.click()
        expect(page).to_have_url(re.compile(r"[?&]checkerPath="))
        assert "requirement=" not in page.url
        assert "reqVersion=" not in page.url
        page.get_by_role("button", name="返回审核规则", exact=True).click()
        expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
        rule_link = page.get_by_role("link", name="编号规则", exact=True).first
        rule_link.click()
        expect(page.get_by_text("检查功能尚待完善", exact=False)).to_have_count(0)
        expect(page).to_have_url(re.compile(rf"[?&]requirement={f.rule_id}"))
        deep_link = page.url
        page.goto(f.web + "/requirements")
        expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
        page.goto(deep_link)
        page.go_back()
        expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
        page.go_forward()
        section = page.get_by_role("region", name="系统是这样理解的 c1", exact=True)
        expect(section.get_by_role("heading", name="系统是这样理解的", exact=True)).to_be_visible()
        expect(page.get_by_text("文档要有编号").first).to_be_visible()
        section.get_by_role("button", name="让系统解释这条规则", exact=True).click()
        expect(section).to_contain_text("至少一个目标")
        expect(section).to_contain_text("恰好一个目标")

        def latest(clause_id: str) -> dict:
            detail = httpx.get(f"{f.api}/rules/{f.rule_id}/formalizations/{clause_id}", cookies={SESSION_COOKIE: f.session}, timeout=10)
            assert detail.status_code == 200, detail.text
            return detail.json()["candidates"][-1]

        opened = latest("c1")
        assert "forall (t2)" not in opened["clif_content"]
        expect(section.get_by_role("button", name="确认这一理解", exact=True)).to_be_disabled()
        section.get_by_role("button", name="暂不确定", exact=True).click()
        expect(section).to_contain_text("用户还不能在这些理解中选择")
        expect(section.get_by_role("button", name="确认这一理解", exact=True)).to_be_disabled()
        section.get_by_role("radio", name="恰好一个目标").check()
        section.get_by_role("button", name="按这个选择重新解释", exact=True).click()
        expect(section).to_contain_text("恰好关联一条")
        chosen = latest("c1")
        assert chosen["id"] != opened["id"]
        assert chosen["formal_digest"] != opened["formal_digest"]
        assert "forall (t2)" in chosen["clif_content"]
        assert "恰好" in chosen["cnl_text"]
        expect(section).to_contain_text("原文没有被修改")
        expect(section).to_contain_text("上一次理解")
        confirm = section.get_by_role("button", name="确认这一理解", exact=True)
        expect(confirm).to_be_enabled()
        expect(confirm).to_have_attribute("aria-describedby", "formal-reason-c1")
        expect(section.locator("#formal-reason-c1")).to_contain_text("这还不是检查功能已验证")
        other = page.get_by_role("region", name="系统是这样理解的 c2", exact=True)
        other.get_by_role("button", name="让系统解释这条规则", exact=True).click()
        expect(other).to_contain_text("先按键再对名称")
        expect(other).to_contain_text("键和名称一起匹配")
        open_match = latest("c2")
        assert "forall (u)" in open_match["clif_content"]
        other.get_by_role("radio", name="键和名称一起匹配").check()
        other.get_by_role("button", name="按这个选择重新解释", exact=True).click()
        expect(other).to_contain_text("同时满足")
        matched = latest("c2")
        assert "(= (ex:name s) (ex:name t))" in matched["clif_content"]
        assert "forall (u)" not in matched["clif_content"]
        assert matched["completeness"] == "complete"
        assert matched["unresolved"] == []
        assert matched["formal_digest"] != open_match["formal_digest"]
        measured = page.get_by_role("region", name="系统是这样理解的 c3", exact=True)
        measured.get_by_role("button", name="让系统解释这条规则", exact=True).click()
        expect(page.get_by_text("長度至少 10℃").first).to_be_visible()
        expect(measured).to_contain_text("大于或等于 10")
        expect(measured).not_to_contain_text("mm")
        expect(measured.get_by_role("radio")).to_have_count(0)
        expect(measured.get_by_role("button", name="确认这一理解", exact=True)).to_be_disabled()
        expect(measured).to_contain_text("原文出处未核对。请先核对出处，再确认这一理解。")
        detail = httpx.get(f"{f.api}/rules/{f.rule_id}/formalizations/c1", cookies={SESSION_COOKIE: f.session}, timeout=10)
        assert detail.status_code == 200, detail.text
        candidate = detail.json()["candidates"][-1]
        raced = httpx.post(
            f"{f.api}/rules/{f.rule_id}/formalizations/c1/confirmations",
            cookies={SESSION_COOKIE: f.session},
            timeout=10,
            json={
                "operation_id": str(uuid4()),
                "candidate_id": candidate["id"],
                "expected_candidate_revision": candidate["formalization_revision"],
                "expected_source_digest": detail.json()["source_rule_digest"],
                "expected_display_digest": candidate["display_digest"],
            },
        )
        assert raced.status_code == 201, raced.text
        section.get_by_role("button", name="确认这一理解", exact=True).click()
        expect(section.get_by_role("button", name="重新读取当前候选", exact=True)).to_be_visible()
        section.get_by_role("button", name="重新读取当前候选", exact=True).click()
        expect(section).to_contain_text("规则解释已确认。这只确认需求含义，不是检查功能已验证。")
        page.reload()
        expect(page.get_by_role("region", name="系统是这样理解的 c1", exact=True)).to_contain_text("规则解释已确认。这只确认需求含义，不是检查功能已验证。")
        expect(page.get_by_role("region", name="系统是这样理解的 c1", exact=True)).to_contain_text("检查功能仍需单独验证后才能用于审核。")
        expect(page.get_by_text("检查功能已验证，可用于审核。", exact=True)).to_have_count(0)
        from audit.standard_suites import library_revision

        with f.store._conn() as conn:
            revision_id, digest = conn.execute(
                """SELECT r.id::text, r.resolved_digest
                   FROM product.rule_versions v
                   JOIN product.suite_revisions r ON r.id = (v.body->>'suite_revision_id')::uuid
                   WHERE v.id=%s""",
                (f.rule_id,),
            ).fetchone()
            conn.execute(
                """INSERT INTO product.suite_validations(tenant_id, revision_id, status, revision, suite_digest, related_suite_digests, proof)
                   VALUES (%s, %s, 'verified', 1, %s, %s, %s)""",
                (
                    f.tenant_id,
                    revision_id,
                    digest,
                    Jsonb({}),
                    Jsonb({"reasons": [], "total_samples": 4, "passed_samples": 4, "new_errors": 0, "library_revision": library_revision(), "proof_digest": "ab" * 32, "report": {}}),
                ),
            )
        page.reload()
        verified = page.get_by_role("region", name="系统是这样理解的 c1", exact=True)
        expect(verified).to_contain_text("检查功能已验证，可用于审核。")
        expect(verified).not_to_contain_text("检查功能仍需单独验证后才能用于审核。")
        expect(verified).to_contain_text("规则解释已确认。这只确认需求含义，不是检查功能已验证。")
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/formalization-suite-candidates")) as handed:
            page.get_by_role("button", name="用于检查优化", exact=True).click()
        assert handed.value.status == 201, handed.value.text()
        saved = handed.value.json()
        assert saved["validation"]["status"] == "unverified"
        assert saved["validation"]["verified"] is False
        expect(page.get_by_text("已生成待验证的检查候选。尚未发布，也不会开始审核。")).to_be_visible()
        expect(page.get_by_text("原文没有变化。已确认的含义已变化，检查需要重新验证。")).to_be_visible()
        with f.store._conn() as conn:
            heads = conn.execute(
                "SELECT count(*) FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key=%s",
                (f.tenant_id, f.suite_key),
            ).fetchone()[0]
        assert heads == 0
        context.clear_cookies()
        context.add_cookies([{"name": SESSION_COOKIE, "value": f.renew_session(), "url": f.web, "httpOnly": True}])
        page.reload()
        expect(page.get_by_role("region", name="系统是这样理解的 c1", exact=True)).to_contain_text("规则解释已确认。这只确认需求含义，不是检查功能已验证。")
        expect(page.get_by_role("region", name="系统是这样理解的 c1", exact=True)).to_contain_text("恰好关联一条")
        calls_before_switch = f.script.calls
        switched = browser.new_context()
        switched.add_cookies(context.cookies())
        switched_page = switched.new_page()
        switched_page.goto(page.url)
        switched_view = switched_page.get_by_role("region", name="系统是这样理解的 c1", exact=True)
        expect(switched_view).to_contain_text("规则解释已确认。这只确认需求含义，不是检查功能已验证。")
        expect(switched_view).to_contain_text("恰好关联一条")
        switched.close()
        assert f.script.calls == calls_before_switch
        revised_section = page.get_by_role("region", name="系统是这样理解的 c1", exact=True)
        revised_section.get_by_role("textbox", name="要改成的含义", exact=True).fill("改成至少一条")
        revised_section.get_by_role("button", name="修改理解", exact=True).click()
        expect(revised_section.get_by_role("status").filter(has_text="至少关联")).to_be_visible()
        revised_section.get_by_role("button", name="确认这一理解", exact=True).click()
        expect(revised_section).to_contain_text("规则解释已确认。这只确认需求含义，不是检查功能已验证。")
        revised = httpx.get(f"{f.api}/rules/{f.rule_id}/formalizations/c1", cookies={SESSION_COOKIE: f.session}, timeout=10)
        assert revised.status_code == 200, revised.text
        revised_body = revised.json()
        assert len(revised_body["confirmations"]) == 2
        assert sum(item["current"] for item in revised_body["confirmations"]) == 1
        current_confirmation = next(item for item in revised_body["confirmations"] if item["current"])
        latest_candidate = revised_body["candidates"][-1]
        assert current_confirmation["candidate_id"] == latest_candidate["id"]
        assert "至少关联" in latest_candidate["cnl_text"]
        assert "forall (t2)" not in latest_candidate["clif_content"]
        from product.store.suites import SuiteStore

        frozen = SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(saved["id"], f.tenant_id)
        frozen_rules = json.loads(frozen.files["rules.json"])
        handed_rules = json.loads(saved["files"]["rules.json"])
        assert frozen_rules["formalizations"]["c1"]["cnl_text"] == handed_rules["formalizations"]["c1"]["cnl_text"]
        assert frozen_rules["formalizations"]["c1"]["confirmed_candidate_id"] == handed_rules["formalizations"]["c1"]["confirmed_candidate_id"]
        assert latest_candidate["id"] != handed_rules["formalizations"]["c1"]["confirmed_candidate_id"]
        assert "至少关联" not in frozen_rules["formalizations"]["c1"]["cnl_text"]
        assert f.script.calls == 7
        assert len(generations) == 7
        posts = len(generations)
        f.script.fail_next = True
        digest = httpx.get(f"{f.api}/rules/{f.rule_id}/formalizations/c3", cookies={SESSION_COOKIE: f.session}, timeout=10).json()["source_rule_digest"]
        unknown = httpx.post(
            f"{f.api}/rules/{f.rule_id}/formalizations/c3/generations",
            cookies={SESSION_COOKIE: f.session},
            timeout=10,
            json={"consume_model": True, "operation_id": str(uuid4()), "expected_source_digest": digest},
        )
        assert unknown.status_code == 202, unknown.text
        operation_id = unknown.json()["operation_id"]
        state = ""
        for _ in range(50):
            receipt = httpx.get(f"{f.api}/rule-formalization-generations/{operation_id}", cookies={SESSION_COOKIE: f.session}, timeout=10)
            state = receipt.json().get("state", "")
            if state == "unknown":
                break
            time.sleep(0.1)
        assert state == "unknown", state
        assert f.script.calls == 7
        page.reload()
        expect(page.get_by_role("region", name="系统是这样理解的 c3", exact=True)).to_contain_text("生成结果尚未确定。请读取原操作，不要再次生成。")
        page.reload()
        expect(page.get_by_role("region", name="系统是这样理解的 c3", exact=True)).to_contain_text("生成结果尚未确定。请读取原操作，不要再次生成。")
        assert len(generations) == posts
        assert f.script.calls == 7
        removed = httpx.delete(f"{f.api}/me/model-config", cookies={SESSION_COOKIE: f.session}, timeout=10)
        assert removed.status_code == 200, removed.text
        page.reload()
        plain = page.get_by_role("region", name="系统是这样理解的 c4", exact=True)
        plain.get_by_role("button", name="让系统解释这条规则", exact=True).click()
        expect(plain).to_contain_text("个人模型还不可用。请先配置模型，然后再解释。")
        assert f.script.calls == 7
        assert len(generations) == 8
        page.set_viewport_size({"width": 320, "height": 720})
        confirmed = page.get_by_role("region", name="系统是这样理解的 c1", exact=True)
        advanced = confirmed.get_by_role("button", name="查看形式化表达（高级）", exact=True)
        advanced.focus()
        expect(advanced).to_be_focused()
        page.keyboard.press("Enter")
        copy = confirmed.get_by_role("button", name="复制形式规范", exact=True)
        expect(copy).to_be_visible()
        copy.click()
        expect(confirmed.get_by_text("形式规范已复制。", exact=True)).to_be_visible()
        copied = page.evaluate("() => navigator.clipboard.readText()")
        assert copied.strip()
        assert "<script" not in copied
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, overflow
        page.get_by_role("link", name="返回审核规则", exact=True).click()
        search = page.get_by_role("searchbox", name="搜索审核规则", exact=True)
        search.click()
        for _ in range(15):
            if search.evaluate("element => element === document.activeElement"):
                break
            page.keyboard.press("Tab")
        expect(search).to_be_focused()
        assert search.evaluate("element => element.getBoundingClientRect().height") >= 44
        search.fill("编号规则")
        page.keyboard.press("Enter")
        expect(page.get_by_role("region", name="系统是这样理解的 c1", exact=True)).to_be_visible()
        page.get_by_role("link", name="返回审核规则", exact=True).click()
        search = page.get_by_role("searchbox", name="搜索审核规则", exact=True)
        search.fill("不会命中的词")
        expect(page.get_by_role("heading", name="没有匹配的规则", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="编号规则", exact=True)).to_have_count(0)
        page.keyboard.press("Escape")
        expect(search).to_have_value("")
        expect(search).to_be_focused()
        page.get_by_role("searchbox", name="搜索审核规则", exact=True).fill("无检查")
        bare_row = page.get_by_role("listitem").filter(has_text="无检查规则")
        expect(bare_row).to_contain_text("检查功能尚待完善")
        expect(bare_row).not_to_contain_text("核对规则")
        bare = bare_row.get_by_role("link", name="无检查规则")
        expect(bare).to_be_visible()
        bare.click()
        expect(page.get_by_text("检查功能尚待完善。确认规则解释也不会开始审核。", exact=True)).to_be_visible()
        with f.store._conn() as conn:
            conn.execute("UPDATE product.rule_versions SET body = jsonb_set(body, '{rules,0,rule_to_check}', '\"另一句原文\"') WHERE id=%s", (f.rule_id,))
        page.get_by_role("link", name="返回审核规则", exact=True).click()
        page.get_by_role("searchbox", name="搜索审核规则", exact=True).fill("编号规则")
        page.get_by_role("link", name="编号规则").click()
        changed = page.get_by_role("region", name="系统是这样理解的 c1", exact=True)
        expect(changed).to_contain_text("原文或范围已变化")
        expect(changed.get_by_role("button", name="确认这一理解", exact=True)).to_be_disabled()
        expect(changed.get_by_role("button", name="让系统解释这条规则", exact=True)).to_be_visible()
        expect(changed).to_contain_text("以前的理解仍可阅读")
        browser.close()
