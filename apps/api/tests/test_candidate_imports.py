"""Candidate guards use real tenant transactions and controlled, model-free authors."""

import io
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent.checker_development import DevelopmentOutcome
from agent.checker_spec import CheckerArtifact
from agent.protocol import DevelopmentEnvelope
from agent.run import ExecutionContext
from api import create_app
from audit.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport
from fastapi.testclient import TestClient
from openpyxl import Workbook
from product import build_product_store
from product.files import ObjectExists, ObjectMissing
from product.fixtures import freeze_round_suite
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.checker_development import CheckerDevelopmentStore
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb
from worker.checker_optimizer import development_handler, materialize_request

FACTORY = b"def create_audit(audit_file, rule, group_context=None):\n    return None\n"
OLD_IMPORT = b"from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent\n"
RULES = {
    "kind": "engine",
    "type": "自定义",
    "customers": ["UAES"],
    "rules": [{"id": "R1", "category": "数值", "rule_to_check": "A1至少10", "type_of_rule": "text"}],
    "checker_binding": {"R1": "checker:create_audit"},
}


class Objects:
    def __init__(self):
        self.data = {}

    def put_exclusive(self, key, body, content_type):
        if key in self.data:
            raise ObjectExists()
        self.data[key] = body

    def get(self, key, *, max_bytes=None):
        if key not in self.data:
            raise ObjectMissing()
        body = self.data[key]
        if max_bytes is not None and len(body) > max_bytes:
            raise ValueError("too_large")
        return body


def factory_revision(store, **changes):
    files = {"checker.py": FACTORY, "resources.py": b"VALUE = 1\n", "rules.json": json.dumps(RULES).encode(), **changes}
    return SuiteStore(store.settings, store.objects, role="migrator").create_revision(tenant_id=None, suite_key="control-plan", files=files, created_by=None)


def system(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="candidate-guard", usage_charge_enabled=False, git_sha="a" * 40), objects=Objects())
    principal = store.upsert_feishu_identity("candidate-guard", "operator")
    api = build_product_store(store.settings, objects=store.objects, role="api")
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    client = TestClient(create_app(store.settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, store.create_session(principal))
    return SimpleNamespace(store=store, worker=worker, principal=principal, client=client, selected=factory_revision(store))


@pytest.mark.parametrize(
    "changes",
    [
        {"checker.py": (OLD_IMPORT + FACTORY).decode()},
        {"resources.py": OLD_IMPORT.decode()},
        {"resources.py": "import importlib\nOLD = importlib.import_module('audit.checker_lib.uaes.control_plan')\n"},
        {
            "checker.py": (OLD_IMPORT + b"def check(context):\n    return []\n").decode(),
            "rules.json": json.dumps({**RULES, "checker_binding": {"R1": "checker:check"}}),
        },
    ],
)
def test_manual_factory_candidate_rejects_old_imports_without_saving(auth_store_factory, changes):
    f = system(auth_store_factory)
    path = f"/suite-revisions/{f.selected.id}/candidates"
    operation = str(uuid4())
    response = f.client.post(path, json={"operation_id": operation, "files": changes})
    assert response.status_code == 400, response.text
    assert response.json() == {"error": "checker_cross_version_import"}
    assert f.client.get(path + "/" + operation).status_code == 404
    assert f.client.get(f"/suite-revisions/{f.selected.id}").status_code == 200
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE tenant_id IS NOT NULL").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.suite_candidate_operations").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0] == 0


def test_manual_factory_change_checks_the_inherited_resource(auth_store_factory):
    f = system(auth_store_factory)
    selected = factory_revision(f.store, **{"resources.py": OLD_IMPORT})
    response = f.client.post(f"/suite-revisions/{selected.id}/candidates", json={"files": {"checker.py": FACTORY.decode() + "# changed\n"}})
    assert response.status_code == 400, response.text
    assert response.json() == {"error": "checker_cross_version_import"}


@pytest.mark.parametrize(
    "source",
    [
        OLD_IMPORT + FACTORY,
        b"from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent as create_audit\n",
        OLD_IMPORT + b"create_audit = UaesControlPlanAgent\n",
    ],
    ids=["factory-definition", "factory-import", "factory-assignment"],
)
def test_new_factory_format_is_checked_when_the_base_is_a_native_carrier(auth_store_factory, source):
    f = system(auth_store_factory)
    selected = factory_revision(f.store, **{"checker.py": b"def check(context): return []\n", "rules.json": b'{"rules":[{"id":"R1","rule_to_check":"Required"}]}'})
    response = f.client.post(f"/suite-revisions/{selected.id}/candidates", json={"files": {"checker.py": source.decode()}})
    assert response.status_code == 400, response.text
    assert response.json() == {"error": "checker_cross_version_import"}


@pytest.mark.parametrize("same_program", [False, True])
def test_unchanged_historical_program_can_be_read_and_have_rules_edited(auth_store_factory, same_program):
    f = system(auth_store_factory)
    selected = factory_revision(f.store, **{"checker.py": OLD_IMPORT + FACTORY})
    changes = {"rules.json": json.dumps({**RULES, "title": "Updated title"})}
    if same_program:
        changes["checker.py"] = (OLD_IMPORT + FACTORY).decode()
    response = f.client.post(f"/suite-revisions/{selected.id}/candidates", json={"files": changes})
    assert response.status_code == 201, response.text
    resolved = SuiteStore(f.store.settings, f.store.objects).resolve_suite(response.json()["id"], f.principal.tenant_id)
    assert resolved.files["checker.py"] == OLD_IMPORT + FACTORY
    assert f.client.get(f"/suite-revisions/{selected.id}").status_code == 200


