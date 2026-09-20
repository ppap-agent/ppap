# Web Client

浏览器/PWA 与飞书 H5 使用同一个 PPAP 产品客户端。isolated-dev 通过 `docker compose -f deploy/isolated-dev/compose.yaml up --build -d --wait` 启动本地 Web。

## 当前 main

唯一产品界面是 Vue 3 + TypeScript，源码位于 `src/`，不设 `/v2` 或新旧界面切换。`/` 为审核准备与工作区，`/audit-files` 为审核记录，`/rules` 为规则库，`/usage` 只读展示用量。数据通过 `src/api.ts` 调用 Product API；前端不另建身份或业务存储。

在本目录运行 `pnpm install --frozen-lockfile`、`pnpm dev` 或 `pnpm build`。开发服务器从本地 4173 端口代理读取 runtime-config；Docker 构建运行同一套类型检查与 Vite 构建，将产物交给 nginx。Docs-addon 的 `/addon` 宿主适配仍保留，继续进入同一产品 API，不是第二套 PC 界面。

## 规则准备

PC 多选文件默认待审，参考资料可另行添加或修改角色。上传自动完成文件准备；后台任务提取客户、型号、零件并建议匹配规则，用户确认后提交。URL 可回访草稿和审核，刷新不重新上传或提交。

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
