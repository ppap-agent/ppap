# Web Client

浏览器/PWA 与飞书 H5 使用同一个 PPAP 产品客户端。isolated-dev 通过 `docker compose -f deploy/isolated-dev/compose.yaml up --build -d --wait` 启动本地 Web。

## 当前 main

当前 Web 是可演示的 PC 产品入口（工作台 / 审核文件 / 用量记录），不是旧轻量页面外套一层 shell。工作台覆盖准备资料、选择规则、提交、PASS/NG/ERR、依据与模型建议分栏、原件预览、文件版本、历史轮次切换、人工复核和显式重审。审核文件页提供搜索/执行筛选、版本与结果摘要。数据只走 Product API（`product-api.js`），含 provider-neutral `GET /usage`。飞书 docs-addon 入口是 `addon.html`（`app.json` `blockTypeID blk_69eb1e6ab880ccc61d0520b2`，客户路径 `/addon`）：已有 PPAP session 则复用 `/me`，否则宿主登录 → 当前文档关联文件 → Partner AI 额度门闩 → 普通 Feishu 提交/回访；PASS/NG 完成后自动写回当前文档。宿主 SDK 使用 vendored `@lark-opdev/block-docs-addon-api@0.0.11`，仅在 iframe 宿主中加载；测试可注入 `window.__PPAP_DOCS_ADDON_HOST__`。历史审核记录客户路径是 `/audit-files`，用量记录是 `/usage`。飞书客户功能 parity 由 #11 验收。PC 演示路径由 #57 验收。

## 最终责任

- 产品导航和呈现
- 文件/资料集准备
- 审核提交和结果回访
- 原文定位、预览与下一步
- 人工复核
- 用量/历史视图
- 浏览器与飞书宿主的必要平台适配

`ppap-host` 不再承担 current PC UI 开发责任。

## Boundary

Web 只调用 Product API。它不能：

- 调 Worker / legacy engine tasks API
- 直接访问 PostgreSQL/PostgREST 或 InsForge Database/Storage SDK
- 持有对象存储管理凭据
- 建立飞书专属业务台账或第二套 ledger
- 嵌入服务端 Feishu / Partner AI Secret

UI 可以在不降低 #11 baseline 客户能力的前提下重构；具体前端工具链见 [DEVELOPMENT](../../docs/DEVELOPMENT.md)。
