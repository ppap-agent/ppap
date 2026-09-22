# Web Client

浏览器/PWA 与飞书 H5 使用同一个 PPAP 产品客户端。isolated-dev 通过 `docker compose -f deploy/isolated-dev/compose.yaml up --build -d --wait` 启动本地 Web。

## 当前 main

唯一产品界面是 React + TypeScript，源码位于 `src/`，不设 `/v2` 或新旧界面切换。`/` 为审核准备与工作区，`/audit-files` 为审核结果，`/rules` 为规则库，`/usage` 只读展示用量。数据通过 `src/api.ts` 调用 Product API；前端不另建身份或业务存储。

账户操作统一在导航底部；正常产品页面不显示开发工具或内部上报状态。隔离测试身份只在显式访问 `/dev` 且 API profile 为 `isolated-dev` 时可用。桌面并排显示检查与原件，窄屏切换两者并保留当前检查项；列表在手机上显示为带字段名的记录卡片。

在本目录运行 `pnpm install --frozen-lockfile`、`pnpm dev` 或 `pnpm build`。开发服务器从本地 4173 端口代理读取 runtime-config；Docker 构建运行同一套类型检查与 Vite 构建，将产物交给 nginx。Docs-addon 的 `/addon` 宿主适配仍保留，继续进入同一产品 API，不是第二套 PC 界面。

## 规则准备

PC 多选文件默认待审，参考资料可另行添加或修改角色。上传自动完成文件准备；后台任务提取客户、型号、零件并建议匹配规则，用户确认后提交。首页从 API 读取当前用户可访问的未提交草稿；URL 可回访草稿和审核，刷新不重新上传或提交。再次审核的来源轮次保存在草稿中，不依赖浏览器 URL。进入规则详情前保存当前资料，详情提供返回审核准备入口。

规则准备入口是 `/rules`：选择文档类型并上传 Excel、Word、文本 PDF、Markdown 或结构化 JSON → Worker 解析 → 核对适用范围，修正文件类型、名称与检查条目 → 保存规则。搜索、客户与历史版本筛选合并在列表上方，默认展示最新版本。`/rules?rule=<id>` 可直接回访详情。已保存规则可上传新版本，原版本与旧审核引用保持不变；来源原件独立检查文件权限。

解析复用 `engine.jobs` 保存执行状态与预览结果，不创建第二份规则事实。URL 中的解析任务标识可用于回访，刷新只读取同一任务，不会重新调用模型。租约失联显示“结果未知”，不自动重领；原 Worker 仍可返回迟到结果。用户可以显式新建任务重跑，但原任务可能仍完成，也可能再次消耗模型费用。规则解析每次模型调用超时为 120 秒，不做 SDK 或空响应自动重试。页面在导入、读取和发布期间统一禁用编辑操作，避免迟到响应混入另一个候选版本。

导入和编辑保存客户规则版本，不等于修改内置检查算法。当前自动检查按文档类型和 profile 的内置检查集执行；实际覆盖以结果中的检查项为准。

超过提取 prompt 的 20000 字符上限会要求拆分，类型未知且超过分类 prompt 的 4000 字符上限会要求先选择类型，不静默截断。旧 `.doc` 需先转为 `.docx`。

## 原件预览

PDF、图片直接预览；Office 格式可连接独立 OnlyOffice DocumentServer，只读查看，无 Java 服务或编辑回写。未配置或服务不可用时显示提取文字与原件下载入口。Office 排版与文字摘录可以切换；保存复核不重新加载同一文件版本。按 [OnlyOffice 配置](https://api.onlyoffice.com/docs/docs-api/usage-api/config/editor/customization/customization-standard-branding/) 关闭姓名询问及宏执行；若工作簿靠宏显示工作表，网页只展示其当前可见状态，可用文字摘录辅助核对。

本地可叠加 `deploy/isolated-dev/onlyoffice.yaml`。先在当前终端设置独立的随机 `PPAP_ONLYOFFICE_SECRET`，再用相同变量启动 API 和 DocumentServer，例如：

```sh
export PPAP_ONLYOFFICE_SECRET="$(openssl rand -hex 32)"
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/onlyoffice.yaml up -d --wait
```

命令在仓库根目录执行；若已有 Compose 项目或配置 overlay，应保留相同项目名及配置文件。DocumentServer 仅绑定本机 8082；允许访问 Docker 私网只是本地联调配置。生产需使用受信任的 DocumentServer、HTTPS 和独立密钥，不直接沿用此 overlay。

## 最终责任

普通网页登录请求 `offline_access`，以获取刷新凭据；报告授权在其基础上增加文档写权限。Cookie 与数据库会话的固定上限为 12 小时，不随请求无限延长。有效会话中的飞书 access token 到期后由 API 刷新；无刷新凭据的旧会话仍受 access token 有效期限制，需要重新授权一次。重启 API 不清除 PostgreSQL 会话。部署后应只检查刷新凭据是否存在及到期时间，不输出凭据本身。

报告通过当前用户身份创建飞书文档，分享由飞书处理。若飞书返回 `99991679`，表示缺少用户授权（[官方说明](https://open.feishu.cn/document/faq/trouble-shooting/how-to-resolve-error-99991679)）。报告页提供显式授权入口，请求 `docx:document:create` 和 `docx:document:write_only`；开放平台也需已启用对应用户身份权限。普通登录不请求这些写权限，授权完成不会自动创建报告。只有明确拒绝且尚未创建文档时允许手动重试；超时或内容写入结果未知时先核对文档。

- 产品导航和呈现
- 文件/资料集准备
- 审核提交和结果回访
- 原文定位、预览与下一步
- 人工复核
- 用量/历史视图
- 浏览器与飞书宿主的必要平台适配

## Boundary

Web 只调用 Product API。它不能：

- 调 Worker / legacy engine tasks API
- 直接访问 PostgreSQL/PostgREST 或 InsForge Database/Storage SDK
- 持有对象存储管理凭据
- 建立飞书专属业务台账或第二套 ledger
- 嵌入服务端 Feishu / Partner AI Secret

UI 可以在不降低 #11 baseline 客户能力的前提下重构；具体前端工具链见 [DEVELOPMENT](../../docs/DEVELOPMENT.md)。
