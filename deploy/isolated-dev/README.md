# isolated-dev stack

```sh
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake release
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml up --no-build -d --wait
```

`docker-bake.hcl` is the only application-image build graph. Compose only consumes the resulting `ppap-api`, `ppap-worker`, `ppap-web`, and `ppap-deep` images; changing Dockerfile/context/build args belongs in Bake, not Compose.

This Compose file is the isolated-dev product stack: PostgreSQL, [PGSTY Silo](https://github.com/pgsty/silo), `psql` bootstrap/migrations, the object bucket, API, Worker, and nginx Web. Credentials match [`.env.dev`](../../.env.dev). S3 stays on the compose network at `http://silo:9000` and is not published to the host. Loopback ports for host tools are `5432`, `8080`, and `4173`.

前端热更新把 nginx `web` 换成 Vite，源码来自 `apps/web`：

```sh
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml -f deploy/isolated-dev/web-dev.yaml up --no-build -d --wait
```

Standard 和 Deep 审核都通过 `deep-live.yaml` 中的同一个 Checker Core Controller。基础 Compose 可单独用于数据库、存储和 API 生命周期测试，但没有 Controller 时不能执行审核。

`deep-live.yaml` 使用 Compose project 隔离的 `deep-socket` named volume，在 API、Worker 与 Controller 之间共享 `/run/ppap-deep/controller.sock`。API 和 Worker 等待 Controller readiness；不挂载 Docker socket，不在 Worker 内直接执行 Checker。Controller 在专用 `ppap-deep` 镜像的低权限 guest 中执行冻结代码。Silo 仍不向宿主机发布端口。

浏览器 E2E 使用这套真实拓扑，并通过公开规则和 Suite API 发布每个测试独立的 A1 存在性检查；不依赖 seed 规则自动通过，也不调用付费模型。这只证明产品执行和冻结/复核路径，不证明 PPAP 规则质量。结束时用相同的两个 Compose 文件执行 `down -v --remove-orphans`，同时清理该 project 的 socket volume。

Silo is AGPL and S3-compatible. Phase-1 production compose uses the same pinned classic image; the application still talks S3 through `PPAP_S3_*`.
