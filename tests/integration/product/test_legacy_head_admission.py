"""New audits refuse a legacy carrier and still run a pinned directory Checker.

The controller path is the product worker. Poisoning the historical selector
must not be required for a directory round to finish, and must not run during
refusal.
"""

import io
import json
import os
import subprocess
from uuid import uuid4

from api import create_app
from audit.standard_suites import predefined_directory_suites, standard_suites
from fastapi.testclient import TestClient
from openpyxl import Workbook
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.seed_standard_suites import install_official_sources
from product.settings import Settings
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb
from worker import process_claimed


def test_api_refuses_a_carrier_pin_and_the_worker_runs_the_directory_revision(auth_store_factory, core_system, monkeypatch, tmp_path):
    settings = Settings(usage_charge_enabled=False, feishu_notifications_enabled=False, feishu_app_id="legacy-admission", git_sha="a" * 40)
    admin = auth_store_factory(settings)
    principal = admin.upsert_feishu_identity("legacy-admission", "operator")
    suites = SuiteStore(settings, admin.objects, role="migrator")
    available = {suite.suite_key: suite for suite in predefined_directory_suites()}
    order = []

    def include(key):
        if key in order:
            return
        for related in available[key].related_suite_keys:
            include(related)
        order.append(key)

    include("UAES:U08")
    installed = install_official_sources(suites, [available[key] for key in order])
    revisions = {row["suite_key"]: row["revision_id"] for row in installed}
    legacy = next(suite for suite in standard_suites() if suite.suite_key == "UAES:U08")
    carrier = suites.create_revision(tenant_id=None, suite_key=legacy.suite_key, files=dict(legacy.files), related_suite_keys=legacy.related_suite_keys, created_by=None)
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("legacy")
        raise AssertionError("legacy selector")

    monkeypatch.setattr("audit.standard_library.check_clause", forbidden)
    monkeypatch.setattr("audit.standard_suites._carriers", forbidden)
    monkeypatch.setattr("audit.presets.preset_source_bindings", forbidden)
    monkeypatch.setattr("audit.engine.routing.resolve_engine_agent_class", forbidden)
    hook = tmp_path / "legacy-hook"
    hook.mkdir()
    marker = tmp_path / "legacy-called"
    (hook / "sitecustomize.py").write_text(
        "def _boom(*_args, **_kwargs):\n"
        f"    open({str(marker)!r}, 'w').write('called')\n"
        "    raise SystemExit(86)\n"
        "import audit.standard_library as library\n"
        "library.check_clause = _boom\n"
        "library.prepare_target = _boom\n"
        "import audit.standard_suites as suites\n"
        "suites._carriers = _boom\n"
        "import audit.presets as presets\n"
        "presets.preset_source_bindings = _boom\n"
        "import audit.engine.routing as routing\n"
        "routing.resolve_engine_agent_class = _boom\n",
        encoding="utf-8",
    )
    real_run = subprocess.run

    def run(args, **kwargs):
        env = dict(kwargs.get("env") or os.environ)
        env["PYTHONPATH"] = str(hook) + os.pathsep + env.get("PYTHONPATH", "")
        kwargs["env"] = env
        return real_run(args, **kwargs)

    function = core_system.run.__func__
    subprocess_module = next(cell.cell_contents for cell in function.__closure__ or () if getattr(cell.cell_contents, "__name__", "") == "subprocess")
    monkeypatch.setattr(subprocess_module, "run", run)
    core_system.install(admin, guest_process=True)
    worker = build_product_store(settings, objects=admin.objects, role="worker")
    book = Workbook()
    book.active.title = "控制计划"
    book.active.append(["控制计划"])
    book.active.append(["工序名称/操作描述", "机器/装置/夹具/工装", "特性类型"])
    book.active.append(["装配", "压机", "CC"])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    raw = stream.getvalue()
    with TestClient(create_app(settings, auth_store=build_product_store(settings, objects=admin.objects, role="api"))) as client:
        client.cookies.set(SESSION_COOKIE, admin.create_session(principal))
        for key in order:
            if key == "UAES:U08":
                refused_pin = client.put(
                    f"/suites/{key}/active",
                    json={"revision_id": carrier.id, "expected_revision": 0, "operation_id": str(uuid4()), "force_unverified": True},
                )
                assert refused_pin.status_code == 409, refused_pin.text
                assert refused_pin.json() == {"error": "suite_version_confirmation_required"}
                continue
            response = client.put(f"/suites/{key}/active", json={"revision_id": revisions[key], "expected_revision": 0, "operation_id": str(uuid4()), "force_unverified": True})
            assert response.status_code == 200, response.text
        with admin._conn() as conn:
            conn.execute(
                """INSERT INTO product.tenant_suite_heads(tenant_id,suite_key,current_revision_id,revision,force_unverified)
                   VALUES(%s,'UAES:U08',%s,1,true)""",
                (principal.tenant_id, carrier.id),
            )
        rule_id = str(uuid4())
        with admin._conn() as conn:
            conn.execute(
                "INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'U08',%s)",
                (rule_id, principal.tenant_id, Jsonb({**json.loads(available["UAES:U08"].files["rules.json"]), "suite_key": "UAES:U08"})),
            )
        staged = admin.create_staging(principal, "控制计划.xlsx", "application/octet-stream", len(raw))
        uploaded = admin.finalize_version(admin.put_blob(staged, raw))
        draft = client.post("/drafts", json={"items": [{"file_version_id": uploaded.id, "role": "target", "rule_version_id": rule_id}]})
        assert draft.status_code == 201, draft.text
        refused = client.post(f"/drafts/{draft.json()['id']}/accept", json={"idempotency_key": str(uuid4())})
        assert refused.status_code == 409, refused.text
        assert refused.json() == {"error": "suite_version_confirmation_required"}
        assert worker.claim_job("legacy-admission") is None
        assert calls == []
        assert not marker.exists()
        adopted = client.put(
            "/suites/UAES:U08/active",
            json={"revision_id": revisions["UAES:U08"], "expected_revision": 1, "operation_id": str(uuid4()), "force_unverified": True},
        )
        assert adopted.status_code == 200, adopted.text
        draft = client.post("/drafts", json={"items": [{"file_version_id": uploaded.id, "role": "target", "rule_version_id": rule_id}]})
        accepted = client.post(f"/drafts/{draft.json()['id']}/accept", json={"idempotency_key": str(uuid4())})
        assert accepted.status_code == 201, accepted.text
        process_claimed(worker, worker.claim_job("legacy-admission"), "legacy-admission")
        loaded = client.get(f"/rounds/{accepted.json()['id']}")
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["execution"] == "completed", loaded.text
        frozen = suites.read_snapshot(accepted.json()["id"], principal.tenant_id)
        assert frozen.targets[0].suite_revision_id == revisions["UAES:U08"]
        assert loaded.json()["findings"]
        assert calls == []
        assert not marker.exists()
        rules = json.loads(available["UAES:U08"].files["rules.json"])
        rules.pop("checker_binding")
        saved = client.post(
            f"/suite-revisions/{revisions['UAES:U08']}/candidates",
            json={"operation_id": str(uuid4()), "files": {"rules.json": json.dumps(rules, ensure_ascii=False)}},
        )
        assert saved.status_code == 201, saved.text
        unbound = saved.json()["id"]
        pinned = client.put(
            "/suites/UAES:U08/active",
            json={"revision_id": unbound, "expected_revision": 2, "operation_id": str(uuid4()), "force_unverified": True},
        )
        assert pinned.status_code == 200, pinned.text
        draft = client.post("/drafts", json={"items": [{"file_version_id": uploaded.id, "role": "target", "rule_version_id": rule_id}]})
        unbound_accept = client.post(f"/drafts/{draft.json()['id']}/accept", json={"idempotency_key": str(uuid4())})
        assert unbound_accept.status_code == 400, unbound_accept.text
        assert unbound_accept.json() == {"error": "checker_binding_required"}
        assert worker.claim_job("legacy-admission") is None
        assert calls == []
        restored = client.put(
            "/suites/UAES:U08/active",
            json={"revision_id": revisions["UAES:U08"], "expected_revision": 3, "operation_id": str(uuid4()), "force_unverified": True},
        )
        assert restored.status_code == 200, restored.text
        text = b"not a workbook"
        staged = admin.create_staging(principal, "控制计划.txt", "application/octet-stream", len(text))
        plain = admin.finalize_version(admin.put_blob(staged, text))
        draft = client.post("/drafts", json={"items": [{"file_version_id": plain.id, "role": "target", "rule_version_id": rule_id}]})
        plain_accept = client.post(f"/drafts/{draft.json()['id']}/accept", json={"idempotency_key": str(uuid4())})
        assert plain_accept.status_code == 201, plain_accept.text
        process_claimed(worker, worker.claim_job("legacy-admission"), "legacy-admission")
        plain_round = client.get(f"/rounds/{plain_accept.json()['id']}")
        assert plain_round.status_code == 200, plain_round.text
        assert plain_round.json()["execution"] == "completed"
        assert plain_round.json()["findings"]
        assert {row["status"] for row in plain_round.json()["findings"]} == {"UNABLE"}
        assert calls == []
        assert not marker.exists()
        draft = client.post("/drafts", json={"items": [{"file_version_id": uploaded.id, "role": "target", "rule_version_id": rule_id}]})
        damaged_accept = client.post(f"/drafts/{draft.json()['id']}/accept", json={"idempotency_key": str(uuid4())})
        assert damaged_accept.status_code == 201, damaged_accept.text
        resolved = suites.resolve_suite(revisions["UAES:U08"], principal.tenant_id)
        with admin._conn() as conn:
            damaged = conn.execute(
                "SELECT object_key FROM product.suite_objects WHERE owner_scope='official' AND digest=%s",
                (resolved.object_digests["checker.py"],),
            ).fetchone()[0]
        original = admin.objects.get(damaged)
        admin.objects.delete(damaged)
        admin.objects.put_exclusive(damaged, b"damaged selected source", "application/octet-stream")
        process_claimed(worker, worker.claim_job("legacy-admission"), "legacy-admission")
        damaged_round = client.get(f"/rounds/{damaged_accept.json()['id']}")
        assert damaged_round.json()["execution"] == "failed"
        assert not any(row["status"] == "PASS" for row in damaged_round.json()["findings"])
        admin.objects.delete(damaged)
        admin.objects.put_exclusive(damaged, original, "application/octet-stream")
        assert calls == []
        assert not marker.exists()


