"""Positive original-fact UI journey at the controlled HTTP boundary."""

import copy
import json
import os
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None


PREVIEW = os.environ.get("PPAP_PREVIEW_URL", "").rstrip("/")


@unittest.skipUnless(PREVIEW and sync_playwright, "requires the explicit UI preview and Playwright")
class FactQuestionsPreviewTests(unittest.TestCase):
    def test_original_fact_answer_ranking_confirmation_and_reopen(self):
        questions = [
            {
                "file_version_id": "file-1",
                "field": field,
                "prompt": prompt,
                "answer_type": "boolean",
                "locator": {"kind": "pdf_page", "page": page},
                "clause_ids": clauses,
                "item_keys": clauses,
                "fingerprint": digit * 64,
                "answered": False,
            }
            for field, prompt, page, clauses, digit in (
                ("stage.checked", "量产阶段是否已勾选？", 1, ["R1", "R2", "R3"], "a"),
                ("signature.present", "审批人是否已签字？", 2, ["R2", "R3"], "b"),
                ("date.present", "日期是否完整？", 3, ["R3"], "c"),
                ("stamp.present", "公章是否清晰？", 4, ["R4"], "d"),
            )
        ]
        view = {"round_id": "facts-preview", "revision": 0, "questions": questions, "answers": [], "results": [], "model_calls": 0}
        posts = []
        receipts = {}

        def handle(route):
            if route.request.method == "POST":
                body = route.request.post_data_json
                posts.append(body)
                question = next(row for row in questions if row["field"] == body["field"])
                assert body["expected_revision"] == view["revision"]
                assert body["expected_question_fingerprint"] == question["fingerprint"]
                assert body["file_version_id"] == question["file_version_id"]
                question["answered"] = True
                answer = {
                    "operation_id": body["operation_id"],
                    "round_id": view["round_id"],
                    "file_version_id": body["file_version_id"],
                    "field": body["field"],
                    "value": body["value"],
                    "answered_by": "王工",
                    "answered_at": "2026-11-06T10:00:00Z",
                    "locator": question["locator"],
                    "prompt": question["prompt"],
                    "answer_type": question["answer_type"],
                }
                view["revision"] += 1
                view["answers"].append(answer)
                result = {
                    "item_key": question["item_keys"][0],
                    "status": "UNABLE" if body["value"] is None else "PASS",
                    "suggestions": "人工确认后的确定性结果",
                    "answer_ids": [answer["operation_id"]],
                    "confirmed_by": ["王工"],
                    "confirmed_at": answer["answered_at"],
                    "sources": [{key: answer[key] for key in ("file_version_id", "field", "locator", "answered_by", "answered_at", "value")}],
                }
                view["results"].append(result)
                receipt = {
                    "operation_id": answer["operation_id"],
                    "round_id": view["round_id"],
                    "revision": view["revision"],
                    "answer": answer,
                    "results": copy.deepcopy(view["results"]),
                    "model_calls": 0,
                }
                receipts[answer["operation_id"]] = receipt
                route.fulfill(status=200, content_type="application/json", body=json.dumps(receipt))
            else:
                value = view if route.request.url.endswith("/fact-answers") else receipts.get(route.request.url.rsplit("/", 1)[-1])
                route.fulfill(status=200, content_type="application/json", body=json.dumps(value))

        with sync_playwright() as playwright:
            executable = os.environ.get("PPAP_BROWSER_EXECUTABLE")
            browser = playwright.chromium.launch(**({"executable_path": executable} if executable else {}))
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("**/audit-rounds/facts-preview/fact-answers**", handle)
            page.goto(PREVIEW + "/preview/fact-questions.html")
            section = page.get_by_role("region", name="原件事实确认", exact=True)
            section.get_by_text("原件事实确认", exact=True).click()
            first = section.get_by_role("article", name="量产阶段是否已勾选？", exact=True)
            expect(first).to_be_visible()
            expect(first).to_contain_text("第 1 页")
            expect(first.get_by_role("button", name="保存回答", exact=True)).to_be_disabled()
            expect(section.get_by_role("article", name="公章是否清晰？", exact=True)).to_be_hidden()
            section.get_by_text("更多待确认事实（1）", exact=True).click()
            expect(section.get_by_role("article", name="公章是否清晰？", exact=True)).to_be_visible()
            first.get_by_role("radio", name="是", exact=True).check()
            section.get_by_text("原件事实确认", exact=True).click()
            section.get_by_text("原件事实确认", exact=True).click()
            expect(first.get_by_role("radio", name="是", exact=True)).to_be_checked()
            first.get_by_role("button", name="保存回答", exact=True).click()
            results = section.get_by_role("region", name="人工确认结果", exact=True)
            expect(results).to_be_visible()
            expect(results).to_contain_text("原机器结果：无法判断")
            expect(results).to_contain_text("人工确认")
            expect(results).to_contain_text("符合")
            expect(section.get_by_text("待确认 3 项 · 人工确认结果 1 项", exact=True)).to_be_visible()
            expect(results).to_contain_text("王工")
            expect(results).to_contain_text("第 1 页")
            expect(section.get_by_role("link", name="下载人工确认报告", exact=True)).to_have_attribute("href", "/audit-rounds/facts-preview/fact-answers/report")
            second = section.get_by_role("article", name="审批人是否已签字？", exact=True)
            second.get_by_role("radio", name="看不清", exact=True).check()
            second.get_by_role("button", name="保存回答", exact=True).click()
            expect(section).to_contain_text("已记录“看不清”")
            assert [post["value"] for post in posts] == [True, None]
            assert len({post["operation_id"] for post in posts}) == 2
            assert view["results"][1]["status"] == "UNABLE"
            page.reload()
            section.get_by_text("原件事实确认", exact=True).click()
            expect(results).to_be_visible()
            expect(results).to_contain_text("王工")
            expect(results).to_contain_text("看不清")
            expect(section.get_by_role("article", name="量产阶段是否已勾选？", exact=True)).to_have_count(0)
            browser.close()
