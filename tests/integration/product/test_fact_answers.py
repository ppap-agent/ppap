"""Real PostgreSQL, original source and positive in-round answer recovery."""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import fitz
from audit.checkers.v3.excel_agent import rule
from audit.checkers.v4.uaes.native import UaesSourceAgent
from audit.contracts import AuditInput, RuleInput
from product import build_product_store
from product.settings import Settings
from psycopg.types.json import Jsonb


class SourceCheck(UaesSourceAgent):
    @rule("SOP阶段已勾选")
    def rule_1(self):
        return self.visual_result({"sop": "SOP阶段已勾选"}, "SOP阶段")


def prepare_fact_round(auth_store_factory, tmp_path, monkeypatch):
    settings = Settings(feishu_app_id="fact-answer-tests", usage_charge_enabled=False)
    store = auth_store_factory(settings)
    alice = store.upsert_feishu_identity("fact-enterprise", "alice", "Alice")
    path = tmp_path / "original.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((50, 50), "SOP [ ]")
        document.save(path)
    data = path.read_bytes()
    staged = store.create_staging(alice, "original.pdf", "application/pdf", len(data))
    version = store.finalize_version(store.put_blob(staged, data)).public()
    observed = []

    def observe(*args, **kwargs):
        observed.append("model observation")
        return {"sop": None}

    monkeypatch.setattr("audit.checkers.v4.visual_facts.read_original_pdf_visual_facts", observe)
    checker = SourceCheck(AuditInput(audit_id=version["id"], file_url=str(path)), RuleInput(rule_id="frozen-rule"))
    machine = checker.check(0)
    rid, rule_id = str(uuid4()), str(uuid4())
    rules = {
        "kind": "engine",
        "type": "控制计划",
        "rule_profile": "v3",
        "customers": [],
        "models": [],
        "parts": [],
        "rules": [
            {"id": "R1", "category": "SOP", "rule_to_check": "SOP阶段已勾选", "type_of_rule": "table", "priority": "P0"},
            {"id": "R2", "category": "Original", "rule_to_check": "已核实的独立条款", "type_of_rule": "table", "priority": "P0"},
        ],
    }
    locator = {
        "file_version_id": version["id"],
        "filename": "original.pdf",
        "rule_item_id": "R1",
        "semantic": {"fact_questions": [question.model_dump() for question in machine.fact_questions], "fact_evaluation": machine.fact_evaluation.model_dump()},
    }
    with store._conn() as conn:
        conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES(%s,%s,'audit_round')", (rid, alice.tenant_id))
        conn.execute(
            """INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt,analysis_mode)
                        VALUES(%s,%s,%s,%s,'fact-source','completed',1,'standard')""",
            (rid, alice.tenant_id, alice.user_id, rid),
        )
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'原件事实检查',%s)", (rule_id, alice.tenant_id, Jsonb(rules)))
        conn.execute(
            "INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body) VALUES(%s,%s,'target',%s,'原件事实检查',%s)",
            (rid, version["id"], rule_id, Jsonb(rules)),
        )
        conn.execute(
            """INSERT INTO product.findings(id,round_id,item_key,classification,item,status,suggestions,locator)
                        VALUES(%s,%s,'R1','SOP','SOP','UNABLE',%s,%s),(%s,%s,'R2','Original','Original','PASS','Known',%s)""",
            (str(uuid4()), rid, machine.suggestions, Jsonb(locator), str(uuid4()), rid, Jsonb({"file_version_id": version["id"], "rule_item_id": "R2"})),
        )
    api = build_product_store(settings, objects=store.objects, role="api")
    return store, api, alice, rid, version, observed


def test_original_answer_persists_without_new_round_or_model_call(auth_store_factory, tmp_path, monkeypatch):
    store, api, alice, rid, version, observed = prepare_fact_round(auth_store_factory, tmp_path, monkeypatch)
    settings = store.settings
    original = deepcopy(api.get_round(rid))
    view = api.read_fact_answers(alice, rid)
    question = view["questions"][0]
    request = {
        "operation_id": str(uuid4()),
        "expected_revision": 0,
        "file_version_id": version["id"],
        "field": question["field"],
        "expected_question_fingerprint": question["fingerprint"],
        "value": True,
    }
    receipt = api.apply_fact_answer(alice, rid, request)
    assert receipt["results"][0]["status"] == "PASS"
    assert receipt["results"][0]["confirmed_by"] == [alice.user_id]
    assert receipt["model_calls"] == 0
    assert api.apply_fact_answer(alice, rid, request) == receipt
    reopened = build_product_store(settings, objects=store.objects, role="api")
    assert reopened.read_fact_answer_operation(alice, rid, request["operation_id"]) == receipt
    assert reopened.read_fact_answers(alice, rid)["answers"] == [receipt["answer"]]
    assert reopened.get_round(rid) == original
    exported = reopened.export_fact_answers(alice, rid)
    assert [item["status"] for item in exported["machine_findings"]] == ["UNABLE", "PASS"]
    assert exported["training_eligible"] is False
    assert observed == ["model observation"]
    with store._conn() as conn:
        # Startup reapplies numbered migrations; a repeat must retain answers.
        conn.execute((Path(__file__).resolve().parents[3] / "migrations/0055_original_fact_answers.sql").read_text())
        assert conn.execute("SELECT receipt FROM product.original_fact_answers").fetchone()[0] == receipt
        assert conn.execute("SELECT count(*) FROM product.audit_rounds").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM product.original_fact_answers").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM product.usage_events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0
