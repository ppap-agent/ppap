"""Narrow widths share the desktop pages: no text is squeezed into a sliver and no page scrolls sideways (#370)."""

import pytest
from playwright.sync_api import expect, sync_playwright

from .conftest import TASK, VERSION, WEB, round_report_routes, route_api_address

WIDTHS = (375, 437, 768, 1024, 1279)
DRAFT = "00000000-0000-4000-8000-0000000000d1"
PRINCIPAL = {
    "display_name": "测试用户",
    "tenant_id": "test-tenant",
    "user_id": "test-user",
    "membership": "member",
    "identity": {"provider": "isolated-dev", "app_id": "test-app", "tenant_key": "test-tenant", "subject": "test-user"},
}
CUSTOMER = {
    "customer_id": "uaes",
    "name": "联电科技/UAES",
    "key": "UAES",
    "coverage": {"version": "V3.33", "effective_from": "2026-11-18", "types": [{"code": "T08", "rule_type": "过程能力研究"}], "template_types": ["T08"], "rule_types": []},
}

# Text shorter than this many line heights is never a squeezed column; a sliver this narrow holds one or two characters.
SQUEEZED = """() => {
  const found = []
  for (const el of document.querySelectorAll('body *')) {
    const text = [...el.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent.trim()).join('')
    if (text.length < 3) continue
    const box = el.getBoundingClientRect()
    if (!box.width || !box.height || getComputedStyle(el).visibility === 'hidden') continue
    const style = getComputedStyle(el)
    const line = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.4
    if (box.width < 70 && box.height > line * 3.2) found.push(`${text.slice(0, 24)} (${Math.round(box.width)}x${Math.round(box.height)})`)
  }
  const width = document.documentElement.scrollWidth
  if (width > innerWidth + 1) found.push(`页面横向溢出 ${width}px > ${innerWidth}px`)
  return found
}"""


def file_entry(version, name, document_type, customer):
    return {
        "file": {
            "id": version,
            "file_id": "file-" + version[-2:],
            "original_filename": name,
            "content_type": "application/pdf",
            "byte_size": 2_921_543,
            "sha256": None,
            "status": "ready",
            "origin": {"available": False},
        },
        "selection": "selected",
        "removal_reason": None,
        "validation_id": "validation-" + version[-2:],
        "validation": {"state": "valid", "reasons": []},
        "match": {
            "state": "unmatched",
            "metadata": {"part": "阀体\nThrottle Housing", "model": None, "project": None, "customer": customer, "part_number": "F01R20Y574-1#\nF01R 20Y 574"},
            "document_type": document_type,
            "rule": None,
            "gaps": ["metadata_uses_excerpt", "matching_document_type_rules_missing"],
        },
    }


# A task whose customer line is long: the case that once squeezed the precheck title into one character per line.
draft = {
    "id": DRAFT,
    "number": "PPAP-20261126-002",
    "creator": {"name": "示例用户", "mine": True},
    "state": "pending_confirm",
    "created_at": "2026-11-26T09:02:36Z",
    "updated_at": "2026-11-26T09:03:50Z",
    "source_draft_id": DRAFT,
    "input_revision": 0,
    "round_id": None,
    "customer_preference": "UAES",
    "notification_enabled": True,
    "notification_revision": 0,
    "files": [
        file_entry("00000000-0000-4000-8000-0000000000f1", "574-PPK 过程能力研究报告（阀体 Throttle Housing 第二版）.pdf", "过程能力研究", "联合电子UAES\nUAES"),
        file_entry("00000000-0000-4000-8000-0000000000f2", "574-527-MSA 测量系统分析评价.pdf", "测量系统分析评价", None),
    ],
    "completion_reason": None,
    "confirmation_basis_digest": None,
    "confirmation_scope": {"target_file_version_ids": [], "reference_file_version_ids": [], "excluded_files": []},
    "estimated_points": 0,
    "deep_mode": False,
    "deep_mode_option": {"available": True, "reason": None},
}
task_row = {key: draft[key] for key in ("id", "input_revision", "created_at", "updated_at", "number", "completion_reason", "round_id", "state", "deep_mode", "creator")}
task_row |= {"files": [{"file": entry["file"], "selection": "selected"} for entry in draft["files"]], "subject": {"customer": "联合电子UAES\nUAES", "model": None, "part": "阀体\nThrottle Housing"}}

ARCHIVED = {
    "document_id": "archived-document",
    "document_type": "过程能力研究",
    "latest_activity": None,
    "latest_completed": None,
    "metadata": {"customer": "联合电子UAES", "model": None, "part": "阀体 Throttle Housing", "part_number": "F01R20Y574-1#"},
    "provider": "feishu",
    "title": "574-PPK 过程能力研究报告（阀体 Throttle Housing 第二版）",
    "updated_at": "2026-11-26T09:03:50Z",
}

