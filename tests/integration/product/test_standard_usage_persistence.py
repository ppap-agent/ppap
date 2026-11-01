"""Standard provider receipts reach the current PG attempt without changing results.

Only object storage and provider HTTP responses are controlled. These tests do
not prove real model quality, provider billing, or automatic technical retries.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from io import BytesIO
from unittest.mock import patch

import httpx
import pytest
from api import create_app
from api import web_contract as wc
from audit.engine.checkers.client import AIClient
from audit.usage import get_current_request_id, get_token_tracker
from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
from product import build_product_store
from product.settings import Settings, get_settings
from pydantic import TypeAdapter
from worker import process_claimed


@pytest.fixture
def standard_usage_system(auth_store_factory, monkeypatch):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))}
    with patch.dict(os.environ, keep, clear=True):
        monkeypatch.setenv("LLM_API_KEY", "test-only")
        monkeypatch.setenv("LLM_MODEL", "usage-test")
        monkeypatch.setenv("LLM_BASE_URL", "https://provider.invalid/v1")
        monkeypatch.setenv("DEVELOPMENT", "false")
        monkeypatch.setattr(AIClient, "_instance", None)
        get_settings.cache_clear()
        settings = Settings(usage_charge_enabled=False)
        store = auth_store_factory(settings)
        store.seed_isolated_dev()
        worker = build_product_store(settings, objects=store.objects, role="worker")
        client = TestClient(create_app(settings, auth_store=store))
        try:
            assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
            yield client, worker
        finally:
            client.close()
            if AIClient._instance is not None:
                AIClient._instance.client.close()
            get_settings.cache_clear()


def upload(client, filename, content):
    response = client.post("/files", json={"original_filename": filename, "byte_size": len(content)})
    assert response.status_code == 201, response.text
    version = response.json()
    path = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.put(path + "/blob", content=content).status_code == 200
    ready = client.post(path + "/finalize")
    assert ready.status_code == 200, ready.text
    return ready.json()


def psw_bytes():
    document = Document()
    table = document.add_table(rows=6, cols=2)
    for row, (label, value) in enumerate(
        (("零件提交保证书", "PSW"), ("重量", "1.2"), ("供应商名称/供应商代码", "测试公司/S01"), ("提交原因", "☒ 首次提交"), ("认可情况", "☒ 批准"), ("经授权的供方代表签字", "代表"))
    ):
        table.cell(row, 0).text, table.cell(row, 1).text = label, value
    image_bytes = BytesIO()
    with Image.new("RGB", (8, 8), "red") as image:
        image.save(image_bytes, format="PNG")
    image_bytes.seek(0)
    table.cell(5, 1).paragraphs[0].add_run().add_picture(image_bytes)
    content = BytesIO()
    document.save(content)
    return content.getvalue()


def publish_and_accept(client, worker, content):
    body = {
        "kind": "engine",
        "type": "零件提交保证书",
        "rule_profile": "v3",
        "customers": [],
        "models": [],
        "parts": [],
        "rules": [
            {"id": str(index), "category": f"规则 {index}", "priority": "P0", "rule_to_check": f"核对第 {index} 项", "type_of_rule": "image" if index in {3, 5} else "table"} for index in range(1, 11)
        ],
    }
    source = upload(client, "已确认规则.json", json.dumps(body, ensure_ascii=False).encode())
    parse_id = str(uuid.uuid4())
    queued = client.post("/rules/parse", json={"request_id": parse_id, "source_file_id": source["file_id"], "source_file_version_id": source["id"]})
    assert queued.status_code == 202, queued.text
    parse_job = worker.claim_job("usage-rule-parse")
    assert parse_job is not None and parse_job.id == parse_id and parse_job.kind == "rule_parse"
    process_claimed(worker, parse_job, "usage-rule-parse")
    parsed = client.get("/rules/parse/" + parse_id).json()
    assert parsed["status"] == "succeeded", parsed
    assert parsed["result"]["body"] == body
    published = client.post("/rules", json=parsed["result"])
    assert published.status_code == 201, published.text
    rule = published.json()
    assert rule["source_file_version_id"] == source["id"]
    target = upload(client, "零件提交保证书.docx", content)
    draft = client.post("/drafts").json()["id"]
    saved = client.put(f"/drafts/{draft}", json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": rule["id"]}]})
    assert saved.status_code == 200, saved.text
    key = "standard-usage-" + str(uuid.uuid4())
    accepted = client.post(f"/drafts/{draft}/accept", json={"idempotency_key": key})
    assert accepted.status_code == 201, accepted.text
    frozen = accepted.json()
    assert frozen["analysis_mode"] == "standard"
    assert len(frozen["inputs"]) == 1
    assert frozen["inputs"][0]["file_version_id"] == target["id"]
    assert frozen["inputs"][0]["rule_version_id"] == rule["id"]
    assert frozen["inputs"][0]["rule"]["body"] == body
    replay = client.post(f"/drafts/{draft}/accept", json={"idempotency_key": key})
    assert replay.status_code == 201 and replay.json() == frozen
    return target, frozen


@pytest.mark.parametrize("receipts_known", [(True, True), (True, False), (False, False)], ids=["known", "mixed", "unreported"])
def test_standard_sdk_receipts_survive_worker_writeback_and_read_only_recovery(standard_usage_system, monkeypatch, receipts_known):
    client, worker = standard_usage_system
    provider_calls = []

    def provider(_transport, request):
        # TestClient uses its own ASGI transport. Only the real SDK's external
        # HTTP boundary reaches this substitute; unexpected external IO fails.
        assert request.url.host == "provider.invalid"
        assert request.url.path == "/v1/responses"
        index = len(provider_calls)
        assert index < len(receipts_known)
        provider_calls.append(request)
        facts = {"psw_stamp_ends_with_company": True, "authorized_signature_present": False, "psw_stamp_present": True}
        receipt = {"input_tokens": 9, "output_tokens": 5, "total_tokens": 14, "output_tokens_details": {"reasoning_tokens": 2}} if receipts_known[index] else None
        return httpx.Response(
            200,
            request=request,
            json={
                "id": f"response-{index}",
                "object": "response",
                "created_at": 0,
                "model": "usage-test",
                "status": "completed",
                "output": [
                    {"type": "message", "id": f"message-{index}", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": json.dumps(facts), "annotations": []}]}
                ],
                "usage": receipt,
            },
        )

    async def provider_async(transport, request):
        return provider(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", provider)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", provider_async)
    content = psw_bytes()
    target, frozen = publish_and_accept(client, worker, content)
    assert provider_calls == []
    claimed = worker.claim_job("standard-usage-worker")
    assert claimed is not None and claimed.kind == "audit" and claimed.round_id == frozen["id"]
    previous_scope = get_current_request_id()
    result = process_claimed(worker, claimed, "standard-usage-worker")
    assert len(provider_calls) == 2
    assert get_current_request_id() == previous_scope

    with worker._conn() as conn:
        persisted = conn.execute(
            """SELECT a.outcome, a.diagnostic, j.status, j.attempt
               FROM engine.attempts a JOIN engine.jobs j ON j.id = a.job_id
               WHERE a.job_id = %s AND a.attempt = %s""",
            (claimed.id, claimed.attempt),
        ).fetchone()
    assert persisted is not None
    assert (persisted[0], persisted[2], persisted[3]) == ("succeeded", "succeeded", claimed.attempt)
    usage = persisted[1]["usage"]
    assert usage == result["usage"]
    assert get_token_tracker().get_request_summary(usage["request_id"]) is None
    known_count = sum(receipts_known)
    assert usage["llm_calls_count"] == 2
    assert usage["unreported_calls_count"] == 2 - known_count
    assert usage["usage_complete"] is all(receipts_known)
    assert len(usage["llm_calls"]) == known_count
    for call in usage["llm_calls"]:
        assert (call["input_tokens"], call["output_tokens"], call["thinking_tokens"], call["total_tokens"]) == (9, 5, 2, 14)
        assert call["operation"] == "image_analysis"
        assert call["model_name"] == "usage-test"
    totals = tuple(usage[key] for key in ("total_input_tokens", "total_output_tokens", "total_thinking_tokens", "total_tokens"))
    assert totals == ((18, 10, 4, 28) if all(receipts_known) else (None, None, None, None))
    assert "test-only" not in json.dumps(usage)
    assert "psw_stamp_ends_with_company" not in json.dumps(usage)

    path = "/rounds/" + frozen["id"]
    response = client.get(path, params={"read_view": "true"})
    assert response.status_code == 200, response.text
    loaded = response.json()
    TypeAdapter(wc.Round).validate_python(loaded)
    assert loaded["execution"] == "completed" and loaded["analysis_mode"] == "standard"
    assert loaded["inputs"] == frozen["inputs"]
    assert loaded["result_attempt"] == claimed.attempt
    statuses = {row["locator"]["rule_item_id"]: row["status"] for row in loaded["findings"]}
    assert statuses["3"] == "PASS" and statuses["5"] == "NG"
    assert len(statuses) == 10
    files = loaded["read_view"]["files"]
    assert len(files) == 1 and files[0]["file"]["id"] == target["id"]
    assert files[0]["file"]["sha256"] == hashlib.sha256(content).hexdigest()
    original = client.get(f"{path}/files/{target['file_id']}/versions/{target['id']}/download")
    assert original.status_code == 200 and original.content == content
    for _ in range(2):
        assert client.get(path, params={"read_view": "true"}).json() == loaded
    assert [row["id"] for row in client.get("/rounds").json()["rounds"]] == [frozen["id"]]
    assert worker.claim_job("standard-usage-readback") is None
    assert len(provider_calls) == 2
    with worker._conn() as conn:
        after = conn.execute("SELECT diagnostic FROM engine.attempts WHERE job_id = %s AND attempt = %s", (claimed.id, claimed.attempt)).fetchone()
        assert after == (persisted[1],)
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind = 'audit' AND audit_round_id = %s", (frozen["id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM engine.attempts WHERE job_id = %s", (claimed.id,)).fetchone()[0] == 1
