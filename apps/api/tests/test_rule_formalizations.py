"""Real PostgreSQL formalization generation, confirmation and tenant boundary.

The scripted model proves the software contract. It is not model-quality acceptance.
"""

import json
import uuid

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from pydantic import SecretStr
from worker import process_claimed

NS = "https://example.test/ns#"
OWL = f"""Prefix(ex:=<{NS}>)
Prefix(xsd:=<http://www.w3.org/2001/XMLSchema#>)
Prefix(ppap:=<https://ppap.example/formal/v1#>)
Ontology(
  Declaration(Class(ex:Document))
  Declaration(DataProperty(ex:code))
  SubClassOf(Annotation(ppap:axiomId "present") ex:Document DataSomeValuesFrom(ex:code xsd:string))
)
"""


def test_formalization_problems_are_in_the_openapi_contract():
    from api import app

    spec = app.openapi()
    schema = spec["components"]["schemas"]["FormalizationProblem"]
    assert schema["properties"]["error"]["type"] == "string"
    paths = spec["paths"]
    confirm = paths["/rules/{rule_version_id}/formalizations/{clause_id}/confirmations"]["post"]["responses"]
    generate = paths["/rules/{rule_version_id}/formalizations/{clause_id}/generations"]["post"]["responses"]
    handoff = paths["/rules/{rule_version_id}/formalization-suite-candidates"]["post"]["responses"]
    read = paths["/rules/{rule_version_id}/formalizations/{clause_id}"]["get"]["responses"]
    for code in ("401", "403", "409", "422"):
        for responses in (confirm, generate, handoff):
            assert responses[code]["content"]["application/json"]["schema"]["$ref"].endswith("/FormalizationProblem")
    for code in ("401", "403", "404", "422"):
        assert read[code]["content"]["application/json"]["schema"]["$ref"].endswith("/FormalizationProblem")
    assert "503" in generate


class ScriptedFormalizer:
    def __init__(self, raw: str, before=None):
        self.raw = raw
        self.calls = 0
        self.before = before

    def structured_completion(self, **_kwargs):
        from audit.engine.checkers.client import TextCompletion

        self.calls += 1
        if self.before:
            self.before()
        usage = getattr(self, "last_usage", None)
        numbers = usage if isinstance(usage, dict) else {}

        def number(key: str) -> int | None:
            value = numbers.get(key)
            return value if type(value) is int and value >= 0 else None

        return TextCompletion(self.raw, number("input_tokens"), number("output_tokens"), number("thinking_tokens"))


def _proposal(rule_id: str) -> str:
    return json.dumps(
        {
            "format_version": "1",
            "formal_profile": "owl2-dl-v1",
            "owl_content": OWL,
            "clif_content": None,
            "vocabulary": {"prefixes": {"ex": NS}, "entities": [{"iri": NS + "Document", "kind": "class", "label": "文档"}, {"iri": NS + "code", "kind": "dataProperty", "label": "编号"}]},
            "source_rule_version_id": rule_id,
            "clause_id": "c1",
            "obligations": [{"id": "o1", "source_span": "0:2", "semantic_kind": "field_presence", "formal_ref": "present"}],
            "unresolved": [],
            "completeness": "complete",
            "assumptions": [],
            "cnl_text": "规则解释已确认，全部符合",
        },
        ensure_ascii=False,
    )