def _carrier_files():
    checker = (
        b"from audit.standard_library import check_clause, prepare_target\n"
        b"RESOURCES = {'carriers': {}, 'methods': {'C1': 'check'}}\n"
        b"def check(context):\n    return check_clause(context, RESOURCES)\n"
        b"def precheck(context):\n    return prepare_target(context, RESOURCES)\n"
    )
    rules = {"title": "旧", "rules": [{"id": "C1"}], "checker_binding": {"C1": "checker:check"}}
    return {"checker.py": checker, "rules.json": json.dumps(rules).encode()}


def test_http_refuses_an_official_carrier_head_and_records_precheck_confirmation(auth_store_factory, core_system, monkeypatch):
    from audit.presets import preset_document_type, preset_rule
    from audit.standard_suites import library_revision

    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("legacy")
        raise AssertionError("legacy selector")

    settings = Settings(usage_charge_enabled=False, feishu_notifications_enabled=False, feishu_app_id="official-carrier", git_sha="b" * 40)
    admin = auth_store_factory(settings)
    principal = admin.upsert_feishu_identity("official-carrier", "operator")
    suites = SuiteStore(settings, admin.objects, role="migrator")
    files = _carrier_files()
    carrier = suites.create_revision(tenant_id=None, suite_key="OFFICIAL:OLD", files=files, created_by=None)
    proof = {
        "reasons": [],
        "total_samples": 4,
        "passed_samples": 4,
        "new_errors": 0,
        "library_revision": library_revision(),
        "proof_digest": "ab" * 32,
        "report": {"related_revision_ids": []},
    }
    with admin._conn() as conn:
        conn.execute(
            """INSERT INTO product.suite_validations(tenant_id, revision_id, status, revision, suite_digest, related_suite_digests, proof)
               VALUES (NULL, %s, 'verified', 1, %s, '{}', %s)""",
            (carrier.id, carrier.resolved_digest, Jsonb(proof)),
        )
        conn.execute(
            """INSERT INTO product.official_suite_heads(suite_key, current_revision_id, revision, validation_revision)
               VALUES ('OFFICIAL:OLD', %s, 1, 1)""",
            (carrier.id,),
        )
    from audit.standard_suites import predefined_directory_suites
    from product.seed_standard_suites import install_official_sources

    directory = {suite.suite_key: suite for suite in predefined_directory_suites()}
    directory_order = []

    def include_directory(key):
        if key in directory_order:
            return
        for related in directory[key].related_suite_keys:
            include_directory(related)
        directory_order.append(key)

    include_directory("UAES:U08")
    installed = {row["suite_key"]: row["revision_id"] for row in install_official_sources(suites, [directory[key] for key in directory_order])}
    baselines = {suite.suite_key: suite for suite in standard_suites()}
    order = []

    def include(key):
        if key in order:
            return
        for related in baselines[key].related_suite_keys:
            include(related)
        order.append(key)

    include("UAES:U08")
    pinned = {key: suites.create_revision(tenant_id=None, suite_key=key, files=dict(baselines[key].files), related_suite_keys=baselines[key].related_suite_keys, created_by=None) for key in order}
    monkeypatch.setattr("audit.standard_library.check_clause", forbidden)
    monkeypatch.setattr("audit.standard_suites._carriers", forbidden)
    monkeypatch.setattr("audit.presets.preset_source_bindings", forbidden)
    monkeypatch.setattr("audit.engine.routing.resolve_engine_agent_class", forbidden)
    core_system.install(admin, guest_process=False)
    worker = build_product_store(settings, objects=admin.objects, role="worker")
    raw = b"official-carrier-bytes"
    with TestClient(create_app(settings, auth_store=build_product_store(settings, objects=admin.objects, role="api"))) as client:
        client.cookies.set(SESSION_COOKIE, admin.create_session(principal))
        rule_id = str(uuid4())
        with admin._conn() as conn:
            conn.execute(
                "INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'旧',%s)",
                (rule_id, principal.tenant_id, Jsonb({"title": "旧", "rules": [{"id": "C1"}], "suite_key": "OFFICIAL:OLD"})),
            )
        staged = admin.create_staging(principal, "旧.xlsx", "application/octet-stream", len(raw))
        uploaded = admin.finalize_version(admin.put_blob(staged, raw))
        draft = client.post("/drafts", json={"items": [{"file_version_id": uploaded.id, "role": "target", "rule_version_id": rule_id}]})
        refused = client.post(f"/drafts/{draft.json()['id']}/accept", json={"idempotency_key": str(uuid4())})
        assert refused.status_code == 409, refused.text
        assert refused.json() == {"error": "suite_version_confirmation_required"}
        assert worker.claim_job("official-carrier") is None

        for key in directory_order:
            response = client.put(
                f"/suites/{key}/active",
                json={"revision_id": installed[key], "expected_revision": 0, "operation_id": str(uuid4()), "force_unverified": True},
            )
            assert response.status_code == 200, response.text
        book = Workbook()
        book.active.title = "控制计划"
        book.active.append(["控制计划", "零件号", "P-1"])
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        body = stream.getvalue()
        created = client.post("/files", json={"original_filename": "控制计划.xlsx", "byte_size": len(body)})
        assert created.status_code == 201, created.text
        version = created.json()
        path = f"/files/{version['file_id']}/versions/{version['id']}"
        assert client.put(path + "/blob", content=body).status_code == 200
        assert client.post(path + "/finalize").status_code == 200
        validation = {"request_id": str(uuid4()), "source_file_id": version["file_id"], "source_file_version_id": version["id"]}
        assert client.post("/file-validations", json=validation).status_code == 202
        process_claimed(worker, worker.claim_job("official-validation"), "official-validation")

        class Metadata:
            def structured_completion(self, *, operation, **_kwargs):
                if operation == "document_classification":
                    return preset_document_type(preset_rule("UAES", "U08"))
                return json.dumps({"customer": "UAES", "part_number": "P-1"})

        task = client.post(
            "/audit-tasks",
            json={
                "request_id": str(uuid4()),
                "analysis_mode": "standard",
                "customer_preference": "UAES",
                "files": [{"file_version_id": version["id"], "validation_id": validation["request_id"], "selection": "selected", "removal_reason": None}],
            },
        )
        assert task.status_code == 202, task.text
        process_claimed(worker, worker.claim_job("official-recognition"), "official-recognition", rule_ai=Metadata())
        current = client.get("/audit-tasks/" + task.json()["id"])
        assert current.status_code == 200, current.text
        matched = current.json()["files"][0]
        assert matched.get("match", {}).get("state") == "matched", matched.get("match")
        directory_precheck = client.post(
            f"/audit-tasks/{task.json()['id']}/prechecks",
            json={"request_id": str(uuid4()), "expected_revision": current.json()["input_revision"]},
        )
        assert directory_precheck.status_code == 202, directory_precheck.text
        prepared = directory_precheck.json()["input"]["files"][0]
        assert prepared["suite_target"]["suite_revision_id"] == installed["UAES:U08"]
        assert prepared["rule_execution"].get("reason") != "suite_version_confirmation_required"
        process_claimed(worker, worker.claim_job("official-directory-precheck"), "official-directory-precheck")
        assert calls == []
        current = client.get("/audit-tasks/" + task.json()["id"])
        assert current.status_code == 200, current.text
        switched = client.put(
            "/suites/UAES:U08/active",
            json={"revision_id": pinned["UAES:U08"].id, "expected_revision": 1, "operation_id": str(uuid4()), "force_unverified": True},
        )
        assert switched.status_code == 409, switched.text
        assert switched.json() == {"error": "suite_version_confirmation_required"}
        with admin._conn() as conn:
            conn.execute(
                "UPDATE product.tenant_suite_heads SET current_revision_id=%s WHERE tenant_id=%s AND suite_key='UAES:U08'",
                (pinned["UAES:U08"].id, principal.tenant_id),
            )
        precheck = client.post(
            f"/audit-tasks/{task.json()['id']}/prechecks",
            json={"request_id": str(uuid4()), "expected_revision": current.json()["input_revision"]},
        )
        assert precheck.status_code == 202, precheck.text
        body = precheck.json()
        execution = body["input"]["files"][0]["rule_execution"]
        assert execution == {"mode": "unavailable", "reason": "suite_version_confirmation_required"}, body
        process_claimed(worker, worker.claim_job("official-precheck"), "official-precheck")
        current = client.get("/audit-tasks/" + task.json()["id"])
        assert current.status_code == 200, current.text
        confirmed = client.post(
            f"/audit-tasks/{task.json()['id']}/confirm",
            json={
                "request_id": str(uuid4()),
                "expected_revision": current.json()["input_revision"],
                "expected_confirmation_basis_digest": current.json()["confirmation_basis_digest"],
            },
        )
        assert confirmed.status_code == 409, confirmed.text
        assert confirmed.json() == {"error": "suite_version_confirmation_required"}
        assert worker.claim_job("official-confirm") is None
        assert calls == []


