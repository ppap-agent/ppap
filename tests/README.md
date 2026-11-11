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

Cross-owner product integration owns PostgreSQL and Silo before starting any pytest
workers, and tears both down only after all workers exit. Each worker has its own
database and S3 bucket. Keep the Compose bridge stable while browsers are running:
creating/removing bridges mid-suite can invalidate Chromium's active connections,
even to localhost.

```sh
python3 -m scripts.ci_shards integration
```

For a focused direct pytest run, start the same services first and keep them up
until pytest exits (including every xdist worker). The explicit test-only endpoint
prevents a fixture from starting a new Docker network while another worker uses
Chromium. S3 buckets live in this disposable project's volume until teardown.

```sh
export COMPOSE_PROJECT_NAME=ppap-product-integration
compose=(docker compose -f deploy/isolated-dev/compose.yaml -f tests/integration/product/services.yaml)
"${compose[@]}" up -d postgres silo --wait
"${compose[@]}" run --rm init-db
export PGPORT=$("${compose[@]}" port postgres 5432 | sed 's/.*://')
export PPAP_TEST_S3_ENDPOINT=http://$("${compose[@]}" port silo 9000)
uv run --no-sync pytest tests/integration/product
"${compose[@]}" down -v --remove-orphans
```

## Corpus

`assets/corpus/` 保存原件、组合、人工真值和对应的规则含义：

- 原件放在各公司目录 `assets/<公司>/samples/…`（`uaes`、`li`、`nio`），保留原测试分组，用提交时的文件名；`cases.csv` 的数据集名就是公司目录名。每家公司下另有 `rules`（客户规则原文）、`templates`（空白模板）和必要时的 `history`；UAES 的预检规则在 `uaes/prechecks`。
- `cases.csv`：一次提交包含的文件。列为数据集、组合ID、文件ID、原件、提交文件名、角色、Suite版本、Checker组、Binding。Suite版本是检查程序目录路径，例如 `li/L08` 或 `uaes/U05`；必须恰好对应一个 Suite 来源，没有 latest 或回退。Checker组是 Suite key，Binding 是显式条款 ID 集合。原 PDF、Excel、DOCX 使用同一来源的 Suite；完整内容摘要在构建执行快照时计算，仍写入不可变 Suite revision、程序和输入身份。缺失、歧义、组别或 Binding 不一致在加载完整表时失败。表驱动 runner 调用正式 guest 的 `run_audit` 函数，进程/授权/Worker 路径由 Runtime 集成测试覆盖。
- `oracle.csv`：人工结论。列为数据集、组合ID、文件ID、条款ID、人工真值、条款说明、人工判定依据、判定人、判定日期、已知差距。

- `reviewed-rules.json`：每个已使用来源对应的可读人工判定上下文，保留完整规则正文、附录、文档类型、条款入口、实际方法／载体绑定和关联 Suite。加载时与当前来源逐项比较，仅忽略载体实现的 Python 类路径；支持的文件格式与条款到方法的绑定仍须一致。历史类路径保留为复核时的记录，不随实现重命名刷新；实际执行路径仍进入完整 Suite／程序摘要并接受测试。它不参与产品路由，也不会自动从当前实现重生成。

规则原文和人工结果是开发 Checker 的依据。实现修改可保留人工判定上下文，但完整 Suite／程序／输入摘要仍改变。规则、附录或绑定改变时，旧人工真值不能直接沿用；须独立重审并在带 `Oracle-Basis:` 依据的单独提交中更新 `reviewed-rules.json` 及必要的人工答案，不能与实现或原件内容改动混在一起。`cases.csv` 里的原件／文件身份、角色和 Suite／Binding 关联修改也遵循同一独立复核流程，不能把旧答案直接改绑到另一个已存在的来源。行序与 Binding 条款顺序不改变关联。完整规则正文比较是有意保守的，正文里的描述或来源元数据变化也需要显式复核。运行时按显式 Binding 调用实现，不按规则文字猜测方法。

