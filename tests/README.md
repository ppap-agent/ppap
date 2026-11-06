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

`assets/corpus/` 只保存三类内容：

- `documents/<数据集>/…`：原件，用提交时的文件名。
- `cases.csv`：一次提交包含的文件。列为数据集、组合ID、文件ID、原件、提交文件名、角色、Suite版本、Checker组、Binding。Suite版本是源内容的 `sha256:<digest>`，Checker组是 Suite key，Binding 是显式条款 ID 集合。原 PDF、Excel、DOCX 使用同一文档类型的 Suite；历史样例固定各自不可变 revision，不从旧 profile 或类名选择检查器。身份或 Binding 不一致在执行前失败。表驱动 runner 调用正式 guest 的 `run_audit` 函数，进程/授权/Worker 路径由 Runtime 集成测试覆盖。
- `oracle.csv`：人工结论。列为数据集、组合ID、文件ID、条款ID、人工真值、条款说明、人工判定依据、判定人、判定日期、已知差距。

规则原文和人工结果是开发 Checker 的依据。规则、附录、Binding 和程序共同进入不可变执行身份；规则正文发生变化会使旧验证失效，不能仅因条款 ID 相同沿用旧人工标签。运行时按显式 Binding 调用实现，不按规则文字猜测方法。

`replay/<组合>.json` 是录制的模型回答，记录模型看到的文字、图片尺寸和回答，可以直接阅读。

两份 CSV 是带 BOM 的 UTF-8，可用 Excel 打开，改动在 PR 中逐行可审。缺原件、重复行、公式、未知取值都在执行前报错。

[`test_cases.py`](corpus/test_cases.py) 是唯一的表驱动 runner，每次执行完整组合。`--dataset` 选数据集，`--ftype` 按组合划分（含 PDF 的组合只属于 pdf），`-k` 选组合。[`regressions/`](corpus/regressions) 放针对具体原件或路径的回归，[`psw/`](corpus/psw) 在专用镜像中运行。

### 结果判定

产品结果必须等于人工真值，没有比例容错：`UNABLE`、相反结论、ERR 和缺失都阻断。

产品暂时做不到的结论不改人工真值，写进“已知差距”，格式为 `<产品结果> <原因>`，例如 `UNABLE .xls 原件尚不能走视觉读取`。产品修好后这一列会让用例失败，提醒删除。

### 模型回答

默认回放：模型看到的文字和图片尺寸与录制一致就不调模型；否则只有显式配置测试模型时才真实调用，用例通过后记录回答。用例失败时不记录，下次重新询问。测试通过独立的 ModelAccess scope 授权 Core，优先使用完整 OCR_* 配置，其次完整 OPENAI_* 或 LLM_* 配置，同一测试的文本和视觉工具共享该凭据、endpoint 和模型。生产无凭据 scope 不受此测试逻辑影响。

本地无付费验证使用 `PPAP_CORPUS_OFFLINE=1`；即使环境已有 Key，也只回放缓存，任何缺失记录都令测试失败且不会调用提供方。没有测试凭据时同样只读回放。`PPAP_CORPUS_MODEL=live` 必须有完整测试配置，不能与 offline 同时使用。回放验证取证和判定逻辑，不验证模型能力；图片尺寸不变、内容变化时不会重新询问，这部分由每晚评测兜底。

CI 把新回答放进任务产物的 `replay/`。下载后提交，之后的运行就不再调用这些请求：

```sh
gh run download <run-id> --pattern 'corpus-*' --dir /tmp/corpus-replay
for dir in /tmp/corpus-replay/*/replay; do cp -R "$dir"/. assets/corpus/replay/; done
```

录制的回答错了就编辑或删除失败信息列出的文件。

[`corpus-eval`](../.github/workflows/corpus-eval.yml) 每晚用 `PPAP_CORPUS_MODEL=live` 真实跑多轮，判定标准相同，并单独统计 UNABLE、错误 PASS 和同一文件答案不一致的条款。提示词、裁图或模型变更后可手动触发。

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

### Checker workspace browser journey

