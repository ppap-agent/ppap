# Deployment

## 当前 main

当前仓库提供 isolated-dev 运行方式：

```sh
python tools/stack.py up
```

以及可选的 `deploy/isolated-dev/compose.yaml` 基础设施。它们用于开发，不代表 production deployment 已完成。

## 目标生产组成

1. Web static application
2. API process
3. Worker process
4. PostgreSQL
5. S3-compatible object storage
6. 仅在当前 DOC 支持需要时存在的受限 converter sandbox

API 和 Worker 是两个核心应用进程；基础设施与可选安全 sandbox 不改变产品边界。

Production 必须外部注入 Secret，不能继承 isolated-dev credential 或 loopback endpoint。部署 artifact 必须能关联到明确 Git revision/build identity。

## Production build

Canonical 入口：

```sh
python tools/dev.py install
python tools/dev.py build
PPAP_BUILD_IMAGES=1 python tools/dev.py build
```

同一 Git revision 的制品：

- `dist/web`：静态 Web（`build-identity.json` 记录 `git_sha`；不把 isolated-dev `runtime-config.json` 打进制品）
- `dist/identity.json`：记录 `git_sha`、`dist/web` 以及可选镜像标签
- `ppap-api:<git_sha>` / `ppap-worker:<git_sha>`：`deploy/app/Dockerfile`（`PPAP_SERVICE=api|worker`，共用安装层）
- `ppap-web:<git_sha>`：`deploy/web/Dockerfile`

API `GET /version` 与 Worker heartbeat 读取 `PPAP_BUILD_SHA`。Web 进程在 `PPAP_PUBLIC_API_URL` 存在时写 `runtime-config.json`。当前镜像不含 isolated-dev 凭据，也不含 `.doc` converter sandbox。

## Production deploy

真正的 production deploy、upgrade、backup、restore、rollback 命令仍由 #15 后续交付并验收。命令存在并实际运行之前，不在本文件编造 production runbook。

终态不得把 `ppap-host`、Java PPAP 服务或独立部署的 `ppap-agent` HTTP 作为永久 runtime dependency。
