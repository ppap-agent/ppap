"""Product audit round/review loop. Does not copy checker corpus into E2E."""

from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

import pytest
from api import create_app
from fastapi.testclient import TestClient
from product import build_product_store
from product.fixtures import RULE_A, USER_ALICE
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore
from psycopg import errors, sql
from worker import process_claimed


def rule_body():
    return {
        "kind": "engine",
        "rule_profile": "v3",
        "type": "控制计划",
        "customers": ["理想"],
        "models": ["M1"],
        "parts": ["P1"],
        "rules": [{"id": "1", "category": "基本信息", "priority": "P0", "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
    }


def text_pdf(text: str) -> bytes:
    import fitz

    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    try:
        return document.tobytes()
    finally:
        document.close()


def text_docx(text: str) -> bytes:
    from io import BytesIO

    from docx import Document

    document, stream = Document(), BytesIO()
    document.add_paragraph(text)
    document.save(stream)
    return stream.getvalue()


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("PPAP_", "FEISHU_"))}
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class AuditFlowTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory, core_system, audit_round):
        self.auth_store = auth_store_factory
        self.core = core_system
        self.audit_round = audit_round

    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings(usage_charge_enabled=False)
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.core.adopt_presence(self.store)
        self.core.install(self.store)
        self.originals = {}
        self.client = TestClient(create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store))

    def tearDown(self):
        self._env.stop()

    def _scalar(self, query: str, params=()):
        with self.store._conn() as conn:
            return conn.execute(query, params).fetchone()[0]

    def _execute(self, query: str, params=()) -> None:
        with self.store._conn() as conn:
            conn.execute(query, params)

    def _login(self, subject: str) -> None:
        self.client.cookies.clear()
        response = self.client.post("/isolated-dev/sessions", json={"subject": subject})
        self.assertEqual(response.status_code, 200)

    def _principal(self):
        return self.store.load_session(self.client.cookies.get(SESSION_COOKIE)).principal

    def _rounds(self, **query):
        return self.store.list_rounds(self._principal(), **query)

    def _usage(self):
        return self.store.list_usage_events(self._principal())

    def _upload(self, name: str, body: bytes, file_id: str | None = None) -> dict:
        if name.endswith(".xlsx"):
            body = self.core.workbook(body.decode())
        meta = {"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)}
        created = self.client.post(f"/files/{file_id}/versions", json=meta) if file_id else self.client.post("/files", json=meta)
        self.assertEqual(created.status_code, 201)
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.assertEqual(self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=body).status_code, 200)
        ready = self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        self.assertEqual(ready.status_code, 200)
        self.originals[vid] = body
        return ready.json()

    def _accept(self, files: dict, **options) -> dict:
        return self.audit_round(self.store, self._principal(), files, **options)

    def _finish_worker(self) -> None:
        """Run queued work up to and including the next audit; a finished task's report is queued before it."""
        while True:
            claimed = self.store.claim_job("test-worker")
            self.assertIsNotNone(claimed)
            process_claimed(self.store, claimed, "test-worker")
            if claimed.kind == "audit":
                return

    def test_worker_writeback(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        reference = self._upload("context.pdf", text_pdf("reference-bytes"))
        round_id = self._accept({target["id"]: RULE_A, reference["id"]: None})["id"]
        self._finish_worker()
        body = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(body["execution"], "completed")
        statuses = {row["status"] for row in body["findings"]}
        self.assertEqual(statuses, {"PASS"}, body["findings"])
        self.assertFalse(body["coverage"]["locator_gap"])
        self.assertTrue(any(row["locator"] and row["locator"]["kind"] == "file_version" for row in body["findings"]))
        original = body["findings"][0]
        download = self.client.get(f"/rounds/{round_id}/files/{original['locator']['file_id']}/versions/{original['locator']['file_version_id']}/download")
        self.assertIn(download.content, {self.originals[target["id"]], self.originals[reference["id"]]})

    def test_standard_result_writeback_failure_does_not_fabricate_engine_results_or_new_audits(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        frozen = self._accept({target["id"]: RULE_A})
        usage = self._usage()
        worker = build_product_store(self.settings, objects=self.store.objects, role="worker")
        job = worker.claim_job("standard-writeback")
        self.assertIsNotNone(job)
        self.assertEqual((job.kind, job.round_id, job.attempt), ("audit", frozen["id"], 1))

        suffix = uuid.uuid4().hex
        sequence = sql.Identifier("engine", "test_write_sequence_" + suffix)
        function = sql.Identifier("engine", "test_write_failure_" + suffix)
        trigger = sql.Identifier("test_write_failure_" + suffix)
        try:
            with self.store._conn() as conn:
                # This fault belongs to the isolated test DB, not a store mock.
                # nextval survives the rejected transaction, so a second write
                # succeeds and exposes a fabricated engine_error on current code.
                conn.execute(sql.SQL("CREATE SEQUENCE {}").format(sequence))
                conn.execute(
                    sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS {}").format(
                        function,
                        sql.Literal("BEGIN IF nextval(TG_ARGV[0]::regclass) = 1 THEN RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'test-only audit writeback failure'; END IF; RETURN NEW; END;"),
                    )
                )
                conn.execute(
                    sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON product.findings FOR EACH ROW WHEN (NEW.round_id = {}::uuid) EXECUTE FUNCTION {}({})").format(
                        trigger, sql.Literal(frozen["id"]), function, sql.Literal("engine.test_write_sequence_" + suffix)
                    )
                )
            write_error = None
            try:
                process_claimed(worker, job, "standard-writeback")
            except errors.SerializationFailure as error:
                write_error = error
            loaded = self.client.get(f"/rounds/{frozen['id']}").json()
            writes = self._scalar(sql.SQL("SELECT last_value FROM {}").format(sequence))
            self.assertEqual(loaded["inputs"], frozen["inputs"])
            self.assertIsNone(loaded["failure"])
            if write_error is not None:
                self.assertEqual(write_error.sqlstate, "40001")
                self.assertEqual(loaded["execution"], "running")
                self.assertEqual(loaded["findings"], [])
                self.assertEqual(loaded["coverage"], {})
                self.assertIsNone(loaded["result_attempt"])
            else:
                # A safe retry of the same known result is also valid. It must
                # not replace the actual presence PASS with an engine error.
                self.assertEqual(loaded["execution"], "completed")
                self.assertEqual([row["status"] for row in loaded["findings"]], ["PASS"], f"write attempts={writes}")
                self.assertEqual(loaded["result_attempt"], job.attempt)
            for _ in range(2):
                self.assertEqual(self.client.get(f"/rounds/{frozen['id']}").json(), loaded)
            self.assertIsNone(worker.claim_job("no-writeback-reexecution"))
            self.assertEqual([row["id"] for row in self._rounds()], [frozen["id"]])
            self.assertEqual(self._usage(), usage)
        finally:
            with self.store._conn() as conn:
                conn.execute(sql.SQL("DROP TRIGGER IF EXISTS {} ON product.findings").format(trigger))
                conn.execute(sql.SQL("DROP FUNCTION IF EXISTS {}()").format(function))
                conn.execute(sql.SQL("DROP SEQUENCE IF EXISTS {}").format(sequence))

    def test_cross_tenant_and_creator_only_review(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"secret-bytes")
        round_id = self._accept({target["id"]: RULE_A})["id"]
        self._finish_worker()
        self._login("carol")
        denied = self.client.get(f"/rounds/{round_id}")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json(), {"error": "access_denied"})
        self.assertNotIn(b"secret-bytes", denied.content)
        self._login("bob")
        visible = self.client.get(f"/rounds/{round_id}")
        self.assertEqual(visible.status_code, 200)
        finding = visible.json()["findings"][0]
        original = self.client.get(f"/rounds/{round_id}/files/{finding['locator']['file_id']}/versions/{finding['locator']['file_version_id']}/download")
        self.assertEqual(original.status_code, 200)
        self.assertEqual(original.content, self.originals[target["id"]])
        review = {"item_key": finding["item_key"], "judgment": "同意", "rationale": "x", "next_step": "y", "version": 0}
        # Another member of the enterprise reads the round and its originals, but only its creator reviews it.
        self.assertEqual(self.client.put(f"/rounds/{round_id}/reviews", json=review).status_code, 403)
        self.assertEqual(self._scalar("SELECT count(*) FROM product.reviews WHERE round_id = %s", (round_id,)), 0)
        self._login("alice")
        reviewed = self.client.put(f"/rounds/{round_id}/reviews", json=review)
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.json()["findings"][0]["review"]["judgment"], "同意")
        self.assertEqual(
            str(
                self._scalar(
                    "SELECT user_id FROM product.reviews WHERE round_id = %s AND item_key = %s",
                    (round_id, finding["item_key"]),
                )
            ),
            USER_ALICE,
        )

    def test_review_does_not_mutate_finding_and_conflicts(self):
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        round_id = self._accept({target["id"]: RULE_A})["id"]
        self._finish_worker()
        finding = self.client.get(f"/rounds/{round_id}").json()["findings"][0]
        before_review = self._rounds()[0]
        self.assertEqual([row["id"] for row in self._rounds(unreviewed=True)], [round_id])
        machine = finding["status"]
        saved = self.client.put(
            f"/rounds/{round_id}/reviews",
            json={"item_key": finding["item_key"], "judgment": "需修改", "rationale": "字段不清", "next_step": "补传", "version": 0},
        )
        self.assertEqual(saved.status_code, 200)
        after_review = self._rounds()[0]
        self.assertEqual(after_review["created_at"], before_review["created_at"])
        self.assertGreater(after_review["updated_at"], before_review["updated_at"])
        self.assertEqual(self._rounds(unreviewed=True), [])
        self.assertEqual(len(self._rounds()), 1)
        again = saved.json()["findings"][0]
        self.assertEqual(again["status"], machine)
        self.assertEqual(again["review"]["judgment"], "需修改")
        conflict = self.client.put(
            f"/rounds/{round_id}/reviews",
            json={"item_key": finding["item_key"], "judgment": "覆盖", "rationale": "", "next_step": "", "version": 0},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"], "review_conflict")
        updated = self.client.put(
            f"/rounds/{round_id}/reviews",
            json={"item_key": finding["item_key"], "judgment": "通过", "rationale": "已核对", "next_step": "关闭", "version": 1},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["findings"][0]["status"], machine)
        self.assertEqual(updated.json()["findings"][0]["review"]["judgment"], "通过")

    def test_a_corrected_review_keeps_every_earlier_version(self):
        """A correction adds a version and never rewrites one; a severity change carries its reason (#358)."""
        self._login("alice")
        target = self._upload("report.xlsx", b"target-bytes")
        round_id = self._accept({target["id"]: RULE_A})["id"]
        self._finish_worker()
        key = self.client.get(f"/rounds/{round_id}").json()["findings"][0]["item_key"]

        def save(version, **fields):
            return self.client.put(f"/rounds/{round_id}/reviews", json={"item_key": key, "judgment": "confirm", "rationale": "", "next_step": "", "version": version, **fields})

        self.assertEqual(save(0, rationale="第一次").status_code, 200)
        refused = save(1, severity="A")
        self.assertEqual((refused.status_code, refused.json()["error"]), (400, "review_severity_reason_required"))
        self.assertEqual(save(1, rationale="改为 A：影响装配", severity="A").status_code, 200)
        third = save(2, judgment="disagree", rationale="再次核对")
        self.assertEqual(third.status_code, 200)
        # The severity a reviewer set stays in effect until changed again.
        self.assertEqual(third.json()["findings"][0]["review"] | {"citations": []}, {"judgment": "disagree", "rationale": "再次核对", "next_step": "", "version": 3, "citations": [], "severity": "A"})
        self.assertEqual(save(2, rationale="过期版本").json()["error"], "review_conflict")
        self.assertEqual(save(9, rationale="不存在的版本").json()["error"], "review_conflict")
        history = self.client.get(f"/rounds/{round_id}/reviews/history", params={"item_key": key}).json()["versions"]
        self.assertEqual(
            [(row["version"], row["judgment"], row["rationale"], row.get("severity")) for row in history],
            [(3, "disagree", "再次核对", "A"), (2, "confirm", "改为 A：影响装配", "A"), (1, "confirm", "第一次", None)],
        )
        self.assertTrue(all(row["reviewer"] and row["reviewed_at"] for row in history))
        # Versions are only ever added: the API role cannot rewrite or delete one.
        self.assertFalse(self._scalar("SELECT has_table_privilege('ppap_api', 'product.reviews', 'UPDATE') OR has_table_privilege('ppap_api', 'product.reviews', 'DELETE')"))

    def test_reaudit_keeps_old_round(self):
        self._login("alice")
        first = self._upload("report.xlsx", b"version-one")
        old_id = self._accept({first["id"]: RULE_A})["id"]
        self._finish_worker()
        self.client.put(
            f"/rounds/{old_id}/reviews",
            json={
                "item_key": self.client.get(f"/rounds/{old_id}").json()["findings"][0]["item_key"],
                "judgment": "旧复核",
                "rationale": "",
                "next_step": "",
                "version": 0,
            },
        )
        second = self._upload("report.xlsx", b"version-two", file_id=first["file_id"])
        new_id = self._accept({second["id"]: RULE_A}, predecessor_id=old_id)["id"]
        self.assertNotEqual(new_id, old_id)
        self._finish_worker()
        old = self.client.get(f"/rounds/{old_id}").json()
        new = self.client.get(f"/rounds/{new_id}").json()
        self.assertEqual(old["findings"][0]["review"]["judgment"], "旧复核")
        self.assertEqual(new["predecessor_id"], old_id)
        self.assertIsNone(new["findings"][0]["review"])
        old_bytes = self.client.get(f"/rounds/{old_id}/files/{first['file_id']}/versions/{first['id']}/download")
        new_bytes = self.client.get(f"/rounds/{new_id}/files/{second['file_id']}/versions/{second['id']}/download")
        self.assertEqual(old_bytes.content, self.originals[first["id"]])
        self.assertEqual(new_bytes.content, self.originals[second["id"]])

    def test_accept_keeps_every_target_and_reference(self):
        self._login("alice")
        first = self._upload("a.xlsx", b"one")
        second = self._upload("b.xlsx", b"two")
        reference = self._upload("context.pdf", text_pdf("ref"))
        round_id = self._accept({first["id"]: RULE_A, second["id"]: RULE_A, reference["id"]: None})["id"]
        self._finish_worker()
        body = self.client.get(f"/rounds/{round_id}").json()
        keys = {row["item_key"] for row in body["findings"]}
        self.assertEqual(keys, {f"{first['id']}:rule:presence", f"{second['id']}:rule:presence"})
        self.assertEqual(sorted(body["coverage"]["checked"]), ["a.xlsx", "b.xlsx"])
        self.assertTrue(any("context.pdf" in row for row in body["coverage"]["unchecked"]))
        self.assertFalse(body["coverage"]["locator_gap"])

    def test_round_history_lists_authorized_rounds_only(self):
        self._login("alice")
        self.assertEqual(self._rounds(), [])
        target = self._upload("report.xlsx", b"target-bytes")
        round_id = self._accept({target["id"]: RULE_A})["id"]
        listed = self._rounds()
        self.assertEqual([row["id"] for row in listed], [round_id])
        self.assertEqual(listed[0]["inputs"][0]["original_filename"], "report.xlsx")
        self.assertTrue(listed[0]["inputs"][0]["file_version_id"])
        self.assertGreaterEqual(listed[0]["updated_at"], listed[0]["created_at"])
        self.assertNotIn("findings", listed[0])
        self._finish_worker()
        done = self._rounds()
        self.assertGreater(done[0]["result_counts"]["PASS"], 0)
        self.assertGreater(done[0]["updated_at"], listed[0]["updated_at"])
        self._login("carol")
        self.assertEqual(self._rounds(), [])
        self._login("bob")
        bob_list = self._rounds()
        self.assertEqual([row["id"] for row in bob_list], [round_id])

    def test_preview_authorization_and_search_across_history_pages(self):
        self._login("alice")
        rounds = []
        for filename in ("first.docx", "second.docx"):
            version = self._upload(filename, text_docx("test"))
            path = f"/files/{version['file_id']}/versions/{version['id']}"
            self.assertEqual(self.client.get(path + "/preview").json()["text"], "test")
            rounds.append(self._accept({version["id"]: RULE_A})["id"])
        alice = self.store.principal_for_fixture("alice")
        first, second = (self.store.list_rounds(alice, 1, offset) for offset in (0, 1))
        self.assertEqual({first[0]["id"], second[0]["id"]}, set(rounds))
        self.assertEqual(self.store.list_rounds(alice, 1, 2), [])
        self.assertEqual(len(self.store.list_rounds(alice, q="first")), 1)
        round_path = f"/rounds/{rounds[-1]}" + path + "/preview"
        self.assertEqual(self.client.get(round_path).status_code, 200)
        self.assertEqual(self.client.get(round_path + "?start_row=0").status_code, 422)
        self.assertEqual(self.client.get(round_path + "?start_row=1048577").status_code, 422)
        # Everyone in the enterprise reads its files and rounds.
        self._login("bob")
        self.assertEqual(self.client.get(path + "/preview").status_code, 200)
        self.assertEqual(self.client.get(round_path).status_code, 200)
        self.assertEqual(self.client.get(round_path + "?sheet=Sheet1&start_row=30").status_code, 200)
        self.assertEqual({row["id"] for row in self.store.list_rounds(self.store.principal_for_fixture("bob"))}, set(rounds))

    def test_selected_rule_items_are_frozen_in_confirmed_order(self):
        self._login("alice")
        source = self._upload("rules.json", b"{}")
        mapped_text = "明确绑定的首条检查"
        body = rule_body()
        body["rules"] = [
            {"id": "keep", "category": mapped_text, "priority": "P1", "rule_to_check": mapped_text, "type_of_rule": "text"},
            {"id": "extra", "category": "customer-only", "priority": None, "rule_to_check": "arbitrary customer requirement", "type_of_rule": "text"},
        ]
        published = self.client.post("/rules", json={"title": "控制计划清单", "body": body, "source_file_version_id": source["id"]})
        self.assertEqual(published.status_code, 201)
        self.core.adopt(
            self.store,
            published.json()["id"],
            """from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

def check(context):
    clause = next(row for row in context.rule.rules if row["id"] == context.clause_id)
    from openpyxl import load_workbook
    from io import BytesIO
    book = load_workbook(BytesIO(context.sources[context.audit_file.audit_id]))
    assert book.active["A1"].value == "target-bytes"
    book.close()
    return AuditResultItem(status="PASS", classification="规则", item=clause["rule_to_check"], priority="P0",
        suggestions="已核对受控原件", clause_id=context.clause_id, checks_performed=1,
        evidence=AuditEvidence(required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
            sources=(EvidenceSource(kind="structure", locator='{"kind":"xlsx_cell","sheet":"Sheet","cell":"A1"}'),)))
""",
        )
        target = self._upload("plan.xlsx", b"target-bytes")
        accepted = self._accept({target["id"]: published.json()["id"]})
        frozen = self._scalar(
            "SELECT rule_execution FROM product.round_inputs WHERE round_id = %s AND role = 'target'",
            (accepted["id"],),
        )
        self.assertEqual(frozen["mode"], "checker_core")
        accepted_rules = accepted["inputs"][0]["rule"]["body"]["rules"]
        self.assertEqual([row["id"] for row in accepted_rules], ["keep", "extra"])
        self.assertEqual(frozen["verification"], "unverified")
        self.assertEqual(frozen["binding"]["entrypoints"], {"keep": "checker:check", "extra": "checker:check"})

        self._finish_worker()
        loaded = self.client.get("/rounds/" + accepted["id"]).json()
        self.assertEqual(loaded["findings"][0]["item_key"], target["id"] + ":rule:keep")
        self.assertEqual(loaded["findings"][0]["item"], mapped_text)
        self.assertFalse(loaded["coverage"]["unchecked"])
        self.assertEqual(loaded["findings"][1]["item"], "arbitrary customer requirement")
        releases = SuiteReleaseStore(self.store.settings, self.store.objects, role="api")
        principal = self.store.principal_for_fixture("alice")
        suite_key = published.json()["body"]["suite_key"]
        active = releases.active(principal, suite_key)
        changed = rule_body()
        changed["rules"][0]["rule_to_check"] = "新版要求"
        successor = self.client.post("/rules", json={"title": "新版", "body": changed, "source_file_version_id": source["id"], "predecessor_id": published.json()["id"]})
        self.assertEqual(successor.status_code, 201)
        # Upload is a candidate based on the adopted Checker, not adoption.
        after_upload = releases.active(principal, suite_key)
        self.assertEqual(after_upload["current_revision_id"], active["current_revision_id"])
        self.assertEqual(after_upload["revision"], active["revision"])
        self.assertEqual(after_upload["history"], active["history"])
        self.assertEqual(after_upload["verification_status"], "stale")
        self.assertEqual(after_upload["rule_update"]["target_revision_id"], successor.json()["body"]["suite_revision_id"])
        self.assertEqual(self.client.get("/rounds/" + accepted["id"]).json(), loaded)
        candidate_id = successor.json()["body"]["suite_revision_id"]
        releases.publish(principal, suite_key, candidate_id, active["revision"], str(uuid.uuid4()), force_unverified=True)
        self.assertEqual(releases.active(principal, suite_key)["current_revision_id"], candidate_id)
        replay = self.client.get("/rounds/" + accepted["id"]).json()
        self.assertEqual(replay, loaded)
        self.assertEqual(
            self._scalar("SELECT rule_execution FROM product.round_inputs WHERE round_id = %s AND role = 'target'", (accepted["id"],)),
            frozen,
        )
        self.assertEqual(replay["findings"][0]["item"], mapped_text)
        self.assertEqual(replay["inputs"][0]["rule"]["id"], published.json()["id"])
