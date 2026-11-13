# Web Client

桌面浏览器与飞书 H5 使用同一个 PPAP 产品客户端（React + TypeScript，源码在 `src/`），只支持桌面宽度（最小 1280px），不提供手机/平板布局、PWA 或离线缓存。数据只通过 Product API 读取；JSON 类型由 OpenAPI 生成（见 [DEVELOPMENT](../../docs/DEVELOPMENT.md#web-wire-contract)）。产品地址以 `src/audit/routes.ts` 为准；Docs-addon 的 `/addon` 宿主适配进入同一产品 API。

## 运行

在本目录运行 `pnpm install --frozen-lockfile`、`pnpm dev` 或 `pnpm build`。isolated-dev 先设置 `PPAP_BUILD_SHA=${PPAP_BUILD_SHA:-local}` 并执行 `docker buildx bake`，再用 `docker compose -f deploy/isolated-dev/compose.yaml up --no-build -d --wait` 启动。开发服务器从本地 4173 端口代理读取 runtime-config；Docker 构建运行同一套类型检查与 Vite 构建，将产物交给 nginx。

`pnpm preview:workbench` 在 `http://127.0.0.1:4185/__preview` 启动本地预览台：固定 fixture 场景只用于视觉与交互检查，业务 API 请求被拒绝，不进入生产构建，不连接数据库、对象存储、飞书或 OnlyOffice。真实上传、审核、复核和原件排版使用 isolated-dev 或 remote-dev。

`pnpm typecheck`、`pnpm test`、`pnpm lint` 与 `pnpm build` 是前端合并门槛。页面直接使用 `src/components/ui` 中的 shadcn/ui 源码组件和 Tailwind v4 主题；`src/ui/domain.tsx` 只放 PPAP 专属组合。ESLint 禁止 `@insforge/*` 与 PostgREST 客户端；跨文件产品 invariant 由 `src/webContract.test.ts` 拥有。跨栈浏览器路径由仓库根目录的 Python Playwright（`tests/e2e`）执行。

## 原件预览

PDF、图片直接预览；Office 格式可连接独立 OnlyOffice DocumentServer 只读查看，未配置或不可用时显示提取文字与原件下载入口。本地可叠加 `deploy/isolated-dev/onlyoffice.yaml`，用同一个随机 `PPAP_ONLYOFFICE_SECRET` 启动 API 和 DocumentServer：

```sh
export PPAP_ONLYOFFICE_SECRET="$(openssl rand -hex 32)"
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/onlyoffice.yaml up -d --wait
```

DocumentServer 仅绑定本机 8082；生产需使用受信任的 DocumentServer、HTTPS 和独立密钥，不直接沿用此 overlay。

## 飞书授权

普通登录请求 `offline_access`；需要创建报告文档时显式请求 `docx:document:create` 与 `docx:document:write_only`，普通登录不请求写权限。Cookie 与数据库会话上限 12 小时；会话内的飞书 access token 由 API 刷新，日志不输出凭据。

## Boundary

Web 只调用 Product API。它不能调用 Worker、直接访问 PostgreSQL/PostgREST 或 InsForge Database/Storage SDK、持有对象存储管理凭据、建立第二套业务台账，或嵌入服务端 Feishu / Partner AI Secret。
