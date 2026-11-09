"""Real PG/HTTP rule-source and reconfirmation contracts, without a provider.

Completed baseline rows are seeded here. Live execution and candidate authorship
are not claimed by this protocol test; the live driver must prove them separately.
"""

import json
from uuid import uuid4

import pytest
from api import create_app
from audit.checker_core import ExecutionSnapshot, FrozenInput, RuleBinding, checker_digest
from fastapi.testclient import TestClient
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore
from product.store.suites import SuiteStore, freeze_execution
from psycopg.types.json import Jsonb

from scripts import run_checker_live as live


@pytest.mark.parametrize("scenario", ["rule-threshold", "rule-wording"])
def test_rule_live_scenario_uses_uploaded_rule_text_and_explicit_reconfirmation(auth_store_factory, scenario):
    store = auth_store_factory(Settings(feishu_app_id="local-live-protocol", usage_charge_enabled=False))
    alice = store.upsert_feishu_identity("local-live-test", "alice")
    key, rules, files, _ = live.scenario_source(scenario, "a" * 40)
    suites = SuiteStore(store.settings, store.objects, role="migrator")
    base = suites.create_revision(tenant_id=None, suite_key=key, files=files, created_by=None)
    SuiteReleaseStore(store.settings, store.objects, role="api").publish(alice, key, base.id, 0, str(uuid4()), force_unverified=True)
    rule_id, rid = str(uuid4()), str(uuid4())
    raw = live.workbook(5, 1)
    staged = store.create_staging(alice, "business-input.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", len(raw))
    version = store.finalize_version(store.put_blob(staged, raw)).public()
    target = freeze_execution(
        suite=suites.resolve_suite(base.id, alice.tenant_id),
        file_version_id=version["id"],
        binding=RuleBinding(key, checker_digest({"checker.py": files["checker.py"]}), rules["checker_binding"]),
        validation=None,
        related_suite_digests={},
        library_revision="protocol-fixture",
        allow_unverified=True,
    )
    with store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'原规则',%s)", (rule_id, alice.tenant_id, Jsonb({**rules, "suite_revision_id": base.id})))
        conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES(%s,%s,'audit_round')", (rid, alice.tenant_id))
        conn.execute(
            """INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt,analysis_mode)
               VALUES(%s,%s,%s,%s,'protocol-baseline','completed',1,'standard')""",
            (rid, alice.tenant_id, alice.user_id, rid),
        )
        conn.execute(
            "INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body) VALUES(%s,%s,'target',%s,'原规则',%s)", (rid, version["id"], rule_id, Jsonb(rules))
        )
        for clause in ("R1", "R2"):
            conn.execute(
                "INSERT INTO product.findings(id,round_id,item_key,classification,item,status,locator) VALUES(%s,%s,%s,'阈值',%s,'PASS',%s)",
                (uuid4(), rid, clause, clause, Jsonb({"file_version_id": version["id"], "rule_item_id": clause})),
            )
        suites.save_snapshot(
            conn,
            snapshot=ExecutionSnapshot(
                tenant_id=alice.tenant_id,
                initiating_user_id=alice.user_id,
                inputs=(FrozenInput(version["id"], version["sha256"], "target", "business-input.xlsx"),),
                targets=(target,),
                runtime_revision="protocol-fixture",
                library_revision="protocol-fixture",
                model_identity={},
                round_id=rid,
            ),
        )
    api = build_product_store(store.settings, objects=store.objects, role="api")
    original = api.get_round(rid)
    with TestClient(create_app(store.settings, auth_store=api)) as http:
        http.cookies.set(SESSION_COOKIE, store.create_session(alice))
        client = live.Client.__new__(live.Client)
        client.http = http
        feedback = []
        for clause in ("R1", "R2"):
            feedback.append(
                client.request(
                    "POST",
                    f"/audit-rounds/{rid}/checker-feedback",
                    payload={
                        "operation_id": str(uuid4()),
                        "file_version_id": version["id"],
                        "rule_version_id": rule_id,
                        "rule_clause_id": clause,
                        "result_attempt": 1,
                        "original_status": "PASS",
                        "expected_status": "NG" if scenario == "rule-threshold" and clause == "R1" else "PASS",
                        "rationale": "独立核对原件A1=5和所选规则",
                        "evidence": [{"file_version_id": version["id"], "locator": {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}, "excerpt": "5"}],
                    },
                )
            )
        updated = live.publish_rule_change(client, rules, rule_id, scenario)
        assert updated["source_file_version_id"] != version["id"]
        source = api.readable_version(alice, updated["source_file_version_id"])
        document = json.loads(api.version_bytes(source))
        assert document["rules"] == updated["body"]["rules"]
        registration = json.loads(updated["case_context"]["registration"])["basis"]
        assert all(clause["checked"] and clause["provenance"]["fileVersionId"] == source.id for clause in registration["clauses"])
        active = client.request("GET", f"/suites/{key}/active")
        assert active["current_revision_id"] == base.id and active["verification_status"] == "stale"
        assert [row["kind"] for row in active["rule_update"]["clauses"]] == ["changed", "unchanged"]
        with store._conn() as conn:
            assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0
        revision = updated["body"]["suite_revision_id"]
        body = {
            "operation_id": str(uuid4()),
            "feedback_ids": [row["id"] for row in feedback],
            "action": "save_only",
            "consume_model": False,
            "confirmed_rule_revision_id": revision,
            "expected_feedback_revisions": {row["id"]: row["revision"] for row in feedback},
        }
        changed = http.post(f"/suite-revisions/{revision}/optimizations", json={**body, "expected_feedback_revisions": {}})
        assert changed.status_code == 409 and changed.json()["error"] == "checker_rule_confirmation_changed"
        receipt = client.request("POST", f"/suite-revisions/{revision}/optimizations", payload=body)
        assert receipt["state"] == "saved"
        with store._conn() as conn:
            frozen = conn.execute("SELECT frozen FROM product.checker_optimizations WHERE operation_id=%s", (body["operation_id"],)).fetchone()[0]
            assert frozen["confirmed_rule_revision_id"] == revision
            assert all(row["rule_reconfirmed_by"] == alice.user_id for row in frozen["samples"])
            assert {row["rule_clause_id"] for row in frozen["samples"]} == {"R1", "R2"}
            assert frozen["consume_model"] is False
            assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0
        assert api.get_round(rid) == original
