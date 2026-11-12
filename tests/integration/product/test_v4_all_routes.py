"""Carrier/routing acceptance for all 41 sources, not engineering certification.

Incomplete originals must keep actual NG/UNABLE results. These tests prove the
API/Worker/frozen-source path, not the quality of real supplier documents.
"""

import io
import re
import uuid

import pytest
from api import create_app
from audit.presets import customer_presets, preset_document_type
from documents.ai import AIResult
from fastapi.testclient import TestClient
from openpyxl import Workbook
from PIL import Image
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from worker import process_claimed


class SourceIdentity:
    def __call__(self, operation, *, prompt, **kwargs):
        if operation == "document_metadata_extraction":
            return AIResult({"part_number": "P-1"})
        if operation == "document_classification":
            match = re.search(r"\b([ULN]\d{2})\b", prompt)
            assert match, "The original must identify the controlled routing fixture"
            return AIResult({"document_type": next(preset_document_type(source) for source in customer_presets() if source.entry_id == match[1])})
        raise AssertionError("No external model operation is authorized by this routing test")


def content(source):
    if source.entry_id == "U13":
        stream = io.BytesIO()
        Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
        return stream.getvalue(), ".png"
    book = Workbook()
    book.active.title = {"N01": "CoverSheet", "N02": "PFMEA", "N03": "控制计划", "N04": "特殊特性清单", "N05": "过程流程图"}.get(source.entry_id, "报告")
    for row in [[source.entry_id, source.title], ["客户", source.customer], ["零件号", "P-1"], ["版本", "A"]]:
        book.active.append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue(), ".xlsx"


@pytest.mark.parametrize(("customer", "expected_count"), [("UAES", 19), ("理想", 17), ("蔚来", 17)])
def test_each_source_accepts_and_freezes_its_actual_customer_checker(auth_store_factory, v4_objects, core_system, tmp_path, customer, expected_count):
    settings = Settings(usage_charge_enabled=False, feishu_app_id="v4-route-acceptance", git_sha="a" * 40)
    admin = auth_store_factory(settings)
    admin.objects = v4_objects
    principal = admin.upsert_feishu_identity("routes-" + uuid.uuid4().hex, "owner")
    api = build_product_store(settings, objects=admin.objects, role="api")
    worker = build_product_store(settings, objects=admin.objects, role="worker")
    sources = [source for source in customer_presets() if source.customer == customer]
    assert len(sources) == expected_count
    core_system.adopt_standard(admin, principal, *(f"{source.customer}:{source.entry_id}" for source in sources))
    core_system.install(admin)
    originals = {}
    with TestClient(create_app(settings, auth_store=api)) as client:
        client.cookies.set(SESSION_COOKIE, admin.create_session(principal))
        files = []
        for source in sources:
            data, suffix = content(source)
            filename = source.entry_id + " " + source.title + suffix
            uploaded = client.post("/files", json={"original_filename": filename, "byte_size": len(data)})
            assert uploaded.status_code == 201, uploaded.text
            file = uploaded.json()
            path = f"/files/{file['file_id']}/versions/{file['id']}"
            assert client.put(path + "/blob", content=data).status_code == 200
            assert client.post(path + "/finalize").status_code == 200
            request = {"request_id": str(uuid.uuid4()), "source_file_id": file["file_id"], "source_file_version_id": file["id"]}
            if source.entry_id == "U13":
                request["scope"] = {"customer_preference": "UAES", "preset_key": "U13"}
            assert client.post("/file-validations", json=request).status_code == 202
            claimed = worker.claim_job("routes-validation")
            assert claimed.kind == "file_validation"
            process_claimed(worker, claimed, "routes-validation")
            files.append({"file_version_id": file["id"], "validation_id": request["request_id"], "selection": "selected", "removal_reason": None})
            originals[file["id"]] = (source, path, data)
        accepted = client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "customer_preference": customer, "files": files})
        assert accepted.status_code == 202, accepted.text
        path = "/audit-tasks/" + accepted.json()["id"]
        for _ in sources:
            claimed = worker.claim_job("routes-recognition")
            assert claimed.kind == "file_analysis"
            process_claimed(worker, claimed, "routes-recognition", rule_ai=SourceIdentity())
        task = client.get(path).json()
        for row in task["files"]:
            source = originals[row["file"]["id"]][0]
            assert row["match"]["state"] == "matched", (source.entry_id, row["match"])
            assert row["match"]["rule"]["body"]["preset_key"] == source.entry_id
        assert task["estimated_points"] == 500 * expected_count
        confirmed = client.post(
            path + "/confirm", json={"request_id": str(uuid.uuid4()), "expected_revision": 0, "expected_confirmation_basis_digest": client.get(path).json()["confirmation_basis_digest"]}
        )
        assert confirmed.status_code == 201, confirmed.text
        job = worker.claim_job("routes-audit")
        assert job.kind == "audit"
        guest_calls = core_system.calls
        process_claimed(worker, job, "routes-audit")
        assert core_system.calls == guest_calls + 1
        assert [job.allow_model_calls for job in core_system.audit_jobs] == [False]
        assert not list(tmp_path.rglob("model.json")), "No model grant is authorized by the routing fixture"
        round_path = "/rounds/" + confirmed.json()["round_id"]
        round = client.get(round_path).json()
        assert round["findings"], round
        with admin._conn() as conn:
            frozen = conn.execute("SELECT rule_execution FROM product.round_inputs WHERE round_id = %s AND role = 'target'", (round["id"],)).fetchall()
        assert len(frozen) == expected_count
        assert all(row[0]["mode"] == "checker_core" and row[0]["suite_revision_id"] for row in frozen), frozen
        assert all(row[0]["binding"]["entrypoints"] and row[0]["verification"] == "unverified" for row in frozen), frozen
        assert all(finding["status"] != "ERR" for finding in round["findings"]), round["findings"]
        if customer == "UAES":
            photo = next(identity for identity, (source, _, _) in originals.items() if source.entry_id == "U13")
            photo_findings = [finding for finding in round["findings"] if finding["locator"]["file_version_id"] == photo]
            assert photo_findings and all(finding["status"] == "UNABLE" for finding in photo_findings), photo_findings
        for _, original_path, data in originals.values():
            assert client.get(original_path + "/download").content == data
        with admin._conn() as conn:
            assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind = 'audit' AND audit_round_id = %s", (round["id"],)).fetchone()[0] == 1
