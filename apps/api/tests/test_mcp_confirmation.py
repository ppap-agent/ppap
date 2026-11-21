"""An approved effect cannot borrow another grant, operation or route."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from api.mcp_confirmation import require_approved_source
from product.identity import Principal, Session
from product.mcp import ApprovedCall, Delegation, McpError, fingerprint
from starlette.requests import Request


def approved_request():
    session = Session(Principal("user", "tenant", "feishu", "app", "org", "subject", "member"), datetime.now(timezone.utc))
    delegation = Delegation("grant", "client", frozenset({"reports:send"}), session)
    source = {"review": {"version": 1, "rationale": "approved"}}
    proof = ApprovedCall("grant", "operation", "POST", "/send", (("/rounds/source", fingerprint(source)),))
    scope = {"type": "http", "method": "POST", "path": "/send", "ppap.delegation": delegation, "ppap.mcp_operation_id": "operation", "ppap.mcp_approval": proof}
    return scope, proof, source


@pytest.mark.parametrize("field", ["grant_id", "operation_id", "method", "path"])
def test_other_approved_call_cannot_authorize_effect(field):
    scope, proof, source = approved_request()
    scope["ppap.mcp_approval"] = replace(proof, **{field: "different"})
    with pytest.raises(McpError, match="^invalid_confirmation$") as caught:
        require_approved_source(Request(scope), "/rounds/source", source)
    assert caught.value.status == 403


@pytest.mark.parametrize("proof", [None, "approved", {"grant_id": "grant", "operation_id": "operation"}])
def test_delegated_effect_requires_internal_proof(proof):
    scope, _, source = approved_request()
    scope["ppap.mcp_approval"] = proof
    with pytest.raises(McpError, match="^invalid_confirmation$"):
        require_approved_source(Request(scope), "/rounds/source", source)


def test_same_source_required_but_web_keeps_its_existing_authority():
    scope, _, source = approved_request()
    require_approved_source(Request(scope), "/rounds/source", source)
    with pytest.raises(McpError, match="^confirmation_content_changed$") as caught:
        require_approved_source(Request(scope), "/rounds/source", {"review": {"version": 2, "rationale": "edited"}})
    assert caught.value.status == 409
    with pytest.raises(McpError, match="^confirmation_content_changed$"):
        require_approved_source(Request(scope), "/rounds/other", source)
    del scope["ppap.delegation"]
    scope["headers"] = [(b"ppap.mcp_approval", b"approved")]
    require_approved_source(Request(scope), "/rounds/source", {"review": "web"})


def test_authorization_pages_take_every_colour_from_the_design_tokens_and_run_no_script():
    import re

    from api.mcp_pages import lines, page

    html = page("确认这次操作", lines(["开始审核：共 2 份文件。", "· a.pdf", "· <b>.xlsx", "预计扣减 3 点。"])).body.decode()
    style = re.search(r"<style>(.*)</style>", html, re.S)[1]
    # The only literal colours are the token values themselves, declared as variables for light and dark.
    declared = set(re.findall(r"--color-[\w-]+:(#[0-9a-f]{6})", style))
    assert declared and set(re.findall(r"#[0-9a-fA-F]{3,8}\b", style)) <= declared
    assert "--color-primary:#1456f0" in style and "prefers-color-scheme:dark" in style
    assert "<script" not in html and "http" not in style
    assert "<ul><li>a.pdf</li><li>&lt;b&gt;.xlsx</li></ul><p>预计扣减 3 点。</p>" in html


def test_an_approval_page_shows_what_the_web_shows_at_the_same_decision():
    from api.mcp_pages import details, page
    from api.mcp_views import audit_start

    task = {
        "number": "PPAP-1",
        "customer_preference": "UAES",
        "estimated_points": 1000,
        "deep_mode": False,
        "confirmation_scope": {"target_file_version_ids": ["v1", "v2"], "reference_file_version_ids": [], "excluded_files": [{"file_version_id": "v3", "reason": "没有对应的审核要求"}]},
        "files": [
            {
                "file": {"id": "v1", "original_filename": "控制计划.pdf"},
                "match": {"document_type": "控制计划", "metadata": {"part_number": "F01", "part": "磁性罩"}, "rule": {"title": "控制计划审核规则"}},
            },
            {"file": {"id": "v2", "original_filename": "MSA.xls"}, "match": {"document_type": "测量系统分析评价", "metadata": {}, "rule": {"title": "MSA 审核规则"}}},
            {"file": {"id": "v3", "original_filename": "<说明>.docx"}, "match": {"state": "unmatched"}},
        ],
    }
    precheck = {"blocks": [], "warnings": [{"file_version_id": "v2", "filename": "MSA.xls", "problem": "公式错误值", "action": "修正公式"}]}
    summary, blocks = audit_start(task, precheck, {"v1": "测试文件/10 控制计划", "v2": "测试文件/06 测量系统分析评价"})
    html = page("确认这次操作", details(blocks), wide=True).body.decode()
    assert summary == ["开始审核：送审 2 份文件，预计扣 1000 点。"]
    assert "测试文件/" not in html  # the folder every file shares is not repeated on each row
    for shown in (
        "送审的文件 · 2 份",
        "10 控制计划",
        "控制计划审核规则",
        "F01",
        "磁性罩",
        "预检发现 · 1 项",
        "修正公式",
        "不送审的文件 · 1 份",
        "&lt;说明&gt;.docx",
        "有条件通过 · 1 项警告：可以开始审核",
    ):
        assert shown in html, shown


def test_long_clauses_and_reasons_are_set_as_points_and_lists_without_changing_a_word():
    from api.mcp_pages import prose

    clause = "须有关键工序标记；须有图例<b>；如果无法辨识则不通过"
    assert prose(clause) == "<ol><li>须有关键工序标记；</li><li>须有图例&lt;b&gt;；</li><li>如果无法辨识则不通过</li></ol>"
    reason = "13 个工序的生产资源为空：压盖入库、原材料入库、存储材料等。"
    assert prose(reason) == "13 个工序的生产资源为空：<ul><li>压盖入库</li><li>原材料入库</li><li>存储材料</li></ul><span class=muted>等。</span>"
    assert prose("一句话") == "一句话"
