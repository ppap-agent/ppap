"""Real PostgreSQL: a clause, or part of one, with no checkable criterion is rejected, confirmed and never passes.

No model key is configured: rejecting needs no model call and queues no job.
"""

import hashlib
import json
from uuid import uuid4

from agent.execution.audit_runtime import run_audit
from agent.execution.checker_guest import _formal_clause
from api import create_app
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.contracts import RuleInput
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from product import build_product_store
from product.formalization.text import accept_text_points
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb
from pydantic import SecretStr

VAGUE = "零件生产过程追溯性，及过程先进先出是否满足要求？"
MIXED = "S/O/D 均为 1 至 10 的整数，O 与失效起因和预防控制匹配。"
PROGRAM = """from audit.contracts import AuditResultItem

def check(context):
    context.cache.setdefault('ran', []).append(context.clause_id)
    return AuditResultItem(classification='test', item=context.clause_id, priority='P0', status='PASS', suggestions='S/O/D 范围已核对', clause_id=context.clause_id)
""".encode()


def clause(clause_id: str, text: str) -> dict:
    return {"id": clause_id, "category": "条款", "priority": "P0", "rule_to_check": text, "type_of_rule": "text"}


def test_rejected_clauses_are_confirmed_handed_over_and_never_pass(auth_store_factory):
    settings = Settings(feishu_app_id="formalization-rejection", git_sha="d" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-rejection", "alice")
    client = TestClient(create_app(settings, auth_store=build_product_store(settings, objects=store.objects, role="api")))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    rules = {"type": "自定义", "rules": [clause("u08", VAGUE), clause("u07", MIXED)]}
    suite = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:rejection",
        files={"rules.json": json.dumps(rules, ensure_ascii=False).encode(), "checker.py": b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"},
        created_by=alice.user_id,
    )
    rule_id = str(uuid4())
    checked = {"registration": json.dumps({"basis": {"clauses": [{"id": "u08", "checked": True}, {"id": "u07", "checked": True}]}}, ensure_ascii=False)}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'规则',%s,%s,%s)",
            (rule_id, alice.tenant_id, Jsonb({**rules, "kind": "engine", "suite_key": "custom:rejection", "suite_revision_id": suite.id}), alice.user_id, Jsonb(checked)),
        )
        jobs_before = conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0]
    base = f"/rules/{rule_id}/formalizations"

    def detail(clause_id: str) -> dict:
        response = client.get(f"{base}/{clause_id}")
        assert response.status_code == 200, response.text
        return response.json()

    def confirm(clause_id: str, candidate: dict) -> dict:
        response = client.post(
            f"{base}/{clause_id}/confirmations",
            json={
                "operation_id": str(uuid4()),
                "candidate_id": candidate["id"],
                "expected_candidate_revision": candidate["formalization_revision"],
                "expected_source_digest": detail(clause_id)["source_rule_digest"],
                "expected_display_digest": candidate["display_digest"],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    # The whole clause, rejected before anything was generated.
    digest = detail("u08")["source_rule_digest"]
    request = {"operation_id": str(uuid4()), "expected_source_digest": digest, "scope": "clause", "reason": "没有说明须追溯哪些信息、先进先出在哪些工序体现"}
    assert client.post(f"{base}/u08/rejections", json={**request, "reason": " "}).json()["error"] == "invalid_rejection"
    made = client.post(f"{base}/u08/rejections", json=request)
    assert made.status_code == 201, made.text
    whole = made.json()
    assert whole["completeness"] == "complete" and whole["obligations"] == [] and whole["unresolved"] == []
    assert whole["rejections"] == [{"id": "x1", "scope": "clause", "parts": [], "reason": request["reason"]}]
    assert "已拒绝（无可程序化判定标准）：整条条款" in whole["cnl_text"]
    assert client.post(f"{base}/u08/rejections", json=request).json()["id"] == whole["id"]
    conflict = client.post(f"{base}/u08/rejections", json={**request, "reason": "另一个原因"})
    assert conflict.status_code == 409 and conflict.json()["error"] == "operation_conflict"
    assert detail("u08")["state"] == "candidate"
    confirm("u08", whole)
    assert detail("u08")["state"] == "rejected"

    # Part of a clause, cut from the tidied points; the rest stays a check point.
    reading = accept_text_points(
        json.dumps({"points": ["S/O/D 均为 1 至 10 的整数", "O 与失效起因和预防控制匹配"], "review": []}, ensure_ascii=False), source_rule_version_id=rule_id, clause_id="u07", clause_text=MIXED
    )
    spec, first = reading["spec"], str(uuid4())
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO product.rule_formalizations(
                 id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,owl_content,clif_content,
                 vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,formal_digest,display_digest,created_by)
               VALUES (%s,%s,%s,'u07',1,%s,%s,NULL,NULL,%s,%s,%s,'[]','complete',%s,%s,%s,%s,%s)""",
            (
                first,
                alice.tenant_id,
                rule_id,
                detail("u07")["source_rule_digest"],
                spec["formal_profile"],
                Jsonb({}),
                Jsonb(spec["obligations"]),
                Jsonb([]),
                reading["cnl_text"],
                reading["cnl_renderer_version"],
                reading["formal_digest"],
                reading["display_digest"],
                alice.user_id,
            ),
        )
    part = {"expected_source_digest": detail("u07")["source_rule_digest"], "based_on_candidate_id": first, "scope": "part", "reason": "何为有效预防没有定义", "obligation_ids": ["p2"]}
    made = client.post(f"{base}/u07/rejections", json={**part, "operation_id": str(uuid4())})
    assert made.status_code == 201, made.text
    cut = made.json()
    assert cut["formalization_revision"] == 2 and [item["text"] for item in cut["obligations"]] == ["S/O/D 均为 1 至 10 的整数"]
    assert cut["rejections"][0]["parts"] == ["O 与失效起因和预防控制匹配"]
    stale = client.post(f"{base}/u07/rejections", json={**part, "operation_id": str(uuid4())})
    assert stale.status_code == 409 and stale.json()["error"] == "candidate_stale"
    confirm("u07", cut)
    listed = {row["clause_id"]: row for row in client.get(base).json()["clauses"]}
    assert listed["u08"]["state"] == "rejected" and listed["u08"]["rejections"][0]["reason"] == request["reason"]
    assert listed["u07"]["state"] == "confirmed" and listed["u07"]["rejections"][0]["reason"] == "何为有效预防没有定义"

    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == jobs_before

    # Handed to a suite: the snapshot carries the rejections and passes the generator's seal check.
    handed = client.post(f"/rules/{rule_id}/formalization-suite-candidates", json={"operation_id": str(uuid4()), "suite_revision_id": suite.id})
    assert handed.status_code == 201, handed.text
    frozen = json.loads(SuiteStore(settings, store.objects, role="migrator").resolve_suite(handed.json()["id"], alice.tenant_id).files["rules.json"])
    snapshots = frozen["formalizations"]
    assert snapshots["u08"]["rejections"][0]["scope"] == "clause" and snapshots["u07"]["rejections"][0]["scope"] == "part"
    rule_input = RuleInput.model_validate({**frozen, "rule_id": rule_id})
    whole_line, whole_snapshot = _formal_clause(rule_input, "u08", snapshots["u08"])
    part_line, part_snapshot = _formal_clause(rule_input, "u07", snapshots["u07"])
    assert whole_snapshot and part_snapshot
    assert "已被用户整条拒绝" in whole_line and "不要为这些部分编写检查逻辑" in part_line

    # At audit time only the frozen snapshot is read: the whole rejection runs nothing, the part cannot pass.
    files = {"checker.py": PROGRAM}
    program = checker_digest(files)
    sources = {"target": b"original"}
    inputs = (FrozenInput("target", hashlib.sha256(b"original").hexdigest(), "target", "target.xlsx"),)
    target = FrozenTarget("target", handed.json()["id"], "custom:rejection", "a" * 64, frozen, RuleBinding("custom:rejection", program, {"u08": "checker:check", "u07": "checker:check"}))
    result = run_audit(ExecutionSnapshot(alice.tenant_id, alice.user_id, inputs, (target,), "runtime", "library", {}), sources, {program: files})
    by_clause = {row["clause_id"]: row for row in result["findings"]}
    assert by_clause["u08"]["status"] == "UNABLE" and request["reason"] in by_clause["u08"]["suggestions"] and "请人工复核" in by_clause["u08"]["suggestions"]
    assert by_clause["u07"]["status"] == "UNABLE" and by_clause["u07"]["suggestions"].startswith("已定义部分：S/O/D 范围已核对")
    assert "何为有效预防没有定义" in by_clause["u07"]["suggestions"]

    # Withdrawn, it waits for the person again; a changed source starts over.
    current = next(item for item in detail("u08")["confirmations"] if item["current"])
    assert client.post(f"{base}/u08/confirmations/{current['id']}/revocation").status_code == 200
    assert detail("u08")["state"] == "candidate"
    with store._conn() as conn:
        conn.execute("UPDATE product.rule_versions SET case_context=%s WHERE id=%s", (Jsonb({**checked, "note": "原文出处已更新"}), rule_id))
    assert detail("u07")["state"] == "source_changed"
    moved = client.post(f"{base}/u08/rejections", json={**request, "operation_id": str(uuid4())})
    assert moved.status_code == 409 and moved.json()["error"] == "source_changed"
    # The whole clause can be rejected again on the new source; a part needs a reading of the new source.
    fresh = detail("u07")["source_rule_digest"]
    again = client.post(
        f"{base}/u07/rejections", json={**part, "operation_id": str(uuid4()), "expected_source_digest": fresh, "based_on_candidate_id": cut["id"], "obligation_ids": [], "parts": ["后果与 S 明显不符"]}
    )
    assert again.status_code == 409 and again.json()["error"] == "source_changed"
    redo = client.post(f"{base}/u07/rejections", json={"operation_id": str(uuid4()), "expected_source_digest": fresh, "based_on_candidate_id": cut["id"], "scope": "clause", "reason": "整条转人工"})
    assert redo.status_code == 201, redo.text
    assert detail("u07")["state"] == "candidate"


def test_a_rewrite_keeps_the_rejected_parts_out(auth_store_factory):
    from documents.ai import AIResult
    from product.store.model_credentials import TenantModelStore
    from worker import process_claimed

    settings = Settings(feishu_app_id="formalization-rejection-rewrite", git_sha="d" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-rejection-rewrite", "alice")
    models = TenantModelStore(store)
    models.put(alice.tenant_id, models.candidate(alice.tenant_id, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", text_model="test-model"), operator="test")
    rule_id = str(uuid4())
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'规则',%s,%s)",
            (rule_id, alice.tenant_id, Jsonb({"kind": "engine", "type": "自定义", "rules": [clause("u07", MIXED)]}), alice.user_id),
        )
    client = TestClient(create_app(settings, auth_store=build_product_store(settings, objects=store.objects, role="api")))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    worker = build_product_store(settings, objects=store.objects, role="worker")
    base = f"/rules/{rule_id}/formalizations/u07"
    digest = client.get(base).json()["source_rule_digest"]
    prompts: list[str] = []

    def model(points: list[str]):
        def call(_task, **kwargs):
            prompts.append(kwargs["prompt"])
            return AIResult({"points": points, "review": []}, 1, 1)

        return call

    def generate(body: dict, points: list[str]) -> dict:
        operation_id = str(uuid4())
        assert client.post(f"{base}/generations", json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": digest, **body}).status_code == 202
        process_claimed(worker, worker.claim_job("rewrite-owner"), "rewrite-owner", rule_ai=model(points))
        receipt = client.get(f"/rule-formalization-generations/{operation_id}").json()
        assert receipt["state"] == "completed", receipt
        return next(item for item in client.get(base).json()["candidates"] if item["id"] == receipt["candidate_id"])

    first = generate({}, ["S/O/D 均为 1 至 10 的整数", "O 与失效起因和预防控制匹配"])
    cut = client.post(
        f"{base}/rejections",
        json={"operation_id": str(uuid4()), "expected_source_digest": digest, "based_on_candidate_id": first["id"], "scope": "part", "reason": "何为有效预防没有定义", "obligation_ids": ["p2"]},
    )
    assert cut.status_code == 201, cut.text
    rewritten = generate({"based_on_candidate_id": cut.json()["id"], "user_instruction": "写得更短"}, ["S/O/D 为 1 至 10 的整数"])
    assert rewritten["rejections"] == cut.json()["rejections"]
    assert "已拒绝（无可程序化判定标准）：O 与失效起因和预防控制匹配" in rewritten["cnl_text"]
    assert "rejected_parts" not in prompts[0] and "O 与失效起因和预防控制匹配" in json.loads(prompts[1].split("\n", 1)[1])["rejected_parts"]
