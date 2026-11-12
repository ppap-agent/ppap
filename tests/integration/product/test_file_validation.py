"""P3 checks use saved source bytes, not upload status or filename guesses."""

import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from api import create_app
from fastapi.testclient import TestClient
from openpyxl import Workbook
from product import build_product_store
from product.settings import Settings
from worker import process_claimed


@pytest.fixture
def validation_system(auth_store_factory):
    settings = Settings()
    store = auth_store_factory(settings)
    store.seed_isolated_dev()
    worker = build_product_store(settings, objects=store.objects, role="worker")
    client = TestClient(create_app(settings, auth_store=store))
    assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
    yield client, worker
    client.close()


def upload(client, filename, content):
    version = client.post("/files", json={"original_filename": filename, "byte_size": len(content)}).json()
    path = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.put(path + "/blob", content=content).status_code == 200
    assert client.post(path + "/finalize").status_code == 200
    return version, path


def validation_request(version, request_id=None):
    return {"request_id": request_id or str(uuid.uuid4()), "source_file_id": version["file_id"], "source_file_version_id": version["id"]}


def test_file_validation_is_recoverable_without_creating_an_audit(validation_system):
    client, worker = validation_system
    output = io.BytesIO()
    book = Workbook()
    book.active["A1"] = "=1+1"
    book.save(output)
    book.close()
    content = output.getvalue()
    version, path = upload(client, "report.xlsx", content)
    request_id = str(uuid.uuid4())
    body = validation_request(version, request_id)
    accepted = client.post("/file-validations", json=body)
    assert accepted.status_code == 202
    assert accepted.json()["id"] == request_id
    assert accepted.json()["status"] == "queued"
    assert client.post("/file-validations", json=body).json() == accepted.json()
    claimed = worker.claim_job("validation-worker")
    assert claimed.kind == "file_validation"
    with patch("documents.ai.run_json", side_effect=AssertionError("file_validation_must_not_call_model")):
        process_claimed(worker, claimed, "validation-worker")
    recovered = client.get(f"/file-validations/{request_id}")
    assert recovered.status_code == 200
    assert recovered.json()["source_file_version_id"] == version["id"]
    assert recovered.json()["result"] == {"state": "valid", "reasons": []}
    assert recovered.json()["status"] == "succeeded"
    assert client.get(path + "/validation").json() == {"validation": recovered.json()}
    assert client.get("/drafts").json()["drafts"] == []
    assert client.get("/rounds").json()["rounds"] == []
    assert client.get("/usage").json()["events"] == []


def test_saved_but_damaged_file_is_not_a_valid_input(validation_system):
    client, worker = validation_system
    version, path = upload(client, "report.xlsx", b"not-an-excel-workbook")
    assert client.get(path).json()["status"] == "ready"
    assert client.get(path + "/validation").json() == {"validation": None}
    body = validation_request(version)
    assert client.post("/file-validations", json=body).status_code == 202
    claimed = worker.claim_job("validation-worker")
    process_claimed(worker, claimed, "validation-worker")
    result = client.get(f"/file-validations/{body['request_id']}").json()
    assert result["status"] == "succeeded"
    assert result["result"] == {"state": "invalid", "reasons": ["文件加密或异常，无法识别"]}
    assert client.get(path).json()["status"] == "ready"
    assert client.get(path + "/download").content == b"not-an-excel-workbook"


def test_duplicate_requests_share_one_job_and_cannot_replace_the_source(validation_system):
    client, worker = validation_system
    version, _ = upload(client, "first.xlsx", b"first")
    other, _ = upload(client, "second.xlsx", b"second")
    body = validation_request(version)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/file-validations", json=body), range(2)))
    assert [response.status_code for response in responses] == [202, 202]
    assert [response.json()["id"] for response in responses] == [body["request_id"], body["request_id"]]
    conflict = client.post("/file-validations", json=validation_request(other, body["request_id"]))
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "idempotency_conflict"}
    assert worker.claim_job("first-worker").id == body["request_id"]
    assert worker.claim_job("second-worker") is None


def test_unknown_check_is_not_automatically_restarted_and_only_owner_can_complete(validation_system):
    client, worker = validation_system
    version, _ = upload(client, "report.xlsx", b"damaged workbook")
    body = validation_request(version)
    assert client.post("/file-validations", json=body).status_code == 202
    job = worker.claim_job("original-worker")
    # Inject a lost heartbeat at the database boundary, not a second acceptance.
    with worker._conn() as conn:
        conn.execute("UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s", (job.id,))
    path = f"/file-validations/{job.id}"
    assert client.get(path).json()["status"] == "unknown"
    assert client.post("/file-validations", json=body).json()["status"] == "unknown"
    assert worker.claim_job("replacement-worker") is None
    assert not worker.complete_job(job, "replacement-worker", {"state": "valid", "reasons": []})
    assert client.get(path).json()["status"] == "unknown"
    process_claimed(worker, job, "original-worker")
    confirmed = client.get(path).json()
    assert confirmed["status"] == "succeeded"
    assert confirmed["result"]["state"] == "invalid"
    assert not worker.complete_job(job, "original-worker", {"state": "valid", "reasons": []})
    assert client.get(path).json() == confirmed


def test_file_validation_requires_ready_source_and_current_file_access(validation_system):
    client, _ = validation_system
    version = client.post("/files", json={"original_filename": "report.xlsx", "byte_size": 1}).json()
    body = validation_request(version)
    assert client.post("/file-validations", json=body).status_code == 409
    ready, path = upload(client, "ready.xlsx", b"content")
    body = validation_request(ready)
    assert client.post("/file-validations", json=body).status_code == 202
    for subject in ("bob", "carol"):
        client.cookies.clear()
        assert client.post("/isolated-dev/sessions", json={"subject": subject}).status_code == 200
        assert client.post("/file-validations", json=body).status_code == 403
        assert client.get(f"/file-validations/{body['request_id']}").status_code == 403
        assert client.get(path + "/validation").status_code == 403
    client.cookies.clear()
    assert client.post("/file-validations", json=body).status_code == 401
    assert client.get(f"/file-validations/{body['request_id']}").status_code == 401