def test_generation_confirmation_is_durable_authorized_and_not_retried(auth_store_factory):
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="rule-formalization", git_sha="a" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization", "alice")
    carol = store.upsert_feishu_identity("other", "carol")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    other = TestClient(create_app(settings, auth_store=api))
    other.cookies.set(SESSION_COOKIE, store.create_session(carol))

    denied = other.get(f"/rules/{rule_id}/formalizations")
    assert denied.status_code == 403
    assert "owl" not in denied.text.lower()
    denied_write = other.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"consume_model": True, "operation_id": str(uuid.uuid4()), "expected_source_digest": "a" * 64},
    )
    assert denied_write.status_code == 403
    assert "owl" not in denied_write.text.lower()

    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert detail.status_code == 200
    digest = detail.json()["source_rule_digest"]
    assert detail.json()["state"] == "not_started"
    blank_id = str(uuid.uuid4())
    blank_body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "   ", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'空',%s,%s)", (blank_id, alice.tenant_id, Jsonb(blank_body), alice.user_id))
    blank = client.get(f"/rules/{blank_id}/formalizations/c1")
    assert blank.status_code == 200
    blank_post = client.post(
        f"/rules/{blank_id}/formalizations/c1/generations",
        json={"consume_model": True, "operation_id": str(uuid.uuid4()), "expected_source_digest": blank.json()["source_rule_digest"]},
    )
    assert blank_post.status_code == 422
    assert blank_post.json()["error"] == "invalid_formalization"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.rule_formalization_operations WHERE rule_version_id=%s", (blank_id,)).fetchone()[0] == 0
    operation_id = str(uuid.uuid4())
    unavailable = client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": digest})
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "model_unavailable"
    loose = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"consume_model": True, "operation_id": str(uuid.uuid4()), "expected_source_digest": digest, "clarification_answer": {"choice_id": "exact"}},
    )
    assert loose.status_code == 422
    assert loose.json()["error"] == "invalid_formalization"

    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    created = client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": digest})
    assert created.status_code == 202
    queued = client.get(f"/rules/{rule_id}/formalizations/c1").json()
    assert queued["operation"]["operation_id"] == operation_id
    assert queued["operation"]["state"] == "queued"
    again = client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": digest})
    assert again.status_code == 202
    assert again.json()["operation_id"] == created.json()["operation_id"]
    same = client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": str(uuid.uuid4()), "expected_source_digest": digest})
    assert same.status_code == 202
    assert same.json()["operation_id"] == operation_id
    second = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"consume_model": True, "operation_id": str(uuid.uuid4()), "expected_source_digest": digest, "user_instruction": "另一句"},
    )
    assert second.status_code == 409
    assert second.json()["error"] == "operation_conflict"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='rule_formalization' AND tenant_id=%s", (alice.tenant_id,)).fetchone()[0] == 1
    conflict = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": digest, "user_instruction": "改成恰好一条"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "operation_conflict"

    def expire_formalization_lease() -> None:
        with store._conn() as conn:
            conn.execute("UPDATE engine.jobs SET lease_until = clock_timestamp() - interval '1 second' WHERE kind='rule_formalization' AND status='leased'")

    script = ScriptedFormalizer(_proposal(rule_id), before=expire_formalization_lease)
    claimed = worker.claim_job("owner")
    assert claimed is not None and claimed.kind == "rule_formalization"
    process_claimed(worker, claimed, "owner", rule_ai=script)
    assert script.calls == 1
    process_claimed(worker, claimed, "owner", rule_ai=script)
    assert script.calls == 1
    receipt = client.get(f"/rule-formalization-generations/{operation_id}")
    assert receipt.status_code == 200
    assert receipt.json()["state"] == "completed"
    assert receipt.json()["usage"]["uncertain"] is True
    identity = receipt.json()["model_identity"]
    assert identity["model"] == "test-model"
    assert identity["provider"] and identity["revision"]
    assert "test-only-no-provider-called" not in receipt.text
    candidate_id = receipt.json()["candidate_id"]
    reading = client.get(f"/rules/{rule_id}/formalizations/c1").json()
    candidate = next(item for item in reading["candidates"] if item["id"] == candidate_id)
    assert "规则解释已确认" not in candidate["cnl_text"]
    assert "至少有一个值" in candidate["cnl_text"]
    assert candidate["completeness"] == "complete"
    with store._conn() as conn:
        operation_count = conn.execute("SELECT count(*) FROM product.rule_formalization_operations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0]
    bogus = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={
            "consume_model": True,
            "operation_id": str(uuid.uuid4()),
            "expected_source_digest": digest,
            "based_on_candidate_id": candidate_id,
            "clarification_answer": {"unresolved_id": "missing", "choice_id": "not-a-choice"},
        },
    )
    assert bogus.status_code == 422
    assert bogus.json()["error"] == "invalid_formalization"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.rule_formalization_operations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0] == operation_count

    confirmation = {
        "candidate_id": candidate_id,
        "expected_candidate_revision": candidate["formalization_revision"],
        "expected_source_digest": digest,
        "expected_display_digest": candidate["display_digest"],
    }
    from concurrent.futures import ThreadPoolExecutor

    from product.store.formalizations import FormalizationStore

    operations = [str(uuid.uuid4()), str(uuid.uuid4())]

    def confirm_once(operation):
        local = FormalizationStore(build_product_store(settings, objects=store.objects, role="api"))
        try:
            return local.confirm(alice, rule_id, "c1", {**confirmation, "operation_id": operation})
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        raced = list(pool.map(confirm_once, operations))
    wins = [item for item in raced if isinstance(item, dict)]
    assert len(wins) == 1, raced
    assert sorted(item for item in raced if isinstance(item, str)) == ["conflicting_confirmation"]
    winner = next(operation for operation in operations if any(item.get("operation_id") == operation for item in wins))
    replay = client.post(f"/rules/{rule_id}/formalizations/c1/confirmations", json={**confirmation, "operation_id": winner})
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == wins[0]["id"]
    stale = {**confirmation, "operation_id": str(uuid.uuid4())}
    assert client.post(f"/rules/{rule_id}/formalizations/c1/confirmations", json=stale).status_code == 409
    assert client.get(f"/rules/{rule_id}/formalizations/c1").json()["state"] == "confirmed"
    assert other.get(f"/rules/{rule_id}/formalizations/c1").status_code == 403
    auditor = store.upsert_feishu_identity("formalization", "auditor")
    reader = TestClient(create_app(settings, auth_store=api))
    reader.cookies.set(SESSION_COOKIE, store.create_session(auditor))
    shared = reader.get(f"/rules/{rule_id}/formalizations/c1")
    assert shared.status_code == 200, shared.text
    assert shared.json()["state"] == "confirmed"
    assert shared.json()["operation"] is None
    assert "至少有一个值" in shared.text
    assert "test-model" not in shared.text
    assert reader.get(f"/rule-formalization-generations/{operation_id}").status_code == 403
    assert client.get(f"/rules/{rule_id}/formalizations/missing").status_code == 404

    from product.store.suites import SuiteStore

    suite_key = "custom:" + rule_id
    rules = {"type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    program = b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"
    appendix = b'["keep-me"]'
    related = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:related",
        files={
            "rules.json": json.dumps({"type": "关联", "rules": [{"id": "r1", "category": "列", "priority": "P0", "rule_to_check": "关联条款", "type_of_rule": "text"}]}).encode(),
            "checker.py": program,
        },
        created_by=alice.user_id,
    )
    related_adopted = client.put(
        "/suites/custom:related/active",
        json={"revision_id": related.id, "expected_revision": 0, "operation_id": str(uuid.uuid4()), "force_unverified": True},
    )
    assert related_adopted.status_code == 200, related_adopted.text
    base = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key=suite_key,
        files={"rules.json": json.dumps(rules).encode(), "checker.py": program, "appendix.json": appendix},
        related_suite_keys=("custom:related",),
        created_by=alice.user_id,
    )
    with store._conn() as conn:
        conn.execute(
            "UPDATE product.rule_versions SET body = body || %s::jsonb WHERE id = %s",
            (Jsonb({"suite_key": suite_key, "suite_revision_id": base.id}), rule_id),
        )
    other_suite = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:other",
        files={"rules.json": json.dumps(rules).encode(), "checker.py": program},
        created_by=alice.user_id,
    )
    mismatch = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": other_suite.id},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"] == "suite_key_mismatch"
    changed_clause = {**rules["rules"][0], "rule_to_check": "另一句原文"}
    changed_text = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key=suite_key,
        files={"rules.json": json.dumps({**rules, "rules": [changed_clause]}).encode(), "checker.py": program},
        created_by=alice.user_id,
    )
    text_mismatch = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": changed_text.id},
    )
    assert text_mismatch.status_code == 409
    assert text_mismatch.json()["error"] == "source_changed"
    from audit.standard_suites import library_revision

    proof_digest = "ab" * 32
    with store._conn() as conn:
        related_digest = conn.execute("SELECT resolved_digest FROM product.suite_revisions WHERE id=%s", (related.id,)).fetchone()[0]
        conn.execute(
            """INSERT INTO product.suite_validations(tenant_id, revision_id, status, revision, suite_digest, related_suite_digests, proof)
               VALUES (%s, %s, 'verified', 1, %s, %s, %s)""",
            (
                alice.tenant_id,
                base.id,
                base.resolved_digest,
                Jsonb({"custom:related": related_digest}),
                Jsonb({"reasons": [], "total_samples": 4, "passed_samples": 4, "new_errors": 0, "library_revision": library_revision(), "proof_digest": proof_digest, "report": {}}),
            ),
        )
    forged = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": base.id, "owl_content": "FORGED-OWL", "confirmed": True},
    )
    assert forged.status_code == 422, forged.text
    handoff = str(uuid.uuid4())
    materialized = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": handoff, "suite_revision_id": base.id},
    )
    assert materialized.status_code == 201, materialized.text
    payload = materialized.json()
    saved_rules = json.loads(payload["files"]["rules.json"])
    assert saved_rules["rules"][0]["rule_to_check"] == "文档要有编号"
    assert "至少有一个值" in saved_rules["formalizations"]["c1"]["cnl_text"]
    assert saved_rules["formalizations"]["c1"]["rule_version_id"] == rule_id
    assert saved_rules["formalizations"]["c1"]["confirmed_candidate_id"] == candidate_id
    assert saved_rules["formalizations"]["c1"]["source_suite_revision_id"] == base.id
    assert saved_rules["formalizations"]["c1"]["source_rule_version_id"] == rule_id
    assert saved_rules["formalizations"]["c1"]["unresolved"] == []
    from agent.execution.checker_guest import _prompt
    from audit.contracts import AuditInput, RuleInput

    guided = _prompt(
        RuleInput.model_validate({"rule_id": rule_id, "rules": saved_rules["rules"], "appendix": [], "formalizations": saved_rules["formalizations"]}),
        None,
        None,
        AuditInput(audit_id="file-1", file_url="sheet.xlsx", file_metadata={"file_version_id": "file-1", "sha256": "a" * 64, "role": "target"}),
        None,
    )
    assert "条款 c1 的已确认形式要求" in guided
    assert "形式快照未通过核验" not in guided
    assert payload["rule_impact"]["changed"] is True
    assert payload["rule_impact"]["appendix_changed"] is False
    assert payload["rule_impact"]["clauses"]
    assert {row["kind"] for row in payload["rule_impact"]["clauses"]} == {"unchanged"}
    assert payload["validation"]["status"] == "unverified"
    assert payload["validation"]["verified"] is False
    assert "proof_digest" not in payload["validation"]
    assert "FORGED-OWL" not in payload["files"]["rules.json"]
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_validations WHERE revision_id=%s", (payload["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT status FROM product.suite_validations WHERE revision_id=%s", (base.id,)).fetchone()[0] == "verified"
    assert payload["id"] != base.id
    with store._conn() as conn:
        previous_digest = conn.execute("SELECT resolved_digest FROM product.suite_revisions WHERE id=%s", (base.id,)).fetchone()[0]
    assert payload["resolved_digest"] != previous_digest
    assert payload["files"]["checker.py"] == program.decode()
    assert payload["files"]["appendix.json"] == appendix.decode()
    assert payload["related_suite_keys"] == ["custom:related"]
    with store._conn() as conn:
        revision_count = conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key=%s", (suite_key,)).fetchone()[0]
        original_body = conn.execute("SELECT body FROM product.rule_versions WHERE id=%s", (rule_id,)).fetchone()[0]
        conn.execute("UPDATE product.rule_versions SET body = jsonb_set(body, '{customers}', %s) WHERE id=%s", (Jsonb(["其他客户"]), rule_id))
    scoped_handoff = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": base.id},
    )
    assert scoped_handoff.status_code == 409, scoped_handoff.text
    assert scoped_handoff.json()["error"] == "source_changed"
    with store._conn() as conn:
        conn.execute("UPDATE product.rule_versions SET body = %s WHERE id=%s", (Jsonb(original_body), rule_id))
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key=%s", (suite_key,)).fetchone()[0] == revision_count
    absent = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": str(uuid.uuid4())},
    )
    assert absent.status_code == 403
    assert absent.json()["error"] == "access_denied"
    carol_rule = str(uuid.uuid4())
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)",
            (carol_rule, carol.tenant_id, Jsonb({**body, "suite_key": suite_key, "suite_revision_id": base.id}), carol.user_id),
        )
    foreign = other.post(
        f"/rules/{carol_rule}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": base.id},
    )
    assert foreign.status_code == 403
    assert foreign.json()["error"] == "access_denied"
    assert "至少有一个值" not in foreign.text
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key=%s", (suite_key,)).fetchone()[0] == revision_count
    partial_id, stale_id = str(uuid.uuid4()), str(uuid.uuid4())
    with store._conn() as conn:
        conn.execute(
            "UPDATE product.rule_versions SET body = jsonb_set(body, '{rules}', (body->'rules') || %s) WHERE id=%s",
            (Jsonb([{"id": "c-partial", "category": "列", "priority": "P0", "rule_to_check": "不完整条款", "type_of_rule": "text"}]), rule_id),
        )
        conn.execute(
            """INSERT INTO product.rule_formalizations (
                 id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by)
               SELECT %s,tenant_id,rule_version_id,'c-partial',1,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,%s,'partial',cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by
               FROM product.rule_formalizations WHERE id=%s""",
            (partial_id, Jsonb([{"id": "open", "reason": "尚未选择"}]), candidate_id),
        )
        conn.execute(
            """INSERT INTO product.rule_formalization_confirmations (
                 id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                 source_rule_digest,display_digest,confirmed_by,current)
               SELECT %s,%s,tenant_id,rule_version_id,'c-partial',%s,1,source_rule_digest,display_digest,confirmed_by,true
               FROM product.rule_formalization_confirmations
               WHERE candidate_id=%s AND current LIMIT 1""",
            (str(uuid.uuid4()), str(uuid.uuid4()), partial_id, candidate_id),
        )
        conn.execute(
            """INSERT INTO product.rule_formalizations (
                 id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by)
               SELECT %s,tenant_id,rule_version_id,clause_id,2,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by
               FROM product.rule_formalizations WHERE id=%s""",
            (stale_id, candidate_id),
        )
        conn.execute(
            """INSERT INTO product.rule_formalization_confirmations (
                 id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                 source_rule_digest,display_digest,confirmed_by,current)
               SELECT %s,%s,tenant_id,rule_version_id,clause_id,%s,2,source_rule_digest,display_digest,confirmed_by,false
               FROM product.rule_formalization_confirmations
               WHERE candidate_id=%s AND current LIMIT 1""",
            (str(uuid.uuid4()), str(uuid.uuid4()), stale_id, candidate_id),
        )
    filtered = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": base.id},
    )
    assert filtered.status_code == 201, filtered.text
    handed = json.loads(filtered.json()["files"]["rules.json"])
    assert set(handed["formalizations"]) == {"c1"}
    assert handed["formalizations"]["c1"]["confirmed_candidate_id"] == candidate_id
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key=%s", (suite_key,)).fetchone()[0] == revision_count + 1
    again = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": handoff, "suite_revision_id": base.id},
    )
    assert again.status_code == 201
    assert again.json()["id"] == payload["id"]
    with api._conn() as conn:
        assert conn.execute("SELECT 1 FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key=%s", (alice.tenant_id, suite_key)).fetchone() is None
    active_path = f"/suites/{suite_key}/active"
    adopted = client.put(active_path, json={"revision_id": payload["id"], "expected_revision": 0, "operation_id": str(uuid.uuid4()), "force_unverified": True})
    assert adopted.status_code == 200, adopted.text
    current = client.get(active_path).json()
    assert current["current_revision_id"] == payload["id"]
    restored = client.put(active_path, json={"revision_id": base.id, "expected_revision": current["revision"], "operation_id": str(uuid.uuid4()), "force_unverified": True})
    assert restored.status_code == 200, restored.text
    head = client.get(active_path).json()
    assert head["current_revision_id"] == base.id
    suites = SuiteStore(settings, store.objects, role="api")
    rolled = suites.resolve_suite(head["current_revision_id"], alice.tenant_id)
    kept = suites.resolve_suite(payload["id"], alice.tenant_id)
    rolled_files = json.loads(rolled.files["rules.json"])
    kept_files = json.loads(kept.files["rules.json"])
    assert rolled.files["checker.py"] == program and rolled.files["appendix.json"] == appendix
    assert kept.files["checker.py"] == program and kept.files["appendix.json"] == appendix
    assert list(rolled.revision.related_suite_keys) == ["custom:related"]
    assert list(kept.revision.related_suite_keys) == ["custom:related"]
    assert "formalizations" not in rolled_files
    snapshot = saved_rules["formalizations"]["c1"]
    stored = kept_files["formalizations"]["c1"]
    assert stored["owl_content"] == snapshot["owl_content"]
    assert stored["clif_content"] == snapshot["clif_content"]
    assert stored["cnl_text"] == snapshot["cnl_text"]
    assert stored["formal_digest"] == snapshot["formal_digest"]
    assert stored["display_digest"] == snapshot["display_digest"]
    assert stored["rule_version_id"] == rule_id
    extra = str(uuid.uuid4())
    with store._conn() as conn:
        conn.execute(
            "UPDATE product.rule_versions SET body = jsonb_set(body, '{rules}', (body->'rules') || %s) WHERE id=%s",
            (Jsonb([{"id": "c2", "category": "列", "priority": "P0", "rule_to_check": "另一条", "type_of_rule": "text"}]), rule_id),
        )
        conn.execute(
            """INSERT INTO product.rule_formalizations (
                 id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by)
               SELECT %s,tenant_id,rule_version_id,'c2',1,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by
               FROM product.rule_formalizations WHERE rule_version_id=%s AND clause_id='c1' LIMIT 1""",
            (extra, rule_id),
        )
        conn.execute(
            """INSERT INTO product.rule_formalization_confirmations (
                 id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                 source_rule_digest,display_digest,confirmed_by,current)
               SELECT %s,%s,tenant_id,rule_version_id,'c2',%s,1,source_rule_digest,display_digest,confirmed_by,true
               FROM product.rule_formalization_confirmations
               WHERE rule_version_id=%s AND clause_id='c1' AND current LIMIT 1""",
            (str(uuid.uuid4()), str(uuid.uuid4()), extra, rule_id),
        )
    missing = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": base.id},
    )
    assert missing.status_code == 409
    assert missing.json()["error"] == "not_confirmable"
    wrong_display = {**confirmation, "operation_id": str(uuid.uuid4()), "expected_display_digest": "d" * 64}
    display_conflict = client.post(f"/rules/{rule_id}/formalizations/c1/confirmations", json=wrong_display)
    assert display_conflict.status_code == 409
    assert display_conflict.json()["error"] == "display_changed"

    class TimeoutFormalizer:
        def __init__(self):
            self.calls = 0

        def structured_completion(self, **_kwargs) -> str:
            self.calls += 1
            raise TimeoutError("provider timed out")

    retry = str(uuid.uuid4())
    second = client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": retry, "expected_source_digest": digest, "user_instruction": "再读一次"})
    assert second.status_code == 202, second.text
    claimed_again = worker.claim_job("owner-timeout")
    assert claimed_again is not None and claimed_again.payload.get("operation_id") == retry
    timed_out = TimeoutFormalizer()
    process_claimed(worker, claimed_again, "owner-timeout", rule_ai=timed_out)
    assert timed_out.calls == 1
    process_claimed(worker, claimed_again, "owner-timeout", rule_ai=timed_out)
    assert timed_out.calls == 1
    unknown = client.get(f"/rule-formalization-generations/{retry}")
    assert unknown.status_code == 200
    assert unknown.json()["state"] == "unknown"
    resumed = client.get(f"/rules/{rule_id}/formalizations/c1").json()
    assert resumed["operation"]["operation_id"] == retry
    assert resumed["operation"]["state"] == "unknown"

    with store._conn() as conn:
        before_undecided = conn.execute("SELECT count(*) FROM product.rule_formalization_operations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0]
    posted = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={
            "consume_model": True,
            "operation_id": str(uuid.uuid4()),
            "expected_source_digest": digest,
            "based_on_candidate_id": candidate_id,
            "clarification_answer": {"unresolved_id": "match", "choice_id": "undecided"},
        },
    )
    assert posted.status_code == 422
    assert posted.json()["error"] == "invalid_formalization"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.rule_formalization_operations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0] == before_undecided
    store.put_personal_model_config(auditor, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="auditor-model")
    with store._conn() as conn:
        conn.execute(
            "UPDATE product.rule_versions SET body = jsonb_set(body, '{rules}', (body->'rules') || %s::jsonb) WHERE id=%s",
            (Jsonb([{"id": "c2", "category": "列", "priority": "P0", "rule_to_check": "文档要有日期", "type_of_rule": "text"}]), rule_id),
        )
    c2_digest = client.get(f"/rules/{rule_id}/formalizations/c2").json()["source_rule_digest"]
    alice_open = str(uuid.uuid4())
    assert client.post(f"/rules/{rule_id}/formalizations/c2/generations", json={"consume_model": True, "operation_id": alice_open, "expected_source_digest": c2_digest}).status_code == 202
    auditor_open = str(uuid.uuid4())
    assert reader.post(f"/rules/{rule_id}/formalizations/c2/generations", json={"consume_model": True, "operation_id": auditor_open, "expected_source_digest": c2_digest}).status_code == 202
    assert client.get(f"/rules/{rule_id}/formalizations/c2").json()["operation"]["operation_id"] == alice_open
    assert reader.get(f"/rules/{rule_id}/formalizations/c2").json()["operation"]["operation_id"] == auditor_open
    with store._conn() as conn:
        conn.execute("UPDATE product.rule_versions SET body = jsonb_set(body, '{rules,0,rule_to_check}', '\"另一句原文\"') WHERE id=%s", (rule_id,))
    changed = client.post(f"/rules/{rule_id}/formalizations/c1/confirmations", json={**confirmation, "operation_id": str(uuid.uuid4())})
    assert changed.status_code == 409
    assert changed.json()["error"] == "source_changed"
    reread = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert reread.status_code == 200
    assert reread.json()["state"] == "source_changed"
    assert reread.json()["source_rule_digest"] != digest
    assert reread.json()["operation"] is None
    fresh = str(uuid.uuid4())
    started = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"consume_model": True, "operation_id": fresh, "expected_source_digest": reread.json()["source_rule_digest"]},
    )
    assert started.status_code == 202, started.text
    assert started.json()["operation_id"] == fresh
    assert started.json()["operation_id"] != retry
    with store._conn() as conn:
        conn.execute("UPDATE engine.jobs SET status='cancelled' WHERE kind='rule_formalization' AND status='queued' AND payload->>'operation_id' <> %s", (fresh,))
    claimed = worker.claim_job("owner-refresh")
    assert claimed is not None and claimed.payload.get("operation_id") == fresh
    process_claimed(worker, claimed, "owner-refresh", rule_ai=ScriptedFormalizer(_proposal(rule_id)))
    renewed = client.get(f"/rules/{rule_id}/formalizations/c1").json()
    assert renewed["state"] == "candidate"
    latest = renewed["candidates"][-1]
    current_confirmation = next(item for item in renewed["confirmations"] if item["current"])
    saved = client.post(
        f"/rules/{rule_id}/formalizations/c1/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": latest["id"],
            "expected_candidate_revision": latest["formalization_revision"],
            "expected_source_digest": renewed["source_rule_digest"],
            "expected_display_digest": latest["display_digest"],
            "expected_current_confirmation_id": current_confirmation["id"],
        },
    )
    assert saved.status_code == 201, saved.text
    assert client.get(f"/rules/{rule_id}/formalizations/c1").json()["state"] == "confirmed"


