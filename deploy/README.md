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

Canonical 入口：

```sh
PPAP_BUILD_IMAGES=1 python tools/dev.py deploy
python tools/dev.py test deploy
python tools/dev.py backup
python tools/dev.py test backup
python tools/dev.py restore dist/backups/<stamp>
python tools/dev.py rollback
python tools/dev.py upgrade
python tools/dev.py undeploy
```

`deploy` / `upgrade` 用同一 Git revision 的 `ppap-api` / `ppap-worker` / `ppap-web` 镜像启动 `deploy/compose.yaml`。生产字段目录是仓库根 [`.env.production`](../.env.production)；`deploy/compose.env.example` 只是 verify fixture，不是生产密钥库。真实部署通过 `PPAP_COMPOSE_ENV` 或 `--env-file` 注入填好的 env 文件；未指定时仍用该 fixture。`init` 与 compose 插值使用同一份选定文件。禁止 isolated-dev 口令与 `http://127.0.0.1:9000`。`init` 是唯一读取 bootstrap/migrator 口令的组件并执行 `python tools/dev.py provision`（建角色/库、apply SQL、确保对象桶）。API 与 Worker 只持有各自数据库角色口令加共享的对象存储/LLM/Web 配置。然后 API `GET /health`、`GET /version`、`GET /ready` 必须成功且 `git_sha` 与镜像一致。

`backup` 在短暂停下 API/Worker/Web 后，把当前 compose PostgreSQL（`ppap`）和 MinIO `/data` 写到 `dist/backups/<UTC stamp>/`（`postgres.sql`、`objects.tar.gz`、`manifest.json`），再恢复进程并要求 `/ready`。`restore` 按指定目录 drop/create 数据库并覆盖对象存储；`rollback` 恢复最新一份 backup。这是当前可执行的生产数据回退路径，不是异地备份策略。

API 与 Worker 在 production 下向 stderr 写 JSON 行，字段包括 `request_id` / `tenant_id` / `round_id` / `job_id` / `attempt` 以及 HTTP method/path/status 或 job outcome。不记录 token 或客户内容。`GET /health` `/ready` `/version` `/public-config` 不打 INFO。响应带头 `X-Request-ID`。

该 Compose 里的对象存储是用与 isolated-dev 相同校验和的 MinIO 二进制本地构建的验证端点，不是从 Docker Hub 拉 `minio/minio`，也不是 production 许可证决定。真实生产应注入自有 PostgreSQL 与对象存储。当前不含 `.doc` converter sandbox。

终态不得把 `ppap-host`、Java PPAP 服务或独立部署的 `ppap-agent` HTTP 作为永久 runtime dependency。
