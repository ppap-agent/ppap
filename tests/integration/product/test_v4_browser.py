"""Actual browser/API/PG/Worker journeys with explicit Standard Suite adoption.

Metadata transport and Core RPC/guest OS boundaries are controlled. No business
HTTP response or Checker result is mocked. These cases do not certify process
isolation, external model quality, Feishu delivery, charging, or visual judgments.
"""

import io
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from api import create_app
from openpyxl import Workbook
from product import build_product_store
from product.identity import SESSION_COOKIE
from product.settings import Settings
from worker import process_claimed

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402


class Metadata:
    def __init__(self, customer):
        self.customer = customer

    def structured_completion(self, *, operation, **kwargs):
        if operation == "document_metadata_extraction":
            return json.dumps({"customer": self.customer, "part_number": "P12345"})
        if operation == "document_classification":
            return "过程特殊特性清单"
        raise AssertionError("Unexpected external operation in a native XLSX journey")


def original(customer):
    book = Workbook()
    sheet = book.active
    sheet.title = "特殊特性清单"
    sheet.append(["客户", customer])
    if customer == "UAES":
        sheet.append(["物料名称", "支架"])
        sheet.append(["物料编号", "P12345"])
        sheet.append(["编号", "产品特性", "特性及公差", "特性类型"])
        sheet.append([1, "孔径", None, "CC"])
    elif customer == "理想":
        sheet.append(["零件名称", "支架", "零件号", "P12345"])
        sheet.append(["供应商名称", None, "供应商编码", "S01"])
        columns = ["过程/流程步骤", "部件(来料检时涉及)", "客户要求/功能要求", "特性名称", "检验岗位", "产品/过程特性分类", "名义值/规格中心值", "规格下限", "规格上限"]
        controls = ["测量设备", "测量方式", "防错控制", "检查控制", "抽样频率", "SPC监控", "SPC线上管理计划时间", "说明（关键要求/操作参数等）"]
        sheet.append(columns + ["T1-特性监控方法"] + [None] * 7 + ["现有管理方式风险评估", "高风险管理项预防措施"])
        sheet.append([None] * 9 + controls)
        sheet.merge_cells("J4:Q4")
        for column in (*range(1, 10), 18, 19):
            sheet.merge_cells(start_row=4, end_row=5, start_column=column, end_column=column)
        sheet.append(["10 压装", "衬套", "定位", "孔径", "检验", "产品", 10, 9.9, 10.1] + ["适用"] * 8 + ["高风险", "定位防错"])
    else:
        sheet.append(["P12345 特殊特性清单"])
        names = ["零件编号", "零件名称", "特性分类", "特性描述", "特性值", "特性类别", "特殊特性种类", "控制方法（制造过程）", "防错方案描述", "频次", "样本数"]
        sheet.append(names)
        sheet.append(["P12345", "支架", "产品", "孔径", "10±0.1", "CC", "CC", "量规", "定位", "每班", None])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


