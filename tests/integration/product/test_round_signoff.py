"""Signing off a round (#357): the person who ran it signs it in the Web and may withdraw it with a reason; MCP only
proposes. What was signed is frozen on the round, who did what is their recorded action, and nothing delivered changes."""

import json
from uuid import uuid4

import pytest
from product.identity import SESSION_COOKIE

from .test_mcp import Drive, audited_folder, call, complete, folder_client, intent_of

pytestmark = pytest.mark.usefixtures("generated_rules")


def test_the_person_who_ran_the_round_signs_withdraws_with_a_reason_and_signs_again(mcp_app, core_system):
    _, store = mcp_app
    drive = Drive()
    with folder_client(store, drive) as (_app, client, token, _):
        task, _ = audited_folder(client, store, token, core_system)
        detail = client.get(f"/audit-tasks/{task}").json()
        round_id = detail["round_id"]
        path = f"/rounds/{round_id}/signoff"
        key = client.get(f"/rounds/{round_id}").json()["findings"][0]["item_key"]
        review = {"item_key": key, "judgment": "confirm", "rationale": "已核对原件", "next_step": "", "version": 0}
        assert client.put(f"/rounds/{round_id}/reviews", json=review).status_code == 200

        # What signing would freeze, as the task page shows it.
        standing = client.get(path).json()
        assert (standing["state"], standing["can_sign"], standing["signed"], standing["unavailable"], standing["history"]) == ("unsigned", True, None, None, [])
        current = standing["current"]
        (file,) = detail["files"]
        audit = file["audit"]
        assert current["conclusion"] == detail["conclusion"] and current["machine_conclusion"] == detail["machine_conclusion"]
        (frozen_file,) = current["files"]
        assert {key: frozen_file[key] for key in ("file_version_id", "filename", "conclusion", "machine_conclusion")} == {
            "file_version_id": file["file"]["id"],
            "filename": file["file"]["original_filename"],
            "conclusion": audit["confirmed_conclusion"],
            "machine_conclusion": audit["conclusion"],
        }
        assert frozen_file["report"]["report_id"] == audit["report"]["report_id"] and len(frozen_file["report"]["sha256"]) == 64
        assert current["reviews"] == [{"item_key": key, "version": 1, "judgment": "confirm"}]
        assert current["reviewed_count"] == frozen_file["reviewed_count"] == 1
        assert len(current["unreviewed"]) == len(audit["checks"]) - 1
        # Accepted as the machine concluded: decided items nobody reviewed; 待审核 ones are counted apart, never accepted.
        decided = [item for item in current["unreviewed"] if item["outcome"] not in {"UNABLE", "uncovered"}]
        assert current["accepted_count"] == frozen_file["accepted_count"] == len(decided)
        assert frozen_file["pending_count"] == sum(check["outcome"] in {"UNABLE", "uncovered"} for check in audit["checks"])
        assert all(item["item_key"] != key for item in current["unreviewed"])
        assert call(client, token, "get_results", task_id=task)["result"]["signoff"] == {"state": "unsigned", "signed_at": None, "signer": None}

        # Another member of the enterprise sees it unsigned, but only the person who ran the round signs it.
        mine = client.cookies.get(SESSION_COOKIE)
        client.cookies.set(SESSION_COOKIE, store.create_session(store.principal_for_fixture("bob")))
        seen = client.get(path).json()
        assert (seen["state"], seen["can_sign"], seen["unavailable"]["code"]) == ("unsigned", False, "not_creator")
        assert client.post(path, json={"if_match": current["fingerprint"]}).json() == {"error": "access_denied"}
        client.cookies.set(SESSION_COOKIE, mine)

        # The client proposes; nothing is signed until the person does it on the page, binding what they saw.
        arguments = {"task_id": task, "request_id": str(uuid4())}
        asked = call(client, token, "sign_round", **arguments)
        ident = intent_of(asked)
        assert client.get(path).json()["state"] == "unsigned"
        stale = client.post(path, json={"if_match": "0" * 64}, headers={"X-PPAP-Intent": ident})
        assert (stale.status_code, stale.json()) == (409, {"error": "content_changed"})
        signed = client.post(path, json={"if_match": current["fingerprint"]}, headers={"X-PPAP-Intent": ident})
        assert signed.status_code == 200, signed.text
        body = signed.json()
        assert (body["state"], body["signed"], body["current"], body["can_sign"], body["can_withdraw"]) == ("signed", current, None, False, True)
        assert body["signer"] == {"name": detail["creator"]["name"], "mine": True}
        assert [(row["action"], row["by"], row["reason"]) for row in body["history"]] == [("signed", detail["creator"]["name"], None)]
        assert signed.headers["x-ppap-action"]
        done = call(client, token, "sign_round", **arguments)
        assert done["next"]["kind"] == "done" and done["result"]["signoff"]["state"] == "signed" and done["result"]["receipt"]["number"], done
        live = call(client, token, "get_results", task_id=task)["result"]["signoff"]
        assert live["state"] == "signed" and live["signer"] == detail["creator"]["name"]

        # Signed: it is not signed twice, and reviews wait for a withdrawal, through the Web and through MCP alike.
        assert client.post(path, json={}).json() == {"error": "round_already_signed"}
        corrected = {**review, "version": 1, "judgment": "disagree", "rationale": "原件缺零件号"}
        refused = client.put(f"/rounds/{round_id}/reviews", json=corrected)
        assert (refused.status_code, refused.json()) == (409, {"error": "round_signed"})
        said = call(client, token, "record_review", task_id=task, request_id=str(uuid4()), item_key=key, judgment="disagree", rationale="原件缺零件号", next_step="")
        assert said["next"]["error_code"] == "round_signed" and "撤回签发" in said["next"]["message"], said

        # A report delivered now says it was signed, and by whom; it is never rewritten afterwards.
        _, delivered = complete(client, call(client, token, "publish_report", task_id=task, request_id=str(uuid4())))
        assert delivered.status_code == 200, delivered.text
        sent = json.dumps(drive.written, ensure_ascii=False)
        assert "机器初审结果，以人工确认和签发为准。" in sent and f"交付时已签发 · {detail['creator']['name']} · " in sent
        written = list(drive.written)

        # Another member of the enterprise reads where it stands, but neither signs nor withdraws it.
        client.cookies.set(SESSION_COOKIE, store.create_session(store.principal_for_fixture("bob")))
        seen = client.get(path).json()
        assert (seen["state"], seen["signer"]["mine"], seen["can_withdraw"], seen["unavailable"]["code"]) == ("signed", False, False, "not_creator")
        assert client.post(path + "/withdraw", json={"reason": "不是我的"}).json() == {"error": "access_denied"}
        client.cookies.set(SESSION_COOKIE, store.create_session(store.principal_for_fixture("carol")))
        assert client.get(path).status_code == 403
        client.cookies.set(SESSION_COOKIE, mine)

        # Withdrawing needs a reason; afterwards the review is corrected and the round is signed again.
        for empty in ({}, {"reason": "   "}):
            missing = client.post(path + "/withdraw", json=empty)
            assert (missing.status_code, missing.json()) == (400, {"error": "withdraw_reason_required"})
        withdrawn = client.post(path + "/withdraw", json={"reason": "第 2 页  漏看了零件号"})
        assert withdrawn.status_code == 200, withdrawn.text
        assert (withdrawn.json()["state"], withdrawn.json()["signed"], withdrawn.json()["signer"]) == ("unsigned", None, None)
        assert [(row["action"], row["reason"]) for row in withdrawn.json()["history"]] == [("signed", None), ("withdrawn", "第 2 页 漏看了零件号")]
        assert client.post(path + "/withdraw", json={"reason": "再撤一次"}).json() == {"error": "round_not_signed"}
        assert client.put(f"/rounds/{round_id}/reviews", json=corrected).status_code == 200
        again = client.get(path).json()["current"]
        assert again["reviews"] == [{"item_key": key, "version": 2, "judgment": "disagree"}] and again["fingerprint"] != current["fingerprint"]
        resigned = client.post(path, json={"if_match": again["fingerprint"]})
        assert resigned.status_code == 200 and resigned.json()["signed"] == again
        assert [row["action"] for row in resigned.json()["history"]] == ["signed", "withdrawn", "signed"]
        # The document delivered before is left as it was.
        assert drive.written == written

        # Each signing and each withdrawal is one action of the person's, the reason its parameter (#362).
        with store._conn() as conn:
            rows = conn.execute("SELECT route, outcome, error, params, intent_id::text, channel FROM product.actions WHERE route LIKE '/rounds/{round_id}/signoff%%' ORDER BY at, id").fetchall()
        assert [(route.rsplit("/", 1)[-1], outcome, error) for route, outcome, error, *_ in rows] == [
            ("signoff", "failed", "access_denied"),
            ("signoff", "failed", "content_changed"),
            ("signoff", "succeeded", None),
            ("signoff", "failed", "round_already_signed"),
            ("withdraw", "failed", "access_denied"),
            ("withdraw", "failed", "withdraw_reason_required"),
            ("withdraw", "failed", "withdraw_reason_required"),
            ("withdraw", "succeeded", None),
            ("withdraw", "failed", "round_not_signed"),
            ("signoff", "succeeded", None),
        ]
        succeeded = [row for row in rows if row[1] == "succeeded"]
        assert [(row[4], row[5]) for row in succeeded] == [(ident, "web"), (None, "web"), (None, "web")]
        assert succeeded[1][3] == {"reason": "第 2 页  漏看了零件号"}
        # The receipt names the signing moments by their labels.
        receipts = client.get("/receipts").json()["receipts"]
        assert {"签发审核结论", "撤回签发"} <= {row["action"] for row in receipts}
        # Its subject is the task the round belongs to, and a withdrawal says why.
        withdrawal = next(row for row in receipts if row["action"] == "撤回签发" and row["outcome"]["state"] == "succeeded")
        assert withdrawal["task_id"] == task and withdrawal["subject"] == detail["number"] and withdrawal["details"] == ["原因：第 2 页  漏看了零件号"]