`tests/integration/product/test_checker_workspace_browser.py` uses the production Web,
HTTP API, PostgreSQL migrations and roles, durable Worker queue, Controller authorization,
Core guest entrypoint, and actual candidate/baseline/holdout execution. It starts from
four real audit results (including false PASS, false NG and UNABLE), saves independent
answers, checks explicit personal-key consent, save-only, refresh/re-login recovery,
verified publication, explicit re-audit and rollback. A companion HTTP case also checks
stale sample edits, private access and preservation of existing human reviews.
No findings or validation reports are injected. Deterministic metadata and author adapters supply only model responses and Python
at the model boundary; model usage is labeled `controlled`. This does not prove provider
quality, real DeepSeek consumption, paid acceptance or willingness to configure a key.

Run with the Product integration prerequisites above, including the `e2e` dependency
group and installed Chromium:

```sh
uv run --no-sync pytest tests/integration/product/test_checker_workspace_browser.py -q
```

Default storage uses the existing real Silo fixture and Controller calls use its Unix
socket protocol. The guest runs in a fresh local subprocess, **not a container security
boundary**. Existing container/runtime gates remain responsible for isolation.
For a restricted local executor only, explicitly set
`PPAP_CHECKER_BROWSER_LOCAL_OBJECTS=1` to use persistent local objects and
`PPAP_CHECKER_BROWSER_LOCAL_TRANSPORT=1` to replace the socket transport with serialized
calls to the same Controller. The tests reject either override when `CI` is set; local
success with these overrides is not S3 or UDS acceptance. `--local-postgres` creates a
disposable TCP-only cluster. If a browser cannot launch, `-k http_receipts` runs the real
HTTP/PG/Worker/trial/publication chain without claiming browser coverage.

## Live

`tests/live` is explicit-only. For the current Deep live smoke:

```sh
PPAP_DEEP_LIVE=1 uv run --no-sync pytest tests/live/test_deep_isolated.py
```

The separate InsForge native lifecycle remains path-filtered / manual / scheduled. It is not part of the default `CI Gate`. LLM readiness and Feishu readiness keep `workflow_dispatch` and add a weekly schedule; they probe real credentials and endpoints and stay outside the default `CI Gate`.

## CI Gate

The default merge contract is the required groups: Web, Repository contract, Python modules, Distribution closure, Schema, Corpus, and Product integration. Being external-backed does not make a test optional or live; Corpus stays a required CI Gate child even when a cache miss calls real OCR / model capability. pytest does not inject the repository root via `pythonpath`; production imports come from uv workspace packaging. `tests/live` is opt-in real-environment validation, not the set of all external tests. The final `CI Gate` runs no tests; it succeeds only when every required group succeeds. Repository-side merge-contract semantics are therefore stable even when internal matrix groups evolve; whether the hosting plan can enforce `CI Gate` through branch protection/rulesets is a separate platform capability.

### Real-model Checker optimization test and regression guard

`Checker optimization live test` is the expensive real-model **T** in the
red/green/refactor loop and a reusable regression guard. It uses the same product
path and independent assertions each time, not a separate release approval
system. A green test proves that this exact candidate, using this run's Actions
model configuration, repaired the synthetic defect, passed independent validation,
and worked after explicit adoption and re-audit. It is positive evidence for
production, not a guarantee for every production scenario or permission to deploy.

It is a separate `workflow_dispatch` only, outside `Tests` / `CI Gate`, with no
push, pull-request or schedule trigger. Clicking **Run workflow** expresses the
intent to run one paid test; there is no second approval flag. Select the branch,
enter its exact reviewed commit as `expected_sha`, and choose the bounded request
and token limits. Fix implementation after a red result, run the changed SHA,
and refactor while preserving the assertions. Offline checks are fast feedback,
not a replacement for this live test.

The sole model configuration source is `CI_LLM_BASE_URL`, `CI_LLM_MODEL`, and
`CI_LLM_API_KEY`, the same Actions settings used for Deep. There are no duplicate
endpoint/model inputs or fixed provider requirements. The key is injected only
into the private test process on the disposable runner. Real Chromium saves the
configuration through the formal settings UI. The test reads back the redacted
configuration, checks its revision binding, and observes the authorized outbound
model after the production gateway selects it. The requested model is recorded;
the provider-returned model is explicitly unknown because the production gateway
does not retain it. Neither aliases nor unknown returned model names decide the
business assertions. No provider key is supplied in a Worker RPC or guest
environment. Browser traces, video, request bodies and settings screenshots are
not recorded. Product endpoint safety and per-request identity checks remain.