def test_clauses_confirm_separately_and_a_new_version_does_not_inherit(auth_store_factory):
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-clauses", git_sha="b" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-clauses", "alice")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    rule_id, successor = str(uuid.uuid4()), str(uuid.uuid4())
    body = {
        "kind": "engine",
        "type": "自定义",
        "rules": [
            {"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"},
            {"id": "c2", "category": "列", "priority": "P0", "rule_to_check": "文档要有日期", "type_of_rule": "text"},
        ],
    }
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'两条款',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    digest = client.get(f"/rules/{rule_id}/formalizations/c1").json()["source_rule_digest"]
    operation_id = str(uuid.uuid4())
    assert client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": digest}).status_code == 202
    claimed = worker.claim_job("clause-owner")
    assert claimed is not None and claimed.payload.get("operation_id") == operation_id
    process_claimed(worker, claimed, "clause-owner", rule_ai=ScriptedFormalizer(_proposal(rule_id)))
    reading = client.get(f"/rules/{rule_id}/formalizations/c1").json()
    candidate = reading["candidates"][-1]
    confirmed = client.post(
        f"/rules/{rule_id}/formalizations/c1/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": candidate["id"],
            "expected_candidate_revision": candidate["formalization_revision"],
            "expected_source_digest": digest,
            "expected_display_digest": candidate["display_digest"],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    digest_c2 = client.get(f"/rules/{rule_id}/formalizations/c2").json()["source_rule_digest"]
    operation_c2 = str(uuid.uuid4())
    assert client.post(f"/rules/{rule_id}/formalizations/c2/generations", json={"consume_model": True, "operation_id": operation_c2, "expected_source_digest": digest_c2}).status_code == 202
    claimed_c2 = worker.claim_job("clause-two")
    assert claimed_c2 is not None and claimed_c2.payload.get("operation_id") == operation_c2
    process_claimed(worker, claimed_c2, "clause-two", rule_ai=ScriptedFormalizer(_proposal(rule_id)))
    reading_c2 = client.get(f"/rules/{rule_id}/formalizations/c2").json()
    candidate_c2 = reading_c2["candidates"][-1]
    confirmed_c2 = client.post(
        f"/rules/{rule_id}/formalizations/c2/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": candidate_c2["id"],
            "expected_candidate_revision": candidate_c2["formalization_revision"],
            "expected_source_digest": digest_c2,
            "expected_display_digest": candidate_c2["display_digest"],
        },
    )
    assert confirmed_c2.status_code == 201, confirmed_c2.text
    listed = client.get(f"/rules/{rule_id}/formalizations").json()["clauses"]
    assert {item["clause_id"]: item["state"] for item in listed} == {"c1": "confirmed", "c2": "confirmed"}
    assert candidate["id"] != candidate_c2["id"]
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,predecessor_id) VALUES (%s,%s,'两条款',%s,%s,%s)",
            (successor, alice.tenant_id, Jsonb(body), alice.user_id, rule_id),
        )
    fresh = client.get(f"/rules/{successor}/formalizations/c1")
    assert fresh.status_code == 200
    assert fresh.json()["state"] == "not_started"
    assert fresh.json()["candidates"] == []
    assert "至少有一个值" not in fresh.text
    stolen = client.post(
        f"/rules/{successor}/formalizations/c1/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": candidate["id"],
            "expected_candidate_revision": candidate["formalization_revision"],
            "expected_source_digest": fresh.json()["source_rule_digest"],
            "expected_display_digest": candidate["display_digest"],
        },
    )
    assert stolen.status_code == 409
    assert stolen.json()["error"] == "candidate_stale"
    assert client.get(f"/rules/{rule_id}/formalizations/c1").json()["state"] == "confirmed"
    with store._conn() as conn:
        conn.execute(
            "UPDATE product.rule_versions SET body = jsonb_set(jsonb_set(body, '{customers}', %s), '{parts}', %s) WHERE id=%s",
            (Jsonb(["新客户"]), Jsonb(["P-9"]), rule_id),
        )
    scoped = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert scoped.status_code == 200
    assert scoped.json()["state"] == "source_changed"
    assert scoped.json()["source_rule_digest"] != digest
    assert "至少有一个值" in scoped.text
    reused = client.post(
        f"/rules/{rule_id}/formalizations/c1/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": candidate["id"],
            "expected_candidate_revision": candidate["formalization_revision"],
            "expected_source_digest": digest,
            "expected_display_digest": candidate["display_digest"],
        },
    )
    assert reused.status_code == 409
    assert reused.json()["error"] == "source_changed"