`replay/<组合>.json` 是录制的模型回答，记录模型看到的文字、图片尺寸和回答，可以直接阅读。

两份 CSV 是带 BOM 的 UTF-8，可用 Excel 打开，改动在 PR 中逐行可审。缺原件、重复行、公式、未知取值都在执行前报错。

[`test_cases.py`](corpus/test_cases.py) 是唯一的表驱动 runner，每次执行完整组合。`--dataset` 选数据集，`--ftype` 按组合划分（含 PDF 的组合只属于 pdf），`-k` 选组合。[`regressions/`](corpus/regressions) 放针对具体原件或路径的回归，[`psw/`](corpus/psw) 在专用镜像中运行。

### 官方 Suite 验证与发布

`python -m scripts.official_suites` 是平台侧的显式发布工具，使用已有 Suite validation 和 official head，不新增发布台账。普通 corpus 测试通过、known-gap 放行或官方来源标签都不算发布证明。默认读取当前配置；执行前必须确认是本次授权的环境，开发测试不能借此操作生产。

```sh
# 执行全部对应样本，但不写验证或发布默认版本。
uv run --no-sync python -m scripts.official_suites validate --revision-id <official-revision-uuid>
# 明确使用真实文本/视觉模型和 Paddle，不读写 replay。
uv run --no-sync python -m scripts.official_suites validate --revision-id <official-revision-uuid> --model live
# 显式写入验证；CAS revision 来自 read。此步骤仍不发布。
uv run --no-sync python -m scripts.official_suites validate --revision-id <official-revision-uuid> --record --expected-validation-revision <revision>
# 只读：包含当前官方 head、验证 write_revision、proof_digest 和完整结构化报告。
uv run --no-sync python -m scripts.official_suites read --revision-id <official-revision-uuid>
# 另行审阅并授权后，才显式发布这一个证明。
uv run --no-sync python -m scripts.official_suites publish --revision-id <official-revision-uuid> --expected-head-revision <revision> --expected-validation-revision <revision> --expected-proof-digest <sha256>
```

操作人员用 `--revision-id` 选定确切版本。验证只消费仓库当前完整 corpus 中规则含义与该版本相同的全部目标，其他代际（如 v2 与 v3）的标记不沿用也不阻断；没有样本筛选或 proof JSON 导入选项。每个样本保留整个原件组合，只执行所选 Suite。运行正式 Core 的固定程序子进程，默认 `--model disabled` 不提供模型授权；`--model live` 显式允许文本 LLM 和模型 OCR 读取原件。两种模式都不录制或回放模型答案，不更改现有 timeout。它只适用于已审阅的平台程序；本地子进程不是敌对代码沙箱，不能据此声称具备生产隔离。

Live 沿用 corpus 的完整测试配置选择：优先 `OCR_OPENAI_API_KEY` / `OCR_OPENAI_BASE_URL` / `OCR_MODEL_NAME`，其次完整 `OPENAI_*` 或 `LLM_*`。本次文本和视觉工具共享所选模型，所以它必须支持视觉和 Responses；这不是生产分开的模型配置验收。`PPAP_CORPUS_OFFLINE=1` 与 live 冲突，普通 corpus 的 `PPAP_CORPUS_MODEL` 不会悄悄改变官方 runner 的模式。配置 `PADDLE_OCR_URL` / `PADDLE_TOKEN` 时，子进程经与生产相同的 bridge 读取本样本的 PDF。密钥只留在 host，子进程只拿本次 loopback 临时 grant 和 Paddle capability。

CI 和开发环境的模型与 Paddle 用量属于开发预算，不设限额。每个样本后输出已知用量，结束撤销授权并输出最终回执；用量未知会阻断验证，不自动重试。进程被外部终止可能没有最终回执，先核对既有输出和供应商账单，不能假定没有计费。回执只保留所选模型身份、endpoint 摘要和供应商确认的 token 数，不含密钥、原件、提示词或模型原始回答；返回模型名称和货币费用仍未知。证明绑定本次模型身份，`read` / `publish` 不需要模型密钥，也不调用模型。

