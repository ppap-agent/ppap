# Tests

Canonical 测试策略见 [docs/TESTING.md](../docs/TESTING.md)。本目录只记录当前可执行入口。

## Module / deterministic

Single-owner tests live with their production owner under `packages/*/tests` or `services/*/tests`. Root `tests/` is reserved for cross-owner/system contracts; `tests/integration` and `tests/repository` are the current deterministic root groups.

```sh
uv sync --locked --extra dev
git show --format= --check HEAD -- . ':!assets' ':!tests/fixtures'
uv run --no-sync python -m compileall -q packages services tests
uv run --no-sync python -m unittest discover -s packages/ppap_core/tests -t . -v
uv run --no-sync python -m unittest discover -s packages/ppap_deep/tests -t . -v
uv run --no-sync python -m unittest discover -s packages/ppap_excel_audit_engine/tests -t . -v
uv run --no-sync python -m unittest discover -s packages/ppap_ocr_adapter/tests -t . -v
uv run --no-sync python -m unittest discover -s services/api/tests -t . -v
uv run --no-sync python -m unittest discover -s services/worker/tests -t . -v
uv run --no-sync python -m unittest discover -s tests/integration -t . -v
uv run --no-sync python -m unittest discover -s tests/repository -t . -v
```

## Corpus

```sh
uv run --no-sync pytest tests/corpus/v2_assets --ftype excel -q
uv run --no-sync pytest tests/corpus/v2_assets --ftype pdf -q
uv run --no-sync pytest tests/corpus/v2_assets --ftype scanned -q
uv run --no-sync pytest tests/corpus/v3_poc -q
# PSW .doc 用例在从 ppap-worker 派生的测试镜像中运行（含 LibreOffice 与 sandbox）
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake worker
docker build \
  --build-arg PPAP_WORKER_IMAGE="ppap-worker:${PPAP_BUILD_SHA}" \
  --build-context psw-tests=tests \
  --build-context psw-assets="assets/V2.0测试/模拟测试文件V2-01/16-零件提交保证书" \
  -f tests/corpus/Dockerfile.psw \
  -t "ppap-psw-test:${PPAP_BUILD_SHA}" \
  .
docker run --rm \
  "ppap-psw-test:${PPAP_BUILD_SHA}" \
  python -m pytest tests/corpus/test_psw_cross_format_regression.py tests/corpus/test_psw_cross_format_audit.py -q
```

## Schema / deploy / E2E

#7 / #11 浏览器 E2E：`uv run --no-sync python -m unittest tests.e2e.test_web_audit tests.e2e.test_ui_a11y tests.e2e.test_rules tests.e2e.test_rule_interactions -v`（需 `uv sync --locked --extra dev --extra e2e` 与 `docker compose -f deploy/isolated-dev/compose.yaml up -d --wait`）。

InsForge static config contract：`node deploy/insforge/verify-config.mjs`（原生 `docker compose config` + PPAP invariant；只需 Docker Compose CLI，不需要 daemon/Buildx，不启动容器）。live native lifecycle：`bash deploy/insforge/smoke-local.sh`（需要 `local start` 的 runtime Docker capability；相关 path / `workflow_dispatch` / 每周 schedule，不是每个无关 PR）。

Schema PostgreSQL contracts：`docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait && docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db && uv run --no-sync python -m unittest discover -s tests/schema -v`。CI 与本地都用该 discovery 收集 `tests/schema/test_*.py`（含 upgrade、rule parse、audit bindings、usage acceptance）；测试通过 `docker compose exec -T postgres psql` 使用容器内 `psql`。新增真实 PostgreSQL schema 契约放到 `tests/schema/test_*.py`。

Production compose：`PPAP_BUILD_SHA="$(git rev-parse HEAD)" docker buildx bake && docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait && uv run --no-sync python -m unittest tests.deploy.test_runtime tests.deploy.test_backup -v && docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans`。
