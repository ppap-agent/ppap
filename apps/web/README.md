# Web Client

浏览器/PWA 与飞书 H5 使用同一个 PPAP 产品客户端。isolated-dev 通过 `docker compose -f deploy/isolated-dev/compose.yaml up --build -d --wait` 启动本地 Web。

## 当前 main

当前 Web 是可演示的 PC 产品入口（工作台 / 审核文件 / 用量记录），不是旧轻量页面外套一层 shell。工作台覆盖准备资料、选择规则、提交、PASS/NG/ERR、依据与模型建议分栏、原件预览、文件版本、历史轮次切换、人工复核和显式重审。审核文件页提供搜索/执行筛选、版本与结果摘要。数据只走 Product API（`product-api.js`），含 provider-neutral `GET /usage`。飞书 docs-addon 入口是 `addon.html`（`app.json` `blockTypeID blk_69eb1e6ab880ccc61d0520b2`，客户路径 `/addon`）：已有 PPAP session 则复用 `/me`，否则宿主登录 → 当前文档关联文件 → Partner AI 额度门闩 → 普通 Feishu 提交/回访；PASS/NG 完成后自动写回当前文档。宿主 SDK 使用 vendored `@lark-opdev/block-docs-addon-api@0.0.11`，仅在 iframe 宿主中加载；测试可注入 `window.__PPAP_DOCS_ADDON_HOST__`。历史审核记录客户路径是 `/audit-files`，用量记录是 `/usage`。飞书客户功能 parity 由 #11 验收。PC 演示路径由 #57 验收。

## 规则准备

规则准备入口是 `/rules`：上传 Excel、Word `.docx`、文本 PDF、Markdown 或结构化 JSON → Worker 解析 → 检查/编辑条目、类型、profile 和适用范围 → 确认发布。发布内容对本企业可见，来源原件仍独立检查文件权限。每次发布创建不可变规则版本；编辑已发布规则只会生成新版本，不更新原版本或旧审核快照。

解析复用 `engine.jobs` 保存执行状态与预览结果，不创建第二份规则事实。URL 中的解析任务标识可用于回访，刷新只读取同一任务，不会重新调用模型。租约失联显示“结果未知”，不自动重领；原 Worker 仍可返回迟到结果。用户可以显式新建任务重跑，但原任务可能仍完成，也可能再次消耗模型费用。规则解析每次模型调用超时为 120 秒，不做 SDK 或空响应自动重试。页面在导入、读取和发布期间统一禁用编辑操作，避免迟到响应混入另一个候选版本。

导入和编辑保存客户规则版本，不等于修改内置检查算法。当前自动检查按文档类型和 profile 的内置检查集执行；实际覆盖以结果中的检查项为准。

超过提取 prompt 的 20000 字符上限会要求拆分，类型未知且超过分类 prompt 的 4000 字符上限会要求先选择类型，不静默截断。旧 `.doc` 需先转为 `.docx`。

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
