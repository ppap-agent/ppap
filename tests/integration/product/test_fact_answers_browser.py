"""Native Web, real HTTP and PostgreSQL for in-round human fact confirmation.

The source checker observes a controlled null at the model boundary. Answering
uses the production deterministic recipe, with no Controller or model request.
"""

import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from api import create_app
from product.identity import SESSION_COOKIE
from product.store.checker_development import CheckerDevelopmentStore

from .test_fact_answers import freeze_fact_source, prepare_fact_round

ROOT = Path(__file__).resolve().parents[3]


def test_confirm_original_fact_in_native_history(auth_store_factory, tmp_path, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    store, api, alice, round_id, version, observed = prepare_fact_round(auth_store_factory, tmp_path, monkeypatch)
    freeze_fact_source(store, alice, round_id, version)
    original = api.get_round(round_id)
    question = api.read_fact_answers(alice, round_id)["questions"][0]
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    api_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(store.settings, auth_store=api), log_level="warning"))
    api_thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    api_thread.start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        web_port = probe.getsockname()[1]
    web = f"http://127.0.0.1:{web_port}"
    with (tmp_path / "web.log").open("w+") as log:
        vite = subprocess.Popen(
            ["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(web_port), "--strictPort"],
            cwd=ROOT / "apps/web",
            env={**os.environ, "PPAP_PREVIEW": "0", "PPAP_PUBLIC_API_URL": api_url},
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            until = time.monotonic() + 45
            while True:
                try:
                    if httpx.get(web + "/runtime-config.json", timeout=1).status_code == 200 and server.started:
                        break
                except httpx.HTTPError:
                    pass
                assert vite.poll() is None, "Native Web did not start"
                assert time.monotonic() < until, "Native product startup timed out"
                time.sleep(0.1)
            with sync_playwright() as playwright:
                executable = os.environ.get("PPAP_BROWSER_EXECUTABLE")
                browser = playwright.chromium.launch(**({"executable_path": executable} if executable else {}))
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": SESSION_COOKIE, "value": store.create_session(alice), "url": web, "httpOnly": True}])
                page = context.new_page()
                page.goto(web + f"/audit?round={round_id}&file={version['id']}")
                section = page.get_by_role("region", name="原件事实确认", exact=True)
                section.get_by_text("原件事实确认", exact=True).click()
                prompt = section.get_by_role("article", name=question["prompt"], exact=True)
                expect(prompt).to_contain_text("第 1 页")
                prompt.get_by_role("radio", name="是", exact=True).check()
                prompt.get_by_role("button", name="保存回答", exact=True).click()
                result = section.get_by_role("region", name="人工确认结果", exact=True)
                expect(result).to_be_visible()
                expect(result).to_contain_text("原机器结果：无法判断")
                expect(result).to_contain_text("人工确认")
                expect(result).to_contain_text("符合")
                expect(result).to_contain_text(alice.user_id)
                expect(result).to_contain_text("第 1 页")
                saved = api.read_fact_answers(alice, round_id)
                assert saved["answers"][0]["value"] is True
                assert saved["results"][0]["status"] == "PASS"
                assert saved["model_calls"] == 0
                assert api.get_round(round_id) == original
                assert observed == ["model observation"]
                assert CheckerDevelopmentStore(api).list_feedback(alice, round_id)["feedback"] == []
                result.get_by_role("button", name="作为优化样本", exact=True).click()
                result.get_by_role("radio", name="符合", exact=True).check()
                result.get_by_role("textbox", name="样本判定依据", exact=True).fill("已对照原件确认SOP勾选，作为明确的条款预期")
                result.get_by_role("button", name="确认加入优化样本", exact=True).click()
                expect(result).to_contain_text("已加入优化样本，原机器结果保留。")
                sample = CheckerDevelopmentStore(api).list_feedback(alice, round_id)["feedback"]
                assert len(sample) == 1
                assert sample[0]["original_status"] == "UNABLE" and sample[0]["expected_status"] == "PASS"
                assert saved["answers"][0]["operation_id"] in sample[0]["rationale"]
                assert api.get_round(round_id) == original
                assert observed == ["model observation"]
                with page.expect_download() as download:
                    section.get_by_role("link", name="下载人工确认报告", exact=True).click()
                assert download.value.suggested_filename == "original-fact-confirmations.pdf"
                assert Path(download.value.path()).read_bytes().startswith(b"%PDF")
                page.reload()
                section.get_by_text("原件事实确认", exact=True).click()
                expect(result).to_be_visible()
                expect(result).to_contain_text(alice.user_id)
                expect(prompt).to_have_count(0)
                assert len(api.read_fact_answers(alice, round_id)["answers"]) == 1
                assert CheckerDevelopmentStore(api).list_feedback(alice, round_id)["feedback"] == sample
                page.set_viewport_size({"width": 390, "height": 844})
                expect(result).to_contain_text("原机器结果：无法判断")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                browser.close()
        finally:
            server.should_exit = True
            api_thread.join(timeout=15)
            listener.close()
            try:
                os.killpg(vite.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                vite.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(vite.pid, signal.SIGKILL)
                vite.wait(timeout=5)