ROUTES = {
    "/public-config": {"api_url": WEB + "/api", "profile": "isolated-dev", "login": "isolated-dev", "feishu_app_id": "", "current_access": "allowed", "usage_charge_enabled": False},
    "/me": PRINCIPAL,
    "/audit-customers": {"customers": [CUSTOMER], "unregistered": [], "type_names": {}},
    "/audit-customers/default": {"customer_preference": "UAES", "revision": 0},
    "/audit-tasks": {"tasks": [task_row], "next_offset": None},
    "/audit-tasks/" + DRAFT: draft,
    "/rules": {
        "rules": [
            {
                "id": "00000000-0000-4000-8000-0000000000a5",
                "title": "过程能力研究审核规则（联电科技 UAES 第三版）",
                "kind": "engine",
                "created_at": "2026-11-26T08:32:29Z",
                "predecessor_id": None,
                "customer_scope": "all",
                "type": "过程能力研究",
                "rule_profile": None,
                "customers": [],
                "models": [],
                "parts": [],
                "suite_key": "custom:a5",
                "clause_count": 12,
            }
        ]
    },
    "/template-library": {
        "customers": [
            {
                "id": "uaes",
                "name": "联电科技/UAES",
                "templates": [
                    {
                        "id": "uaes-capability",
                        "name": "过程能力研究报告（初始过程能力与长期能力）",
                        "document_type": "过程能力研究",
                        "extension": ".xlsx",
                        "filename": "过程能力研究报告.xlsx",
                        "byte_size": 96_117,
                        "type_code": "T08",
                    }
                ],
            }
        ]
    },
    "/audit-archive": {
        "query": {"search": "", "category_id": None, "document_type": None, "customer": None, "initiator": None, "from_date": None, "through_date": None, "result": None, "page": 1, "page_size": 10},
        "documents": [ARCHIVED],
        "total": 1,
        "document_count": 1,
        "categories": [],
        "customer_groups": [],
        "document_types": [],
        "initiators": [],
    },
    "/audit-usage": {
        "organization_id": "test-tenant",
        "organization_name": "示例企业",
        "query": {"range": "30d", "search": "", "settlement": "", "page": 1, "page_size": 10},
        "summary": {
            "month": "2026-11",
            "timezone": "Asia/Shanghai",
            "as_of": "2026-11-26T00:00:00Z",
            **{key: {"value": 0, "reason": None} for key in ("consumption_count", "previous_consumption_count", "charged_points", "pending_points")},
            "free_total": {"value": 10000, "reason": None},
            "free_remaining": {"value": 10000, "reason": None},
        },
        "entries": [],
        "total": 0,
        "from_date": "2026-10-29",
        "through_date": "2026-11-27",
        "notices": [],
        "unattributed": {"slice_count": 0, "points": 0, "reason": None},
    },
    "/receipts": {"receipts": [], "next_offset": None},
    "/rule-questionnaires": {"questionnaires": []},
    "/intents": {"intents": []},
    "/rulebooks/uaes": {"revisions": []},
}

# Each page is checked once its own content has rendered, never after a fixed wait.
PAGES = {
    "/tasks": ("button", "PPAP-20261126-002"),
    "/tasks/new": ("heading", "新建审核"),
    "/tasks/" + DRAFT: ("heading", "预检并匹配规则"),
    "/files": ("checkbox", "选择 " + ARCHIVED["title"]),
    "/templates": ("heading", "模板库"),
    "/requirements": ("heading", "审核规则库"),
    "/usage": ("heading", "用量记录"),
    "/receipts": ("heading", "我的回执"),
}
RESULT_PAGES = {
    f"/tasks/{TASK}": ("heading", "原控制计划.xlsx"),
    f"/tasks/{TASK}?file={VERSION}": ("heading", "原控制计划.xlsx"),
    f"/tasks/{TASK}?file={VERSION}&clause=1&focus=review": ("heading", "原控制计划.xlsx"),
    f"/tasks/{TASK}?report=1": ("heading", "审核报告"),
}


def product_routes(page):
    route_api_address(page)

    def api(route):
        path = route.request.url.split("/api", 1)[1].split("?", 1)[0]
        if path in ROUTES:
            route.fulfill(json=ROUTES[path])
        else:
            route.fulfill(status=404, json={"error": "unexpected_test_request"})

    page.route("**/api/**", api)


def squeezed_on(page, address, ready):
    page.goto(WEB + address)
    role, name = ready
    expect(page.get_by_role(role, name=name).first).to_be_visible()
    return [f"{address}: {problem}" for problem in page.evaluate(SQUEEZED)]


@pytest.mark.parametrize("width", WIDTHS)
def test_narrow_pages_keep_text_readable_without_sideways_scroll(width):
    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        product_routes(page)
        for address, ready in PAGES.items():
            problems += squeezed_on(page, address, ready)
        results = context.new_page()
        round_report_routes(results)
        for address, ready in RESULT_PAGES.items():
            problems += squeezed_on(results, address, ready)
        browser.close()
    assert not problems, "\n".join(problems)