def test_revoked_membership_cannot_read_or_spend_a_model_call(auth_store_factory):
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-revoke", git_sha="c" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-revoke", "alice")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert detail.status_code == 200
    operation_id = str(uuid.uuid4())
    assert (
        client.post(
            f"/rules/{rule_id}/formalizations/c1/generations",
            json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": detail.json()["source_rule_digest"]},
        ).status_code
        == 202
    )
    with store._conn() as conn:
        conn.execute("DELETE FROM product.memberships WHERE user_id=%s AND tenant_id=%s", (alice.user_id, alice.tenant_id))
    denied = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert denied.status_code in {401, 403}
    assert "至少有一个值" not in denied.text
    assert "owl" not in denied.text.lower()
    script = ScriptedFormalizer(_proposal(rule_id))
    claimed = worker.claim_job("revoke-owner")
    assert claimed is not None and claimed.payload.get("operation_id") == operation_id
    result = process_claimed(worker, claimed, "revoke-owner", rule_ai=script)
    assert script.calls == 0
    assert result["failure"] == "access_denied"
    receipt = client.get(f"/rule-formalization-generations/{operation_id}")
    assert receipt.status_code in {401, 403}
    with store._conn() as conn:
        state = conn.execute("SELECT state FROM product.rule_formalization_operations WHERE operation_id=%s", (operation_id,)).fetchone()[0]
        assert state == "failed"
        assert conn.execute("SELECT count(*) FROM product.rule_formalizations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0] == 0


def test_a_newer_confirmation_must_name_the_current_one_and_an_empty_model_stops(auth_store_factory):
    """Scripted model calls prove the software contract. They are not model-quality acceptance."""
    from concurrent.futures import ThreadPoolExecutor

    from api import create_app
    from product.store.formalizations import FormalizationStore
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-replace", git_sha="d" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-replace", "alice")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
        before_rounds = conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0]
        before_body = conn.execute("SELECT body FROM product.rule_versions WHERE id=%s", (rule_id,)).fetchone()[0]
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    digest = client.get(f"/rules/{rule_id}/formalizations/c1").json()["source_rule_digest"]

    def operations():
        with store._conn() as conn:
            return conn.execute("SELECT count(*) FROM product.rule_formalization_operations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0]

    assert operations() == 0
    missing_base = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"consume_model": True, "operation_id": str(uuid.uuid4()), "expected_source_digest": digest, "clarification_answer": {"choice_id": "exact"}},
    )
    assert missing_base.status_code == 422
    assert missing_base.json()["error"] == "invalid_formalization"
    assert operations() == 0

    open_choice = json.loads(_proposal(rule_id))
    open_choice["completeness"] = "partial"
    open_choice["unresolved"] = [
        {
            "id": "quantity",
            "reason": "数量没有写明",
            "choices": [{"id": "exact", "summary": "恰好一条", "counterexample": "两条也通过"}],
        }
    ]
    script = ScriptedFormalizer(json.dumps(open_choice))
    opened = str(uuid.uuid4())
    assert client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": opened, "expected_source_digest": digest}).status_code == 202
    claimed = worker.claim_job("replace-open")
    process_claimed(worker, claimed, "replace-open", rule_ai=script)
    partial = client.get(f"/rules/{rule_id}/formalizations/c1").json()["candidates"][-1]
    assert partial["completeness"] == "partial"
    before = operations()
    for payload in (
        {"based_on_candidate_id": partial["id"], "clarification_answer": {"unresolved_id": "quantity"}},
        {"based_on_candidate_id": partial["id"], "clarification_answer": {"unresolved_id": "other", "choice_id": "exact"}},
        {"based_on_candidate_id": partial["id"], "clarification_answer": {"choice_id": "exact"}},
    ):
        rejected = client.post(
            f"/rules/{rule_id}/formalizations/c1/generations",
            json={"consume_model": True, "operation_id": str(uuid.uuid4()), "expected_source_digest": digest, **payload},
        )
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"] == "invalid_formalization"
    assert operations() == before
    undecided = str(uuid.uuid4())
    posted = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={
            "consume_model": True,
            "operation_id": undecided,
            "expected_source_digest": digest,
            "based_on_candidate_id": partial["id"],
            "clarification_answer": {"unresolved_id": "quantity", "choice_id": "undecided"},
        },
    )
    assert posted.status_code == 202, posted.text
    claimed = worker.claim_job("replace-undecided")
    process_claimed(worker, claimed, "replace-undecided", rule_ai=ScriptedFormalizer(_proposal(rule_id)))
    held = client.get(f"/rules/{rule_id}/formalizations/c1").json()
    latest = held["candidates"][-1]
    assert latest["completeness"] == "partial"
    assert latest["unresolved"][-1]["id"] == "undecided"
    assert "尚不能确定" in latest["cnl_text"]
    refused = client.post(
        f"/rules/{rule_id}/formalizations/c1/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": latest["id"],
            "expected_candidate_revision": latest["formalization_revision"],
            "expected_source_digest": digest,
            "expected_display_digest": latest["display_digest"],
        },
    )
    assert refused.status_code == 409
    assert refused.json()["error"] == "not_confirmable"

    complete = str(uuid.uuid4())
    assert (
        client.post(
            f"/rules/{rule_id}/formalizations/c1/generations",
            json={"consume_model": True, "operation_id": complete, "expected_source_digest": digest, "user_instruction": "按原文"},
        ).status_code
        == 202
    )
    claimed = worker.claim_job("replace-complete")
    process_claimed(worker, claimed, "replace-complete", rule_ai=ScriptedFormalizer(_proposal(rule_id)))
    first = client.get(f"/rules/{rule_id}/formalizations/c1").json()["candidates"][-1]
    confirmation = {
        "candidate_id": first["id"],
        "expected_candidate_revision": first["formalization_revision"],
        "expected_source_digest": digest,
        "expected_display_digest": first["display_digest"],
    }
    saved = client.post(f"/rules/{rule_id}/formalizations/c1/confirmations", json={**confirmation, "operation_id": str(uuid.uuid4())})
    assert saved.status_code == 201, saved.text
    current_id = saved.json()["id"]

    revised = str(uuid.uuid4())
    assert (
        client.post(
            f"/rules/{rule_id}/formalizations/c1/generations",
            json={"consume_model": True, "operation_id": revised, "expected_source_digest": digest, "user_instruction": "改成另一句理解"},
        ).status_code
        == 202
    )
    claimed = worker.claim_job("replace-revised")
    process_claimed(worker, claimed, "replace-revised", rule_ai=ScriptedFormalizer(_proposal(rule_id)))
    second = client.get(f"/rules/{rule_id}/formalizations/c1").json()["candidates"][-1]
    replacement = {
        "candidate_id": second["id"],
        "expected_candidate_revision": second["formalization_revision"],
        "expected_source_digest": digest,
        "expected_display_digest": second["display_digest"],
    }
    silent = client.post(f"/rules/{rule_id}/formalizations/c1/confirmations", json={**replacement, "operation_id": str(uuid.uuid4())})
    assert silent.status_code == 409
    assert silent.json()["error"] == "conflicting_confirmation"
    assert client.get(f"/rules/{rule_id}/formalizations/c1").json()["state"] == "candidate"

    def confirm_once(operation):
        local = FormalizationStore(build_product_store(settings, objects=store.objects, role="api"))
        try:
            return local.confirm(alice, rule_id, "c1", {**replacement, "operation_id": operation, "expected_current_confirmation_id": current_id})
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        raced = list(pool.map(confirm_once, [str(uuid.uuid4()), str(uuid.uuid4())]))
    wins = [item for item in raced if isinstance(item, dict)]
    assert len(wins) == 1, raced
    assert sorted(item for item in raced if isinstance(item, str)) == ["conflicting_confirmation"]
    final = client.get(f"/rules/{rule_id}/formalizations/c1").json()
    assert final["state"] == "confirmed"
    assert sum(item["current"] for item in final["confirmations"]) == 1
    assert {item["candidate_id"] for item in final["confirmations"]} == {first["id"], second["id"]}

    stopped = str(uuid.uuid4())
    assert client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": stopped, "expected_source_digest": digest}).status_code == 202
    claimed = worker.claim_job("replace-empty")
    empty = ScriptedFormalizer("")
    process_claimed(worker, claimed, "replace-empty", rule_ai=empty)
    assert empty.calls == 1
    receipt = client.get(f"/rule-formalization-generations/{stopped}")
    assert receipt.status_code == 200
    assert receipt.json()["state"] == "stopped"
    assert receipt.json()["candidate_id"] is None
    illegal = str(uuid.uuid4())
    assert client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": illegal, "expected_source_digest": digest}).status_code == 202
    claimed = worker.claim_job("replace-illegal")
    rejected = process_claimed(worker, claimed, "replace-illegal", rule_ai=ScriptedFormalizer("{"))
    assert rejected["state"] == "failed"
    failed = client.get(f"/rule-formalization-generations/{illegal}")
    assert failed.json()["state"] == "failed"
    assert failed.json()["candidate_id"] is None
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == before_rounds
        assert conn.execute("SELECT body FROM product.rule_versions WHERE id=%s", (rule_id,)).fetchone()[0] == before_body
        assert conn.execute("SELECT count(*) FROM product.tenant_suite_heads WHERE tenant_id=%s", (alice.tenant_id,)).fetchone()[0] == 0