这里的 `--record` 仅表示写入正式 validation，和模型答案录制无关。可将它与 `--model live` 及 CAS revision 同时使用；不带它仍会执行、可能计费，但不保存发布证明。跳过 replay 不能跳过人工来源、样本独立性或实际结果取证。

发布要求每条人工答案有已确认的判定人、ISO 日期和完整条款覆盖，并绑定确切原件、文件身份及已复核的 Suite／条款含义。人工确认的 PASS 可以不写人工判定依据，已有依据原样保留；NG、NA 仍须说明依据，UNABLE 仍是未解决结论。不能用自动生成的通用说明替代人工判断。至少四个不同原件样本，第四个及其倍数为独立验证样本，不能与训练样本共享任何原件。当前缺失的必填人工字段不会自动补齐。证据不足时返回 `not_started_evidence_incomplete`、具体原因和零个已执行样本，不启动检查、不把缺失当通过；返回码为 2。执行中断标记 `incomplete`，不自动重跑。人工 PASS 的说明可空不免除实际运行的取证要求：只有实际结果完全符合人工答案、断言数大于零且证据可定位时才可验证；UNABLE、ERR、缺失和 known-gap 都不能豁免。

证明绑定官方 Suite revision／完整内容、程序、共享库、当前直接依赖的 revision／内容、全组合输入与两份人工表的身份。写入前检查原件仍相同；发布重新核对当前 corpus，并在现有官方事务锁下同时检查 head、validation revision、proof digest 和依赖选择。先发布直接依赖，例如 U12/U16 → U07 → U08 → U05；保存候选或完成验证不会自动更新下游。

操作使用不可变、已审阅的发布 checkout 和原件；运行期间不能编辑它们。库或文件改变须重新验证。工具在写入前输出本次 proof／CAS 身份，异常不自动重试。结果未知时先用原 revision 执行 `read`，核对原 proof digest、验证 revision，以及发布后的目标和 head revision。读不到相同记录或已被后续写入覆盖时仍属未知，不能据此断言原写入未发生。测试中的四个程序化工作簿只证明执行和发布协议，不是客户效果验收或生产发布证据。

### 结果判定

产品结果必须等于人工真值，没有比例容错：`UNABLE`、相反结论、ERR 和缺失都阻断。

产品暂时做不到的结论不改人工真值，写进“已知差距”，格式为 `<产品结果> <原因>`，例如 `UNABLE .xls 原件尚不能走视觉读取`。产品修好后这一列会让用例失败，提醒删除。

### 模型回答

默认回放：完整请求摘要与录制一致才复用回答，摘要包括图片内容、提示词、Schema、预算、显式选项和独立观察编号；图片尺寸只用于可读展示。不能证明图片内容的旧录制不命中，旧资产保留。缺少匹配录制时，只有显式配置测试模型才真实调用，用例通过后记录回答。用例失败时不记录，下次重新询问；因此通过用例的录制仅作逻辑回归样本，不作为准确率或稳定性统计。测试通过独立的 ModelAccess scope 授权 Core，优先使用完整 OCR_* 配置，其次完整 OPENAI_* 或 LLM_* 配置，同一测试的文本和视觉工具共享该凭据、endpoint 和模型。生产无凭据 scope 不受此测试逻辑影响。

本地无付费验证使用 `PPAP_CORPUS_OFFLINE=1`；即使环境已有 Key，也只回放缓存，任何缺失记录都令测试失败且不会调用提供方。没有测试凭据时同样只读回放。`PPAP_CORPUS_MODEL=live` 必须有完整测试配置，不能与 offline 同时使用。回放验证取证和判定逻辑，不验证新模型或部署的能力；真实视觉验收使用预先固定的样本和运行次数，并保留全部运行结果。