def test_api_selected_pi_passes_full_sources_and_host_rejects_a_guest_bypass(auth_store_factory):
    f = system(auth_store_factory)
    rule_id = str(uuid4())
    with f.store._conn() as conn:
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'Threshold',%s)", (rule_id, f.principal.tenant_id, Jsonb(RULES)))
    feedback = []
    for value in (5, 15, 16, 6):
        book = Workbook()
        book.active["A1"] = value
        buffer = io.BytesIO()
        book.save(buffer)
        book.close()
        raw = buffer.getvalue()
        version = f.store.create_staging(f.principal, "sample.xlsx", "application/octet-stream", len(raw))
        version = f.store.finalize_version(f.store.put_blob(version, raw))
        round_id = str(uuid4())
        with f.store._conn() as conn:
            conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES(%s,%s,'audit_round')", (round_id, f.principal.tenant_id))
            conn.execute("INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES(%s,%s,'review')", (round_id, f.principal.user_id))
            conn.execute(
                "INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt) VALUES(%s,%s,%s,%s,'test','completed',1)",
                (round_id, f.principal.tenant_id, f.principal.user_id, round_id),
            )
            conn.execute(
                "INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body) VALUES(%s,%s,'target',%s,'Threshold',%s)",
                (round_id, version.id, rule_id, Jsonb(RULES)),
            )
            conn.execute(
                "INSERT INTO product.findings(id,round_id,item_key,classification,item,status,locator) VALUES(%s,%s,'R1','rule','Threshold','PASS',%s)",
                (uuid4(), round_id, Jsonb({"file_version_id": version.id, "rule_item_id": "R1"})),
            )
        freeze_round_suite(f.store, f.principal, round_id, version.id, version.sha256, f.selected.id)
        response = f.client.post(
            f"/audit-rounds/{round_id}/checker-feedback",
            json={
                "operation_id": str(uuid4()),
                "file_version_id": version.id,
                "rule_version_id": rule_id,
                "rule_clause_id": "R1",
                "result_attempt": 1,
                "original_status": "PASS",
                "expected_status": "NG" if value < 10 else "PASS",
                "rationale": "Controlled original A1",
                "evidence": [{"file_version_id": version.id, "locator": {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}, "excerpt": str(value)}],
            },
        )
        assert response.status_code == 200, response.text
        feedback.append(response.json()["id"])
    command = {"operation_id": str(uuid4()), "feedback_ids": [feedback[0]], "action": "run_tests", "consume_model": False}
    path = f"/suite-revisions/{f.selected.id}/optimizations"
    admitted = f.client.post(path, json=command)
    assert admitted.status_code == 200, admitted.text
    job = f.worker.claim_job("candidate-guard")
    envelope = DevelopmentEnvelope(op="develop", operation_id=job.payload["operation_id"], job_id=job.id, tenant_id=job.tenant_id, attempt=job.attempt, lease_owner="candidate-guard")
    selected = SuiteStore(f.store.settings, f.store.objects).resolve_suite(f.selected.id, f.principal.tenant_id)
    development = CheckerDevelopmentStore(f.worker)
    request, _ = materialize_request(development, development.operation_for_dispatch(envelope))
    assert request.suite_revision_id == f.selected.id
    assert request.source_files == dict(selected.files)
    assert request.prior.files == {path: body for path, body in selected.files.items() if path in {"checker.py", "resources.py"}}
    artifact = CheckerArtifact(input_digest=request.target.digest, executor_revision=request.executor_revision, entrypoint="checker:create_audit", files={"checker.py": OLD_IMPORT + FACTORY})
    proof = SuiteValidationReport(
        suite_revision_id=request.suite_revision_id,
        suite_digest=request.suite_digest,
        library_revision=request.library_revision,
        program_digest=artifact.program_digest,
        oracle_revision=request.oracle_revision,
        cases=tuple(
            CaseEvaluation(
                case_id=sample.case_id,
                ordinal=sample.ordinal,
                input_digest=sample.inputs.digest,
                split="holdout" if sample.holdout else "training",
                coverage=True,
                clauses=(ClauseEvaluation(clause_id="R1", expected=sample.expected["R1"], actual=sample.expected["R1"], evidence_locatable=True, checks_performed=1),),
            )
            for sample in request.oracle
        ),
    )

    class Bypass:
        calls = 0

        def develop_and_run(self, request, *, spec, context):
            self.calls += 1
            self.source_files = request.source_files
            return DevelopmentOutcome(artifact, [], [], {"model_requests": 0}, validation=proof, usage_source="controlled")

    runtime = Bypass()
    result = development_handler(f.worker, runtime)(envelope, ExecutionContext())
    assert runtime.calls == 1, result
    assert runtime.source_files == dict(selected.files)
    assert result["state"] == "failed", result
    assert result["failure"] == "checker_cross_version_import"
    assert result["candidate_revision_id"] is None
    assert result["report"]["validation"]["verified"] is False
    assert "candidate_rejected" in result["report"]["validation"]["reasons"]
    assert f.client.post(path, json=command).json() == result
    assert runtime.calls == 1
    with f.store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE tenant_id IS NOT NULL").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.suite_publications").fetchone()[0] == 0
    assert admitted.json()["suite_revision_id"] == f.selected.id
