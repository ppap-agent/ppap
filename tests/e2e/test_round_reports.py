"""Read a frozen Round's existing report from its Task in the authorized native UI."""

import json

from playwright.sync_api import expect, sync_playwright

from .conftest import TASK, VERSION, WEB, round_report_routes


def test_task_opens_its_frozen_existing_report_and_return_preserves_result_context():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1057, "height": 853})
        page.set_default_timeout(5000)
        calls, content = round_report_routes(page)
        address = WEB + f"/tasks/{TASK}?file={VERSION}&clause=1&focus=review&checkFilter=ng"
        page.goto(address)
        expect(page.get_by_role("heading", name="原控制计划.xlsx", exact=True)).to_be_visible()
        expect(page.get_by_role("region", name="专注复核", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="结果数据需要核对", exact=True)).to_have_count(0)
        report = page.get_by_role("button", name="审核报告", exact=True)
        expect(report).to_be_enabled()
        report.click()
        expect(page.get_by_role("toolbar", name="PDF 阅读工具", exact=True)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role("button", name="下载当前报告", exact=True).click()
        with open(download.value.path(), "rb") as source:
            assert source.read() == content
        # Opening the report replaces the task entry; the breadcrumb returns to the same result position.
        page.get_by_role("navigation", name="当前位置", exact=True).get_by_role("link", name="返回审核结果", exact=True).click()
        expect(page).to_have_url(address)
        expect(page.get_by_role("heading", name="原控制计划.xlsx", exact=True)).to_be_visible()
        expect(page.get_by_role("region", name="专注复核", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="第 1 条 · 原签字要求", exact=True)).to_be_visible()
        assert all(method == "GET" for method, _path in calls)
        browser.close()


def test_embedded_native_task_report_keeps_the_selected_report_when_its_host_reloads():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(5000)
        calls, _content = round_report_routes(page)
        initial = f"/tasks/{TASK}?file={VERSION}"
        # The external host only carries the existing public navigation message.
        host = """<iframe title="原生客户端" id="product" style="width:100%;height:90vh"></iframe><script>
        const frame = document.getElementById('product');
        const address = new URL(location.href);
        frame.src = address.searchParams.has('task')
          ? '/tasks/' + encodeURIComponent(address.searchParams.get('task')) + '?report=' + encodeURIComponent(address.searchParams.get('report') || '')
          : INITIAL;
        addEventListener('message', event => {
          if (event.origin !== location.origin || event.source !== frame.contentWindow || event.data?.type !== 'ppap-audit-task') return;
          const next = new URL(location.href);
          next.searchParams.set('task', event.data.task || '');
          next.searchParams.set('report', event.data.report || '');
          history.replaceState(null, '', next);
        });</script>""".replace("INITIAL", json.dumps(initial))
        page.route(WEB + "/round-report-host.html*", lambda route: route.fulfill(body=host, content_type="text/html"))
        page.goto(WEB + "/round-report-host.html")
        product = page.frame_locator("#product")
        product.get_by_role("button", name="审核报告", exact=True).click()
        expect(product.get_by_role("toolbar", name="PDF 阅读工具", exact=True)).to_be_visible()
        expect(page).to_have_url(WEB + f"/round-report-host.html?task={TASK}&report={VERSION}")
        page.reload()
        expect(product.get_by_role("heading", name="原控制计划.xlsx", exact=True)).to_be_visible()
        expect(product.get_by_role("toolbar", name="PDF 阅读工具", exact=True)).to_be_visible()
        assert all(method == "GET" for method, _path in calls)
        browser.close()


def test_failed_round_report_generation_reaches_the_existing_report_recovery_ui():
    state, label = "failed", "生成失败"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 844})
        calls, _content = round_report_routes(page, report_state=state)
        page.goto(WEB + f"/tasks/{TASK}?file={VERSION}")
        page.get_by_role("button", name="审核报告", exact=True).click()
        preview = page.get_by_role("region", name="当前报告", exact=True)
        expect(preview.get_by_role("heading", name="原控制计划.xlsx", exact=True)).to_be_visible()
        expect(preview.get_by_text(label, exact=True)).to_be_visible()
        expect(preview.get_by_role("button", name="下载当前报告", exact=True)).to_be_disabled()
        expect(preview.get_by_text("生成服务暂不可用", exact=True)).to_be_visible()
        expect(preview.get_by_role("button", name="重生成同一结果", exact=True)).to_be_disabled()
        assert not any(path.endswith("/pdf") for _method, path in calls)
        assert all(method == "GET" for method, _path in calls)
        browser.close()


def test_internal_checker_verification_stays_out_of_results_focus_and_report():
    from product.checker_verification import frozen_checker_verification

    verification = frozen_checker_verification({"mode": "checker_core", "suite_revision_id": "frozen-checker-3", "suite_revision_number": 3, "verification": "unverified", "samples_not_rerun": False})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1057, "height": 853})
        page.set_default_timeout(5000)
        round_report_routes(page, verification=verification)
        page.goto(WEB + f"/tasks/{TASK}?file={VERSION}&clause=1")
        details = page.get_by_role("region", name="审核详情")
        expect(details.get_by_role("table")).to_be_visible()

        def expect_no_internal_identity():
            html = page.content()
            for internal in ("样本验证", "frozen-checker-3", "检查版本第", "data-suite-revision", verification["notice"]):
                assert internal not in html, internal
            expect(page.get_by_role("note", name="本轮验证状态")).to_have_count(0)

        expect_no_internal_identity()
        page.get_by_role("button", name="逐条复核", exact=True).click()
        expect(page.get_by_role("region", name="专注复核")).to_be_visible()
        expect_no_internal_identity()
        page.get_by_role("button", name="审核报告", exact=True).click()
        expect(page.get_by_role("region", name="当前报告")).to_be_visible()
        expect_no_internal_identity()
        browser.close()
