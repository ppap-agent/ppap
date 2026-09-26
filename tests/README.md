# Tests

Canonical 测试策略见 [docs/TESTING.md](../docs/TESTING.md)。本目录只记录当前可执行入口。

## Unit

```sh
python -m pip install -e ".[dev]"
git show --format= --check HEAD -- . ':!assets' ':!tests/fixtures'
python -m compileall -q packages services tests
python -m unittest discover -s tests/unit -v
```

## Corpus

```sh
python -m pytest tests/corpus/v2_assets --ftype excel -q
python -m pytest tests/corpus/v2_assets --ftype pdf -q
python -m pytest tests/corpus/v2_assets --ftype scanned -q
python -m pytest tests/corpus/v3_poc -q
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

#7 / #11 浏览器 E2E：`python -m unittest tests.e2e.test_web_audit tests.e2e.test_ui_a11y tests.e2e.test_rules tests.e2e.test_rule_interactions -v`（需 `.[e2e]` Playwright 与 `docker compose -f deploy/isolated-dev/compose.yaml up -d --wait`）。

InsForge 配置契约：`node deploy/insforge/verify-config.mjs`。live CLI 生命周期：`bash deploy/insforge/smoke-local.sh`（opt-in）。

Schema upgrade：`docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait && docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db && python -m unittest tests.schema.test_upgrade -v`（测试通过 `docker compose exec -T postgres psql` 使用容器内 `psql`）。

Production compose：`PPAP_BUILD_SHA="$(git rev-parse HEAD)" docker buildx bake && docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait && python -m unittest tests.deploy.test_runtime tests.deploy.test_backup -v && docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans`。