def test_known_provider_usage_is_saved_without_credentials(auth_store_factory):
    """A scripted token receipt is not model-quality acceptance."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-usage", git_sha="e" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-usage", "alice")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    digest = client.get(f"/rules/{rule_id}/formalizations/c1").json()["source_rule_digest"]
    operation_id = str(uuid.uuid4())
    assert client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": digest}).status_code == 202
    script = ScriptedFormalizer(_proposal(rule_id))
    script.last_usage = {"input_tokens": 11, "output_tokens": 7, "thinking_tokens": 2, "api_key": "secret-value", "source": "provider"}
    claimed = worker.claim_job("usage-owner")
    process_claimed(worker, claimed, "usage-owner", rule_ai=script)
    receipt = client.get(f"/rule-formalization-generations/{operation_id}")
    assert receipt.status_code == 200, receipt.text
    usage = receipt.json()["usage"]
    assert usage["uncertain"] is False
    assert usage["source"] == "provider"
    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 7
    assert usage["thinking_tokens"] == 2
    assert "secret-value" not in receipt.text
    assert "test-only-no-provider-called" not in receipt.text


def test_generation_and_confirmation_leave_older_rows_unchanged(auth_store_factory):
    """Generating and confirming a clause does not rewrite older product rows.

    The scripted model proves the software contract. It is not model-quality acceptance.
    """
    from api import create_app
    from product.store.suites import SuiteStore
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="rule-formalization-bytes", git_sha="b" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-bytes", "alice")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    rule_id, file_id, version_id, round_id, report_id = (str(uuid.uuid4()) for _ in range(5))
    marker = "OLD-RULE-BYTES"
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": marker, "type_of_rule": "text"}]}
    suite = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:old-bytes",
        files={"rules.json": json.dumps(body).encode(), "checker.py": b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"},
        created_by=alice.user_id,
    )
    snapshot_body = {"tenant_id": alice.tenant_id, "round_id": round_id, "marker": marker}
    with store._conn() as conn:
        for ident, kind in ((file_id, "file"), (round_id, "audit_round")):
            conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES (%s,%s,%s)", (ident, alice.tenant_id, kind))
        conn.execute("INSERT INTO product.files(id,tenant_id,created_by,original_filename) VALUES (%s,%s,%s,'old.xlsx')", (file_id, alice.tenant_id, alice.user_id))
        conn.execute(
            """INSERT INTO product.file_versions(id,file_id,tenant_id,original_filename,content_type,byte_size,sha256,object_key,status)
               VALUES (%s,%s,%s,'old.xlsx','application/octet-stream',3,%s,'old-object','ready')""",
            (version_id, file_id, alice.tenant_id, "a" * 64),
        )
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'旧规则',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
        conn.execute(
            """INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt)
               VALUES (%s,%s,%s,'old-op','old-fp','completed',1)""",
            (round_id, alice.tenant_id, alice.user_id),
        )
        conn.execute(
            """INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body,rule_execution)
               VALUES (%s,%s,'target',%s,'旧规则',%s,%s)""",
            (round_id, version_id, rule_id, Jsonb(body), Jsonb({"old": marker})),
        )
        conn.execute(
            """INSERT INTO product.checker_execution_snapshots(round_id,tenant_id,input_digest,body)
               VALUES (%s,%s,%s,%s)""",
            (round_id, alice.tenant_id, "c" * 64, Jsonb(snapshot_body)),
        )
        conn.execute(
            """INSERT INTO product.report_exports(id,round_id,tenant_id,created_by,fingerprint,status)
               VALUES (%s,%s,%s,%s,%s,'ready')""",
            (report_id, round_id, alice.tenant_id, alice.user_id, marker),
        )

    def frozen_rows():
        with store._conn() as conn:

            def cell(statement, args):
                return conn.execute(statement, args).fetchone()[0]

            return {
                "rule": cell("SELECT to_jsonb(t) FROM product.rule_versions t WHERE id=%s", (rule_id,)),
                "suite": cell("SELECT to_jsonb(t) FROM product.suite_revisions t WHERE id=%s", (suite.id,)),
                "suite_objects": cell("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY digest), '[]'::jsonb) FROM product.suite_objects t WHERE owner_scope=%s", (alice.tenant_id,)),
                "round_input": cell("SELECT to_jsonb(t) FROM product.round_inputs t WHERE round_id=%s", (round_id,)),
                "snapshot": cell("SELECT to_jsonb(t) FROM product.checker_execution_snapshots t WHERE round_id=%s", (round_id,)),
                "report": cell("SELECT to_jsonb(t) FROM product.report_exports t WHERE id=%s", (report_id,)),
                "rounds": cell("SELECT count(*) FROM product.audit_rounds", ()),
            }

    before = frozen_rows()
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert detail.status_code == 200, detail.text
    operation_id = str(uuid.uuid4())
    created = client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": operation_id, "expected_source_digest": detail.json()["source_rule_digest"]})
    assert created.status_code == 202, created.text
    assert frozen_rows() == before
    claimed = worker.claim_job("bytes-owner")
    assert claimed is not None
    process_claimed(worker, claimed, "bytes-owner", rule_ai=ScriptedFormalizer(_proposal(rule_id)))
    assert frozen_rows() == before
    candidate = client.get(f"/rules/{rule_id}/formalizations/c1").json()["candidates"][-1]
    confirmed = client.post(
        f"/rules/{rule_id}/formalizations/c1/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": candidate["id"],
            "expected_candidate_revision": candidate["formalization_revision"],
            "expected_source_digest": detail.json()["source_rule_digest"],
            "expected_display_digest": candidate["display_digest"],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    assert frozen_rows() == before
    assert marker in json.dumps(before["rule"], ensure_ascii=False)


def test_refusing_model_consumption_does_not_queue_a_provider_job(auth_store_factory):
    """A generation request is not consent. No job and no candidate are created."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="rule-formalization-consent", git_sha="c" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-consent", "alice")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    digest = detail.json()["source_rule_digest"]
    for payload in (
        {"operation_id": str(uuid.uuid4()), "expected_source_digest": digest},
        {"operation_id": str(uuid.uuid4()), "expected_source_digest": digest, "consume_model": False},
    ):
        refused = client.post(f"/rules/{rule_id}/formalizations/c1/generations", json=payload)
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"] == "model_consumption_refused"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='rule_formalization'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.rule_formalization_operations").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.rule_formalizations").fetchone()[0] == 0
    assert worker.claim_job("consent-owner") is None
    allowed = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"operation_id": str(uuid.uuid4()), "expected_source_digest": digest, "consume_model": True},
    )
    assert allowed.status_code == 202, allowed.text


