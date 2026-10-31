"""Browser feedback against real API/PG/Worker/core, with a controlled model boundary."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest
import uvicorn
from ppap_core.db import connect
from ppap_core.identity import SESSION_COOKIE
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def feedback_web(app, directory, settings=None):
    """Serve the candidate application and Vite with actual loopback HTTP."""
    api_socket = socket.socket()
    api_socket.bind(("127.0.0.1", 0))
    api_port = api_socket.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [api_socket]}, daemon=True)
    thread.start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        web_port = probe.getsockname()[1]
    web = f"http://127.0.0.1:{web_port}"
    if settings is not None:
        settings.web_url = web
    output = (directory / "vite.log").open("w")
    process = subprocess.Popen(
        ["pnpm", "dev", "--host", "127.0.0.1", "--port", str(web_port)],
        cwd=ROOT / "apps/web",
        env={**os.environ, "PPAP_PUBLIC_API_URL": f"http://127.0.0.1:{api_port}"},
        stdout=output,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(web + "/runtime-config.json", timeout=1) as response:
                    assert response.status == 200
                    break
            except (URLError, TimeoutError):
                assert process.poll() is None, (directory / "vite.log").read_text()
                assert time.monotonic() < deadline, "Candidate Vite did not start"
                time.sleep(0.1)
        yield web
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        output.close()
        server.should_exit = True
        thread.join(timeout=10)
        api_socket.close()


def local_browser_boundary(context, web):
    """Keep the fixture identity local; all product HTTP remains real."""
    external = []

    def local_only(route):
        url = route.request.url
        if url.startswith(web + "/"):
            route.continue_()
        elif url.startswith("https://lf-scm-cn.feishucdn.com/lark/op/h5-js-sdk-") and url.endswith(".js"):
            # Identity is already supplied by the real test session. Do not
            # fetch the external host SDK during offline product acceptance.
            route.fulfill(content_type="application/javascript", body="")
        else:
            external.append(url)
            route.abort("blockedbyclient")
            raise AssertionError(f"External browser request in offline acceptance: {url}")

    context.route("**/*", local_only)
    return external


def test_confirm_explanation_recompute_and_reopen_without_erasing_conflict(deep_feedback_env, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    env = deep_feedback_env
    question = env["store"].read_deep_feedback(env["principal"], env["round_id"])["clarifications"][0]
    assert question["scope"]["rule_targets"]
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        page.set_default_timeout(15_000)
        submitted = []

        def mark_test_confirmation(route):
            payload = route.request.post_data_json
            payload["confirmation_source"] = "synthetic"
            submitted.append(payload)
            # The real API still authorizes, validates, commits and queues this
            # request. Only label the automated user input as synthetic.
            route.continue_(post_data=json.dumps(payload, ensure_ascii=False))

        page.route("**/audit-rounds/*/rule-changes", mark_test_confirmation)
        page.goto(f"{web}/audit?round={env['round_id']}")
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        expect(panel.get_by_role("radiogroup", name="解释候选")).to_be_visible()
        affected = panel.get_by_role("list", name="受影响的文件与要求")
        expect(affected.get_by_role("listitem")).to_have_count(len(question["scope"]["rule_targets"]))
        expect(panel.get_by_role("button", name="确认解释并继续", exact=True)).to_be_disabled()
        expect(panel.get_by_text("PFD.xlsx", exact=True).first).to_be_visible()
        expect(panel.get_by_text("CP.xlsx", exact=True).first).to_be_visible()
        expect(panel.get_by_role("table", name="CP 单元格内容")).to_be_visible()
        expect(panel.get_by_role("table", name="PFD 单元格内容")).to_be_visible()
        expect(panel.get_by_role("table", name="CP 单元格内容").get_by_role("cell", name="A1：作业单元编号", exact=True)).to_be_visible()
        evidence_a, evidence_b = panel.get_by_role("region", name="解释证据 1", exact=True), panel.get_by_role("region", name="解释证据 2", exact=True)
        assert abs(evidence_a.bounding_box()["y"] - evidence_b.bounding_box()["y"]) < 2
        expect(evidence_a.get_by_text("CP · A1", exact=True)).to_be_visible()
        expect(evidence_b.get_by_text(re.compile("PFD · A[23]"), exact=True).first).to_be_visible()
        expect(panel.locator('button[data-variant="default"]')).to_have_count(1)
        page.screenshot(path=str(tmp_path / "deep-feedback-desktop.png"), full_page=True)

        panel.get_by_role("radio", name="所有候选都不符合", exact=True).click()
        expect(panel.get_by_text("该问题保持未解", exact=True)).to_be_visible()
        assert submitted == []
        panel.get_by_role("radio", name="按工序编号理解").focus()
        page.keyboard.press("Space")
        explanation = panel.get_by_role("textbox", name="拟保存的补充说明")
        expect(explanation).to_have_value(re.compile("作业单元编号"))
        draft = explanation.input_value()
        # Navigate the source with the keyboard, then edit beside it. The
        # selected cell and source viewport must not jump back to A1.
        position = evidence_a.get_by_role("textbox", name="单元格位置", exact=True)
        position.focus()
        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.type("B2")
        page.keyboard.press("Enter")
        expect(evidence_a.get_by_role("cell", name="B2：SC", exact=True)).to_be_focused()
        reader_scroll = evidence_a.locator("div.absolute.inset-0.overflow-auto")
        original_scroll = reader_scroll.evaluate("element => {element.scrollTop = 40; return element.scrollTop}")
        assert original_scroll > 0
        explanation.focus()
        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.type(draft + " 仅适用于本文件。")
        expect(position).to_have_value("B2")
        scope = panel.get_by_role("checkbox", name="CP.xlsx", exact=True)
        scope.focus()
        page.keyboard.press("Space")
        expect(panel.get_by_role("button", name="确认解释并继续", exact=True)).to_be_disabled()
        page.keyboard.press("Space")
        expect(scope).to_be_checked()
        close_source = evidence_a.get_by_role("button", name="收起原件 CP.xlsx", exact=True)
        close_source.focus()
        page.keyboard.press("Enter")
        evidence_a.get_by_role("button", name="展开原件 CP.xlsx", exact=True).focus()
        page.keyboard.press("Enter")
        expect(position).to_have_value("B2")
        assert reader_scroll.evaluate("element => element.scrollTop") == original_scroll
        expect(explanation).to_have_value(draft + " 仅适用于本文件。")
        panel.get_by_text("这次需要改变审核要求？", exact=True).focus()
        page.keyboard.press("Enter")
        panel.get_by_label("修改作用", exact=True).select_option("standard_change")
        comparison = panel.get_by_label("审核要求变更对照", exact=True)
        expect(comparison.get_by_text("原审核要求", exact=True)).to_be_visible()
        expect(comparison.get_by_text(draft + " 仅适用于本文件。", exact=True)).to_be_visible()
        expect(panel.get_by_role("button", name="修改规则并重新分析", exact=True)).to_be_enabled()
        panel.get_by_label("修改作用", exact=True).select_option("interpretation")
        panel.get_by_role("button", name="关闭当前解释", exact=True).focus()
        page.keyboard.press("Enter")
        expect(panel.get_by_role("button", name="查看解释", exact=True)).to_be_focused()
        panel.get_by_role("button", name="查看解释", exact=True).click()
        expect(panel.get_by_role("radio", name="按工序编号理解")).to_be_checked()
        assert submitted == []

        page.set_viewport_size({"width": 390, "height": 844})
        assert evidence_b.bounding_box()["y"] >= evidence_a.bounding_box()["y"] + evidence_a.bounding_box()["height"]
        expect(position).to_have_value("B2")
        expect(explanation).to_have_value(draft + " 仅适用于本文件。")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / "deep-feedback-mobile.png"), full_page=True)
        # Restore the fixed sample's canonical candidate before acceptance.
        # The controlled model only recognizes that exact confirmed meaning;
        # a free-text edit must never be assumed to force a PASS.
        explanation.focus()
        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.type(draft)
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as saved:
            panel.get_by_role("button", name="确认解释并继续", exact=True).focus()
            page.keyboard.press("Enter")
        assert saved.value.status == 200, saved.value.text()
        receipt = saved.value.json()
        assert len(submitted) == 1
        assert submitted[0]["scope"]["document_ids"] == [env["uploaded"]["CP"]["id"]]
        assert submitted[0]["scope"] == question["scope"]
        env["run_jobs"]()
        panel.get_by_role("button", name="读取新分析结果", exact=True).click()
        current = panel.get_by_role("region", name="当前分析判断", exact=True)
        expect(current.get_by_text("不符合", exact=True)).to_be_visible()
        expect(panel.get_by_text("当前没有待确认解释", exact=True)).to_be_visible()
        expect(current.get_by_text("此前：无法判断 → 当前：满足", exact=True)).to_be_visible()
        expect(current.get_by_text("此前：无法判断 → 当前：不符合", exact=True)).to_be_visible()
        expect(current.get_by_text("结论未变：满足", exact=True)).to_be_visible()
        expect(current.get_by_text(submitted[0]["content"], exact=True)).to_be_visible()
        actual = env["client"].get("/rounds/" + receipt["analysis_round_id"]).json()
        assert [finding["status"] for finding in actual["findings"]] == ["PASS", "NG", "PASS"]
        assert len(submitted) == 1
        page.screenshot(path=str(tmp_path / "deep-feedback-confirmed-mobile.png"), full_page=True)
        context.close()

        fresh = browser.new_context(viewport={"width": 1440, "height": 1000})
        local_browser_boundary(fresh, web)
        fresh.add_cookies([{"name": SESSION_COOKIE, "value": env["store"].create_session(env["principal"]), "url": web}])
        reopened = fresh.new_page()
        reopened.goto(f"{web}/audit?round={env['round_id']}")
        restored = reopened.get_by_role("region", name="解释与重新分析", exact=True)
        expect(restored.get_by_text("当前没有待确认解释", exact=True)).to_be_visible()
        restored.get_by_text("本次已确认说明（1）", exact=True).click()
        expect(restored.get_by_text(submitted[0]["content"], exact=True).first).to_be_visible()
        expect(restored.get_by_role("region", name="当前分析判断").get_by_text("不符合", exact=True)).to_be_visible()
        fresh.close()
        browser.close()


def test_save_only_then_explicit_analysis_does_not_save_the_explanation_twice(deep_feedback_env, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    env = deep_feedback_env
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        page.route("**/audit-rounds/*/rule-changes", lambda route: route.continue_(post_data=json.dumps({**route.request.post_data_json, "confirmation_source": "synthetic"}, ensure_ascii=False)))
        page.goto(f"{web}/audit?round={env['round_id']}")
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        panel.get_by_role("radio", name="按工序编号理解").click()
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as saved:
            panel.get_by_role("button", name="仅保存说明", exact=True).click()
        assert saved.value.status == 200
        assert saved.value.json()["analysis_round_id"] is None
        view = env["client"].get(f"/audit-rounds/{env['round_id']}/deep-feedback").json()
        assert len(view["results"]) == 1 and len(view["appendix"]) == 1
        expect(panel.get_by_text("说明已保存。下方结果使用此前输入，新的依据尚未产生分析结果。", exact=True)).to_be_visible()
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as started:
            panel.get_by_role("button", name="按已保存说明开始分析", exact=True).click()
        assert started.value.status == 200
        assert started.value.json()["analysis_round_id"] is not None
        env["run_jobs"]()
        panel.get_by_role("button", name="读取新分析结果", exact=True).click()
        expect(panel.get_by_role("region", name="当前分析判断").get_by_text("不符合", exact=True)).to_be_visible()
        current = env["client"].get(f"/audit-rounds/{env['round_id']}/deep-feedback").json()
        assert len(current["appendix"]) == 1 and len(current["operations"]) == 2
        assert len(current["results"]) == 2
        assert current["appendix"][0]["confirmation_source"] == "synthetic"
        context.close()
        browser.close()


def test_lost_save_reply_refreshes_and_reads_original_operation_without_another_post(deep_feedback_env, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    env = deep_feedback_env
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        sent = []

        def lose_reply(route):
            payload = route.request.post_data_json
            payload["confirmation_source"] = "synthetic"
            sent.append(payload)
            response = route.fetch(post_data=json.dumps(payload, ensure_ascii=False))
            assert response.status == 200
            # The real transaction committed; only its browser reply is lost.
            route.abort("connectionfailed")

        page.route("**/audit-rounds/*/rule-changes", lose_reply)
        page.goto(f"{web}/audit?round={env['round_id']}")
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        panel.get_by_role("radio", name="按工序编号理解").click()
        panel.get_by_role("button", name="确认解释并继续", exact=True).click()
        expect(panel.get_by_text("操作结果待核对", exact=True)).to_be_visible()
        assert len(sent) == 1
        assert "ruleChange=" in page.url
        # Hold the initial view read on reload. Recovery must accept the first
        # click even before that read returns; it cannot silently ignore it.
        initial_reads = []

        def hold_initial_read(route):
            if not initial_reads:
                initial_reads.append((route, route.fetch()))
            else:
                route.continue_()

        page.route("**/audit-rounds/*/deep-feedback", hold_initial_read)
        page.reload(wait_until="domcontentloaded")
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        expect(panel.get_by_text("正在读取解释问题…", exact=True)).to_be_visible()
        panel.get_by_role("button", name="读取原操作", exact=True).click()
        expect(panel.get_by_text("已保存并受理新分析", exact=True)).to_be_visible()
        assert len(initial_reads) == 1
        old_route, old_response = initial_reads[0]
        old_route.fulfill(response=old_response)
        # The late old read must not restore an obsolete question after the
        # recovered save has already advanced the shared application state.
        expect(panel.get_by_text("当前没有待确认解释", exact=True)).to_be_visible()
        expect(panel.get_by_role("radiogroup", name="解释候选")).to_have_count(0)
        assert "ruleChange=" not in page.url
        env["run_jobs"]()
        panel.get_by_role("button", name="读取新分析结果", exact=True).click()
        expect(panel.get_by_role("region", name="当前分析判断").get_by_text("不符合", exact=True)).to_be_visible()
        assert len(sent) == 1
        view = env["client"].get(f"/audit-rounds/{env['round_id']}/deep-feedback").json()
        assert len(view["operations"]) == 1 and len(view["results"]) == 2
        context.close()
        browser.close()


def test_upload_originals_choose_rules_start_and_complete_feedback_from_product_entry(deep_feedback_env, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    env = deep_feedback_env
    with feedback_web(env["app"], tmp_path, env["settings"]) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        external_requests = local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        page.goto(web + "/audit?prepareDeep=1")
        expect(page.get_by_role("heading", name="按明确规则开始深度分析", exact=True)).to_be_visible()
        with page.expect_file_chooser() as picker:
            page.get_by_role("button", name="添加资料", exact=True).click()
        picker.value.set_files([{"name": item["name"], "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": item["bytes"]} for item in env["documents"].values()])
        target = page.get_by_role("article", name="资料 CP.xlsx", exact=True)
        reference = page.get_by_role("article", name="资料 PFD.xlsx", exact=True)
        expect(target.get_by_text("原件已保存", exact=True)).to_be_visible()
        expect(reference.get_by_text("原件已保存", exact=True)).to_be_visible()
        target.get_by_label("本次文件用途", exact=True).select_option("target")
        target.get_by_label("使用的审核规则", exact=True).select_option(env["rule"]["id"])
        expect(target.get_by_text("本文件使用的规则原文", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="开始深度分析", exact=True)).to_be_enabled()
        page.screenshot(path=str(tmp_path / "deep-prepare-selected.png"), full_page=True)
        with page.expect_response(lambda response: response.request.method == "POST" and "/drafts/" in response.url and response.url.endswith("/accept")) as accepted:
            page.get_by_role("button", name="开始深度分析", exact=True).click()
        assert accepted.value.status == 201, accepted.value.text()
        # Navigation may release the old response body. Read the durable round
        # reached by the actual UI rather than racing Network.getResponseBody.
        from urllib.parse import parse_qs, urlsplit

        page.wait_for_url(re.compile(r".*[?&]round=[^&]+"))
        created_id = parse_qs(urlsplit(page.url).query)["round"][0]
        created = env["client"].get("/rounds/" + created_id).json()
        assert created["id"] != env["round_id"]
        assert created["analysis_mode"] == "deep"
        assert {item["file_version_id"] for item in created["inputs"]}.isdisjoint({item["id"] for item in env["uploaded"].values()})
        assert {item["original_filename"]: item["role"] for item in created["inputs"]} == {"CP.xlsx": "target", "PFD.xlsx": "reference"}
        env["run_jobs"]()
        page.goto(f"{web}/audit?round={created['id']}")
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        initial = env["client"].get("/rounds/" + created["id"]).json()
        assert [finding["status"] for finding in initial["findings"]] == ["UNABLE", "UNABLE", "PASS"]
        expect(panel.get_by_role("table", name="CP 单元格内容")).to_be_visible()
        expect(panel.get_by_role("table", name="PFD 单元格内容")).to_be_visible()
        page.screenshot(path=str(tmp_path / "deep-product-initial-desktop.png"), full_page=True)
        operations = []
        panel.get_by_role("radio", name="按工序编号理解").click()
        operations.append("选择解释候选")
        scope = panel.get_by_role("checkbox", name="CP.xlsx", exact=True)
        scope.click()
        operations.append("取消范围选择，验证禁止提交")
        expect(panel.get_by_role("button", name="确认解释并继续", exact=True)).to_be_disabled()
        scope.click()
        operations.append("明确选择CP文件范围")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / "deep-product-confirm-mobile.png"), full_page=True)
        sent = []

        def mark(route):
            value = {**route.request.post_data_json, "confirmation_source": "synthetic"}
            sent.append(value)
            route.continue_(post_data=json.dumps(value, ensure_ascii=False))

        page.route("**/audit-rounds/*/rule-changes", mark)
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as changed:
            panel.get_by_role("button", name="确认解释并继续", exact=True).click()
        operations.append("确认解释并继续")
        assert changed.value.status == 200, changed.value.text()
        assert len(sent) == 1
        expect(panel.get_by_text("新输入已受理，等待分析。", exact=True)).to_be_visible()
        env["run_jobs"]()
        panel.get_by_role("button", name="读取新分析结果", exact=True).click()
        expect(panel.get_by_role("region", name="当前分析判断").get_by_text("不符合", exact=True)).to_be_visible()
        result = env["client"].get("/rounds/" + changed.value.json()["analysis_round_id"]).json()
        assert [finding["status"] for finding in result["findings"]] == ["PASS", "NG", "PASS"]
        expect(panel.get_by_text("此前：无法判断 → 当前：满足", exact=True)).to_be_visible()
        page.screenshot(path=str(tmp_path / "deep-product-result-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.screenshot(path=str(tmp_path / "deep-product-result-desktop.png"), full_page=True)
        view = env["client"].get(f"/audit-rounds/{created['id']}/deep-feedback").json()
        assert len(view["operations"]) == 1 and len(view["results"]) == 2
        with env["store"]._conn() as conn:
            actual_recomputations = conn.execute("SELECT count(*) FROM engine.jobs WHERE audit_round_id=%s AND kind='audit'", (result["id"],)).fetchone()[0]
        metrics = {
            "confirmation_actions": operations,
            "confirmation_action_count": len(operations),
            "repeat_confirmations": len(sent) - 1,
            "evidence_navigation_count": 0,
            "actual_recomputations": actual_recomputations,
            "boundary": "automated controlled model; real parser, evaluator, API, PG and Worker; no human usability claim",
        }
        assert metrics["confirmation_action_count"] == 4 and metrics["repeat_confirmations"] == 0 and actual_recomputations == 1
        assert external_requests == []
        metrics["external_browser_requests"] = len(external_requests)
        (tmp_path / "deep-product-operations.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
        context.close()
        browser.close()


def test_native_mcp_url_opens_actual_web_confirmation_and_reads_same_operation(deep_feedback_env, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    env = deep_feedback_env
    with feedback_web(env["app"], tmp_path, env["settings"]) as web, sync_playwright() as playwright:
        token = env["mcp_access"]()
        view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
        question = view["clarifications"][0]
        candidate = question["candidates"][0]
        change = {
            "operation_id": str(uuid4()),
            "expected_revision": view["revision"],
            "question_id": question["id"],
            "expected_question_fingerprint": question["fingerprint"],
            "candidate_id": candidate["id"],
            "kind": "interpretation",
            "content": candidate["explanation"],
            "scope": question["scope"],
            "intent": "save_and_analyze",
            "confirmation_source": "synthetic",
        }
        capabilities = {"elicitation": {"url": {}}, "extensions": {"io.modelcontextprotocol/tasks": {}}}
        requested = env["mcp_rpc"](token, "tools/call", {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}, capabilities)["result"]
        assert requested["resultType"] == "task"
        task_id = requested["taskId"]
        key, needed = next(iter(requested["inputRequests"].items()))
        url = needed["params"]["url"]
        assert url.startswith(web + "/audit?") and "mcpRuleChange=" in url
        # URL consent merely opens the actual UI; no business change yet.
        env["mcp_rpc"](token, "tasks/update", {"taskId": task_id, "inputResponses": {key: {"action": "accept"}}}, capabilities)
        assert env["store"].read_deep_feedback(env["principal"], env["round_id"])["revision"] == 0
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        submitted = []

        def mark(route):
            payload = route.request.post_data_json
            payload["confirmation_source"] = "synthetic"
            submitted.append(payload)
            route.continue_(post_data=json.dumps(payload, ensure_ascii=False))

        page.route("**/audit-rounds/*/rule-changes", mark)
        page.goto(url)
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        expect(panel.get_by_text("核对拟提交的规则修改", exact=True)).to_be_visible()
        expect(panel.get_by_role("textbox", name="拟保存的补充说明")).to_have_value(candidate["explanation"])
        assert submitted == []
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as saved:
            panel.get_by_role("button", name="确认解释并继续", exact=True).click()
        assert saved.value.status == 200, saved.value.text()
        assert len(submitted) == 1 and submitted[0]["operation_id"] == change["operation_id"]
        assert submitted[0]["scope"] == change["scope"]
        env["run_jobs"]()
        completed = env["mcp_rpc"](token, "tasks/get", {"taskId": task_id}, capabilities)["result"]
        assert completed["status"] == "completed"
        assert completed["result"]["structuredContent"]["receipt"]["operation_id"] == change["operation_id"]
        panel.get_by_role("button", name="读取新分析结果", exact=True).click()
        expect(panel.get_by_role("region", name="当前分析判断").get_by_text("不符合", exact=True)).to_be_visible()
        assert len(submitted) == 1
        page.goto(url)
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        expect(panel.get_by_text("已保存并受理新分析", exact=True)).to_be_visible()
        expect(panel.get_by_role("region", name="当前分析判断").get_by_text("不符合", exact=True)).to_be_visible()
        assert len(submitted) == 1
        current = env["store"].read_deep_feedback(env["principal"], env["round_id"])
        removal = {
            "operation_id": str(uuid4()),
            "expected_revision": current["revision"],
            "remove_appendix_ids": [current["appendix"][0]["id"]],
            "intent": "save_only",
            "confirmation_source": "synthetic",
        }
        requested = env["mcp_rpc"](token, "tools/call", {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": removal}}, capabilities)["result"]
        removal_task = requested["taskId"]
        _, needed = next(iter(requested["inputRequests"].items()))
        page.goto(needed["params"]["url"])
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        deletion = panel.get_by_role("region", name="核对拟执行操作", exact=True)
        expect(deletion.get_by_text(candidate["explanation"], exact=True)).to_be_visible()
        scope = deletion.get_by_label("说明作用范围", exact=True)
        expect(scope.get_by_text("含义所在文件：CP.xlsx", exact=True)).to_be_visible()
        for target in current["appendix"][0]["scope"]["rule_targets"]:
            assert target["rule_id"] in scope.inner_text()
        expect(scope.get_by_text(re.compile("适用审核要求：CP.xlsx"))).to_be_visible()
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as removed:
            deletion.get_by_role("button", name="仅移除说明", exact=True).click()
        assert removed.value.status == 200
        assert len(submitted) == 2 and submitted[-1]["operation_id"] == removal["operation_id"]
        completed = env["mcp_rpc"](token, "tasks/get", {"taskId": removal_task}, capabilities)["result"]
        assert completed["status"] == "completed"
        current = env["store"].read_deep_feedback(env["principal"], env["round_id"])
        assert current["appendix"] == [] and current["input_changed"]
        assert len(current["results"]) == 2  # Save-only removal preserves both analyses.
        context.close()
        browser.close()


def confirmation_payload(env, *, intent="save_only"):
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    question = view["clarifications"][0]
    candidate = question["candidates"][0]
    return {
        "operation_id": str(uuid4()),
        "expected_revision": view["revision"],
        "question_id": question["id"],
        "expected_question_fingerprint": question["fingerprint"],
        "candidate_id": candidate["id"],
        "kind": "interpretation",
        "content": candidate["explanation"],
        "scope": question["scope"],
        "intent": intent,
        "confirmation_source": "synthetic",
    }


@pytest.mark.parametrize("failure", ["question_changed", "expired", "concurrent", "revoked"])
def test_changed_question_concurrent_save_and_revoked_access_never_claim_success(deep_feedback_env, tmp_path, failure):
    from playwright.sync_api import expect, sync_playwright

    env = deep_feedback_env
    payload = confirmation_payload(env)
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        submitted = []

        def mark(route):
            value = {**route.request.post_data_json, "confirmation_source": "synthetic"}
            submitted.append(value)
            route.continue_(post_data=json.dumps(value, ensure_ascii=False))

        page.route("**/audit-rounds/*/rule-changes", mark)
        page.goto(f"{web}/audit?round={env['round_id']}")
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        panel.get_by_role("radio", name="按工序编号理解").click()
        if failure == "concurrent":
            # Another authorized application entry saves the same question.
            assert env["client"].post(f"/audit-rounds/{env['round_id']}/rule-changes", json=payload).status_code == 200
        else:
            # Removing object grants is an administrative fixture action;
            # the production API role must not gain DELETE permission.
            with connect(env["settings"], "migrator") if failure == "revoked" else env["store"]._conn() as conn:
                if failure == "revoked":
                    # Legacy pre-Task fixture roots are tenant-readable. Make
                    # this fault fixture use the actual Task grant boundary.
                    conn.execute(
                        "UPDATE product.audit_drafts SET task_fingerprint='auth-revocation',task_day=current_date,task_sequence=1 "
                        "WHERE id=(SELECT source_draft_id FROM product.audit_rounds WHERE id=%s)",
                        (env["round_id"],),
                    )
                    conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (env["round_id"], env["principal"].user_id))
                else:
                    coverage = conn.execute("SELECT coverage FROM product.audit_rounds WHERE id=%s", (env["round_id"],)).fetchone()[0]
                    if failure == "expired":
                        coverage["clarifications"] = []
                    else:
                        coverage["clarifications"][0]["candidates"][0]["explanation"] += "（已改变）"
                    conn.execute("UPDATE product.audit_rounds SET coverage=%s WHERE id=%s", (Jsonb(coverage), env["round_id"]))
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as rejected:
            panel.get_by_role("button", name="确认解释并继续", exact=True).click()
        assert rejected.value.status == (403 if failure == "revoked" else 409)
        expect(panel.get_by_text("解释暂未更新", exact=True)).to_be_visible()
        expect(panel.get_by_text(re.compile("本次修改未应用"))).to_be_visible()
        expect(panel.get_by_text("已保存并受理新分析", exact=True)).to_have_count(0)
        assert len(submitted) == 1 and "ruleChange=" not in page.url
        if failure == "revoked":
            expect(panel.get_by_role("radiogroup", name="解释候选")).to_have_count(0)
        else:
            panel.get_by_role("button", name="读取最新解释与分析", exact=True).click()
            if failure in {"expired", "concurrent"}:
                expect(panel.get_by_text("当前没有待确认解释", exact=True)).to_be_visible()
            else:
                expect(panel.get_by_role("textbox", name="拟保存的补充说明")).to_have_value("")
                expect(panel.get_by_role("button", name="确认解释并继续", exact=True)).to_be_disabled()
        with env["store"]._conn() as conn:
            assert conn.execute("SELECT count(*) FROM product.deep_rule_changes WHERE workspace_round_id=%s", (env["round_id"],)).fetchone()[0] == (1 if failure == "concurrent" else 0)
            assert conn.execute("SELECT count(*) FROM product.audit_rounds WHERE predecessor_id=%s", (env["round_id"],)).fetchone()[0] == 0
        page.screenshot(path=str(tmp_path / f"deep-feedback-{failure}-mobile.png"), full_page=True)
        context.close()
        browser.close()


@pytest.mark.parametrize("after_admission", [False, True])
def test_atomic_save_and_start_failure_keeps_unknown_and_reads_back_no_save(deep_feedback_env, tmp_path, monkeypatch, after_admission):
    from playwright.sync_api import expect, sync_playwright

    env = deep_feedback_env
    original_accept = env["store"]._accept_locked

    def fail(*args, **kwargs):
        if after_admission:
            original_accept(*args, **kwargs)
        raise RuntimeError("synthetic failure at the atomic admission boundary")

    monkeypatch.setattr(env["store"], "_accept_locked", fail)
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        submitted = []

        def mark(route):
            value = {**route.request.post_data_json, "confirmation_source": "synthetic"}
            submitted.append(value)
            route.continue_(post_data=json.dumps(value, ensure_ascii=False))

        page.route("**/audit-rounds/*/rule-changes", mark)
        page.goto(f"{web}/audit?round={env['round_id']}")
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        panel.get_by_role("radio", name="按工序编号理解").click()
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as failed:
            panel.get_by_role("button", name="确认解释并继续", exact=True).click()
        assert failed.value.status == 500
        expect(panel.get_by_text("操作结果待核对", exact=True)).to_be_visible()
        panel.get_by_role("button", name="读取原操作", exact=True).click()
        expect(panel.get_by_text("尚未读到原操作结果。当前状态仍未确定，可以稍后继续读取。", exact=True)).to_be_visible()
        expect(panel.get_by_role("button", name="确认解释并继续", exact=True)).to_be_disabled()
        expect(panel.get_by_text("已保存并受理新分析", exact=True)).to_have_count(0)
        assert len(submitted) == 1
        view = env["client"].get(f"/audit-rounds/{env['round_id']}/deep-feedback").json()
        assert view["revision"] == 0 and view["appendix"] == [] and view["operations"] == []
        assert len(view["results"]) == 1
        with env["store"]._conn() as conn:
            assert conn.execute("SELECT count(*) FROM product.audit_rounds WHERE predecessor_id=%s", (env["round_id"],)).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM product.deep_rule_changes WHERE workspace_round_id=%s", (env["round_id"],)).fetchone()[0] == 0
        context.close()
        browser.close()


def test_custom_explanation_is_saved_exactly_without_a_default_candidate_or_calculation(deep_feedback_env, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    env = deep_feedback_env
    content = "仅确认本次 CP 文件的作业单元编号表示工序编号。其他内容仍须核对。"
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        submitted = []

        def mark(route):
            value = {**route.request.post_data_json, "confirmation_source": "synthetic"}
            submitted.append(value)
            route.continue_(post_data=json.dumps(value, ensure_ascii=False))

        page.route("**/audit-rounds/*/rule-changes", mark)
        page.goto(f"{web}/audit?round={env['round_id']}")
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        panel.get_by_role("radio", name="填写其他解释", exact=True).focus()
        page.keyboard.press("Space")
        panel.get_by_role("textbox", name="拟保存的补充说明").focus()
        page.keyboard.type(content)
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as saved:
            panel.get_by_role("button", name="仅保存说明", exact=True).focus()
            page.keyboard.press("Enter")
        assert saved.value.status == 200
        assert saved.value.json()["analysis_round_id"] is None
        assert len(submitted) == 1 and submitted[0]["content"] == content
        assert "candidate_id" not in submitted[0]
        view = env["client"].get(f"/audit-rounds/{env['round_id']}/deep-feedback").json()
        assert len(view["appendix"]) == len(view["operations"]) == 1 and len(view["results"]) == 1
        assert view["appendix"][0]["content"] == content
        assert [finding["status"] for finding in view["results"][0]["findings"]] == ["UNABLE", "UNABLE", "PASS"]
        with env["store"]._conn() as conn:
            assert conn.execute("SELECT count(*) FROM product.audit_rounds WHERE predecessor_id=%s", (env["round_id"],)).fetchone()[0] == 0
        expect(panel.get_by_text("说明已保存。下方结果使用此前输入，新的依据尚未产生分析结果。", exact=True)).to_be_visible()
        context.close()
        browser.close()


@pytest.mark.parametrize("precise_scope", [True, False], ids=["document-rule-target", "legacy-document-rule-scope"])
def test_same_rule_id_on_two_targets_keeps_the_question_evidence_in_its_document_scope(deep_feedback_env, tmp_path, precise_scope):
    from playwright.sync_api import expect, sync_playwright

    """Local UI projection regression; the full parser/PG journey is tested above."""
    env = deep_feedback_env
    cp1, pfd = env["uploaded"]["CP"], env["uploaded"]["PFD"]
    data = env["documents"]["CP"]["bytes"]
    created = env["client"].post("/files", json={"original_filename": "CP2.xlsx", "content_type": "application/octet-stream", "byte_size": len(data)})
    assert created.status_code == 201
    cp2 = created.json()
    path = f"/files/{cp2['file_id']}/versions/{cp2['id']}"
    assert env["client"].put(path + "/blob", content=data).status_code == 200
    assert env["client"].post(path + "/finalize").status_code == 200
    draft = env["client"].post("/drafts").json()["id"]
    items = [
        {"file_version_id": cp1["id"], "role": "target", "rule_version_id": env["rule"]["id"]},
        {"file_version_id": cp2["id"], "role": "target", "rule_version_id": env["rule"]["id"]},
        {"file_version_id": pfd["id"], "role": "reference"},
    ]
    assert env["client"].put(f"/drafts/{draft}", json={"items": items, "analysis_mode": "deep"}).status_code == 200
    accepted = env["client"].post(f"/drafts/{draft}/accept", json={"idempotency_key": str(uuid4())})
    assert accepted.status_code == 201
    round_id = accepted.json()["id"]
    projection = env["store"].read_deep_feedback(env["principal"], round_id)
    scope = {"document_ids": [cp2["id"]], "rule_ids": ["R1"]}
    if precise_scope:
        scope["rule_targets"] = [{"document_id": cp2["id"], "rule_id": "R1"}]
    projection["clarifications"] = [
        {
            "id": "cp2-question",
            "input_id": cp2["id"],
            "fingerprint": "controlled-ui-projection",
            "kind": "interpretation",
            "question": "仅确认 CP2 的编号解释",
            "document_ids": [cp2["id"]],
            "rule_ids": ["R1"],
            "candidates": [{"id": "cp2-candidate", "label": "CP2 编号含义", "explanation": "仅用于 CP2。"}],
            "sources": [{"document_id": cp2["id"], "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "A1"}, "excerpt": "作业单元编号"}],
            "scope": scope,
        }
    ]
    wrong = {"file_version_id": cp1["id"], "filename": "CP.xlsx", "role": "target", "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "B2"}, "excerpt": "其他目标文件的依据"}
    correct = {"file_version_id": pfd["id"], "filename": "PFD.xlsx", "role": "reference", "locator": {"kind": "xlsx_cell", "sheet": "PFD", "cell": "A2"}, "excerpt": "010"}
    # Both targets use R1. An unassigned R1 also cannot supply sources by
    # matching the rule alone. The actual target identity is inside locator.
    projection["results"][0]["findings"] = [
        {"item_key": f"{cp1['id']}:rule:R1", "locator": {"file_version_id": cp1["id"], "rule_item_id": "R1", "semantic": {"sources": [wrong]}}},
        {"item_key": "unassigned:R1", "locator": {"rule_item_id": "R1", "semantic": {"sources": [wrong]}}},
        {"item_key": f"{cp2['id']}:rule:R1", "locator": {"file_version_id": cp2["id"], "rule_item_id": "R1", "semantic": {"sources": [correct]}}},
    ]
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        page.route(f"**/audit-rounds/{round_id}/deep-feedback", lambda route: route.fulfill(json=projection))
        try:
            page.goto(f"{web}/audit?round={round_id}")
            panel = page.get_by_role("region", name="解释与重新分析", exact=True)
            expect(panel).to_be_visible(timeout=15_000)
            comparison = panel.get_by_role("region", name="待确认问题的原件对照", exact=True)
            evidence = comparison.locator('[aria-label^="解释证据 "]')
            expect(evidence).to_have_count(2, timeout=15_000)
            expect(evidence.nth(0).get_by_text("CP2.xlsx", exact=True).first).to_be_visible()
            expect(evidence.nth(1).get_by_text("PFD.xlsx", exact=True).first).to_be_visible()
            expect(evidence.nth(1).get_by_text("PFD · A2", exact=True)).to_be_visible()
            expect(comparison.get_by_text("CP.xlsx", exact=True)).to_have_count(0)
            expect(comparison.get_by_text("其他目标文件的依据", exact=True)).to_have_count(0)
            affected = panel.get_by_role("list", name="受影响的文件与要求")
            expect(affected.get_by_role("listitem")).to_have_count(1)
            expect(affected.get_by_text("CP2.xlsx · R1", exact=True)).to_be_visible()
            # The real workbook parser has a 30-second execution limit. Wait for
            # its actual table instead of applying Playwright's default 5 seconds.
            expect(evidence.nth(0).get_by_role("table", name="CP 单元格内容", exact=True)).to_be_visible(timeout=35_000)
            expect(evidence.nth(1).get_by_role("table", name="PFD 单元格内容", exact=True)).to_be_visible(timeout=35_000)
            page.screenshot(path=str(tmp_path / "scope-evidence-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            expect(evidence.nth(1).get_by_text("PFD · A2", exact=True)).to_be_visible()
            assert evidence.nth(1).bounding_box()["y"] >= evidence.nth(0).bounding_box()["y"] + evidence.nth(0).bounding_box()["height"]
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(tmp_path / "scope-evidence-mobile.png"), full_page=True)
        except Exception:
            # Keep the original failure, with secret-free fixture UI evidence.
            # These reads never resubmit an application operation.
            try:
                (tmp_path / "scope-failure-dom.txt").write_text(page.locator("body").aria_snapshot(timeout=5_000))
                page.screenshot(path=str(tmp_path / "scope-failure.png"), full_page=True, timeout=10_000)
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()


def test_real_two_target_results_name_each_file_and_keep_cp2_confirmation_scoped(deep_feedback_env, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    """Real API/PG/Worker/core results for two targets sharing one rule version."""
    env, client = deep_feedback_env, deep_feedback_env["client"]
    cp1 = env["uploaded"]["CP"]
    raw = env["documents"]["CP"]["bytes"]
    created = client.post("/files", json={"original_filename": "CP2.xlsx", "content_type": "application/octet-stream", "byte_size": len(raw)})
    assert created.status_code == 201, created.text
    cp2 = created.json()
    path = f"/files/{cp2['file_id']}/versions/{cp2['id']}"
    assert client.put(path + "/blob", content=raw).status_code == 200
    assert client.post(path + "/finalize").status_code == 200
    draft = client.post("/drafts").json()["id"]
    target_ids = [cp1["id"], cp2["id"]]
    items = [{"file_version_id": identity, "role": "target", "rule_version_id": env["rule"]["id"]} for identity in target_ids]
    items.append({"file_version_id": env["uploaded"]["PFD"]["id"], "role": "reference"})
    assert client.put("/drafts/" + draft, json={"items": items, "analysis_mode": "deep"}).status_code == 200
    accepted = client.post("/drafts/" + draft + "/accept", json={"idempotency_key": str(uuid4())})
    assert accepted.status_code == 201, accepted.text
    round_id = accepted.json()["id"]
    env["run_jobs"]()
    initial = client.get("/rounds/" + round_id).json()
    unresolved = {"R1": "UNABLE", "R2": "UNABLE", "R3": "PASS"}
    for identity in target_ids:
        assert {row["locator"]["rule_item_id"]: row["status"] for row in initial["findings"] if row["locator"]["file_version_id"] == identity} == unresolved
    view = client.get(f"/audit-rounds/{round_id}/deep-feedback").json()
    assert len(view["clarifications"]) == 2
    question_index = next(at for at, question in enumerate(view["clarifications"]) if question["document_ids"] == [cp2["id"]])
    question = view["clarifications"][question_index]
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        submitted = []

        def mark(route):
            payload = {**route.request.post_data_json, "confirmation_source": "synthetic"}
            submitted.append(payload)
            route.continue_(post_data=json.dumps(payload, ensure_ascii=False))

        page.route("**/audit-rounds/*/rule-changes", mark)
        page.goto(f"{web}/audit?round={round_id}")
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        panel.get_by_role("navigation", name="待确认解释", exact=True).get_by_role("button").nth(question_index).click()
        comparison = panel.get_by_role("region", name="待确认问题的原件对照", exact=True)
        expect(comparison.get_by_text("CP2.xlsx", exact=True).first).to_be_visible()
        expect(comparison.get_by_role("table", name="CP 单元格内容", exact=True)).to_be_visible(timeout=35_000)
        expect(comparison.get_by_role("table", name="PFD 单元格内容", exact=True)).to_be_visible(timeout=35_000)
        panel.get_by_role("radio", name="按工序编号理解").click()
        expect(panel.get_by_role("checkbox", name="CP2.xlsx", exact=True)).to_be_checked()
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as saved:
            panel.get_by_role("button", name="确认解释并继续", exact=True).click()
        assert saved.value.status == 200, saved.value.text()
        receipt = saved.value.json()
        assert len(submitted) == 1
        assert submitted[0]["question_id"] == question["id"]
        assert submitted[0]["scope"] == question["scope"]
        assert submitted[0]["scope"]["document_ids"] == [cp2["id"]]
        env["run_jobs"]()
        panel.get_by_role("button", name="读取新分析结果", exact=True).click()
        current = panel.get_by_role("region", name="当前分析判断", exact=True)
        expect(current.get_by_text("新输入分析已结束。", exact=True)).to_be_visible(timeout=15_000)
        expect(current.get_by_role("article")).to_have_count(6)
        expected = {cp1["id"]: unresolved, cp2["id"]: {"R1": "PASS", "R2": "NG", "R3": "PASS"}}
        labels = {"PASS": "满足", "NG": "不符合", "UNABLE": "无法判断"}
        filenames = {cp1["id"]: "CP.xlsx", cp2["id"]: "CP2.xlsx"}
        for identity, statuses in expected.items():
            for rule_id, status in statuses.items():
                name = f"{filenames[identity]} · {rule_id}"
                finding = current.get_by_role("article", name=name, exact=True)
                expect(finding.get_by_text(name, exact=True)).to_be_visible()
                expect(finding.get_by_text(labels[status], exact=True)).to_be_visible()
                change = f"此前：无法判断 → 当前：{labels[status]}" if identity == cp2["id"] and rule_id != "R3" else f"结论未变：{labels[status]}"
                expect(finding.get_by_text(change, exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="此前输入的审核结果", exact=True)).to_be_visible()
        actual = client.get("/rounds/" + receipt["analysis_round_id"]).json()
        assert {item["rule"]["id"] for item in actual["inputs"] if item["role"] == "target"} == {env["rule"]["id"]}
        for identity, statuses in expected.items():
            assert {row["locator"]["rule_item_id"]: row["status"] for row in actual["findings"] if row["locator"]["file_version_id"] == identity} == statuses
        assert client.get("/rounds/" + round_id).json()["findings"] == initial["findings"]
        stored = client.get(f"/audit-rounds/{round_id}/deep-feedback").json()
        assert len(stored["operations"]) == len(stored["appendix"]) == 1 and len(stored["results"]) == 2
        assert stored["appendix"][0]["scope"]["document_ids"] == [cp2["id"]]
        with env["store"]._conn() as conn:
            assert conn.execute("SELECT count(*) FROM engine.jobs WHERE audit_round_id=%s AND kind='audit'", (receipt["analysis_round_id"],)).fetchone()[0] == 1
        page.screenshot(path=str(tmp_path / "multi-target-results-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        current.screenshot(path=str(tmp_path / "multi-target-results-mobile.png"))
        assert len(submitted) == 1
        context.close()
        browser.close()


@pytest.mark.parametrize("deep_feedback_env", ["compiled_candidate_model"], indirect=True)
def test_compiled_judge_candidate_requires_confirmation_and_explicit_new_analysis(deep_feedback_env, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    """A compiled Judge question uses the existing real browser feedback path."""
    env, client = deep_feedback_env, deep_feedback_env["client"]
    root = env["round_id"]
    initial = client.get("/rounds/" + root).json()
    assert [row["status"] for row in initial["findings"]] == ["UNABLE", "NG", "PASS"]
    question = client.get(f"/audit-rounds/{root}/deep-feedback").json()["clarifications"][0]
    rule_text = env["rule"]["body"]["rules"][0]["rule_to_check"]
    assert question["scope"]["rule_targets"] == [{"document_id": env["uploaded"]["CP"]["id"], "rule_id": "R1"}]
    assert env["model"].semantic_answers == []

    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        external = local_browser_boundary(context, web)
        context.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        page = context.new_page()
        submitted = []

        def mark(route):
            payload = {**route.request.post_data_json, "confirmation_source": "synthetic"}
            submitted.append(payload)
            route.continue_(post_data=json.dumps(payload, ensure_ascii=False))

        page.route("**/audit-rounds/*/rule-changes", mark)
        try:
            page.goto(f"{web}/audit?round={root}")
            panel = page.get_by_role("region", name="解释与重新分析", exact=True)
            radios = panel.get_by_role("radiogroup", name="解释候选")
            expect(radios.get_by_role("radio", checked=True)).to_have_count(0)
            expect(radios.get_by_role("radio", name="按工序编号逐字对应")).to_be_visible()
            expect(radios.get_by_role("radio", name="改为设备编号标准")).to_be_visible()
            expect(panel.get_by_role("textbox", name="拟保存的补充说明")).to_have_value("")
            expect(panel.get_by_role("button", name="确认解释并继续", exact=True)).to_be_disabled()
            expect(panel.get_by_text("候选不会自动生效，请先选择或填写解释。", exact=True)).to_be_visible()
            affected = panel.get_by_role("list", name="受影响的文件与要求")
            expect(affected.get_by_role("listitem")).to_have_count(1)
            expect(affected.get_by_text("CP.xlsx · R1", exact=True)).to_be_visible()
            source_rule = panel.get_by_role("region", name="待确认解释的规则原文", exact=True)
            expect(source_rule.get_by_text("R1 · " + rule_text, exact=True)).to_be_visible()
            comparison = panel.get_by_role("region", name="待确认问题的原件对照", exact=True)
            expect(comparison.get_by_text("CP · A1", exact=True)).to_be_visible()
            expect(comparison.get_by_text("PFD · A1", exact=True)).to_be_visible()
            expect(comparison.get_by_role("table", name="CP 单元格内容", exact=True)).to_be_visible(timeout=35_000)
            expect(comparison.get_by_role("table", name="PFD 单元格内容", exact=True)).to_be_visible(timeout=35_000)
            expect(comparison.get_by_role("cell", name="A1：作业单元编号", exact=True)).to_be_visible()
            expect(comparison.get_by_role("cell", name="A1：工序编号", exact=True)).to_be_visible()
            details = page.get_by_role("region", name="审核详情", exact=True)
            original_r1 = details.get_by_role("row").filter(has=page.get_by_role("cell", name="R1", exact=True))
            original_r2 = details.get_by_role("row").filter(has=page.get_by_role("cell", name="R2", exact=True))
            expect(original_r1.get_by_role("cell", name="无法判断", exact=True)).to_be_visible()
            expect(original_r2.get_by_role("cell", name="不符合", exact=True)).to_be_visible()
            assert submitted == [] and env["model"].semantic_answers == []
            page.screenshot(path=str(tmp_path / "judge-candidate-unconfirmed-desktop.png"), full_page=True)

            radios.get_by_role("radio", name="按工序编号逐字对应").click()
            expect(panel.get_by_role("checkbox", name="CP.xlsx", exact=True)).to_be_checked()
            expect(panel.get_by_role("checkbox", name="PFD.xlsx", exact=True)).to_be_checked()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(tmp_path / "judge-candidate-selected-mobile.png"), full_page=True)
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as saved:
                panel.get_by_role("button", name="仅保存说明", exact=True).click()
            assert saved.value.status == 200, saved.value.text()
            assert saved.value.json()["analysis_round_id"] is None
            assert len(submitted) == 1
            assert submitted[0]["candidate_id"] == "literal"
            assert submitted[0]["scope"] == question["scope"]
            expect(panel.get_by_text("说明已保存。下方结果使用此前输入，新的依据尚未产生分析结果。", exact=True)).to_be_visible()
            expect(original_r1.get_by_role("cell", name="无法判断", exact=True)).to_be_visible()
            assert env["model"].semantic_answers == []
            assert client.get("/rounds/" + root).json()["findings"] == initial["findings"]

            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as analyzed:
                panel.get_by_role("button", name="按已保存说明开始分析", exact=True).click()
            assert analyzed.value.status == 200, analyzed.value.text()
            receipt = analyzed.value.json()
            assert receipt["analysis_round_id"] != root
            env["run_jobs"]()
            panel.get_by_role("button", name="读取新分析结果", exact=True).click()
            expect(panel.get_by_text("当前没有待确认解释", exact=True)).to_be_visible()
            current = panel.get_by_role("region", name="当前分析判断", exact=True)
            expect(current.get_by_role("article", name="CP.xlsx · R1", exact=True).get_by_text("此前：无法判断 → 当前：满足", exact=True)).to_be_visible()
            expect(current.get_by_role("article", name="CP.xlsx · R2", exact=True).get_by_text("结论未变：不符合", exact=True)).to_be_visible()
            expect(current.get_by_role("article", name="CP.xlsx · R3", exact=True).get_by_text("结论未变：满足", exact=True)).to_be_visible()
            expect(current.get_by_text(submitted[0]["content"], exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name="此前输入的审核结果", exact=True)).to_be_visible()
            actual = client.get("/rounds/" + receipt["analysis_round_id"]).json()
            assert [row["status"] for row in actual["findings"]] == ["PASS", "NG", "PASS"]
            assert len(env["model"].semantic_answers) == 2
            history = client.get("/rounds/" + root).json()
            assert history["findings"] == initial["findings"] and history["inputs"] == initial["inputs"]
            stored = client.get(f"/audit-rounds/{root}/deep-feedback").json()
            assert len(stored["appendix"]) == 1 and len(stored["results"]) == 2
            assert stored["appendix"][0]["confirmation_source"] == "synthetic"
            assert len(submitted) == 2 and external == []
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.screenshot(path=str(tmp_path / "judge-candidate-confirmed-desktop.png"), full_page=True)
        except Exception:
            try:
                (tmp_path / "judge-candidate-failure-dom.txt").write_text(page.locator("body").aria_snapshot(timeout=5_000))
                page.screenshot(path=str(tmp_path / "judge-candidate-failure.png"), full_page=True, timeout=10_000)
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()
