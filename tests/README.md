# Tests

Canonical 测试策略见 [docs/TESTING.md](../docs/TESTING.md)。本目录只记录当前 ownership 和可执行入口。

## Ownership

Single-owner tests live with their production owner under `packages/*/tests` or `services/*/tests`. Root `tests/` is system-level authority only:

- `repository`: repository/build/config/tooling contracts
- `schema`: real PostgreSQL contracts
- `corpus`: assembled engine regression and regression fixtures
- `integration/product`: cross-owner production-store contracts
- `integration/runtime`: production runtime and backup/restore
- `e2e`: Python Playwright full-product black-box paths
- `live`: explicit real-environment validation that is outside the default merge contract; not a bucket for every external-backed test

CI consumes these physical owners. It does not maintain a second test manifest.

## Repository and Python modules

Repository contract:

```sh
uv sync --locked
git show --format= --check HEAD -- . ':!assets' ':!tests/corpus/fixtures'
uv run --no-sync python -m compileall -q packages services tests
node deploy/insforge/verify-config.mjs
docker compose -f deploy/isolated-dev/compose.yaml config --quiet >/dev/null
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example config --quiet >/dev/null
docker buildx bake --print >/dev/null
docker buildx bake --check
uv run --no-sync pytest tests/repository
```

Distribution closure:

```sh
python3 scripts/validate_distribution.py --discover
python3 scripts/validate_distribution.py --member ppap-core
python3 scripts/validate_distribution.py --prove-missing-dependency
```

Python module groups:

```sh
# One pytest process per physical owner. CI discovers every packages/*/tests and services/*/tests.
uv run --no-sync pytest packages/ppap_application/tests
uv run --no-sync pytest packages/ppap_core/tests
uv run --no-sync pytest packages/ppap_corpus_contracts/tests
uv run --no-sync pytest packages/ppap_deep/tests
uv run --no-sync pytest packages/ppap_excel_audit_engine/tests
uv run --no-sync pytest packages/ppap_ocr_adapter/tests
uv run --no-sync pytest packages/ppap_product_store/tests
```

API and Worker each need the real isolated-dev PostgreSQL fixture, in separate processes:

```sh
export COMPOSE_PROJECT_NAME=ppap-api
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest services/api/tests
export COMPOSE_PROJECT_NAME=ppap-worker
uv run --no-sync pytest services/worker/tests
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

Cross-owner product integration uses the same PostgreSQL authority but remains a root system contract:

```sh
export COMPOSE_PROJECT_NAME=ppap-product-integration
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest tests/integration/product
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

## Corpus

```sh
uv run --no-sync pytest tests/corpus/v2_assets --ftype excel -q
uv run --no-sync pytest tests/corpus/v2_assets --ftype pdf -q
uv run --no-sync pytest tests/corpus/v2_assets --ftype scanned -q
uv run --no-sync pytest tests/corpus/pairs --cross-format -q
uv run --no-sync pytest tests/corpus/v3_poc -q

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
  pytest tests/corpus/psw -q
```

Pairs compares PDF vs Excel outcomes for the same oracle stem at pair+rule granularity. Known format divergences are a structured baseline, not a second expected-status oracle. Excel / PDF / scanned variants do not pass `--cross-format`. PSW remains a purpose-built container entry in the same CI Corpus matrix. Its image starts from the production worker install and runs `uv sync --frozen --no-editable` so the default dev dependency group comes from the same lock.

## Schema

```sh
export COMPOSE_PROJECT_NAME=ppap-schema
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest tests/schema
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

Schema tests use container-native `psql`. New real PostgreSQL schema contracts belong under `tests/schema/test_*.py`.

## Product integration and E2E

Production runtime/backup:

```sh
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
export COMPOSE_PROJECT_NAME=ppap-prod
docker buildx bake
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait
uv run --no-sync pytest tests/integration/runtime
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans
```

Browser E2E uses the same canonical baked API/Worker/Web images in isolated-dev and runs:

```sh
uv sync --locked --group e2e
uv run --no-sync python -m playwright install chromium
uv run --no-sync pytest tests/e2e
```

## Live

`tests/live` is explicit-only. For the current Deep live smoke:

```sh
PPAP_DEEP_LIVE=1 uv run --no-sync pytest tests/live/test_deep_isolated.py
```

The separate InsForge native lifecycle remains path-filtered / manual / scheduled. It is not part of the default `CI Gate`. LLM readiness and Feishu readiness keep `workflow_dispatch` and add a weekly schedule; they probe real credentials and endpoints and stay outside the default `CI Gate`.

## CI Gate

The default merge contract is the required groups: Web, Repository contract, Python modules, Distribution closure, Schema, Corpus, and Product integration. Being external-backed does not make a test optional or live; Corpus stays a required CI Gate child even when a cache miss calls real OCR / model capability. pytest does not inject the repository root via `pythonpath`; production imports come from uv workspace packaging. `tests/live` is opt-in real-environment validation, not the set of all external tests. The final `CI Gate` runs no tests; it succeeds only when every required group succeeds. Repository-side merge-contract semantics are therefore stable even when internal matrix groups evolve; whether the hosting plan can enforce `CI Gate` through branch protection/rulesets is a separate platform capability.