专用 OCR 测试授权选中完整 `OCR_*` 配置时，可显式带入 `OCR_OPENAI_EXTRA_BODY_JSON`；该参数不进入文本请求，不沿用到 `OPENAI_*` / `LLM_*` 替代测试授权或普通文本模型。实际 OCR 参数进入授权身份、OCR 配置摘要和 Replay 请求键；旧参数的回答不能用来验证新参数。当前 CI 专用 OpenRouter `deepseek/deepseek-v4.1-flash-1017` 已用合成图像核验 `reasoning.effort=none`，仍使用 4096 输出上限和零自动重试。更换端点或模型须先核对其支持的参数。

CI 把新回答放进任务产物的 `replay/`。下载后提交，之后的运行就不再调用这些请求：

```sh
gh run download <run-id> --pattern 'corpus-*' --dir /tmp/corpus-replay
for dir in /tmp/corpus-replay/*/replay; do cp -R "$dir"/. assets/corpus/replay/; done
```

录制回答与原件不符时，保留录制资产和失败结果，定位输入或取证缺口；不要手改回答来匹配 Oracle。新的视觉验证使用明确的新运行，不能反复重试直到通过。

[`corpus-eval`](../.github/workflows/corpus-eval.yml) 每晚用 `PPAP_CORPUS_MODEL=live` 真实跑多轮，判定标准相同，并单独统计 UNABLE、错误 PASS 和同一文件答案不一致的条款。每轮结果独立保存，必须完整覆盖当前 oracle 的条款身份和人工真值；缺失、重复、错绑或执行失败都不能通过，后续轮次不会覆盖先前失败。提示词、裁图或模型变更后可手动触发。

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
  --build-context psw-assets="assets/li/samples/模拟测试文件V2-01/16-零件提交保证书" \
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

### Original fact confirmation

`tests/integration/product/test_fact_answers.py` checks actual PostgreSQL persistence and API-role permissions for the positive source-answer path, including idempotent readback, repeatable migration, unchanged machine findings and no new Round, job or usage event. `test_fact_answers_http.py` runs the same path through the production HTTP API and downloads the separate confirmation PDF. The controlled visual boundary returns null; the source checker creates the actual question and frozen predicate.

`test_fact_answers_browser.py` uses the native Web, HTTP and PostgreSQL to submit that answer and reopen its result. It requires Chromium and the product integration setup above; collecting the test does not prove browser behavior. `tests/e2e/test_fact_questions_preview.py` separately exercises the four-question display and an explicit unreadable answer in the preview.

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
quality, real personal-model consumption, paid acceptance or willingness to configure a key.

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

### Feedback grouping controlled comparison (#258)

`packages/product/tests/test_feedback_groups.py` covers deterministic category/column grouping.
`test_grouped_feedback_selection_and_read_only_history` in the workspace browser suite exercises
real results, explicit group selection, removing one item, preserving the full regression set,
and read-only authorized history. Grouping and history never dispatch a model or a re-audit.

`test_controlled_grouped_and_individual_repair_keep_the_full_oracle` compares the same four
confirmed samples through the real fixed execution loop with a controlled author: one grouped
request records 2 model requests and 36 synthetic tokens; three separate requests record 6
and 108. All four samples pass in each run. These are adapter receipts, not provider billing or
real-model quality evidence. Grouped repair therefore remains an explicit opt-in, not the default.

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

The same driver also has an explicit local mode and three bounded scenarios:
`u08`, `rule-threshold`, and `rule-wording`. Each fresh run admits at most one
optimization. U08 uses the unchanged real CP workbook plus controlled mark-only
variants, a frozen existing Checker/resources revision, and **only U08:4**.
It does not certify the other U08 clauses. The original PASS comes from the
existing human Oracle; derived cases are independently rule-based, not additional
human-reviewed corpus labels. The two rule-update scenarios test R1 and unchanged
R2 together, upload the actual changed rule JSON with checked source units, and
require explicit reconfirmation. Wording-only changes may keep code unchanged.

