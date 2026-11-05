# Tests

本文件记录测试归属和运行入口；CI 执行范围由 [Tests workflow](../.github/workflows/tests.yml) 定义。

## Ownership

单一模块的测试放在对应的 `packages/*/tests`、`apps/api/tests`、`apps/worker/tests` 或 `apps/mcp-client/tests`。根目录 `tests/` 只放系统测试：

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
uv run --no-sync python -m compileall -q packages apps/api apps/worker apps/mcp-client tests
uv run --no-sync ruff check packages apps/api apps/worker apps/mcp-client tests scripts
uv run --no-sync ruff format --check packages apps/api apps/worker apps/mcp-client tests scripts
uv run --no-sync mypy --no-incremental
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
python3 scripts/validate_distribution.py --member audit
python3 scripts/validate_distribution.py --prove-missing-dependency
```

Python module groups:

```sh
# One pytest process per physical owner. CI discovers every packages/*/tests and apps/api/tests, apps/worker/tests, and apps/mcp-client/tests.
uv run --no-sync pytest packages/product/tests
uv run --no-sync pytest packages/product/tests/test_settings_priority.py packages/audit/tests/test_token_tracker.py
uv run --no-sync pytest tests/corpus/contracts/tests
uv run --no-sync pytest packages/agent/tests
uv run --no-sync pytest packages/audit/tests
uv run --no-sync pytest packages/ppap_ocr_adapter/tests
uv run --no-sync pytest packages/product/tests
```

API and Worker each need the real isolated-dev PostgreSQL fixture, in separate processes:

```sh
export COMPOSE_PROJECT_NAME=ppap-api
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest apps/api/tests
export COMPOSE_PROJECT_NAME=ppap-worker
uv run --no-sync pytest apps/worker/tests
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

`assets/corpus/` 只保存四类内容：

- `files/<sha256>.<ext>`：原件，按内容寻址，不可变。
- `cases.csv`：一次提交包含的文件。列为数据集、组合ID、文件ID、原件、提交文件名、角色、规则引用。角色为 target 或 reference；规则引用为 `builtin:v2:<类型>`、`builtin:v3:<类型>` 或 `assets/corpus/rules/` 下的冻结规则。
- `oracle.csv`：人工结论。列为数据集、组合ID、文件ID、条款ID、人工真值、条款说明、人工判定依据、判定人、判定日期、已知差距。人工真值须来自原件和规则，不能复制程序输出。
- `replay/<请求哈希>.json`：录制的模型回答。

两份 CSV 是带 BOM 的 UTF-8，可用 Excel 打开，改动在 PR 中逐行可审。缺原件、重复行、公式、未知取值都在执行前报错。

[`test_cases.py`](corpus/test_cases.py) 是唯一的表驱动 runner，每次执行完整组合。`--dataset` 选数据集，`--ftype` 按组合划分（含 PDF 的组合只属于 pdf），`-k` 选组合。[`regressions/`](corpus/regressions) 放针对具体原件或路径的回归，[`psw/`](corpus/psw) 在专用镜像中运行。

### 结果判定

| 人工真值 | 产品结果 | 处理 |
|---|---|---|
| PASS / NG | 相同 | 通过 |
| PASS / NG | 相反 | 阻断 |
| PASS / NG | UNABLE | 不算错，计入 UNABLE 率，单个数据集超过 5% 时阻断 |
| UNABLE | PASS / NG | 阻断 |
| 任意 | ERR、缺失 | 阻断 |

产品暂时做不到的结论不改人工真值，写进“已知差距”，格式为 `<产品结果> #<Issue>`，例如 `UNABLE #123`。产品修好后这一列会让用例失败，提醒删除。

### 模型回答

默认回放：请求按模型看到的内容（文字、图片、输出格式）哈希。命中就不调模型；未命中才真实调用，用例通过后记录回答。用例失败或得到 UNABLE 时不记录，下次重新询问。回放验证取证和判定逻辑，不验证模型能力。

CI 把新回答放进任务产物的 `replay/`。下载后提交，之后的运行就不再调用这些请求：

```sh
gh run download <run-id> --pattern 'corpus-*' --dir /tmp/corpus-replay
mkdir -p assets/corpus/replay
cp /tmp/corpus-replay/*/replay/*.json assets/corpus/replay/
```

录制的回答错了就删除对应文件，失败信息会列出用例用到的哈希。LibreOffice 渲染的图片因平台而异，本地和 CI 的 Excel 视觉请求哈希可能不同，所以提交 CI 产生的录制，不提交本地录制。

[`corpus-eval`](../.github/workflows/corpus-eval.yml) 每晚用 `PPAP_CORPUS_MODEL=live` 真实跑多轮：错误 PASS 必须为 0，错误结果不超过 2%，UNABLE 不超过 5%，并列出同一文件答案不一致的条款。提示词、裁图或模型变更后可手动触发。

`python -m scripts.corpus_report` 从 oracle 计算覆盖矩阵和缺 PASS 或 NG 样本的条款；加 `--results` 再汇总运行结果。

```sh
uv run --no-sync pytest tests/corpus/test_cases.py tests/corpus/regressions --ftype excel -q
uv run --no-sync pytest tests/corpus/test_cases.py tests/corpus/regressions --ftype pdf -q
uv run --no-sync python -m scripts.corpus_report

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

同一文档 PDF 和 Excel 的人工真值必须一致，这由 oracle 契约测试保证；两者各自与真值比较，不再单独配对运行。

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
bash scripts/run-compose-tests.sh tests/integration/runtime -f deploy/compose.yaml --env-file deploy/compose.env.example
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