def test_unauthenticated_formalization_requests_do_not_read_or_queue(auth_store_factory):
    from api import create_app

    settings = Settings(feishu_app_id="formalization-auth", git_sha="d" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    rule_id = str(uuid.uuid4())
    operation_id = str(uuid.uuid4())
    digest = "a" * 64
    responses = [
        client.get(f"/rules/{rule_id}/formalizations"),
        client.get(f"/rules/{rule_id}/formalizations/c1"),
        client.get(f"/rule-formalization-generations/{operation_id}"),
        client.post(
            f"/rules/{rule_id}/formalizations/c1/generations",
            json={"operation_id": operation_id, "expected_source_digest": digest, "consume_model": True},
        ),
        client.post(
            f"/rules/{rule_id}/formalizations/c1/confirmations",
            json={
                "operation_id": str(uuid.uuid4()),
                "candidate_id": str(uuid.uuid4()),
                "expected_candidate_revision": 1,
                "expected_source_digest": digest,
                "expected_display_digest": digest,
            },
        ),
        client.post(
            f"/rules/{rule_id}/formalization-suite-candidates",
            json={"operation_id": str(uuid.uuid4()), "suite_revision_id": str(uuid.uuid4())},
        ),
    ]
    assert [item.status_code for item in responses] == [401, 401, 401, 401, 401, 401]
    assert {item.json()["error"] for item in responses} == {"unauthenticated"}
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.rule_formalizations").fetchone()[0] == 0


def test_an_oversized_model_result_is_rejected_without_a_candidate(auth_store_factory):
    """The scripted overlong result is not stored or truncated. This is not model-quality acceptance."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-size", git_sha="f" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-size", "alice")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    operation_id = str(uuid.uuid4())
    marker = "OVERSIZE-FORMAL-BODY"
    created = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"operation_id": operation_id, "expected_source_digest": detail.json()["source_rule_digest"], "consume_model": True},
    )
    assert created.status_code == 202, created.text
    claimed = worker.claim_job("oversize-owner")
    huge = marker + ("x" * 200001)
    process_claimed(worker, claimed, "oversize-owner", rule_ai=ScriptedFormalizer(huge))
    receipt = client.get(f"/rule-formalization-generations/{operation_id}")
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["state"] == "failed"
    assert receipt.json()["failure"] == "formalization_too_large"
    assert receipt.json()["candidate_id"] is None
    assert marker not in receipt.text
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.rule_formalizations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0] == 0
        stored = conn.execute("SELECT failure, state FROM product.rule_formalization_operations WHERE operation_id=%s", (operation_id,)).fetchone()
        assert stored == ("formalization_too_large", "failed")


def test_removing_the_model_key_before_dispatch_does_not_call_the_provider(auth_store_factory, monkeypatch):
    """A queued explanation stops when the key is gone. No provider call is model-quality acceptance."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-revoke", git_sha="9" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-revoke", "alice")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    operation_id = str(uuid.uuid4())
    created = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={"operation_id": operation_id, "expected_source_digest": detail.json()["source_rule_digest"], "consume_model": True},
    )
    assert created.status_code == 202, created.text
    claimed = worker.claim_job("revoke-key-owner")
    assert claimed is not None
    store.delete_personal_model_config(alice)
    from audit.engine.checkers.client import AIClient

    calls = []
    original = AIClient.response_text

    def response_text(self, *args, **kwargs):
        if getattr(self, "_bound_personal_call", False):
            calls.append("provider")
            raise AssertionError("revoked model key still called the provider")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AIClient, "response_text", response_text)
    result = process_claimed(worker, claimed, "revoke-key-owner")
    assert calls == []
    assert result["failure"] == "model_unavailable"
    receipt = client.get(f"/rule-formalization-generations/{operation_id}")
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["state"] == "failed"
    assert receipt.json()["failure"] == "model_unavailable"
    assert receipt.json()["candidate_id"] is None
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.rule_formalizations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0] == 0


def test_an_unchecked_source_cannot_be_confirmed(auth_store_factory):
    """Saved source review is authoritative. A scripted reading is not model-quality acceptance."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-source", git_sha="8" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-source", "alice")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))

    def rule(checked: bool) -> str:
        rule_id = str(uuid.uuid4())
        body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
        context = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": checked}]}}, ensure_ascii=False)}
        with store._conn() as conn:
            conn.execute(
                "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'列',%s,%s,%s)",
                (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, Jsonb(context)),
            )
        return rule_id

    def candidate(rule_id: str) -> dict:
        detail = client.get(f"/rules/{rule_id}/formalizations/c1")
        assert detail.status_code == 200, detail.text
        operation_id = str(uuid.uuid4())
        created = client.post(
            f"/rules/{rule_id}/formalizations/c1/generations",
            json={"operation_id": operation_id, "expected_source_digest": detail.json()["source_rule_digest"], "consume_model": True},
        )
        assert created.status_code == 202, created.text
        claimed = worker.claim_job("source-owner")
        process_claimed(worker, claimed, "source-owner", rule_ai=ScriptedFormalizer(_proposal(rule_id)))
        reading = client.get(f"/rules/{rule_id}/formalizations/c1").json()
        return reading

    unchecked = rule(False)
    reading = candidate(unchecked)
    item = reading["candidates"][-1]
    refused = client.post(
        f"/rules/{unchecked}/formalizations/c1/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": item["id"],
            "expected_candidate_revision": item["formalization_revision"],
            "expected_source_digest": reading["source_rule_digest"],
            "expected_display_digest": item["display_digest"],
        },
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "source_unchecked"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.rule_formalization_confirmations WHERE rule_version_id=%s", (unchecked,)).fetchone()[0] == 0

    checked = rule(True)
    accepted = candidate(checked)
    saved = accepted["candidates"][-1]
    confirmed = client.post(
        f"/rules/{checked}/formalizations/c1/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": saved["id"],
            "expected_candidate_revision": saved["formalization_revision"],
            "expected_source_digest": accepted["source_rule_digest"],
            "expected_display_digest": saved["display_digest"],
        },
    )
    assert confirmed.status_code == 201, confirmed.text


def test_an_unchecked_source_cannot_be_handed_to_a_suite(auth_store_factory):
    """An old confirmation cannot enter a suite while its saved source review is unchecked."""
    from api import create_app
    from product.store.suites import SuiteStore
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-handoff-source", git_sha="7" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-handoff-source", "alice")
    text = "文档要有编号"
    rules = {"type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": text, "type_of_rule": "text"}]}
    suite = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:source-handoff",
        files={"rules.json": json.dumps(rules).encode(), "checker.py": b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"},
        created_by=alice.user_id,
    )
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))

    def prepare(checked: bool) -> str:
        rule_id = str(uuid.uuid4())
        body = {**rules, "kind": "engine", "suite_key": "custom:source-handoff", "suite_revision_id": suite.id}
        context = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": checked}]}}, ensure_ascii=False)}
        with store._conn() as conn:
            conn.execute(
                "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'列',%s,%s,%s)",
                (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, Jsonb(context)),
            )
        detail = client.get(f"/rules/{rule_id}/formalizations/c1")
        assert detail.status_code == 200, detail.text
        candidate_id, confirmation_id = str(uuid.uuid4()), str(uuid.uuid4())
        digest = detail.json()["source_rule_digest"]
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO product.rule_formalizations(
                     id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                     owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                     formal_digest,display_digest,created_by)
                   VALUES (%s,%s,%s,'c1',1,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete','每一条都有编号','cnl-zh-1',%s,%s,%s)""",
                (candidate_id, alice.tenant_id, rule_id, digest, Jsonb({}), "b" * 64, "c" * 64, alice.user_id),
            )
            conn.execute(
                """INSERT INTO product.rule_formalization_confirmations(
                     id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                     source_rule_digest,display_digest,confirmed_by,current)
                   VALUES (%s,%s,%s,%s,'c1',%s,1,%s,%s,%s,true)""",
                (confirmation_id, str(uuid.uuid4()), alice.tenant_id, rule_id, candidate_id, digest, "c" * 64, alice.user_id),
            )
        return rule_id

    unchecked = prepare(False)
    with store._conn() as conn:
        before = conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:source-handoff'").fetchone()[0]
    refused = client.post(
        f"/rules/{unchecked}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": suite.id},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "source_unchecked"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:source-handoff'").fetchone()[0] == before

    checked = prepare(True)
    handed = client.post(
        f"/rules/{checked}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": suite.id},
    )
    assert handed.status_code == 201, handed.text
    assert handed.json()["validation"]["status"] == "unverified"


