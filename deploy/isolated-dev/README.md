# isolated-dev stack

```sh
export PPAP_BUILD_SHA="${PPAP_BUILD_SHA:-local}"
docker buildx bake
docker compose -f deploy/isolated-dev/compose.yaml up --no-build -d --wait
```

`docker-bake.hcl` is the only application-image build graph. Compose only consumes the resulting `ppap-api`, `ppap-worker`, and `ppap-web` images; changing Dockerfile/context/build args belongs in Bake, not Compose.

This Compose file is the isolated-dev product stack: PostgreSQL, [PGSTY Silo](https://github.com/pgsty/silo), `psql` bootstrap/migrations, the object bucket, API, Worker, and nginx Web. Credentials match [`.env.dev`](../../.env.dev). S3 stays on the compose network at `http://silo:9000` and is not published to the host. Loopback ports for host tools are `5432`, `8080`, and `4173`.

前端热更新把 nginx `web` 换成 Vite，源码来自 `apps/web`：

```sh
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/web-dev.yaml up --no-build -d --wait
```

Live Deep 另加 overlay，不改默认 unpublished Silo 合同：

```sh
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml up --no-build -d --wait
PPAP_DEEP_CONTROLLER_SOCKET=/tmp/ppap-deep/controller.sock python -m ppap_deep
```

`deep-live.yaml` 把 `/tmp/ppap-deep` 挂进 Worker/API 的 `/run/ppap-deep`，并把 Silo 映射到 `127.0.0.1:9000` 给 host Controller 取冻结文件。Worker 仍然没有 Docker 控制接口。Controller 缺席时 Deep 轮次失败，不回退 Standard。

Silo is AGPL and S3-compatible. Phase-1 production compose uses the same pinned classic image; the application still talks S3 through `PPAP_S3_*`.
