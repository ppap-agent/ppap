"""Every user route is either reached by an MCP tool or deliberately left to the Web, with the reason.

A new route makes this fail until someone decides which one it is; that is how MCP stays current with the product.
"""

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "api"
ROUTE = re.compile(r'@(?:app|router)\.(?:get|post|put|patch|delete|api_route)\(\s*"([^"]+)"')
LITERAL = re.compile(r'f?"(/[^"\s]*)"')

# Routes MCP does not reach, and why. A key ending in "/" covers every route under it; any other key is one route.
LEFT_TO_WEB = {
    "/integrations/meego/": "飞书项目插件与 AI 节点自己的入口",
    "/login/": "网页登录",
    "/logout": "网页登录",
    "/oauth/": "MCP 授权本身",
    "/mcp/": "MCP 授权、确认与连接管理本身",
    "/isolated-dev/": "开发环境",
    "/health": "运维探活",
    "/ready": "运维探活",
    "/version": "运维版本",
    "/capabilities": "部署能力自检",
    "/public-config": "网页启动配置",
    "/enter-current": "网页版本切换",
    "/enter-legacy": "网页版本切换",
    "/support": "网页客服二维码",
    "/support/qr": "网页客服二维码",
    "/office/content": "网页内嵌 Office 预览",
    "/integrations/feishu/events": "飞书事件回调",
    "/integrations/feishu/jsapi-config": "飞书客户端网页 JSAPI",
    "/integrations/feishu/docs/": "飞书云文档插件",
    "/integrations/feishu/tasks/{}/document": "飞书云文档插件",
    "/integrations/feishu/rounds/{}/writeback": "飞书云文档插件把结论写回它所在的文档",
    "/integrations/feishu/root-folder": "网页上传另存到飞书个人空间",
    "/files": "字节上传只在网页；MCP 的文件只来自飞书",
    "/files/": "字节上传、下载与预览只在网页；MCP 的文件只来自飞书",
    "/file-uploads/": "字节上传只在网页；MCP 的文件只来自飞书",
    "/drafts": "网页的上传准备草稿；MCP 由 prepare_audit 在服务端准备",
    "/drafts/": "网页的上传准备草稿；MCP 由 prepare_audit 在服务端准备",
    "/rounds": "网页轮次列表；MCP 用 find_audits",
    "/rounds/{}/files/": "原件下载与预览只在网页",
    "/rounds/{}/reaudit-inputs": "网页重审沿用上一轮文件；MCP 的复审来自新的飞书资料",
    "/rounds/{}/review-document-pairs": "网页复核时人工配对两份文件",
    "/audit-task-events": "网页实时推送；MCP 用 get_task",
    "/audit-tasks/{}/events": "网页实时推送；MCP 用 get_task",
    "/audit-tasks/{}/recognitions/{}": "识别回执已含在 get_task 的任务里",
    "/audit-tasks/{}/display-info": "网页编辑结果展示名",
    "/audit-tasks/{}/display-info/{}": "网页编辑结果展示名",
    "/audit-tasks/{}/precheck/report": "预检报告 PDF 下载只在网页；MCP 把预检问题列为 get_task 的 issues",
    "/audit-tasks/{}/reports/{}/pdf": "报告 PDF 下载只在网页；MCP 用 publish_report 交付到飞书",
    "/audit-tasks/{}/reports/{}/regenerations/{}": "重新生成的进度已含在 get_task 的报告状态里",
    "/audit-rounds/{}/fact-answers/": "答复回执与确认报告下载；MCP 按请求 ID 读回",
    "/audit-archive": "网页按文档归档检索；MCP 用 find_audits",
    "/audit-archive/": "网页按文档归档检索；MCP 用 find_audits",
    "/audit-report-packages": "报告打包下载只在网页",
    "/audit-report-packages/": "报告打包下载只在网页",
    "/audit-report-shares": "按人分享报告在网页；MCP 交付的是飞书报告文档",
    "/audit-report-shares/": "按人分享报告在网页；MCP 交付的是飞书报告文档",
    "/audit-share-targets": "按人分享报告在网页；MCP 交付的是飞书报告文档",
    "/review-hints/responses": "网页复核提示统计",
    "/audit-customers/default": "网页记住的默认客户",
    "/audit-usage": "网页费用明细",
    "/audit-usage/export": "网页导出 CSV",
    "/usage": "网页费用明细",
    "/template-library": "模板下载只在网页",
    "/template-library/": "模板下载只在网页",
    "/template-submissions": "网页提交模板需求",
    "/rule-library": "规则只由平台运营在网页维护",
    "/rule-types": "规则只由平台运营在网页维护",
    "/rules": "规则只由平台运营在网页维护",
    "/rules/": "规则只由平台运营在网页维护",
    "/rule-questionnaires": "规则确认问卷由客户成员在网页作答，平台运营在网页导入、关闭和导出",
    "/rule-questionnaires/": "规则确认问卷由客户成员在网页作答，平台运营在网页导入、关闭和导出",
    "/suites": "检查程序只由平台运营在网页发布",
    "/suites/": "检查程序只由平台运营在网页发布",
    "/suite-revisions/": "检查程序只由平台运营在网页发布",
}


def _normal(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def _routes() -> set[str]:
    return {_normal(path) for file in SOURCE.glob("*.py") for path in ROUTE.findall(file.read_text(encoding="utf-8"))}


def _reached() -> set[str]:
    # A tool path is a literal, or a literal base plus a literal suffix (p + "/pdf").
    # Tools state what they do; the runtime runs the task's effects. Both reach the Product API.
    text = "".join((SOURCE / name).read_text(encoding="utf-8") for name in ("mcp_tools.py", "mcp_runtime.py"))
    literals = {_normal(path) for path in LITERAL.findall(text)}
    return literals | {base + suffix for base in literals for suffix in literals}


def _covers(key: str, route: str) -> bool:
    return route.startswith(key) if key.endswith("/") else route == key


def _left(route: str) -> bool:
    return any(_covers(key, route) for key in LEFT_TO_WEB)


def test_every_route_is_reached_by_mcp_or_left_to_the_web_with_a_reason():
    undecided = sorted(route for route in _routes() if route not in _reached() and not _left(route))
    assert undecided == [], "decide for each new route: reach it from an MCP tool, or add it to LEFT_TO_WEB with the reason"


def test_left_routes_still_exist_and_are_not_also_reached():
    routes, reached = _routes(), _reached()
    stale = sorted(key for key in LEFT_TO_WEB if not any(_covers(key, route) for route in routes))
    both = sorted(route for route in routes & reached if _left(route) and not route.startswith("/mcp/"))
    assert stale == [] and both == []