def test_one_unchecked_confirmation_blocks_the_whole_suite_handoff(auth_store_factory):
    """A checked clause is not written while another confirmed clause still has an unchecked source."""
    from api import create_app
    from product.store.suites import SuiteStore
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-mixed-source", git_sha="6" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-mixed-source", "alice")
    rules = {
        "type": "自定义",
        "rules": [
            {"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"},
            {"id": "c2", "category": "列", "priority": "P0", "rule_to_check": "文档要有名称", "type_of_rule": "text"},
        ],
    }
    suite = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:mixed-source",
        files={"rules.json": json.dumps(rules).encode(), "checker.py": b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"},
        created_by=alice.user_id,
    )
    rule_id = str(uuid.uuid4())
    body = {**rules, "kind": "engine", "suite_key": "custom:mixed-source", "suite_revision_id": suite.id}
    context = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": True}, {"id": "c2", "checked": False}]}}, ensure_ascii=False)}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'列',%s,%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, Jsonb(context)),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    for clause_id, reading in (("c1", "每一条都有编号"), ("c2", "每一条都有名称")):
        detail = client.get(f"/rules/{rule_id}/formalizations/{clause_id}")
        assert detail.status_code == 200, detail.text
        digest = detail.json()["source_rule_digest"]
        candidate_id = str(uuid.uuid4())
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO product.rule_formalizations(
                     id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                     owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                     formal_digest,display_digest,created_by)
                   VALUES (%s,%s,%s,%s,1,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete',%s,'cnl-zh-1',%s,%s,%s)""",
                (candidate_id, alice.tenant_id, rule_id, clause_id, digest, Jsonb({}), reading, "b" * 64, "c" * 64, alice.user_id),
            )
            conn.execute(
                """INSERT INTO product.rule_formalization_confirmations(
                     id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                     source_rule_digest,display_digest,confirmed_by,current)
                   VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,true)""",
                (str(uuid.uuid4()), str(uuid.uuid4()), alice.tenant_id, rule_id, clause_id, candidate_id, digest, "c" * 64, alice.user_id),
            )
    with store._conn() as conn:
        before = conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:mixed-source'").fetchone()[0]
    refused = client.post(
        f"/rules/{rule_id}/formalization-suite-candidates",
        json={"operation_id": str(uuid.uuid4()), "suite_revision_id": suite.id},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "source_unchecked"
    assert "每一条都有编号" not in refused.text
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:mixed-source'").fetchone()[0] == before


def test_a_source_uncheck_during_confirmation_is_not_saved(auth_store_factory):
    """The confirmation lock reads the rule again. A concurrent uncheck is not confirmed."""
    import threading
    import time

    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-race", git_sha="5" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-race", "alice")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    checked = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": True}]}}, ensure_ascii=False)}
    unchecked = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": False}]}}, ensure_ascii=False)}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'列',%s,%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, Jsonb(checked)),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    digest = detail.json()["source_rule_digest"]
    candidate_id = str(uuid.uuid4())
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO product.rule_formalizations(
                 id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by)
               VALUES (%s,%s,%s,'c1',1,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete','每一条都有编号','cnl-zh-1',%s,%s,%s)""",
            (candidate_id, alice.tenant_id, rule_id, digest, Jsonb({}), "b" * 64, "c" * 64, alice.user_id),
        )
    held = threading.Event()
    release = threading.Event()

    def editor():
        with store._conn() as conn, conn.transaction():
            conn.execute("SELECT id FROM product.rule_versions WHERE id=%s FOR UPDATE", (rule_id,))
            held.set()
            assert release.wait(5)
            conn.execute("UPDATE product.rule_versions SET case_context=%s WHERE id=%s", (Jsonb(unchecked), rule_id))

    threading.Thread(target=editor).start()
    assert held.wait(5)
    result = {}

    def confirmer():
        result["response"] = client.post(
            f"/rules/{rule_id}/formalizations/c1/confirmations",
            json={
                "operation_id": str(uuid.uuid4()),
                "candidate_id": candidate_id,
                "expected_candidate_revision": 1,
                "expected_source_digest": digest,
                "expected_display_digest": "c" * 64,
            },
        )

    worker_thread = threading.Thread(target=confirmer)
    worker_thread.start()
    time.sleep(0.3)
    release.set()
    worker_thread.join(5)
    assert "response" in result
    assert result["response"].status_code == 409, result["response"].text
    assert result["response"].json()["error"] == "source_changed"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.rule_formalization_confirmations WHERE rule_version_id=%s", (rule_id,)).fetchone()[0] == 0


def test_a_source_uncheck_during_handoff_is_not_saved(auth_store_factory):
    """The handoff lock reads the rule again. A concurrent uncheck writes no Suite candidate."""
    import threading
    import time

    from api import create_app
    from product.store.suites import SuiteStore
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-handoff-race", git_sha="7" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-handoff-race", "alice")
    rules = {"type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    suite = SuiteStore(settings, store.objects, role="migrator").create_revision(
        tenant_id=alice.tenant_id,
        suite_key="custom:handoff-race",
        files={"rules.json": json.dumps(rules).encode(), "checker.py": b"def create_audit(audit_file, rule, group_context=None):\n    return []\n"},
        created_by=alice.user_id,
    )
    rule_id = str(uuid.uuid4())
    body = {**rules, "kind": "engine", "suite_key": "custom:handoff-race", "suite_revision_id": suite.id}
    checked = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": True}]}}, ensure_ascii=False)}
    unchecked = {"registration": json.dumps({"basis": {"clauses": [{"id": "c1", "checked": False}]}}, ensure_ascii=False)}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,case_context) VALUES (%s,%s,'列',%s,%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, Jsonb(checked)),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert detail.status_code == 200, detail.text
    digest = detail.json()["source_rule_digest"]
    candidate_id = str(uuid.uuid4())
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO product.rule_formalizations(
                 id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by)
               VALUES (%s,%s,%s,'c1',1,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete','每一条都有编号','cnl-zh-1',%s,%s,%s)""",
            (candidate_id, alice.tenant_id, rule_id, digest, Jsonb({}), "b" * 64, "c" * 64, alice.user_id),
        )
        conn.execute(
            """INSERT INTO product.rule_formalization_confirmations(
                 id,operation_id,tenant_id,rule_version_id,clause_id,candidate_id,formalization_revision,
                 source_rule_digest,display_digest,confirmed_by,current)
               VALUES (%s,%s,%s,%s,'c1',%s,1,%s,%s,%s,true)""",
            (str(uuid.uuid4()), str(uuid.uuid4()), alice.tenant_id, rule_id, candidate_id, digest, "c" * 64, alice.user_id),
        )
        before = conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:handoff-race'").fetchone()[0]
    held = threading.Event()
    release = threading.Event()

    def editor():
        with store._conn() as conn, conn.transaction():
            conn.execute("SELECT id FROM product.rule_versions WHERE id=%s FOR UPDATE", (rule_id,))
            held.set()
            assert release.wait(5)
            conn.execute("UPDATE product.rule_versions SET case_context=%s WHERE id=%s", (Jsonb(unchecked), rule_id))

    threading.Thread(target=editor).start()
    assert held.wait(5)
    result = {}

    def handoff():
        result["response"] = client.post(
            f"/rules/{rule_id}/formalization-suite-candidates",
            json={"operation_id": str(uuid.uuid4()), "suite_revision_id": suite.id},
        )

    worker_thread = threading.Thread(target=handoff)
    worker_thread.start()
    time.sleep(0.3)
    release.set()
    worker_thread.join(5)
    assert "response" in result
    assert result["response"].status_code == 409, result["response"].text
    assert result["response"].json()["error"] == "source_changed"
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE suite_key='custom:handoff-race'").fetchone()[0] == before


