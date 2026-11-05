"""V3 POC oracle through the product API and worker, with an explicit rule binding."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from api import create_app
from audit.rule_profile import RULE_PROFILE_V3
from corpus_contracts.acceptance_report import load_acceptance_report, write_acceptance_report
from corpus_contracts.poc_oracle import POC_SEQ_TO_DOC_TYPE, build_bundle_map, load_poc_cases
from corpus_contracts.product_round import (
    MATCH,
    compare_file,
    engine_rule_body,
    failing_verdicts,
    format_verdicts,
)
from fastapi.testclient import TestClient
from product.settings import Settings, get_settings
from worker import process_claimed


def _worker_env():
    keep = {
        key: value
        for key, value in os.environ.items()
        if key in {"PGPORT", "PPAP_PG_DATABASE", "CI", "PATH", "LEGACY_DOC_CONVERTER_EXECUTABLE", "LEGACY_DOC_SANDBOX_EXECUTABLE"}
        or key.startswith(("LLM_", "OPENAI_", "MODEL_NAME", "OCR_", "PADDLE_"))
    }
    keep["PPAP_PROFILE"] = "isolated-dev"
    return patch.dict(os.environ, keep, clear=True)


def test_worker_environment_preserves_database_isolation(monkeypatch):
    monkeypatch.setenv("PGPORT", "32123")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("PATH", "/tmp/office-converter:/usr/bin")
    with _worker_env():
        assert os.environ.get("PGPORT") == "32123"
        assert os.environ.get("CI") == "true"
        assert os.environ.get("PATH") == "/tmp/office-converter:/usr/bin"


def _upload(client: TestClient, name: str, body: bytes) -> dict:
    created = client.post(
        "/files",
        json={"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)},
    )
    assert created.status_code == 201, created.text
    version = created.json()
    path = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.put(path + "/blob", content=body).status_code == 200
    assert client.post(path + "/finalize").status_code == 200
    return version


@pytest.mark.usefixtures("ocr_transport_diagnostics")
def test_v3_api_worker_matches_poc_oracle(auth_store_factory, tmp_path: Path) -> None:
    env = _worker_env()
    env.start()
    get_settings.cache_clear()
    client = None
    try:
        settings = Settings(usage_charge_enabled=False)
        store = auth_store_factory(settings)
        store.seed_isolated_dev()
        client = TestClient(create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, store))
        assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
        rows: list[dict] = []
        failures = []
        for bundle_id, cases in sorted(build_bundle_map(load_poc_cases()).items()):
            items = []
            versions: dict[str, str] = {}
            for case in cases:
                doc_type = POC_SEQ_TO_DOC_TYPE[case.file_seq].get_type_name()
                version = _upload(client, case.file_name, case.file_path.read_bytes())
                versions[case.file_name] = version["id"]
                body, _execution = engine_rule_body(doc_type, case.file_name, RULE_PROFILE_V3)
                published = client.post(
                    "/rules",
                    json={"title": f"{doc_type} {case.file_name}"[:200], "body": body, "source_file_version_id": version["id"]},
                )
                assert published.status_code == 201, published.text
                items.append(
                    {
                        "file_version_id": version["id"],
                        "role": "target",
                        "rule_version_id": published.json()["id"],
                    }
                )
            draft = client.post("/drafts")
            assert draft.status_code == 201, draft.text
            draft_id = draft.json()["id"]
            saved = client.put(f"/drafts/{draft_id}", json={"items": items})
            assert saved.status_code == 200, saved.text
            accepted = client.post(
                f"/drafts/{draft_id}/accept",
                json={"idempotency_key": f"v3-oracle-{bundle_id}"},
            )
            assert accepted.status_code == 201, accepted.text
            round_id = accepted.json()["id"]
            claimed = store.claim_job("v3-oracle-worker")
            assert claimed is not None
            assert claimed.round_id == round_id
            process_claimed(store, claimed, "v3-oracle-worker")
            result = client.get(f"/rounds/{round_id}")
            assert result.status_code == 200, result.text
            payload = result.json()
            suggestions = {row["item_key"]: row["suggestions"] for row in payload.get("findings", [])}
            for case in cases:
                expected = {index: item.status for index, item in case.expected.items()}
                verdicts = compare_file(
                    file_name=case.file_name,
                    version_id=versions[case.file_name],
                    expected=expected,
                    result=payload,
                    setup_error=None,
                )
                failures.extend(failing_verdicts(verdicts))
                rows.extend(
                    {
                        "file_name": item.file_name,
                        "rule_index": item.rule_index,
                        "expected_status": item.expected,
                        "actual_status": item.actual or "",
                        "kind": item.kind,
                        "bundle": bundle_id,
                        "file_seq": case.file_seq,
                        "rule_desc": case.expected[item.rule_index].rule_desc,
                        "manual_reason": case.expected[item.rule_index].manual_reason,
                        "agent_suggestions": suggestions.get(f"{versions[case.file_name]}:rule:{item.rule_index}", ""),
                        "failure_stage": "audit" if item.kind == "execution_failure" else "comparison" if item.kind != MATCH else "",
                        "round_id": round_id,
                        "job_id": claimed.id,
                    }
                    for item in verdicts
                )
        report = tmp_path / "v3_worker_oracle.json"
        write_acceptance_report(report, rows, markdown_path=report.with_suffix(".md"), run={"boundary": "Product API / Worker integration; controlled object storage"})
        loaded = load_acceptance_report(report)
        assert len(loaded) == len(rows)
        assert rows, "POC oracle produced no rule rows"
        summary = json.loads(report.read_text(encoding="utf-8"))["summary"]
        assert summary["selected_cases"] == 26
        assert summary["total_rules"] == 209
        assert report.with_suffix(".md").is_file()
        if failures:
            pytest.fail(format_verdicts(failures))
        assert all(row["kind"] == MATCH for row in rows)
    finally:
        if client is not None:
            client.close()
        get_settings.cache_clear()
        env.stop()