@pytest.fixture
def running_product(auth_store_factory, v4_objects, core_system, tmp_path, request):
    customer = request.param
    settings = Settings(usage_charge_enabled=False, feishu_app_id="native-v4", git_sha="a" * 40)
    admin = auth_store_factory(settings)
    admin.objects = v4_objects
    principal = admin.upsert_feishu_identity("browser-v4", "member")
    suites = {"UAES": ("UAES:U12",), "理想": ("理想:L12",), "蔚来": ("蔚来:N04", "蔚来:N02")}
    core_system.adopt_standard(admin, principal, *suites[customer])
    core_system.install(admin, guest_process=True)
    api = build_product_store(settings, objects=admin.objects, role="api")
    worker = build_product_store(settings, objects=admin.objects, role="worker")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    api_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(settings, auth_store=api), log_level="warning"))
    api_thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    api_thread.start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        web_port = probe.getsockname()[1]
    web = f"http://127.0.0.1:{web_port}"
    log = (tmp_path / "web.log").open("w+")
    request.node.web_log = tmp_path / "web.log"
    root = Path(__file__).resolve().parents[3]
    vite = subprocess.Popen(
        ["pnpm", "exec", "vite", "--host", "127.0.0.1", "--port", str(web_port), "--strictPort"],
        cwd=root / "apps/web",
        env={**os.environ, "PPAP_PREVIEW": "0", "PPAP_PUBLIC_API_URL": api_url},
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    stop = threading.Event()
    errors = []

    def work():
        while not stop.wait(0.05):
            try:
                job = worker.claim_job("native-browser")
                if job:
                    started = time.monotonic()
                    result = process_claimed(worker, job, "native-browser", rule_ai=Metadata(customer))
                    if job.kind == "audit":
                        failures = [{key: row.get(key) for key in ("status", "suggestions", "item_key")} for row in result.get("findings", []) if row.get("status") == "ERR"]
                        if result.get("error_code") or failures:
                            print(f"audit execution diagnostics: {result.get('error_code') or failures}", flush=True)
                    print(f"worker job {job.kind} took {time.monotonic() - started:.2f}s", flush=True)
            except Exception as error:
                errors.append(type(error).__name__)
                return

    worker_thread = threading.Thread(target=work, daemon=True)
    try:
        deadline = time.monotonic() + 45
        while True:
            try:
                if httpx.get(web + "/runtime-config.json", timeout=1).status_code == 200 and server.started:
                    break
            except httpx.HTTPError:
                pass
            assert vite.poll() is None, "Native Web did not start"
            assert time.monotonic() < deadline, "Native product server startup timed out"
            time.sleep(0.1)
        worker_thread.start()
        yield web, admin.create_session(principal), errors
    finally:
        stop.set()
        if worker_thread.ident:
            worker_thread.join()
        server.should_exit = True
        api_thread.join()
        try:
            os.killpg(vite.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            vite.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(vite.pid, signal.SIGKILL)
            vite.wait(timeout=5)
        listener.close()
        log.close()
        assert not errors, errors


def print_page(page, *, asset_diagnostics=(), screenshot=None):
    """Collect independent failure evidence without replacing the test exception."""
    for snapshot in asset_diagnostics:
        try:
            print("Browser asset diagnostics:", json.dumps(snapshot()), flush=True)
        except Exception:
            print("Browser asset diagnostics unavailable", flush=True)
    try:
        print(f"Page at failure: {page.url}", flush=True)
    except Exception:
        print("Page URL at failure unavailable", flush=True)
    try:
        document = page.locator("body").evaluate(
            """() => ({
                readyState: document.readyState, appChildren: document.getElementById('app')?.childElementCount ?? null,
                bodyTextLength: document.body.innerText.length, bodyAriaHidden: document.body.getAttribute('aria-hidden')
            })""",
            timeout=1000,
        )
        print("Document at failure:", json.dumps(document), flush=True)
    except Exception:
        print("Document at failure unavailable", flush=True)
    try:
        print(page.locator("body").aria_snapshot(timeout=1000)[:4000], flush=True)
    except Exception:
        print("Page ARIA at failure unavailable", flush=True)
    if screenshot is not None:
        try:
            page.screenshot(path=screenshot, full_page=True)
        except Exception:
            print("Page screenshot at failure unavailable", flush=True)


@pytest.mark.parametrize("running_product", ["UAES", "理想", "蔚来"], indirect=True)
def test_customer_intake_to_original_issue_and_frozen_precheck_export(running_product, core_system, request, observe_browser_assets):
    web, session, errors = running_product
    customer = request.node.callspec.params["running_product"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        context.add_cookies([{"name": SESSION_COOKIE, "value": session, "url": web, "httpOnly": True}])
        page = context.new_page()
        asset_diagnostics = [observe_browser_assets(page, web)]
        page.set_default_timeout(20000)
        try:
            page.goto(web + "/tasks/new")
            picker = page.get_by_role("combobox", name="审核客户", exact=True)
            expect(picker).to_be_visible()
            picker.click()
            page.get_by_role("option", name="理想" if customer != "理想" else "蔚来", exact=True).click()
            picker.click()
            page.get_by_role("option", name="UAES（联电）" if customer == "UAES" else customer, exact=True).click()
            default = page.request.get(web + "/api/audit-customers/default")
            assert default.status == 200 and default.json()["customer_preference"] is None
            if customer == "UAES":
                page.get_by_role("button", name="设为企业默认", exact=True).click()
                dialog = page.get_by_role("dialog", name="更改企业默认客户？")
                dialog.get_by_role("button", name="取消", exact=True).click()
                assert page.request.get(web + "/api/audit-customers/default").json() == default.json()
                page.get_by_role("button", name="设为企业默认", exact=True).click()
                page.get_by_role("button", name="确认更改", exact=True).click()
                expect(page.get_by_role("status")).to_contain_text("已保存企业默认")
                saved_default = page.request.get(web + "/api/audit-customers/default").json()
                assert saved_default == {"customer_preference": "UAES", "revision": 1}
                page.goto(web + "/tasks/new")
                expect(picker).to_contain_text("UAES（联电）")
                picker.click()
                page.get_by_role("option", name="理想", exact=True).click()
                assert page.request.get(web + "/api/audit-customers/default").json() == saved_default
                picker.click()
                page.get_by_role("option", name="UAES（联电）", exact=True).click()
            source_bytes = original(customer)
            with page.expect_file_chooser() as choose:
                page.get_by_role("button", name="选择本地文件", exact=True).click()
            choose.value.set_files({"name": "特殊特性清单.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": source_bytes})
            expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
            page.get_by_role("button", name="开始识别", exact=True).click()
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_enabled(timeout=30000)
            snapshot_region = page.get_by_role("region", name="免费内容预检", exact=True)
            expect(snapshot_region).to_be_visible()
            expect(snapshot_region).to_contain_text("检查已完成", timeout=30000)
            # Polling and reload read the same actual database record.
            page.reload()
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_enabled()
            with page.expect_download() as downloaded:
                page.get_by_role("button", name="导出此预检记录", exact=True).click()
            export = Path(downloaded.value.path()).read_text()
            assert "预检" in export and "特殊特性清单.xlsx" in export
            snapshot_region.get_by_role("button", name="读取预检历史", exact=True).click()
            expect(snapshot_region.get_by_role("list", name="预检历史", exact=True)).to_be_visible()
            snapshot_region.get_by_role("button", name=re.compile("^修订 0")).click()
            page.get_by_role("button", name="开始审核", exact=True).click()
            result_file = page.get_by_role("button", name=re.compile(r"^特殊特性清单\.xlsx 不符合"))
            expect(result_file).to_be_visible(timeout=30000)
            # After the audit starts the precheck stays reachable behind its record button.
            expect(page.get_by_role("region", name="免费内容预检", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="预检记录", exact=True)).to_be_visible()
            result_file.click()
            page.get_by_role("group", name="审核结果筛选", exact=True).get_by_role("button", name=re.compile("^不符合")).click()
            page.get_by_role("button", name=re.compile("^查看依据 ")).first.click()
            expect(page.get_by_role("article", name="条款判定依据", exact=True)).to_be_visible()
            page.get_by_role("button", name="核对 特殊特性清单.xlsx", exact=True).first.click()
            preview = page.get_by_role("region", name="原件预览", exact=True)
            expect(preview).to_be_visible()
            download_link = preview.get_by_role("link", name="下载原件", exact=True)
            expect(download_link).to_be_visible()
            original_response = page.request.get(web + download_link.get_attribute("href") if download_link.get_attribute("href").startswith("/") else download_link.get_attribute("href"))
            assert original_response.status == 200
            assert original_response.body() == source_bytes
            page.keyboard.press("Escape")
            page.get_by_role("button", name="报告中心", exact=True).click()
            report_download = page.get_by_role("button", name="下载当前报告", exact=True)
            expect(report_download).to_be_enabled(timeout=30000)
            with page.expect_download() as report_file:
                report_download.click()
            assert Path(report_file.value.path()).read_bytes().startswith(b"%PDF")
            page.reload()
            expect(page.get_by_role("region", name="逐文件审核报告", exact=True)).to_be_visible()
            if customer == "UAES":
                old_url = page.url
                tasks = page.request.get(web + "/api/audit-tasks").json()
                old_tasks = tasks.get("tasks", [])
                assert old_tasks
                frozen_task = next(row for row in old_tasks if row.get("round_id"))
                frozen = page.request.get(web + "/api/audit-tasks/" + frozen_task["id"]).json()
                fresh = context.new_page()
                asset_diagnostics.append(observe_browser_assets(fresh, web))
                fresh.goto(web + "/tasks/new")
                expect(fresh.get_by_role("combobox", name="审核客户", exact=True)).to_contain_text("UAES（联电）")
                fresh.get_by_role("combobox", name="审核客户", exact=True).click()
                fresh.get_by_role("option", name="理想", exact=True).click()
                fresh.get_by_role("button", name="设为企业默认", exact=True).click()
                fresh.get_by_role("button", name="确认更改", exact=True).click()
                expect(fresh.get_by_role("status")).to_contain_text("已保存企业默认")
                assert fresh.request.get(web + "/api/audit-customers/default").json() == {"customer_preference": "理想", "revision": 2}
                fresh.close()
                restored = page.request.get(web + "/api/audit-tasks/" + frozen_task["id"]).json()
                assert restored["customer_preference"] == frozen["customer_preference"] == "UAES"
                assert restored["round_id"] == frozen["round_id"] and restored["precheck"]["input"] == frozen["precheck"]["input"]
                assert page.url == old_url
            assert core_system.calls >= 2, "Precheck and audit must both execute through Core"
            assert not errors, errors
        except BaseException:
            print_page(page, asset_diagnostics=asset_diagnostics, screenshot=str(Path("/tmp") / f"ppap-v4-real-browser-{customer}.png"))
            raise
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("running_product", ["蔚来"], indirect=True)
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 1000}, {"width": 390, "height": 844}], ids=["desktop", "mobile"])
def test_cross_file_problem_opens_both_authorized_frozen_originals(running_product, core_system, viewport):
    web, session, errors = running_product
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport=viewport)
        context.add_cookies([{"name": SESSION_COOKIE, "value": session, "url": web, "httpOnly": True}])
        page = context.new_page()
        page.set_default_timeout(20000)
        originals = []
        try:
            for title, rows in [
                (
                    "PFMEA",
                    [
                        ["过程步骤", "特殊特性", "过程项功能", "失效影响", "严重度", "现行探测控制", "探测度"],
                        ["10 成型", "KPC", "内部：压装 客户工厂：装车 最终用户：乘坐", "内部：返工（4） 客户工厂：返工（6） 最终用户：异响（5）", 6, "检查（6）", 6],
                    ],
                ),
                ("过程流程图", [["工序号", "工序名称", "产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别"], [10, "成型", "孔径", "CC", "温度", "SC"]]),
            ]:
                book = Workbook()
                book.active.title = title
                for row in [["零件号", "P12345", "版本", "A"], ["客户名称", "蔚来"], *rows]:
                    book.active.append(row)
                stream = io.BytesIO()
                book.save(stream)
                book.close()
                data = stream.getvalue()
                response = page.request.post(web + "/api/files", data={"original_filename": title + ".xlsx", "byte_size": len(data)})
                assert response.status == 201
                file = response.json()
                path = f"{web}/api/files/{file['file_id']}/versions/{file['id']}"
                assert page.request.put(path + "/blob", data=data).status == 200
                assert page.request.post(path + "/finalize").status == 200
                import uuid

                validation_id = str(uuid.uuid4())
                validation = page.request.post(web + "/api/file-validations", data={"request_id": validation_id, "source_file_id": file["file_id"], "source_file_version_id": file["id"]})
                assert validation.status == 202
                deadline = time.monotonic() + 20
                while page.request.get(web + "/api/file-validations/" + validation_id).json()["status"] != "succeeded":
                    assert time.monotonic() < deadline
                    page.wait_for_timeout(50)
                originals.append((title, data, {"file_version_id": file["id"], "validation_id": validation_id, "selection": "selected", "removal_reason": None, "reference_only": title != "PFMEA"}))
            response = page.request.post(
                web + "/api/audit-tasks", data={"request_id": str(uuid.uuid4()), "analysis_mode": "standard", "customer_preference": "蔚来", "files": [file for _, _, file in originals]}
            )
            assert response.status == 202, response.text()
            page.goto(web + "/tasks/" + response.json()["id"])
            expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_enabled(timeout=30000)
            expect(page.get_by_role("region", name="免费内容预检", exact=True)).to_contain_text("检查已完成", timeout=30000)
            page.get_by_role("button", name="开始审核", exact=True).click()
            target = page.get_by_role("button", name=re.compile(r"^PFMEA\.xlsx 不符合"))
            expect(target).to_be_visible(timeout=30000)
            target.click()
            basis = page.get_by_role("button", name=re.compile(r"^查看依据 N02:7\b"))
            basis.click()
            page.get_by_role("button", name="同时核对关联原件", exact=True).click()
            comparison = page.get_by_role("region", name="关联原件对照", exact=True)
            expect(comparison).to_be_visible()
            for title, data, file in originals:
                pane = comparison.get_by_role("region", name="关联原件 " + title + ".xlsx", exact=True)
                expect(pane).to_have_attribute("data-file-version", file["file_version_id"])
                expect(pane.get_by_role("link", name="下载原件", exact=True)).to_be_visible()
            # The sheet changes width while opening. Compare both panes in one
            # layout after that animation, instead of mixing different frames.
            boxes = comparison.evaluate(
                """async (element, versions) => {
                    const sheet = element.closest('[data-slot="sheet-content"]');
                    await Promise.all(sheet.getAnimations().map(animation => animation.finished.catch(() => {})));
                    const panes = Array.from(element.querySelectorAll('[data-file-version]'));
                    return versions.map(version => panes.find(pane => pane.dataset.fileVersion === version).getBoundingClientRect().toJSON());
                }""",
                [file["file_version_id"] for _, _, file in originals],
            )
            assert all(boxes)
            if viewport["width"] >= 1024:
                assert abs(boxes[0]["y"] - boxes[1]["y"]) <= 2
                assert boxes[0]["x"] + boxes[0]["width"] <= boxes[1]["x"] + 2
            else:
                assert boxes[1]["y"] >= boxes[0]["y"] + boxes[0]["height"]
                assert abs(boxes[0]["x"] - boxes[1]["x"]) <= 2
            page.get_by_role("button", name="返回判定依据", exact=True).click()
            expect(page.get_by_role("button", name="同时核对关联原件", exact=True)).to_be_focused()
            page.keyboard.press("Escape")
            expect(basis).to_be_focused()
            for title, data, file in originals:
                page.get_by_role("button", name=re.compile(r"^查看依据 N02:7\b")).click()
                page.get_by_role("button", name="核对 " + title + ".xlsx", exact=True).first.click()
                pane = page.get_by_role("region", name="原件预览", exact=True)
                expect(pane).to_have_attribute("data-file-version", file["file_version_id"])
                link = pane.get_by_role("link", name="下载原件", exact=True)
                expect(link).to_be_visible()
                from urllib.parse import urljoin

                response = page.request.get(urljoin(web, link.get_attribute("href")))
                assert response.status == 200 and response.body() == data
                page.keyboard.press("Escape")
                expect(pane).to_be_hidden()
                expect(basis).to_be_focused()
            assert core_system.calls >= 2, "Precheck and audit must both execute through Core"
            assert not errors, errors
        except Exception:
            print_page(page)
            raise
        finally:
            context.close()
            browser.close()
