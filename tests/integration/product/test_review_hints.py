"""Positive PostgreSQL/API journey for deterministic advisory hints."""

import uuid
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from api import create_app
from fastapi.testclient import TestClient
from openpyxl import Workbook
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from psycopg.types.json import Jsonb

from .test_checker_admission import Admission


def finish(store, round_id, findings):
    with store._conn() as conn:
        conn.execute("UPDATE product.audit_rounds SET execution='completed',result_attempt=1 WHERE id=%s", (round_id,))
        for version, status in findings:
            conn.execute(
                """INSERT INTO product.findings(id,round_id,item_key,classification,item,status,locator)
                VALUES(%s,%s,%s,'Observed result','Requirement',%s,%s)""",
                (str(uuid.uuid4()), round_id, f"{version}:rule:R1", status, Jsonb({"file_version_id": version, "rule_item_id": "R1"})),
            )


def client_for(a):
    api = build_product_store(a.store.settings, objects=a.store.objects, role="api")
    client = TestClient(create_app(a.store.settings, auth_store=api))
    client.cookies.set(SESSION_COOKIE, a.store.create_session(a.alice))
    return client


def test_same_content_hint_response_survives_reconnect_without_changing_results(auth_store_factory):
    a = Admission(auth_store_factory(Settings(feishu_app_id="review-hints-tests", usage_charge_enabled=False, git_sha="a" * 40)))
    # init-db replays migrations on every startup; repeat the new migration.
    with a.store._conn() as conn:
        conn.execute((Path(__file__).resolve().parents[3] / "migrations/0056_review_hint_actions.sql").read_text())
    a.verify()
    first = a.accept()
    finish(a.store, first["id"], [(a.target.id, "PASS")])
    second = a.accept()
    finish(a.store, second["id"], [(a.target.id, "NG")])
    with client_for(a) as client:
        response = client.get(f"/rounds/{second['id']}?read_view=true")
        assert response.status_code == 200, response.text
        view = response.json()
        hint = view["findings"][0]["review_hint"]
        assert hint["compared_round_id"] == first["id"]
        assert hint["suite_revision_id"] == a.official.id
        before = next(row["audit"] for row in view["read_view"]["files"] if row["role"] == "target")
        saved = client.put(f"/rounds/{second['id']}/review-hints", json={"item_key": hint["item_key"], "action": "ignored", "hint": hint})
        assert saved.status_code == 200, saved.text
        assert saved.json()["action"] == "ignored"
    with client_for(a) as reopened:
        view = reopened.get(f"/rounds/{second['id']}?read_view=true").json()
        assert view["findings"][0].get("review_hint") is None
        after = next(row["audit"] for row in view["read_view"]["files"] if row["role"] == "target")
        assert {key: after[key] for key in ("counts", "conclusion", "report")} == {key: before[key] for key in ("counts", "conclusion", "report")}
        assert view["findings"][0]["status"] == "NG" and view["findings"][0]["review"] is None
        assert reopened.get("/review-hints/responses").json() == {"accepted": 0, "ignored": 1, "responded": 1, "acceptance_ratio": 0.0, "ignore_ratio": 1.0}


def test_explicit_actual_pdf_excel_pair_produces_cross_format_hint(auth_store_factory):
    a = Admission(auth_store_factory(Settings(feishu_app_id="review-hints-pair", usage_charge_enabled=False, git_sha="a" * 40)))
    a.verify()
    with fitz.open() as document:
        document.new_page().insert_text((50, 50), "Same business document")
        pdf = a.upload("control.pdf", document.tobytes())
    workbook = Workbook()
    workbook.active["A1"] = "Same business document"
    data = BytesIO()
    workbook.save(data)
    workbook.close()
    excel = a.upload("control.xlsx", data.getvalue())
    a.items = [{"file_version_id": source.id, "role": "target", "rule_version_id": a.rule_id} for source in (pdf, excel)]
    round = a.accept()
    finish(a.store, round["id"], [(pdf.id, "NG"), (excel.id, "PASS")])
    with client_for(a) as client:
        pair = {"pdf_file_version_id": pdf.id, "excel_file_version_id": excel.id}
        response = client.put(f"/rounds/{round['id']}/review-document-pairs", json=pair)
        assert response.status_code == 200, response.text
        assert response.json()["pairs"] == [pair]
        hints = [row["review_hint"] for row in client.get(f"/rounds/{round['id']}").json()["findings"]]
        assert len(hints) == 2
        assert all(hint["reason"] == "cross_format_differs" for hint in hints)
        saved = client.put(f"/rounds/{round['id']}/review-hints", json={"item_key": hints[0]["item_key"], "action": "accepted", "hint": hints[0]})
        assert saved.status_code == 200, saved.text
        assert client.get("/review-hints/responses").json()["accepted"] == 1


@pytest.mark.parametrize("scope", ["other_tenant", "original_access_revoked"])
def test_response_statistics_and_comparisons_do_not_reveal_inaccessible_originals(auth_store_factory, scope):
    a = Admission(auth_store_factory(Settings(feishu_app_id="review-hints-private", usage_charge_enabled=False, git_sha="a" * 40)))
    a.verify()
    first, second = a.accept(), a.accept()
    finish(a.store, first["id"], [(a.target.id, "PASS")])
    finish(a.store, second["id"], [(a.target.id, "NG")])
    with client_for(a) as client:
        hint = client.get(f"/rounds/{second['id']}").json()["findings"][0]["review_hint"]
        response = client.put(f"/rounds/{second['id']}/review-hints", json={"item_key": hint["item_key"], "action": "ignored", "hint": hint})
        assert response.status_code == 200
        assert client.get("/review-hints/responses").json()["responded"] == 1
        before = a.counts()
        if scope == "other_tenant":
            outsider = a.store.upsert_feishu_identity("unrelated-tenant", "outsider", "Outsider")
            client.cookies.set(SESSION_COOKIE, a.store.create_session(outsider))
        else:
            with a.store._conn() as conn:
                conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (a.target.file_id, a.alice.user_id))
        assert client.get("/review-hints/responses").json() == {"accepted": 0, "ignored": 0, "responded": 0, "acceptance_ratio": None, "ignore_ratio": None}
        readback = client.get(f"/rounds/{second['id']}")
        if scope == "other_tenant":
            assert readback.status_code in {403, 404}
        else:
            # These historical tenant-readable rounds retain their existing
            # read policy. Revoked original access must remove the comparison.
            assert readback.status_code == 200
            assert all(not finding.get("review_hint") for finding in readback.json()["findings"])
        rejected = client.put(f"/rounds/{second['id']}/review-hints", json={"item_key": hint["item_key"], "action": "ignored", "hint": hint})
        assert rejected.status_code in {403, 404}
        assert a.counts() == before
    with a.store._conn() as conn:
        rows = conn.execute("SELECT round_id,status FROM product.findings WHERE round_id IN (%s,%s) ORDER BY status", (first["id"], second["id"])).fetchall()
    assert {(str(identity), status) for identity, status in rows} == {(first["id"], "PASS"), (second["id"], "NG")}