def test_frozen_carrier_round_replays_its_bytes_after_the_directory_head_is_current(auth_store_factory, core_system, monkeypatch, tmp_path):
    """An already accepted carrier snapshot keeps its bytes when the head moves.

    New admission still refuses that carrier. Replay calls the frozen program,
    not the directory revision now selected for later rounds.
    """
    from audit.checker_core import ExecutionSnapshot, FrozenInput, RuleBinding, checker_digest, runtime_revision
    from audit.standard_suites import library_revision
    from product.store.suites import freeze_execution

    settings = Settings(usage_charge_enabled=False, feishu_notifications_enabled=False, feishu_app_id="historical-replay", git_sha="c" * 40)
    admin = auth_store_factory(settings)
    principal = admin.upsert_feishu_identity("historical-replay", "operator")
    suites = SuiteStore(settings, admin.objects, role="migrator")
    carrier_source = b"from audit.standard_library import check_clause\nRESOURCES = {'carriers': {}, 'methods': {'C1': 'check'}}\ndef check(context):\n    return check_clause(context, RESOURCES)\n"
    carrier_rules = {"title": "历史", "rules": [{"id": "C1"}], "checker_binding": {"C1": "checker:check"}}
    carrier = suites.create_revision(tenant_id=None, suite_key="HIST:OLD", files={"checker.py": carrier_source, "rules.json": json.dumps(carrier_rules).encode()}, created_by=None)
    directory_rules = {"title": "目录", "rules": [{"id": "C1"}], "checker_binding": {"C1": "checker:create_audit"}}
    directory = suites.create_revision(
        tenant_id=None,
        suite_key="HIST:OLD",
        files={"checker.py": b"def create_audit(context):\n    raise SystemExit('directory-selected')\n", "rules.json": json.dumps(directory_rules).encode()},
        created_by=None,
        source_path="v5/ACME/HIST",
    )
    proof = {"reasons": [], "total_samples": 4, "passed_samples": 4, "new_errors": 0, "library_revision": library_revision(), "proof_digest": "ab" * 32, "report": {"related_revision_ids": []}}
    with admin._conn() as conn:
        conn.execute(
            """INSERT INTO product.suite_validations(tenant_id, revision_id, status, revision, suite_digest, related_suite_digests, proof)
               VALUES (NULL, %s, 'verified', 1, %s, '{}', %s)""",
            (directory.id, directory.resolved_digest, Jsonb(proof)),
        )
        conn.execute(
            """INSERT INTO product.official_suite_heads(suite_key, current_revision_id, revision, validation_revision)
               VALUES ('HIST:OLD', %s, 1, 1)""",
            (directory.id,),
        )
        with conn.transaction():
            from product.store.suites import freeze_selected_targets

            selected = freeze_selected_targets(suites, conn, principal.tenant_id, [{"role": "target", "file_version_id": "file-1", "rule_body": {"suite_key": "HIST:OLD"}}])
            assert selected[0].suite_revision_id == directory.id
    resolved = suites.resolve_suite(carrier.id, principal.tenant_id)
    code = checker_digest({"checker.py": resolved.files["checker.py"]})
    binding = RuleBinding(resolved.revision.suite_key, code, {"C1": "checker:check"})
    raw = b"historical-bytes"
    staged = admin.create_staging(principal, "historical.bin", "application/octet-stream", len(raw))
    uploaded = admin.finalize_version(admin.put_blob(staged, raw))
    assert uploaded.sha256
    target = freeze_execution(
        suite=resolved,
        file_version_id=uploaded.id,
        binding=binding,
        validation=None,
        related_suite_digests={},
        library_revision=library_revision(),
        allow_unverified=True,
    )
    identity = admin.personal_model_identity(principal.tenant_id, principal.user_id)
    library = library_revision()
    round_id = str(uuid4())
    snapshot = ExecutionSnapshot(
        principal.tenant_id,
        principal.user_id,
        (FrozenInput(uploaded.id, uploaded.sha256, "target", uploaded.original_filename, file_id=uploaded.file_id),),
        (target,),
        runtime_revision(settings.deep_execution_revision(identity), library),
        library,
        identity,
        round_id,
    )
    job_id = str(uuid4())
    payload = {
        "round_id": round_id,
        "executor": "ppap-checker-core",
        "executor_revision": snapshot.runtime_revision,
        "checker_snapshot_digest": snapshot.input_digest,
        "model_identity": dict(identity),
    }
    with admin._conn() as conn:
        conn.execute("INSERT INTO product.resources(id,tenant_id,kind) VALUES(%s,%s,'audit_round')", (round_id, principal.tenant_id))
        conn.execute(
            """INSERT INTO product.audit_rounds(id,tenant_id,created_by,idempotency_key,request_fingerprint,execution,analysis_mode)
               VALUES(%s,%s,%s,%s,'historical-replay','queued','standard')""",
            (round_id, principal.tenant_id, principal.user_id, job_id),
        )
        rule_id = str(uuid4())
        conn.execute("INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'历史',%s)", (rule_id, principal.tenant_id, Jsonb(carrier_rules)))
        conn.execute(
            "INSERT INTO product.round_inputs(round_id,file_version_id,role,rule_version_id,rule_title,rule_body) VALUES(%s,%s,'target',%s,'历史',%s)",
            (round_id, uploaded.id, rule_id, Jsonb(carrier_rules)),
        )
        suites.save_snapshot(conn, snapshot=snapshot)
        conn.execute(
            "INSERT INTO engine.jobs(id,tenant_id,audit_round_id,kind,payload,status) VALUES(%s,%s,%s,'audit',%s,'queued')",
            (job_id, principal.tenant_id, round_id, Jsonb(payload)),
        )
        before = conn.execute("SELECT body FROM product.checker_execution_snapshots WHERE round_id=%s", (round_id,)).fetchone()[0]
    marker = tmp_path / "historical-called"
    hook = tmp_path / "historical-hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(
        "import audit.standard_library as library\n"
        "_real = library.check_clause\n"
        "def _mark(*args, **kwargs):\n"
        f"    open({str(marker)!r}, 'a').write('called\\n')\n"
        "    return _real(*args, **kwargs)\n"
        "library.check_clause = _mark\n",
        encoding="utf-8",
    )
    real_run = subprocess.run

    def run(args, **kwargs):
        env = dict(kwargs.get("env") or os.environ)
        env["PYTHONPATH"] = str(hook) + os.pathsep + env.get("PYTHONPATH", "")
        kwargs["env"] = env
        return real_run(args, **kwargs)

    function = core_system.run.__func__
    subprocess_module = next(cell.cell_contents for cell in function.__closure__ or () if getattr(cell.cell_contents, "__name__", "") == "subprocess")
    monkeypatch.setattr(subprocess_module, "run", run)
    core_system.install(admin, guest_process=True)
    worker = build_product_store(settings, objects=admin.objects, role="worker")
    claimed = worker.claim_job("historical-replay")
    assert claimed is not None and claimed.kind == "audit"
    process_claimed(worker, claimed, "historical-replay")
    assert marker.exists()
    with admin._conn() as conn:
        execution = conn.execute("SELECT execution FROM product.audit_rounds WHERE id=%s", (round_id,)).fetchone()[0]
        findings = conn.execute("SELECT status, locator FROM product.findings WHERE round_id=%s", (round_id,)).fetchall()
        after = conn.execute("SELECT body FROM product.checker_execution_snapshots WHERE round_id=%s", (round_id,)).fetchone()[0]
        head = conn.execute("SELECT current_revision_id::text FROM product.official_suite_heads WHERE suite_key='HIST:OLD'").fetchone()[0]
    assert execution == "completed", execution
    assert findings and all(row[0] != "PASS" for row in findings)
    assert all(row[1]["suite_revision_id"] == carrier.id for row in findings)
    assert after == before
    assert head == directory.id
    late = {"findings": [{"item_key": "late", "status": "PASS", "locator": {"suite_revision_id": directory.id}}], "coverage": {}}
    assert worker.complete_job(claimed, "historical-replay", late, "succeeded") is False
    with admin._conn() as conn:
        statuses = [row[0] for row in conn.execute("SELECT status FROM product.findings WHERE round_id=%s", (round_id,)).fetchall()]
    assert "PASS" not in statuses
