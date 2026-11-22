# Web Client

桌面浏览器与飞书 H5 使用同一个 PPAP 产品客户端（React + TypeScript，源码在 `src/`），只支持桌面宽度（最小 1280px），不提供手机/平板布局、PWA 或离线缓存。数据只通过 Product API 读取；JSON 类型由 OpenAPI 生成（见 [AGENTS.md](../../AGENTS.md#开发)「Web 接口类型」）。产品地址以 `src/audit/routes.ts` 为准；Docs-addon 的 `/addon` 宿主适配进入同一产品 API。

## 运行

在本目录运行 `pnpm install --frozen-lockfile`、`pnpm dev` 或 `pnpm build`。isolated-dev 的构建与启动见 [deploy/isolated-dev](../../deploy/isolated-dev/README.md)。开发服务器从本地 4173 端口代理读取 runtime-config；Docker 构建运行同一套类型检查与 Vite 构建，将产物交给 nginx。

`pnpm typecheck`、`pnpm test`、`pnpm lint` 与 `pnpm build` 是前端合并门槛。页面直接使用 `src/components/ui` 中的 shadcn/ui 源码组件和 Tailwind v4 主题；`src/components/*.tsx` 只放 PPAP 共用的组合（加载提示、折叠、分页等）。跨栈浏览器路径由仓库根目录的 Python Playwright（`tests/e2e`）执行。

## 原件预览

PDF、图片直接预览；Office 格式可连接独立 OnlyOffice DocumentServer 只读查看，未配置或不可用时显示提取文字与原件下载入口。本地可叠加 `deploy/isolated-dev/onlyoffice.yaml`，用同一个随机 `PPAP_ONLYOFFICE_SECRET` 启动 API 和 DocumentServer：

```sh
export PPAP_ONLYOFFICE_SECRET="$(openssl rand -hex 32)"
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/onlyoffice.yaml up -d --wait
```

DocumentServer 仅绑定本机 8082；生产需使用受信任的 DocumentServer、HTTPS 和独立密钥，不直接沿用此 overlay。

## 飞书授权

普通登录请求 `offline_access`；需要创建报告文档时显式请求 `docx:document:create` 与 `docx:document:write_only`，普通登录不请求写权限。Cookie 与数据库会话上限 12 小时；会话内的飞书 access token 由 API 刷新，日志不输出凭据。

云文档插件（`/addon`）运行在飞书文档的内嵌框架里，浏览器不会带上本站 Cookie：它用 `POST /login/feishu/addon` 换取 2 小时的 Bearer 会话（只存在内存里，不能当作 Cookie 使用），页面的脚本、样式和 `runtime-config.json` 都从 `/addon/` 下加载。

## Boundary

Web 只调用 Product API。它不能调用 Worker、直接访问数据库、持有对象存储管理凭据、建立第二套业务台账，或嵌入服务端 Feishu / Partner AI Secret。

## 前端规则

- **设计依据**：以当前确认的设计稿为准，文案与设计稿一致。参考外部组件或示例时读它的源码并复制改写，不看截图仿制。
- **视觉值只有一个来源**：颜色、字体、圆角只改 `apps/web/tokens/`，`design.md` 的头部和运行时主题都由它生成，不手改；做法见 [tokens](tokens/README.md)，改页面按 `product-design-loop` 技能的流程。
- **数据先找后端**：页面要的数据，先在现有接口和字段里找；找不到才用已有读取推导，最后才加后端。同一概念全站只有一个定义。
- **不给用户看内部信息**：内部 ID、诊断码、内部编号、命令、脚本内容、路径、日志原文都不显示，确需保留的放进悬停提示。用户自己提供的内容（如上传的文件名）可以显示。
- **不让用户思考**：每屏一个主任务，每个区域只有一个主操作；同一信息只出现一次（标题、表头、徽标、面包屑之间不重复）；用用户的词命名，不用实现层面的术语。
- **等待与空态**：加载状态要有可见文字，不能先显示 0、— 之类的假值；长任务显示实时进度，进度窗口有上限，不刷屏。
- **折叠**：用显式按钮加计数，不用 `<details>`。名称被截断时，必须能看到全名（如悬停提示）。
- **可访问名称**：控件的可访问名称只放主名称；状态、数量、说明通过 `aria-describedby` 作为描述提供。
- **一套界面**：替换时直接删除旧页面和旧组件，旧链接跳转到新页面，不保留新旧两版。唯一例外是页面外壳按打开位置区分：浏览器里是左侧导航，飞书客户端里是顶部导航；视觉值、组件和页面内容只有一套（见 design.md「Page Frame」）。
- **验证**：先读代码判断交互，截图只做对照，不要反复刷新去截加载态；看效果时用真实构建产物，在实际运行的应用里检查；预览台和 fixture 不能作为验收依据。
