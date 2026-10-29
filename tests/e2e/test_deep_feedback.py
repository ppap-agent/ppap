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

import uvicorn
from playwright.sync_api import expect, sync_playwright
from ppap_core.identity import SESSION_COOKIE

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


def test_confirm_explanation_recompute_and_reopen_without_erasing_conflict(deep_feedback_env, tmp_path):
    env = deep_feedback_env
    question = env["store"].read_deep_feedback(env["principal"], env["round_id"])["clarifications"][0]
    assert question["scope"]["rule_targets"]
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
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
        page.screenshot(path=str(tmp_path / "deep-feedback-desktop.png"), full_page=True)

        panel.get_by_role("radio", name="所有候选都不符合", exact=True).click()
        expect(panel.get_by_text("该问题保持未解", exact=True)).to_be_visible()
        assert submitted == []
        panel.get_by_role("radio", name="按工序编号理解").focus()
        page.keyboard.press("Space")
        explanation = panel.get_by_role("textbox", name="拟保存的补充说明")
        expect(explanation).to_have_value(re.compile("作业单元编号"))
        panel.get_by_role("button", name="稍后处理", exact=True).click()
        expect(panel.get_by_role("button", name="查看解释", exact=True)).to_be_focused()
        panel.get_by_role("button", name="查看解释", exact=True).click()
        expect(panel.get_by_role("radio", name="按工序编号理解")).to_be_checked()
        assert submitted == []

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / "deep-feedback-mobile.png"), full_page=True)
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
        actual = env["client"].get("/rounds/" + receipt["analysis_round_id"]).json()
        assert [finding["status"] for finding in actual["findings"]] == ["PASS", "NG", "PASS"]
        assert len(submitted) == 1
        page.screenshot(path=str(tmp_path / "deep-feedback-confirmed-mobile.png"), full_page=True)
        context.close()

        fresh = browser.new_context(viewport={"width": 1440, "height": 1000})
        fresh.add_cookies([{"name": SESSION_COOKIE, "value": env["session_cookie"], "url": web}])
        reopened = fresh.new_page()
        reopened.goto(f"{web}/audit?round={env['round_id']}")
        restored = reopened.get_by_role("region", name="解释与重新分析", exact=True)
        expect(restored.get_by_text("当前没有待确认解释", exact=True)).to_be_visible()
        restored.get_by_text("本次已确认说明（1）", exact=True).click()
        expect(restored.get_by_text(submitted[0]["content"], exact=True)).to_be_visible()
        expect(restored.get_by_role("region", name="当前分析判断").get_by_text("不符合", exact=True)).to_be_visible()
        fresh.close()
        browser.close()


def test_save_only_then_explicit_analysis_does_not_save_the_explanation_twice(deep_feedback_env, tmp_path):
    env = deep_feedback_env
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
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
    env = deep_feedback_env
    with feedback_web(env["app"], tmp_path) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
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
                initial_reads.append(route)
            else:
                route.continue_()

        page.route("**/audit-rounds/*/deep-feedback", hold_initial_read)
        page.reload(wait_until="domcontentloaded")
        panel = page.get_by_role("region", name="解释与重新分析", exact=True)
        expect(panel.get_by_text("正在读取解释问题…", exact=True)).to_be_visible()
        panel.get_by_role("button", name="读取原操作", exact=True).click()
        expect(panel.get_by_text("已保存并受理新分析", exact=True)).to_be_visible()
        assert len(initial_reads) == 1
        initial_reads[0].abort("aborted")
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
    env = deep_feedback_env
    with feedback_web(env["app"], tmp_path, env["settings"]) as web, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
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
        panel.get_by_role("radio", name="按工序编号理解").click()
        page.route("**/audit-rounds/*/rule-changes", lambda route: route.continue_(post_data=json.dumps({**route.request.post_data_json, "confirmation_source": "synthetic"}, ensure_ascii=False)))
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/rule-changes")) as changed:
            panel.get_by_role("button", name="确认解释并继续", exact=True).click()
        assert changed.value.status == 200, changed.value.text()
        env["run_jobs"]()
        panel.get_by_role("button", name="读取新分析结果", exact=True).click()
        expect(panel.get_by_role("region", name="当前分析判断").get_by_text("不符合", exact=True)).to_be_visible()
        result = env["client"].get("/rounds/" + changed.value.json()["analysis_round_id"]).json()
        assert [finding["status"] for finding in result["findings"]] == ["PASS", "NG", "PASS"]
        context.close()
        browser.close()


def test_native_mcp_url_opens_actual_web_confirmation_and_reads_same_operation(deep_feedback_env, tmp_path):
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