The standalone `deploy/isolated-dev/checker-live.yaml` starts fresh PostgreSQL,
Silo, API, Worker, Web and the production Controller/Pi guest image. Its
`remote-dev` profile disables the fixture-login endpoint. Per-run random database
and S3 credentials replace guessable defaults. Ports are bound only to the
runner's loopback; there is no external network attachment, ambient `.env` file
or Docker socket. Only the synthetic identity/session, rule and faulty immutable
Checker are seeded. Eight source files and real baseline rounds use the product
API. Chromium enters eight independent answers, configures the personal account,
starts one real optimization, refreshes, and reopens its result in a fresh session.

The six training expectations are literal and independent. Because this repository
is public, each run privately generates fresh non-training holdout values and
randomly assigns the positive and negative cases to the two hidden ordinals.
A trusted threshold Oracle freezes their answers before optimization; it never
uses candidate code or model output. Completed artifacts cannot predict a later
run's hidden inputs or filename/answer mapping. The
held-out set covers PASS and NG. No corrected Checker or result is injected; the
provider must repair a real missed detection and preserve both sets. The gate
binds each case to the frozen original, Oracle, operation, base Suite, candidate
and actual program digest, and checks evidence and nonempty coverage separately.

A small observational guard wraps the unchanged runtime and gateway. It rejects
holdout document canaries, exact held-out source identities and hidden case IDs
at the author boundary, and rejects canaries or hidden IDs in provider requests.
The label-only leak regression carries no workbook marker. Holdout execution
must be credential-free. The guard also exercises the actual guest UID against
the private evidence directory. Only the guard code is mounted into the Controller; the client source is not.
The private Oracle identities and evidence directory are available to the trusted
Controller, but are root/runner-private and denied to the guest UID. The existing
`container-only` runtime is not a general hostile-code isolation proof.

After real independent validation, Chromium explicitly publishes the candidate
in the disposable tenant. The client explicitly requests a linked re-audit by
the formal API and Chromium opens the new result. Chromium then rolls back the
active Suite. A later sample edit must make the old proof stale, block ordinary
API publication and require the browser's unverified confirmation. The gate
hashes complete historical findings and frozen snapshots to prove that none of
these actions changed the old results. This covers the synthetic self-service
repair journey, not every #255 customer scenario, OAuth login, mobile layout or
the browser's separate file-recognition/re-audit preparation flow.

One optimization may have up to 32 model requests, a 200,000 input-token guard
and 32,000 output-token guard, each reducible at dispatch. These are not currency
ceilings: the final input usage is only known after the response and may exceed
its remaining input allowance. Confirm endpoint pricing and acceptable spend
before running. This run deliberately avoids model-backed metadata preparation;
its initial and repeat audits use fixed native Checkers. Any unexpected gateway
consumption before optimization is rejected; all observed provider requests must
match the optimization's authoritative usage. Other product model capabilities
are not covered by these per-execution gateway limits.

No paid probe or automatic write retry is performed. An explicit GitHub **Re-run
jobs** or a fresh dispatch starts a new, separately paid test, with fresh random
fixtures, credentials, storage and one new optimization operation. It does not
recover or overwrite the old run. Stack, report and artifact identities include
both run ID and attempt; re-entering the same runner invocation is rejected.
Reconcile unknown prior consumption before deciding to run another test. The
UI-generated operation ID is saved before its request is forwarded.
Each polled receipt preserves known usage, including legal unknown receipts with
`report: null`. Before stopping containers, an always-run step makes one final
read-only request to that same operation. This disposable stack is **not
recoverable after cleanup**. Unknown later charges require provider-billing
reconciliation, never automatic redispatch.

Only bounded `report.json` and `controller.json` are uploaded, with known token
and request counts. Assertions already verified are saved before the next stage,
so failures retain progress through optimization, adoption, re-audit, rollback,
stale-proof rejection and history checks. The GitHub run summary distinguishes
PROVEN from NOT_PROVEN and retains known usage; timeout or missing terminal
evidence does not by itself attribute a defect to the product. The controller's
request observation is authorization, not proof of provider receipt or billing. Keys, session tokens, original bytes, prompts, raw model
responses and raw service logs are not artifacts. Monetary cost stays unknown
without a provider billing receipt. No production data, notifications or formal
audit points are affected. The live run must actually pass before any of these
prepared assertions count as acceptance.

Offline false-accept and safety regressions:

```sh
uv run --no-sync python -m pytest tests/repository/test_checker_live_acceptance.py -q
```
