# Web Client

桌面浏览器与飞书 H5 使用同一个 PPAP 产品客户端（React + TypeScript，源码在 `src/`），只支持桌面宽度（最小 1280px），不提供手机/平板布局、PWA 或离线缓存。数据只通过 Product API 读取；JSON 类型由 OpenAPI 生成（见 [DEVELOPMENT](../../docs/DEVELOPMENT.md#web-wire-contract)）。产品地址以 `src/audit/routes.ts` 为准；Docs-addon 的 `/addon` 宿主适配进入同一产品 API。

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