For a local run, supply the approved `CI_LLM_*` values in the process, then set
`LIVE_API_IMAGE`, `LIVE_WORKER_IMAGE`, `LIVE_DEEP_IMAGE`, `LIVE_WEB_IMAGE` to four
existing same-source images and set `PPAP_CHECKER_LIVE_RUNTIME_SHA` to their real
source SHA. Existing immutable pins are in `deploy/tke/current.yaml`. The driver
never builds or pulls images. Reusing an older runtime validates that runtime,
not the current branch: the report separates `harness_sha`, `runtime_sha` and
`same_source_runtime`; `candidate_sha` identifies the runtime being tested.
Local preparation uses a Linux named volume for private evidence: Desktop host
bind mounts can ignore Unix permissions. The runner transfers only the existing
private Oracle and Controller receipt through explicit root execs with byte-digest
checks; the real guest must still fail to read this directory. Linux workflow
bind mounts keep their existing behavior. Read-only snapshots also retain the
local Controller receipt before the disposable volume is removed.
Local preparation also chooses four free loopback ports and UUID-specific database
and bucket names. Before any identity, session, Suite or other business write, the
driver compares its real PostgreSQL cluster identifier/database with a read through
the private container and authenticates a bucket HEAD with the private S3 credentials.
Docker's published-port metadata is insufficient: an existing host `kubectl
port-forward` can otherwise intercept that address. Existing forwards stay untouched.

Use a native ARM64 Deep image on an ARM64 host. Other components can keep their
same-source AMD64 images; the report records each actual image architecture.
The trusted Controller runs Pi `--version` under the unchanged production guest
limits before becoming ready. The driver checks that startup receipt before any
business write. No emulation-specific resource override is used.

```sh
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
export PPAP_APPROVED_SHA="$PPAP_BUILD_SHA"
export PPAP_CHECKER_LIVE_MODE=local
export PPAP_CHECKER_LIVE_SCENARIO=u08
export PPAP_CHECKER_LIVE_BASE_SHA=951f5a1bbfe5285f92a067ea3624a34d44d2f890
export PPAP_DEEP_MODEL_MAX_REQUESTS=32
# Free plan only: no writes, credential preparation, containers or model calls.
uv run --no-sync python -m scripts.run_checker_live --plan

# After authorization for this specific paid scenario, use a fresh identity.
export PPAP_CHECKER_LIVE_LOCAL_ID="$(uv run --no-sync python -c 'from uuid import uuid4; print(uuid4().hex)')"
export COMPOSE_PROJECT_NAME="ppap-checker-local-$PPAP_CHECKER_LIVE_LOCAL_ID"
export PPAP_CHECKER_LIVE_OUTPUT="$(mktemp -d /tmp/ppap-checker-local.XXXXXX)"
export PPAP_CHECKER_LIVE_ENV="$PPAP_CHECKER_LIVE_OUTPUT/private.env"
uv run --no-sync python -m scripts.prepare_checker_live
# Compose reads the prepared random ports/data names from this private env file.
# The stack's API/Worker/Deep services read the platform test key CI_LLM_API_KEY from this shell.
docker compose --env-file "$PPAP_CHECKER_LIVE_ENV" -f deploy/isolated-dev/checker-live.yaml up -d --no-build --pull never --wait api worker web
set -a
. "$PPAP_CHECKER_LIVE_ENV"
set +a
uv run --no-sync python -m scripts.run_checker_live
uv run --no-sync python -m scripts.run_checker_live --snapshot
docker compose --env-file "$PPAP_CHECKER_LIVE_ENV" -f deploy/isolated-dev/checker-live.yaml down --volumes --remove-orphans
```

Run the other scenarios with new local IDs/output directories. Do not rerun a
started operation after uncertain consumption; `--snapshot` only reads the original
operation. The local guard requires a clean tracked source, matching actual image
and running-container identities, this random Compose project, loopback-only PG/Silo
and product ports, and matching API/Web SHA readback before creating test data.
No fixture login, production credentials, notifications or formal usage charges
are enabled. Request/token limits are existing guards, not a fixed price guarantee.

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
