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
    "/mcp/": "MCP 授权与连接管理本身",
    "/intents/": "外部客户端提议的签字动作，由本人在网页完成或拒绝",
    "/receipts": "回执在网页查看与下载；MCP 读回签字提议的结果时附回执编号与链接",
    "/receipts/": "回执在网页查看与下载；MCP 读回签字提议的结果时附回执编号与链接",
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
    "/integrations/feishu/rounds/{}/report/retry": "交付人在网页结果页补设只读、补附报告 PDF；MCP 读回的交付记录写明哪一步没有完成",
    "/integrations/feishu/root-folder": "网页上传另存到飞书个人空间",
    "/files": "字节上传只在网页；MCP 的文件只来自飞书",
    "/files/": "字节上传、下载与预览只在网页；MCP 的文件只来自飞书",
    "/file-uploads/": "字节上传只在网页；MCP 的文件只来自飞书",
    "/drafts": "网页的上传准备草稿；MCP 由 prepare_audit 在服务端准备",
    "/drafts/": "网页的上传准备草稿；MCP 由 prepare_audit 在服务端准备",
    "/rounds/{}/files/": "原件下载与预览只在网页",
    "/rounds/{}/reaudit-inputs": "网页重审沿用上一轮文件；MCP 的复审来自新的飞书资料",
    "/rounds/{}/review-document-pairs": "网页复核时人工配对两份文件",
    "/rounds/{}/signoff/withdraw": "撤回签发由签发人本人在网页写明原因完成；MCP 只提议签发（sign_round）",
    "/audit-task-events": "网页实时推送；MCP 用 get_task",
    "/audit-tasks/{}/events": "网页实时推送；MCP 用 get_task",
    "/audit-tasks/{}/recognitions/{}": "识别回执已含在 get_task 的任务里",
    "/audit-tasks/{}/display-info": "网页编辑结果展示名",
    "/audit-tasks/{}/display-info/{}": "网页编辑结果展示名",
    "/audit-tasks/{}/precheck/report": "预检报告 PDF 下载只在网页；MCP 把预检问题列为 get_task 的 issues",
    "/audit-tasks/{}/reports/{}/pdf": "报告 PDF 下载只在网页；MCP 用 publish_report 交付到飞书",
    "/audit-tasks/{}/reports/{}/regenerations/{}": "重新生成的进度已含在 get_task 的报告状态里",
    "/audit-tasks/{}/charge": "结果页的本轮扣点；MCP 在报价里给出点数，签字回执附本轮扣点",
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
    "/template-library": "模板下载只在网页",
    "/template-library/": "模板下载只在网页",
    "/template-submissions": "网页提交模板需求",
    "/rules": "网页规则页只读列出规则；发布是平台运营的内部写入口",
    "/rules/": "网页规则页只读查看规则；解析是平台运营的内部写入口",
    "/rule-questionnaires/cards": "单步发卡留给线下与网页；MCP 的澄清按计划一次确认（/rulebooks/{}/plan）",
    "/rulebooks/{}/revisions": "单步写修订留给线下导入与网页；MCP 的澄清按计划一次确认（/rulebooks/{}/plan）",
    "/rulebooks/{}/{}/{}/clarified": "单步标已澄清留给网页；MCP 的澄清按计划一次确认（/rulebooks/{}/plan）",
    "/rulebooks/{}/{}/{}/readings": "单步发布解读留给网页；MCP 的澄清按计划一次确认（/rulebooks/{}/plan）",
    "/rule-questionnaires/{}/answers": "规则确认问卷由客户成员在网页作答；MCP 只读答复",
    "/rule-questionnaires/{}/answers/": "规则确认问卷由客户成员在网页作答；MCP 只读答复",
    "/rule-questionnaires/{}/answer-alerts": "「这个版本不再提醒」是本人在网页规则页的设置",
    "/suites": "平台运营查看与采用检查程序的内部入口",
    "/suites/": "平台运营查看与采用检查程序的内部入口",
    "/suite-revisions/": "平台运营查看检查程序修订的内部入口",
}


def _normal(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def _routes() -> set[str]:
    return {_normal(path) for file in SOURCE.glob("*.py") for path in ROUTE.findall(file.read_text(encoding="utf-8"))}


def _reached() -> set[str]:
    # A tool path is a literal, or a literal base plus a literal suffix (p + "/pdf").
    # Tools state what they do; the runtime runs the task's effects. Both reach the Product API.
    text = "".join((SOURCE / name).read_text(encoding="utf-8") for name in ("mcp_tools.py", "mcp_runtime.py", "mcp_clarification.py"))
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
