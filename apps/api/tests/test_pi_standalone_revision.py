"""Pi candidate from one explicit revision: own bytes, no inheritance, no automatic publish."""

import hashlib
import io
import json
from urllib.parse import quote
from uuid import uuid4

from agent.checker_development import DevelopmentOutcome
from agent.checker_spec import CheckerArtifact
from agent.run import ExecutionContext
from audit.checker_validation import CaseEvaluation, ClauseEvaluation, SuiteValidationReport
from openpyxl import Workbook
from product.fixtures import freeze_round_suite
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb
from worker.checker_optimizer import development_handler

from . import test_checker_optimizations as opt

STUB_RULES = {"type": "自定义", "rules": [{"id": "R1", "category": "阈值", "rule_to_check": "present"}], "checker_binding": {"R1": "checker:create_audit"}}
STUB_FILES = {"rules.json": json.dumps(STUB_RULES).encode(), "checker.py": opt.PROGRAM}
OTHER_TEMPLATE = b'[{"kind":"structure","columns":["Z9"]}]'


def _publish(client, suite_key, revision_id, expected, force):
    return client.put(
        "/suites/" + quote(suite_key, safe="") + "/active",
        json={"revision_id": revision_id, "expected_revision": expected, "operation_id": str(uuid4()), "force_unverified": force},
    )


def _rows(store, query, params=()):
    with store._conn() as conn:
        return conn.execute(query, params).fetchall()


def _feedback_snapshot(store):
    return _rows(store, "SELECT id::text, expected_status, revision FROM product.checker_feedback ORDER BY id")


def _round_snapshot(store):
    return _rows(store, "SELECT id::text, execution, result_attempt FROM product.audit_rounds ORDER BY id")