def test_two_candidates_cannot_both_become_current(auth_store_factory):
    """Two concurrent confirmations of one clause leave one current pointer."""
    import threading

    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-two-candidates", git_sha="8" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-two-candidates", "alice")
    rule_id = str(uuid.uuid4())
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'列',%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    digest = client.get(f"/rules/{rule_id}/formalizations/c1").json()["source_rule_digest"]
    older_id, newer_id = str(uuid.uuid4()), str(uuid.uuid4())
    with store._conn() as conn:
        for candidate_id, revision, display in ((older_id, 1, "a" * 64), (newer_id, 2, "b" * 64)):
            conn.execute(
                """INSERT INTO product.rule_formalizations(
                     id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                     owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                     formal_digest,display_digest,created_by)
                   VALUES (%s,%s,%s,'c1',%s,%s,'owl2-dl-v1','Ontology()',%s,'[]','[]','[]','complete',%s,'cnl-zh-1',%s,%s,%s)""",
                (candidate_id, alice.tenant_id, rule_id, revision, digest, Jsonb({}), f"理解{revision}", "c" * 64, display, alice.user_id),
            )
    started = threading.Barrier(2)
    results = {}

    def confirm(name, candidate_id, revision, display):
        started.wait(5)
        results[name] = client.post(
            f"/rules/{rule_id}/formalizations/c1/confirmations",
            json={
                "operation_id": str(uuid.uuid4()),
                "candidate_id": candidate_id,
                "expected_candidate_revision": revision,
                "expected_source_digest": digest,
                "expected_display_digest": display,
            },
        )

    threads = [
        threading.Thread(target=confirm, args=("older", older_id, 1, "a" * 64)),
        threading.Thread(target=confirm, args=("newer", newer_id, 2, "b" * 64)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert results["newer"].status_code == 201, results["newer"].text
    assert results["older"].status_code == 409, results["older"].text
    assert results["older"].json()["error"] in {"candidate_stale", "conflicting_confirmation"}
    fresh = TestClient(create_app(settings, auth_store=api))
    fresh.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = fresh.get(f"/rules/{rule_id}/formalizations/c1")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert [item["id"] for item in payload["candidates"]] == [older_id, newer_id]
    current = [item for item in payload["confirmations"] if item["current"]]
    assert len(payload["confirmations"]) == 1
    assert current[0]["id"] == results["newer"].json()["id"]
    assert current[0]["candidate_id"] == newer_id
    assert "Ontology()" not in results["older"].text


def test_a_member_without_the_source_file_cannot_read_the_formalization(auth_store_factory):
    """Same-tenant membership does not grant the source file. The response has no formula."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-file-acl", git_sha="9" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-file-acl", "alice")
    bob = store.upsert_feishu_identity("formalization-file-acl", "bob")
    rule_id, file_id, version_id = (str(uuid.uuid4()) for _ in range(3))
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": "文档要有编号", "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES (%s,%s,'file')", (file_id, alice.tenant_id))
        conn.execute("INSERT INTO product.files(id,tenant_id,created_by,original_filename) VALUES (%s,%s,%s,'rule.xlsx')", (file_id, alice.tenant_id, alice.user_id))
        conn.execute(
            """INSERT INTO product.file_versions(id,file_id,tenant_id,original_filename,content_type,byte_size,sha256,object_key,status)
               VALUES (%s,%s,%s,'rule.xlsx','application/octet-stream',3,%s,'rule-object','ready')""",
            (version_id, file_id, alice.tenant_id, "d" * 64),
        )
        conn.execute("INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES (%s,%s,'read')", (file_id, alice.user_id))
        conn.execute(
            "INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by,source_file_version_id) VALUES (%s,%s,'列',%s,%s,%s)",
            (rule_id, alice.tenant_id, Jsonb(body), alice.user_id, version_id),
        )
        conn.execute(
            """INSERT INTO product.rule_formalizations(
                 id,tenant_id,rule_version_id,clause_id,formalization_revision,source_rule_digest,formal_profile,
                 owl_content,vocabulary,obligations,assumptions,unresolved,completeness,cnl_text,cnl_renderer_version,
                 formal_digest,display_digest,created_by)
               VALUES (%s,%s,%s,'c1',1,%s,'owl2-dl-v1','Ontology(SecretAxiom)',%s,'[]','[]','[]','complete','秘密回译','cnl-zh-1',%s,%s,%s)""",
            (str(uuid.uuid4()), alice.tenant_id, rule_id, "e" * 64, Jsonb({}), "f" * 64, "1" * 64, alice.user_id),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    owner = TestClient(create_app(settings, auth_store=api))
    owner.cookies.set(SESSION_COOKIE, store.create_session(alice))
    visible = owner.get(f"/rules/{rule_id}/formalizations/c1")
    assert visible.status_code == 200, visible.text
    assert "秘密回译" in visible.text
    stranger = TestClient(create_app(settings, auth_store=api))
    stranger.cookies.set(SESSION_COOKIE, store.create_session(bob))
    denied = stranger.get(f"/rules/{rule_id}/formalizations/c1")
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"] == "access_denied"
    assert "Ontology" not in denied.text
    assert "秘密回译" not in denied.text
    assert "SecretAxiom" not in denied.text


def test_a_chosen_reading_confirms_and_a_new_session_reads_it(auth_store_factory):
    """A scripted choice proves the software path. It is not model-quality acceptance."""
    from api import create_app
    from psycopg.types.json import Jsonb

    settings = Settings(feishu_app_id="formalization-choice", git_sha="2" * 40, usage_charge_enabled=False, model_credential_encryption_key=SecretStr(Fernet.generate_key().decode()))
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("formalization-choice", "alice")
    store.put_personal_model_config(alice, SecretStr("test-only-no-provider-called"), base_url="https://models.example.test", model="test-model")
    rule_id = str(uuid.uuid4())
    clause = "每条源记录都要在目标里出现"
    body = {"kind": "engine", "type": "自定义", "rules": [{"id": "c1", "category": "列", "priority": "P0", "rule_to_check": clause, "type_of_rule": "text"}]}
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body,created_by) VALUES (%s,%s,'对应',%s,%s)", (rule_id, alice.tenant_id, Jsonb(body), alice.user_id))
    owl = (
        f"Prefix(ex:=<{NS}>)\nPrefix(ppap:=<https://ppap.example/formal/v1#>)\nOntology(\n"
        "Declaration(Class(ex:Source))\nDeclaration(Class(ex:Target))\nDeclaration(DataProperty(ex:key))\nDeclaration(DataProperty(ex:name))\n)\n"
    )
    unique = "(sentence s (forall (s) (if (ex:Source s) (exists (t) (and (ex:Target t) (= (ex:key s) (ex:key t)) (forall (t2) (if (and (ex:Target t2) (= (ex:key s) (ex:key t2))) (= t t2))))))))"
    raw = json.dumps(
        {
            "format_version": "1",
            "formal_profile": "owl2-plus-clif-v1",
            "owl_content": owl,
            "clif_content": f"(cl-text {unique})",
            "vocabulary": {
                "prefixes": {"ex": NS},
                "entities": [
                    {"iri": NS + "Source", "kind": "class", "label": "源记录"},
                    {"iri": NS + "Target", "kind": "class", "label": "目标记录"},
                    {"iri": NS + "key", "kind": "dataProperty", "label": "键"},
                    {"iri": NS + "name", "kind": "dataProperty", "label": "名称"},
                ],
            },
            "source_rule_version_id": rule_id,
            "clause_id": "c1",
            "obligations": [{"id": "o1", "source_span": "0:2", "semantic_kind": "unique", "formal_ref": "s"}],
            "unresolved": [],
            "completeness": "complete",
            "assumptions": [],
        },
        ensure_ascii=False,
    )
    api = build_product_store(settings, objects=store.objects, role="api")
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(alice))
    detail = client.get(f"/rules/{rule_id}/formalizations/c1")
    assert detail.status_code == 200, detail.text
    digest = detail.json()["source_rule_digest"]
    opened = str(uuid.uuid4())
    assert client.post(f"/rules/{rule_id}/formalizations/c1/generations", json={"consume_model": True, "operation_id": opened, "expected_source_digest": digest}).status_code == 202
    script = ScriptedFormalizer(raw)
    claimed = worker.claim_job("choice-open")
    process_claimed(worker, claimed, "choice-open", rule_ai=script)
    assert script.calls == 1
    operation = client.get(f"/rule-formalization-generations/{opened}")
    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == "completed"
    partial = client.get(f"/rules/{rule_id}/formalizations/c1").json()
    assert partial["candidates"][-1]["completeness"] == "partial"
    refused = client.post(
        f"/rules/{rule_id}/formalizations/c1/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": partial["candidates"][-1]["id"],
            "expected_candidate_revision": partial["candidates"][-1]["formalization_revision"],
            "expected_source_digest": digest,
            "expected_display_digest": partial["candidates"][-1]["display_digest"],
        },
    )
    assert refused.status_code == 409
    assert refused.json()["error"] == "not_confirmable"
    chosen = str(uuid.uuid4())
    posted = client.post(
        f"/rules/{rule_id}/formalizations/c1/generations",
        json={
            "consume_model": True,
            "operation_id": chosen,
            "expected_source_digest": digest,
            "based_on_candidate_id": partial["candidates"][-1]["id"],
            "clarification_answer": {"unresolved_id": "cardinality", "choice_id": "exactly-one"},
        },
    )
    assert posted.status_code == 202, posted.text
    claimed = worker.claim_job("choice-selected")
    process_claimed(worker, claimed, "choice-selected", rule_ai=script)
    assert script.calls == 2
    saved = client.get(f"/rules/{rule_id}/formalizations/c1").json()["candidates"][-1]
    assert saved["completeness"] == "complete"
    assert "恰好" in saved["cnl_text"]
    confirmed = client.post(
        f"/rules/{rule_id}/formalizations/c1/confirmations",
        json={
            "operation_id": str(uuid.uuid4()),
            "candidate_id": saved["id"],
            "expected_candidate_revision": saved["formalization_revision"],
            "expected_source_digest": digest,
            "expected_display_digest": saved["display_digest"],
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    fresh = TestClient(create_app(settings, auth_store=api))
    fresh.cookies.set(SESSION_COOKIE, store.create_session(alice))
    again = fresh.get(f"/rules/{rule_id}/formalizations/c1")
    assert again.status_code == 200, again.text
    current = [item for item in again.json()["confirmations"] if item["current"]]
    assert current[0]["candidate_id"] == saved["id"]
    assert again.json()["state"] == "confirmed"
