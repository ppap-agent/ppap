"""U08 admission and execution use frozen bytes even when an older Suite exists.

Real API, PostgreSQL, Silo, Worker, Controller and guest process. The existing
core_system fixture substitutes only RPC transport; no model grant is issued.
"""

import ast
import io
import json
from uuid import uuid4

import pytest
from api import create_app
from audit.checker_core import checker_digest
from audit.standard_suites import predefined_directory_suites
from fastapi.testclient import TestClient
from openpyxl import Workbook
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.seed_standard_suites import install_official_sources
from product.settings import Settings
from product.store.suites import SuiteStore
from psycopg.types.json import Jsonb
from worker import process_claimed


@pytest.mark.parametrize("fault", ["native", "missing-binding", "no-reader", "corrupt-source", "broken-original", "unsupported-format"])
def test_u08_api_worker_keeps_selected_source_under_failures(auth_store_factory, core_system, v4_objects, monkeypatch, fault):
    settings = Settings(usage_charge_enabled=False, feishu_notifications_enabled=False, feishu_app_id="u08-source-boundary", git_sha="a" * 40)
    admin = auth_store_factory(settings, objects=v4_objects)
    principal = admin.upsert_feishu_identity("u08-source-boundary", "operator")
    suites = SuiteStore(settings, v4_objects, role="migrator")
    available = {suite.suite_key: suite for suite in predefined_directory_suites()}
    selected_source = available["UAES:U08"]
    assert selected_source.source_path == "uaes/U08"
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
    # A stored historical carrier: its program dispatches through the removed class loader.
    legacy = suites.create_revision(
        tenant_id=None,
        suite_key="UAES:U08",
        files={
            "checker.py": b"from audit.standard_library import check_clause\nRESOURCES = {'carriers': {}, 'methods': {}}\ndef check(context):\n    return check_clause(context, RESOURCES)\n",
            "rules.json": json.dumps({"rules": [{"id": "U08:1"}], "checker_binding": {"U08:1": "checker:check"}}).encode(),
        },
        related_suite_keys=("UAES:U07", "UAES:U16"),
        created_by=None,
    )
    assert b"check_clause" in suites.resolve_suite(legacy.id, principal.tenant_id).files["checker.py"]
    core_system.install(admin, guest_process=True)
    worker = build_product_store(settings, objects=v4_objects, role="worker")
    captured = []
    original_run = core_system.run

    def observe(job, inputs, work, deadline, cancelled):
        manifest = json.loads((inputs / "manifest.json").read_text())
        if job.operation == "execute-audit":
            assert manifest["spec"]["allow_model_calls"] is False
            programs = {digest: {name: (inputs / path).read_bytes() for name, path in paths.items()} for digest, paths in manifest["program_paths"].items()}
            captured.append((manifest["snapshot"], programs))
        return original_run(job, inputs, work, deadline, cancelled)

    monkeypatch.setattr(core_system, "run", observe)
    with TestClient(create_app(settings, auth_store=build_product_store(settings, objects=v4_objects, role="api"))) as client:
        client.cookies.set(SESSION_COOKIE, admin.create_session(principal))
        for key in order:
            response = client.put(f"/suites/{key}/active", json={"revision_id": revisions[key], "expected_revision": 0, "operation_id": str(uuid4()), "force_unverified": True})
            assert response.status_code == 200, response.text
        selected = revisions["UAES:U08"]
        changes = {}
        if fault == "missing-binding":
            rules = json.loads(selected_source.files["rules.json"])
            rules.pop("checker_binding")
            changes["rules.json"] = json.dumps(rules, ensure_ascii=False)
        elif fault == "no-reader":
            resource = ast.literal_eval(selected_source.files["resources.py"].decode().split("RESOURCE = ", 1)[1])
            resource["methods"].pop("U08:6")
            changes["resources.py"] = "RESOURCE = " + repr(resource) + "\n"
        if changes:
            saved = client.post(f"/suite-revisions/{selected}/candidates", json={"operation_id": str(uuid4()), "files": changes})
            assert saved.status_code == 201, saved.text
            selected = saved.json()["id"]
            adopted = client.put("/suites/UAES:U08/active", json={"revision_id": selected, "expected_revision": 1, "operation_id": str(uuid4()), "force_unverified": True})
            assert adopted.status_code == 200, adopted.text
        resolved = suites.resolve_suite(selected, principal.tenant_id)
        assert dict(resolved.files) == {**selected_source.files, **{name: body.encode() for name, body in changes.items()}}
        program = {name: body for name, body in resolved.files.items() if name in {"checker.py", "resources.py"}}
        rule_id = str(uuid4())
        with admin._conn() as conn:
            conn.execute(
                "INSERT INTO product.rule_versions(id,tenant_id,title,body) VALUES(%s,%s,'U08 boundary',%s)",
                (rule_id, principal.tenant_id, Jsonb({**json.loads(selected_source.files["rules.json"]), "suite_key": "UAES:U08"})),
            )
            assert conn.execute("SELECT source_path FROM product.suite_revisions WHERE id=%s", (revisions["UAES:U08"],)).fetchone()[0] == "uaes/U08"
        book = Workbook()
        book.active.title = "控制计划"
        book.active.append(["控制计划"])
        book.active.append(["工序名称/操作描述", "机器/装置/夹具/工装", "特性类型"])
        book.active.append(["装配", "压机", "CC"])
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        raw = b"unreadable original" if fault == "broken-original" else stream.getvalue()
        filename = "控制计划.txt" if fault == "unsupported-format" else "控制计划.xlsx"
        staged = admin.create_staging(principal, filename, "application/octet-stream", len(raw))
        uploaded = admin.finalize_version(admin.put_blob(staged, raw))
        draft = client.post("/drafts", json={"items": [{"file_version_id": uploaded.id, "role": "target", "rule_version_id": rule_id}]})
        assert draft.status_code == 201, draft.text
        accepted = client.post(f"/drafts/{draft.json()['id']}/accept", json={"idempotency_key": str(uuid4())})
        if fault == "missing-binding":
            assert accepted.status_code == 400 and accepted.json() == {"error": "checker_binding_required"}
            assert not captured and worker.claim_job("u08-boundary") is None
            return
        assert accepted.status_code == 201, accepted.text
        round_id = accepted.json()["id"]
        frozen = suites.read_snapshot(round_id, principal.tenant_id)
        target = frozen.targets[0]
        assert target.suite_revision_id == selected != legacy.id
        assert target.suite_digest == resolved.revision.resolved_digest
        assert target.binding.checker_digest == checker_digest(program)
        job = worker.claim_job("u08-boundary")
        assert job.kind == "audit"
        damaged_key = None
        if fault == "corrupt-source":
            with admin._conn() as conn:
                damaged_key = conn.execute("SELECT object_key FROM product.suite_objects WHERE owner_scope='official' AND digest=%s", (resolved.object_digests["checker.py"],)).fetchone()[0]
            original = v4_objects.get(damaged_key)
            v4_objects.delete(damaged_key)
            v4_objects.put_exclusive(damaged_key, b"damaged selected source", "application/octet-stream")
        try:
            process_claimed(worker, job, "u08-boundary")
        finally:
            if damaged_key:
                v4_objects.delete(damaged_key)
                v4_objects.put_exclusive(damaged_key, original, "application/octet-stream")
        loaded = client.get(f"/rounds/{round_id}")
        assert loaded.status_code == 200, loaded.text
        round = loaded.json()
        assert suites.read_snapshot(round_id, principal.tenant_id).to_dict() == frozen.to_dict()
        findings = {row["locator"]["rule_item_id"]: row for row in round["findings"]}
        assert all(row["locator"]["suite_revision_id"] == selected and row["locator"]["checker_digest"] == target.binding.checker_digest for row in round["findings"])
        if fault == "corrupt-source":
            assert round["execution"] == "failed" and not captured
            assert not any(row["status"] == "PASS" for row in round["findings"])
        else:
            assert round["execution"] == "completed", round
            assert len(captured) == 1
            snapshot, programs = captured[0]
            assert snapshot == frozen.to_dict() and programs == {checker_digest(program): program}
            if fault == "native":
                assert findings["U08:1"]["status"] == findings["U08:4"]["status"] == "PASS", {cid: (findings[cid]["status"], findings[cid].get("suggestions")) for cid in ("U08:1", "U08:4")}
            elif fault == "no-reader":
                assert findings["U08:6"]["status"] == "UNABLE"
            else:
                assert not any(row["status"] == "PASS" for row in round["findings"])