def _add_feedback(f, suite_id, rules, clause_id, index=0):
    round_id, file_id, version_id, rule_id = [str(uuid4()) for _ in range(4)]
    buffer = io.BytesIO()
    book = Workbook()
    book.active["A1"] = [5, 15, 16, 6][index % 4]
    book.active["B1"] = "hidden-sentinel" if (index + 1) % 4 == 0 else str(index)
    book.save(buffer)
    book.close()
    raw = buffer.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    f.store.objects.data[version_id] = raw
    with f.store._conn() as conn:
        for ident, kind in ((file_id, "file"), (round_id, "audit_round")):
            conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES (%s,%s,%s)", (ident, f.alice.tenant_id, kind))
            conn.execute("INSERT INTO product.resource_grants(resource_id,user_id,capability) VALUES (%s,%s,'review')", (ident, f.alice.user_id))
        conn.execute("INSERT INTO product.files(id,tenant_id,created_by,original_filename) VALUES (%s,%s,%s,'sample.xlsx')", (file_id, f.alice.tenant_id, f.alice.user_id))
        conn.execute(
            """INSERT INTO product.file_versions(id,file_id,tenant_id,original_filename,content_type,byte_size,sha256,object_key,status)
               VALUES (%s,%s,%s,'sample.xlsx','application/octet-stream',%s,%s,%s,'ready')""",
            (version_id, file_id, f.alice.tenant_id, len(raw), digest, version_id),
        )
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES (%s,%s,'selected',%s)", (rule_id, f.alice.tenant_id, Jsonb(rules)))
        conn.execute(
            """INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,result_attempt)
               VALUES (%s,%s,%s,%s,'test','completed',1)""",
            (round_id, f.alice.tenant_id, f.alice.user_id, round_id),
        )
        conn.execute(
            """INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body)
               VALUES (%s,%s,'target',%s,'selected',%s)""",
            (round_id, version_id, rule_id, Jsonb(rules)),
        )
        conn.execute(
            """INSERT INTO product.findings(id,round_id,item_key,classification,item,status,locator)
               VALUES (%s,%s,%s,'rule','selected','PASS',%s)""",
            (uuid4(), round_id, clause_id, Jsonb({"file_version_id": version_id, "rule_item_id": clause_id})),
        )
    freeze_round_suite(f.store, f.alice, round_id, version_id, digest, suite_id)
    payload = {
        "operation_id": str(uuid4()),
        "file_version_id": version_id,
        "rule_version_id": rule_id,
        "rule_clause_id": clause_id,
        "result_attempt": 1,
        "original_status": "PASS",
        "expected_status": "NG",
        "rationale": "User confirmed the selected revision",
        "evidence": [{"file_version_id": version_id, "locator": {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}, "excerpt": str(index)}],
    }
    response = f.clients[0].post(f"/audit-rounds/{round_id}/checker-feedback", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["id"]


class _KeepSource:
    def __init__(self):
        self.calls = 0
        self.request = None

    def develop_and_run(self, request, *, spec, context):
        self.calls += 1
        self.request = request
        context.raise_if_cancelled()
        files = {"checker.py": request.prior.files["checker.py"] + b"\n# edited\n"}
        if "resources.py" in request.prior.files:
            files["resources.py"] = request.prior.files["resources.py"]
        artifact = CheckerArtifact(input_digest=request.target.digest, executor_revision=request.executor_revision, entrypoint="checker:create_audit", files=files)
        clause = next(iter(request.oracle[0].expected))
        proof = SuiteValidationReport(
            suite_revision_id=request.suite_revision_id,
            suite_digest=request.suite_digest,
            library_revision=request.library_revision,
            related_suite_digests=dict(request.related_suite_digests),
            related_revision_ids=request.related_revision_ids,
            program_digest=artifact.program_digest,
            oracle_revision=request.oracle_revision,
            cases=tuple(
                CaseEvaluation(
                    case_id=row.case_id,
                    ordinal=row.ordinal,
                    input_digest=row.inputs.digest,
                    split="holdout" if row.holdout else "training",
                    coverage=True,
                    clauses=(ClauseEvaluation(clause_id=clause, expected=row.expected[clause], actual=row.expected[clause], evidence_locatable=True, checks_performed=1),),
                )
                for row in request.oracle
            ),
        )
        return DevelopmentOutcome(artifact, [], ["selected source"], {"model_requests": 1}, validation=proof, usage_source="controlled")


def test_exact_revision_becomes_a_standalone_candidate_and_a_different_revision_is_not_used(auth_store_factory):
    f = opt.fixture(auth_store_factory)
    other_checker = opt.PROGRAM + b"# other-revision\n"
    other = SuiteStore(f.store.settings, f.store.objects, role="migrator").create_revision(
        tenant_id=None,
        suite_key="threshold",
        files={"rules.json": json.dumps(opt.RULES).encode(), "checker.py": other_checker, "templates.json": OTHER_TEMPLATE},
        created_by=None,
    )
    before = _rows(f.store, "SELECT base_revision_id::text, overrides::text, resolved_digest FROM product.suite_revisions WHERE id=%s", (f.suite.id,))
    labels, rounds = _feedback_snapshot(f.store), _round_snapshot(f.store)
    response = f.clients[0].post(f"/suite-revisions/{other.id}/optimizations", json=opt.command(f))
    assert response.status_code == 200, response.text
    runtime = _KeepSource()
    result = development_handler(f.worker, runtime)(opt.claim(f), ExecutionContext())
    assert result["state"] == "completed", result
    assert runtime.request.source_files["checker.py"] == other_checker
    assert runtime.request.source_files["templates.json"] == OTHER_TEMPLATE
    candidate = SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(result["candidate_revision_id"], f.alice.tenant_id)
    source = SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(other.id, f.alice.tenant_id)
    untouched = SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(f.suite.id, f.alice.tenant_id)
    assert b"# other-revision" not in untouched.files["checker.py"]
    assert candidate.revision.base_revision_id is None
    assert set(candidate.revision.overrides) == set(candidate.files)
    assert all(candidate.revision.overrides[path] for path in candidate.files)
    assert candidate.files["templates.json"] == OTHER_TEMPLATE
    assert candidate.files["rules.json"] == source.files["rules.json"]
    assert b"# other-revision" in candidate.files["checker.py"] and b"# edited" in candidate.files["checker.py"]
    assert untouched.files["checker.py"] == opt.PROGRAM and untouched.files["templates.json"] != OTHER_TEMPLATE
    assert source.files["checker.py"] == other_checker
    assert _rows(f.store, "SELECT base_revision_id::text, overrides::text, resolved_digest FROM product.suite_revisions WHERE id=%s", (f.suite.id,)) == before
    assert _rows(f.store, "SELECT source_path FROM product.suite_revisions WHERE id=%s", (candidate.revision.id,)) == [(None,)]
    assert _rows(f.store, "SELECT count(*) FROM product.suite_publications")[0][0] == 0
    assert _feedback_snapshot(f.store) == labels and _round_snapshot(f.store) == rounds
    for path, digest in candidate.revision.overrides.items():
        assert f.store.objects.data[f"suites/{f.alice.tenant_id}/objects/{digest}"] == candidate.files[path]


def test_source_path_keeps_its_bytes_binding_and_is_not_published_or_overwritten(auth_store_factory):
    from audit.standard_suites import predefined_directory_suites

    f = opt.fixture(auth_store_factory, count=1)
    suites = SuiteStore(f.store.settings, f.store.objects, role="migrator")
    for key in ("UAES:U07", "UAES:U16"):
        stub = suites.create_revision(tenant_id=None, suite_key=key, files=STUB_FILES, created_by=None)
        published = _publish(f.clients[0], key, stub.id, 0, True)
        assert published.status_code == 200, published.text
    leaf = next(item for item in predefined_directory_suites() if item.source_path == "uaes/U08")
    source_files = dict(leaf.files)
    rules = json.loads(source_files["rules.json"])
    # This case exercises partial-source publication, independently of the
    # shipped leaf's current binding coverage. Business source bytes stay exact.
    rules["directory_coverage"] = "partial"
    rules["checker_binding"] = {clause: rules["checker_binding"][clause] for clause in ("U08:1", "U08:4")}
    source_files["rules.json"] = json.dumps(rules, ensure_ascii=False).encode()
    official = suites.create_revision(
        tenant_id=None,
        suite_key=leaf.suite_key,
        files=source_files,
        created_by=None,
        related_suite_keys=leaf.related_suite_keys,
        source_path=leaf.source_path,
    )
    feedback_ids = [_add_feedback(f, official.id, rules, "U08:1", i) for i in range(4)]
    labels, rounds = _feedback_snapshot(f.store), _round_snapshot(f.store)
    source_before = _rows(f.store, "SELECT source_path, base_revision_id::text, overrides::text, resolved_digest FROM product.suite_revisions WHERE id=%s", (official.id,))
    response = f.clients[0].post(f"/suite-revisions/{official.id}/optimizations", json=opt.command(f, feedback_ids=feedback_ids))
    assert response.status_code == 200, response.text
    runtime = _KeepSource()
    result = development_handler(f.worker, runtime)(opt.claim(f), ExecutionContext())
    assert result["state"] == "completed", result
    assert runtime.calls == 1
    assert runtime.request.source_files == source_files
    candidate = suites.resolve_suite(result["candidate_revision_id"], f.alice.tenant_id)
    source = suites.resolve_suite(official.id, f.alice.tenant_id)
    assert candidate.revision.base_revision_id is None
    assert candidate.revision.related_suite_keys == tuple(sorted(leaf.related_suite_keys))
    assert candidate.files["checker.py"] == leaf.files["checker.py"] + b"\n# edited\n"
    assert candidate.files["rules.json"] == source_files["rules.json"]
    assert candidate.files["resources.py"] == leaf.files["resources.py"]
    assert json.loads(candidate.files["rules.json"])["checker_binding"] == rules["checker_binding"]
    assert set(rules["checker_binding"]) != {row["id"] for row in rules["rules"]}
    assert "oracle_clause_coverage_incomplete" in result["report"]["validation"]["reasons"]
    assert result["report"]["validation"]["verified"] is False
    assert source.files == source_files
    assert _rows(f.store, "SELECT source_path, base_revision_id::text, overrides::text, resolved_digest FROM product.suite_revisions WHERE id=%s", (official.id,)) == source_before
    assert _rows(f.store, "SELECT source_path FROM product.suite_revisions WHERE id=%s", (candidate.revision.id,)) == [(None,)]
    assert _rows(f.store, "SELECT count(*) FROM product.suite_publications WHERE suite_key=%s", (leaf.suite_key,))[0][0] == 0
    refused = _publish(f.clients[0], leaf.suite_key, official.id, 0, True)
    assert refused.status_code == 409 and refused.json()["error"] == "partial_directory_suite_not_adoptable"
    unconfirmed = _publish(f.clients[0], leaf.suite_key, candidate.revision.id, 0, False)
    assert unconfirmed.status_code == 409 and unconfirmed.json()["error"] == "suite_confirmation_required"
    adopted = _publish(f.clients[0], leaf.suite_key, candidate.revision.id, 0, True)
    assert adopted.status_code == 200, adopted.text
    rollback = _publish(f.clients[0], leaf.suite_key, official.id, 1, True)
    assert rollback.status_code == 409 and rollback.json()["error"] == "partial_directory_suite_not_adoptable"
    assert suites.resolve_suite(official.id, f.alice.tenant_id).files == source_files
    assert _feedback_snapshot(f.store) == labels and _round_snapshot(f.store) == rounds


def test_authorization_misselection_and_missing_bytes_do_not_run(auth_store_factory):
    f = opt.fixture(auth_store_factory, count=1)
    private = SuiteStore(f.store.settings, f.store.objects, role="migrator").create_revision(
        tenant_id=f.other.tenant_id,
        suite_key="threshold",
        files={"rules.json": json.dumps(opt.RULES).encode(), "checker.py": opt.PROGRAM},
        created_by=f.other.user_id,
    )
    operation_id = str(uuid4())
    denied = f.clients[0].post(f"/suite-revisions/{private.id}/optimizations", json=opt.command(f, operation_id=operation_id))
    assert denied.status_code == 404 and denied.json()["error"] == "checker_access_denied"
    missing = f.clients[0].post(f"/suite-revisions/{uuid4()}/optimizations", json=opt.command(f))
    assert missing.status_code == 404
    unbound = SuiteStore(f.store.settings, f.store.objects, role="migrator").create_revision(
        tenant_id=None,
        suite_key="threshold",
        files={"rules.json": json.dumps({"type": "自定义", "rules": opt.RULES["rules"]}).encode(), "checker.py": opt.PROGRAM},
        created_by=None,
    )
    started = f.clients[0].post(f"/suite-revisions/{unbound.id}/optimizations", json=opt.command(f))
    assert started.status_code == 200, started.text
    runtime = opt.ControlledRuntime()
    failed = development_handler(f.worker, runtime)(opt.claim(f), ExecutionContext())
    assert failed["state"] == "failed" and failed["candidate_revision_id"] is None and runtime.calls == 0
    receipt, _ = opt.start(f)
    identity = opt.claim(f)
    key = "suites/official/objects/" + f.suite.overrides["checker.py"]
    del f.store.objects.data[key]
    missing_bytes = development_handler(f.worker, runtime)(identity, ExecutionContext())
    assert missing_bytes["state"] == "failed" and missing_bytes["candidate_revision_id"] is None and runtime.calls == 0
    assert _rows(f.store, "SELECT count(*) FROM product.checker_optimizations WHERE operation_id=%s", (operation_id,))[0][0] == 0
    assert _rows(f.store, "SELECT count(*) FROM product.suite_publications")[0][0] == 0


def test_failure_cancel_and_unknown_usage_do_not_publish_or_call_again(auth_store_factory):
    f = opt.fixture(auth_store_factory, count=1)
    labels, rounds = _feedback_snapshot(f.store), _round_snapshot(f.store)
    receipt, body = opt.start(f)
    stopped = f.clients[0].post(f"/checker-optimizations/{receipt['operation_id']}/stop")
    assert stopped.status_code == 200 and stopped.json()["state"] == "stopped" and stopped.json()["candidate_revision_id"] is None
    assert opt.start(f, body)[0]["state"] == "stopped"
    assert f.worker.claim_job("no-retry") is None

    class Once:
        def __init__(self, uncertain):
            self.calls = 0
            self.uncertain = uncertain

        def develop_and_run(self, request, *, spec, context):
            self.calls += 1
            context.raise_if_cancelled()
            return DevelopmentOutcome(None, [], [], {"model_requests": 1}, error_code="deep_output_invalid", usage_uncertain=self.uncertain, usage_source="provider")

    for uncertain, state in ((False, "failed"), (True, "unknown")):
        runtime = Once(uncertain)
        receipt, body = opt.start(f)
        result = development_handler(f.worker, runtime)(opt.claim(f), ExecutionContext())
        assert result["state"] == state and result["candidate_revision_id"] is None and runtime.calls == 1
        assert opt.start(f, body)[0] == result and runtime.calls == 1
        assert f.worker.claim_job("no-retry") is None
    assert _rows(f.store, "SELECT count(*) FROM product.suite_publications")[0][0] == 0
    assert _feedback_snapshot(f.store) == labels and _round_snapshot(f.store) == rounds


def test_explicit_adopt_and_rollback_leave_the_source_revision_unchanged(auth_store_factory):
    f = opt.fixture(auth_store_factory)
    labels, rounds = _feedback_snapshot(f.store), _round_snapshot(f.store)
    source_files = dict(SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(f.suite.id, f.alice.tenant_id).files)
    receipt, _ = opt.start(f)
    result = development_handler(f.worker, opt.ControlledRuntime())(opt.claim(f), ExecutionContext())
    assert result["state"] == "completed" and result["report"]["validation"]["verified"] is True
    assert _rows(f.store, "SELECT count(*) FROM product.suite_publications")[0][0] == 0
    candidate = result["candidate_revision_id"]
    assert _publish(f.clients[0], "threshold", candidate, 0, False).status_code == 200
    blocked = _publish(f.clients[0], "threshold", f.suite.id, 1, False)
    assert blocked.status_code == 409 and blocked.json()["error"] == "suite_confirmation_required"
    rolled = _publish(f.clients[0], "threshold", f.suite.id, 1, True)
    assert rolled.status_code == 200, rolled.text
    active = f.clients[0].get("/suites/threshold/active")
    assert active.status_code == 200 and active.json()["current_revision_id"] == f.suite.id
    assert SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(f.suite.id, f.alice.tenant_id).files == source_files
    saved = SuiteStore(f.store.settings, f.store.objects, role="migrator").resolve_suite(candidate, f.alice.tenant_id)
    assert saved.revision.base_revision_id is None
    assert json.loads(saved.files["rules.json"])["checker_binding"] == opt.RULES["checker_binding"]
    assert _feedback_snapshot(f.store) == labels and _round_snapshot(f.store) == rounds
