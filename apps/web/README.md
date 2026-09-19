# Web Client

浏览器/PWA 与飞书 H5 使用同一个 PPAP 产品客户端。isolated-dev 通过 `python tools/stack.py up` 启动本地 Web。

## 当前 main

当前 Web 提供 isolated-dev 审核工作台：上传并固化多个待审/参考文件版本、选择规则、提交轮次、回访结果与原文、记录复核、显式重审。飞书 docs-addon 入口是 `addon.html`（`app.json` `blockTypeID blk_69eb1e6ab880ccc61d0520b2`）：宿主登录 → 当前文档关联文件 → Partner AI 额度门闩 → 普通 Feishu 提交/回访/写回。宿主 SDK 使用 vendored `@lark-opdev/block-docs-addon-api@0.0.11`，仅在 iframe 宿主中加载；测试可注入 `window.__PPAP_DOCS_ADDON_HOST__`。历史审核记录在 `audit-files.html`，列出当前主体可读取的普通 round。飞书客户功能 parity 由 #11 验收。

## 最终责任

- 产品导航和呈现
- 文件/资料集准备
- 审核提交和结果回访
- 原文定位与下一步
- 人工复核
- 用量/历史视图
- 浏览器与飞书宿主的必要平台适配

## Boundary

Web 只调用 Product API。它不能：

- 调 Worker / legacy engine tasks API
- 直接访问 PostgreSQL/PostgREST
- 持有对象存储管理凭据
- 建立飞书专属业务台账
- 嵌入服务端 Feishu / Partner AI Secret

UI 可以在不降低 #11 baseline 客户能力的前提下重构；具体前端工具链见 [DEVELOPMENT](../../docs/DEVELOPMENT.md)。
