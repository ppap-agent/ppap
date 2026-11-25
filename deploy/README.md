# Deployment

## 当前 main

当前仓库先通过唯一的 Bake build graph 构建应用镜像，再用 Docker Compose 启动 isolated-dev，命令见 [isolated-dev](isolated-dev/README.md)。

这会启动 PostgreSQL、[PGSTY Silo](https://github.com/pgsty/silo)、`psql` 角色/schema、对象桶，以及同一 Git revision 的 API / Worker / Checker Core Controller / nginx Web。用于本机开发。

## 生产组成

1. Web static application
2. API process
3. Worker process
4. PostgreSQL
5. S3-compatible object storage
6. Checker Core Controller 和低权限执行 guest（Standard 与 Deep 共用）
7. 仅在当前 DOC 支持需要时存在的受限 converter sandbox

API 和 Worker 承担产品服务与队列调度；Controller 负责受限执行。基础设施与可选安全 sandbox 不改变产品边界。

Production 必须外部注入 Secret，不能继承 isolated-dev credential 或 loopback endpoint。部署 artifact 必须能关联到明确 Git revision/build identity。

## Production build

```sh
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake
```

同一 Git revision 的制品：

- `ppap-app:<git_sha>`：`deploy/app/Dockerfile`，API 与 Worker 共用同一镜像，由运行时环境变量 `PPAP_SERVICE=api|worker` 选择进程
- `ppap-web:<git_sha>`：`deploy/web/Dockerfile`（nginx；`build-identity.json` 记录 `git_sha`；不把 isolated-dev `runtime-config.json` 打进镜像，进程启动时由 `PPAP_PUBLIC_API_URL` 写入）

Bake 定义在仓库根 [docker-bake.hcl](../docker-bake.hcl)。API `GET /version` 与 Worker heartbeat 读取 `PPAP_BUILD_SHA`。当前镜像不含 isolated-dev 凭据；API/Worker 共用的 app image 包含当前 DOC 支持所需的 LibreOffice 与受限 converter sandbox。

## 线上（TKE current）

Shared customer host `https://ppap.icoastline.com` is split by `deploy/tke/edition-proxy.yaml`. Current PPAP (postgres, silo, api, worker with the Deep controller, web, office) runs in `ppap-test` as `ppap-current-*`. See [tke/README.md](tke/README.md). Do not point `ppap.api.xixifusi.online` at this stack.

## Production deploy

```sh
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait
uv run --no-sync pytest tests/integration/runtime
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans
```

上述基础生产 Compose 和 runtime/backup 测试只覆盖应用生命周期与备份，不启动 Checker Core Controller，也不证明审核能够执行。实际 Standard 和 Deep 审核都要求另行部署受限 Controller，并为 API/Worker 配置可达的 `PPAP_DEEP_CONTROLLER_SOCKET` 与一致的执行配置。构建该镜像用 `docker buildx bake release`；部署和生产切流仍需明确批准，此处不会自动启用。

真实部署把填好密钥的文件交给 `--env-file`（字段目录是仓库根 [`.env.example`](../.env.example)；`deploy/compose.env.example` 只是 verify fixture）。真实部署停服务用 `docker compose down`，不要带 `-v`，否则会删掉数据库与对象卷；`down -v` 只用于 CI 验证栈清理。

`init-db` 是唯一读取 bootstrap/migrator 口令的组件：先用 `psql` 执行 `migrations/bootstrap.sql`（角色与库），再执行 schema migrations（顺序见 [migrations/README](../migrations/README.md)）。`init-bucket` 用 Silo `mcli` 确保对象桶。API 与 Worker 只持有各自数据库角色口令加共享的对象存储/LLM/Web 配置，以及各自拥有的 capability。然后 API `GET /health`、`GET /version`、`GET /ready` 必须成功且 `git_sha` 与镜像一致。

### Backup / restore

不要用 PPAP 代码充当 backup engine。完整 recovery 需要同一时刻的数据库转储和对象存储归档。

生产 Compose：

```sh
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker compose -f deploy/compose.yaml --env-file "$ENV" stop api worker web
docker compose -f deploy/compose.yaml --env-file "$ENV" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" --no-owner --format=plain ppap' > postgres.sql
docker compose -f deploy/compose.yaml --env-file "$ENV" run --rm -T --no-deps \
  --entrypoint tar init-db -C /data -czf - . > objects.tar.gz
docker compose -f deploy/compose.yaml --env-file "$ENV" up -d --no-deps --wait api worker web
```

Silo classic 基于 ubi-micro，容器里没有 `tar`；把 `silo-data` 挂到 `init-db`（postgres 镜像）里打包。Restore 在停应用后 `DROP`/`CREATE DATABASE`、`psql` 灌入 dump、清空并解开对象归档，然后 `docker compose restart silo` 并拉起 API/Worker/Web。`tests/integration/runtime/test_backup.py` 按这条路径检查。

这是当前可执行的生产数据回退路径，不是异地备份策略。

API 与 Worker 在 production 下向 stderr 写 JSON 行，字段包括 `request_id` / `tenant_id` / `round_id` / `job_id` / `attempt` 以及 HTTP method/path/status 或 job outcome。不记录 token 或客户内容。`GET /health` `/ready` `/version` `/public-config` 不打 INFO。响应带头 `X-Request-ID`。

该 Compose 里的对象存储是 pinned 的 [PGSTY Silo](https://github.com/pgsty/silo) classic 镜像（MinIO 协议兼容 fork，release 与 digest 见 [`compose.yaml`](compose.yaml)）。S3 只在 compose 网络内（`http://silo:9000`），不发布到宿主机；根用户仍走 Silo 保留的 `MINIO_ROOT_*`，PPAP 继续用 `PPAP_S3_*`。不用 distroless；classic 是 ubi-micro（有 `sh`/`mcli`/`curl`，没有 `tar`）。不拉 `minio/minio`，也不再本地构建 MinIO。真实生产仍可把 `PPAP_S3_ENDPOINT` 换成自有 S3。当前 app image 保留 DOC 支持所需的受限 converter sandbox。

本仓生产运行不得把 Java PPAP 服务或 `ppap-agent` HTTP 当作 runtime dependency。这不表示拆除 `ppap-agent`：该仓继续服务其它用户，并作为新算法的验证基地和参考实现。
