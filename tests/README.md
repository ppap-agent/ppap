# Tests

本文件记录测试归属和运行入口；CI 执行范围由 [Tests workflow](../.github/workflows/tests.yml) 定义。

## Ownership

单一模块的测试放在对应的 `packages/*/tests`、`apps/api/tests`或 `apps/worker/tests`。根目录 `tests/` 只放系统测试：

- `repository`: repository/build/config/tooling contracts
- `schema`: real PostgreSQL contracts
- `corpus`: assembled engine regression and regression fixtures
- `integration/product`: cross-owner production-store contracts
- `integration/runtime`: production runtime and backup/restore
- `e2e`: Python Playwright journeys on the real stack

CI consumes these physical owners. It does not maintain a second test manifest.

断言要等到状态成立，不读一次性快照；页面元素按角色和可访问名称定位。

## Repository and Python modules

Repository contract、Distribution closure 与 Python modules 的命令分别以 [Tests workflow](../.github/workflows/tests.yml) 的 `repository`、`distribution_closure`、`python_modules` job 为准；模块分组由 `python3 -m scripts.ci_shards plan` 发现，不在这里另列。

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

- 原件放在各公司目录 `assets/<公司>/samples/…`（`uaes`、`li`、`nio`），保留原测试分组，用提交时的文件名；每家客户的语料各占一组表，数据集名就是公司目录名，也是表名前缀。每家公司下另有 `rules`（客户规则原文）、`templates`（空白模板）和必要时的 `history`；UAES 的预检规则在 `uaes/prechecks`。
- 三家客户（`li`、`nio`、`uaes`）的材料分开存放，不混在一张表里；每家一组三个文件，表内只能出现本家数据集。
- `<客户>-cases.csv`：一次提交包含的文件。列为数据集、组合ID、文件ID、原件、提交文件名、角色、Suite版本、Checker组、Binding。Suite版本是检查程序目录路径，例如 `li/L08` 或 `uaes/U05`；必须恰好对应一个 Suite 来源，没有 latest 或回退。Checker组是 Suite key，Binding 是显式条款 ID 集合。原 PDF、Excel、DOCX 使用同一来源的 Suite；完整内容摘要在构建执行快照时计算，仍写入不可变 Suite revision、程序和输入身份。缺失、歧义、组别或 Binding 不一致在加载完整表时失败。表驱动 runner 调用正式 guest 的 `run_audit` 函数，进程/授权/Worker 路径由 Runtime 集成测试覆盖。
- `<客户>-oracle.csv`：人工结论。列为数据集、组合ID、文件ID、条款ID、人工真值、条款说明、人工判定依据、判定人、判定日期、已知差距。

- `<客户>-reviewed-rules.json`：本家每个已使用来源对应的可读人工判定上下文，保留完整规则正文、附录、文档类型、条款入口、实际方法／载体绑定和关联 Suite。加载时与当前来源逐项比较，仅忽略载体实现的 Python 类路径；支持的文件格式与条款到方法的绑定仍须一致。历史类路径保留为复核时的记录，不随实现重命名刷新；实际执行路径仍进入完整 Suite／程序摘要并接受测试。它不参与产品路由，也不会自动从当前实现重生成。

规则原文和人工结果是开发 Checker 的依据。实现修改可保留人工判定上下文，但完整 Suite／程序／输入摘要仍改变。规则、附录或绑定改变时，旧人工真值不能直接沿用；须独立重审并在带 `Oracle-Basis:` 依据的单独提交中更新 `<客户>-reviewed-rules.json` 及必要的人工答案，不能与实现或原件内容改动混在一起。`<客户>-cases.csv` 里的原件／文件身份、角色和 Suite／Binding 关联修改也遵循同一独立复核流程，不能把旧答案直接改绑到另一个已存在的来源。行序与 Binding 条款顺序不改变关联。完整规则正文比较是有意保守的，正文里的描述或来源元数据变化也需要显式复核。运行时按显式 Binding 调用实现，不按规则文字猜测方法。

`UAES 评测集.xlsx` 是 V3.33 产品资料包随附的 UAES 评测集（预检 58 行、审核 186 行，双层表头）。它是另一位评审给的参考判断，不是真值：真值以 `uaes-oracle.csv` 为准，二者冲突的裁决与订正理由记在 [`评测集订正.md`](../assets/corpus/评测集订正.md)。[`uaes_evaluation`](corpus/contracts/src/corpus_contracts/uaes_evaluation.py) 只读载入：工作簿按摘要固定版本，标准答案原样返回；存储地址的旧前缀 `../02_受审文件/` 按声明映射到 `uaes/samples/<零件>/测试文件/…`，缺陷样本在 `uaes/samples/<零件>/缺陷样本(-test-)/…`，不复制受审文件。

评测集用来分析样本运行结果与另一份人工判断的差异，自己不提交批次、不调用模型、不录回放，也不是必须通过的测试。审核明细的规则编号是资料包规则执行包的 rule_id，[`uaes-evaluation-rules.csv`](corpus/uaes-evaluation-rules.csv) 记录它与叶子条款号的对应（按文件类型与条款序，U08 没有第 5 条），只含编号不含规则原文。

样本运行 [`test_samples.py`](corpus/test_samples.py)（[`uaes_samples`](corpus/contracts/src/corpus_contracts/uaes_samples.py)）把每个零件目录 `uaes/samples/<零件>/测试文件` 按产品路径整批提交一次：文件检查、上传识别、预检（含内容级）、已匹配文件全部审核，`比对文件/` 一并提交为比对件。每份缺陷样本单独成批，沿用正常批次并换掉它对应的正常件（缺陷样本是文件夹时换掉同类目的全部正常件）。它只确认整批跑通，设置 `PPAP_CORPUS_REPORT_DIR` 时把每份文件的预检结果和全部条款结论写进报告。差异分析逐行比对，样本运行没有产出的行记为无法比较，`待裁决` 与自由文字列同样不判分，问题等级按 致命=A、主要=B、次要=C 比对条款默认严重程度：

```sh
PPAP_CORPUS_REPORT_DIR=.log/samples-report uv run --no-sync pytest tests/corpus/test_samples.py
uv run --no-sync python -m corpus_contracts.uaes_evaluation .log/samples-report
```

本机直接跑样本、候选检查程序或回放读取时，先用 [`in-app-image.sh`](corpus/in-app-image.sh) 放进本地 `ppap-app` 镜像里跑：和线上 Worker 同一个 LibreOffice 与转换沙箱（Excel 原件渲染、EMF 和旧格式转换在 macOS 宿主上会失败），当前代码只读挂载，平台模型取根目录 `.env` 的 `PPAP_MODEL_TEXT_*`、`PPAP_MODEL_VISION_*`，推理强度取 `.env.production`（与线上一致）。镜像依赖要与当前 `uv.lock` 一致，不一致时先重建镜像。

```sh
tests/corpus/in-app-image.sh -m corpus_contracts.candidate_run assets/uaes/rulebook/v1.0/candidates/uaes/U18 --part F01R204778_水管内接头 --json
```

回放按完整请求摘要在本测试的记录里查找，没有时再用其他测试为同一请求录下的回答，所以同一份样本被多个测试读取时只问一次模型。

`replay/<客户>/<组合>.json` 是录制的模型回答，记录模型看到的文字、图片尺寸和回答，可以直接阅读。

这些 CSV 是带 BOM 的 UTF-8，可用 Excel 打开，改动在 PR 中逐行可审。CI 的 `check_oracle_changes` 守护全部九个文件，按三家合起来的真值、文件绑定和复核语义判断：只在文件之间搬动的行不算改动，任何答案、绑定或复核语义的变化都须单独提交并写明 `Oracle-Basis:`。缺原件、重复行、公式、未知取值都在执行前报错。

[`test_cases.py`](corpus/test_cases.py) 是唯一的表驱动 runner，每次执行完整组合。`--dataset` 选数据集，`--ftype` 按组合划分（含 PDF 的组合只属于 pdf），`-k` 选组合。[`regressions/`](corpus/regressions) 放针对具体原件或路径的回归，[`psw/`](corpus/psw) 在专用镜像中运行。

### 官方 Suite 验证与发布

官方 Suite 的安装、回归与发布是三个动作。仓库自带的目录叶子已离线验证，`product.seed_standard_suites --apply --publish-defaults` 在入库后直接把每个叶子设为官方默认（`repository_default`，不需要语料证明行），已有官方指针的规则组不会被覆盖，关联叶子先发布；企业未自选时自动跟随，显示为已验证。`product.seed_standard_suites --apply` 只保存 `checkers/<客户>/<规则组>/` 目录叶子的不可变候选。平台以 migrator 角色调用 `SuiteReleaseStore.record_official_validation` 保存独立回归证明，再调用 `publish_official(revision_id, expected_revision=..., expected_validation_revision=...)` 发布；发布前用 `official_active(suite_key)` 读取官方指针修订（首次为 0）。发布在同一事务内重查证明、当前关联 Suite、共享库版本及对象字节，并以 CAS 更新唯一官方指针。验证或保存新候选不会替换当前官方版本；回滚也须显式发布准确旧版本。

首次启用先按关联依赖顺序验证并发布官方 Suite，再受理跟随官方版本的审核。既有 latest 或验证记录不被当作发布批准，未激活的官方源仍可按 revision 查看。没有固定企业版本的租户仅跟随已发布官方指针，企业明确固定某个官方或企业 revision 后不会被后续官方发布覆盖。显式发布只影响之后的新 Round，已冻结 Round 继续使用原修订。

`python -m scripts.official_suites` 是平台侧的显式发布工具，使用已有 Suite validation 和 official head，不新增发布台账。普通 corpus 测试通过、known-gap 放行或官方来源标签都不算发布证明。默认读取当前配置；执行前必须确认是本次授权的环境，开发测试不能借此操作生产。

```sh
# 执行全部对应样本，但不写验证或发布默认版本。
uv run --no-sync python -m scripts.official_suites validate --revision-id <official-revision-uuid>
# 明确使用真实文本/视觉模型，不读写 replay。
uv run --no-sync python -m scripts.official_suites validate --revision-id <official-revision-uuid> --model live
# 显式写入验证；CAS revision 来自 read。此步骤仍不发布。
uv run --no-sync python -m scripts.official_suites validate --revision-id <official-revision-uuid> --record --expected-validation-revision <revision>
# 只读：包含当前官方 head、验证 write_revision、proof_digest 和完整结构化报告。
uv run --no-sync python -m scripts.official_suites read --revision-id <official-revision-uuid>
# 另行审阅并授权后，才显式发布这一个证明。
uv run --no-sync python -m scripts.official_suites publish --revision-id <official-revision-uuid> --expected-head-revision <revision> --expected-validation-revision <revision> --expected-proof-digest <sha256>
```

操作人员用 `--revision-id` 选定确切版本。验证只消费仓库当前完整 corpus 中规则含义与该版本相同的全部目标，其他代际（如 v2 与 v3）的标记不沿用也不阻断；没有样本筛选或 proof JSON 导入选项。每个样本保留整个原件组合，只执行所选 Suite。运行正式 Core 的固定程序子进程，默认 `--model disabled` 不提供模型授权；`--model live` 显式允许文本 LLM 和模型 OCR 读取原件。两种模式都不录制或回放模型答案，不更改现有 timeout。它只适用于已审阅的平台程序；本地子进程不是敌对代码沙箱，不能据此声称具备生产隔离。

Live 与生产相同，按用途读取平台模型：文本 `PPAP_MODEL_TEXT_*`，视觉 `PPAP_MODEL_VISION_*`，编码 `PPAP_MODEL_CODING_*`（未配置时用文本模型）；没有文本或视觉 key 时不调用模型。`PPAP_CORPUS_OFFLINE=1` 与 live 冲突，普通 corpus 的 `PPAP_CORPUS_MODEL` 不会悄悄改变官方 runner 的模式。密钥只留在 host，子进程只拿本次 loopback 临时 grant。

CI 和开发环境的模型用量属于开发预算，不设限额。每个样本后输出已知用量，结束撤销授权并输出最终回执；用量未知会阻断验证，不自动重试。进程被外部终止可能没有最终回执，先核对既有输出和供应商账单，不能假定没有计费。回执只保留所选模型身份、endpoint 摘要和供应商确认的 token 数，不含密钥、原件、提示词或模型原始回答；返回模型名称和货币费用仍未知。证明绑定本次模型身份，`read` / `publish` 不需要模型密钥，也不调用模型。

这里的 `--record` 仅表示写入正式 validation，和模型答案录制无关。可将它与 `--model live` 及 CAS revision 同时使用；不带它仍会执行、可能计费，但不保存发布证明。跳过 replay 不能跳过人工来源、样本独立性或实际结果取证。

发布要求每条人工答案有已确认的判定人、ISO 日期和完整条款覆盖，并绑定确切原件、文件身份及已复核的 Suite／条款含义。人工确认的 PASS 可以不写人工判定依据，已有依据原样保留；NG、NA 仍须说明依据，UNABLE 仍是未解决结论。不能用自动生成的通用说明替代人工判断。至少四个不同原件样本，第四个及其倍数为独立验证样本，不能与训练样本共享任何原件。当前缺失的必填人工字段不会自动补齐。证据不足时返回 `not_started_evidence_incomplete`、具体原因和零个已执行样本，不启动检查、不把缺失当通过；返回码为 2。执行中断标记 `incomplete`，不自动重跑。人工 PASS 的说明可空不免除实际运行的取证要求：只有实际结果完全符合人工答案、断言数大于零且证据可定位时才可验证；UNABLE、ERR、缺失和 known-gap 都不能豁免。

证明绑定官方 Suite revision／完整内容、程序、共享库、当前直接依赖的 revision／内容、全组合输入与该 Suite 所属客户的两份人工表（cases、oracle）的身份。写入前检查原件仍相同；发布重新核对当前 corpus，并在现有官方事务锁下同时检查 head、validation revision、proof digest 和依赖选择。先发布直接依赖（依赖关系见各 Suite `rules.json` 的 `related_suite_keys`）；保存候选或完成验证不会自动更新下游。

操作使用不可变、已审阅的发布 checkout 和原件；运行期间不能编辑它们。库或文件改变须重新验证。工具在写入前输出本次 proof／CAS 身份，异常不自动重试。结果未知时先用原 revision 执行 `read`，核对原 proof digest、验证 revision，以及发布后的目标和 head revision。读不到相同记录或已被后续写入覆盖时仍属未知，不能据此断言原写入未发生。测试中的四个程序化工作簿只证明执行和发布协议，不是客户效果验收或生产发布证据。

### 结果判定

产品结果必须等于人工真值，没有比例容错：`UNABLE`、相反结论、ERR 和缺失都阻断。

产品暂时做不到的结论不改人工真值，写进“已知差距”，格式为 `<产品结果> <原因>`，例如 `UNABLE .xls 原件尚不能走视觉读取`。产品修好后这一列会让用例失败，提醒删除。

### 模型回答

默认回放：完整请求摘要与录制一致才复用回答，摘要包括图片内容、提示词、Schema、预算、视觉请求选项和独立观察编号；图片尺寸只用于可读展示。视觉选项是配置的推理强度 `PPAP_MODEL_VISION_REASONING_EFFORT`（语义见 [AGENTS.md](../AGENTS.md#开发)「模型来源」；本地未设置时语料入口取 `.env.production` 的值），严格离线、带 Key 回放、CI 与线上一致。缺少匹配录制时，只有显式配置测试模型才真实调用，用例通过后记录回答。用例失败时不记录，下次重新询问；因此通过用例的录制仅作逻辑回归样本，不作为准确率或稳定性统计。测试通过独立的 ModelAccess scope 授权 Core，优先使用完整 PPAP_MODEL_VISION_* 配置，其次完整 PPAP_MODEL_TEXT_* 配置，同一测试的文本和视觉工具共享该凭据、endpoint 和模型。生产无凭据 scope 不受此测试逻辑影响。

本地无付费验证使用 `PPAP_CORPUS_OFFLINE=1`；即使环境已有 Key，也只回放缓存，任何缺失记录都令测试失败且不会调用提供方。没有测试凭据时同样只读回放。`PPAP_CORPUS_MODEL=live` 必须有完整测试配置，不能与 offline 同时使用。回放验证取证和判定逻辑，不验证新模型或部署的能力；真实视觉验收使用预先固定的样本和运行次数，并保留全部运行结果。

`PPAP_MODEL_VISION_REASONING_EFFORT` 只用于视觉请求。实际强度进入授权身份、OCR 配置摘要和 Replay 请求键；其他强度下录的回答不能用来验证新强度。当前 CI 专用 OpenRouter `deepseek/deepseek-v4.1-flash-1017` 已用合成图像核验 `reasoning.effort=none`，仍使用 4096 输出上限和零自动重试。更换端点或模型须先核对其支持的参数。

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
docker buildx bake app
docker build \
  --build-arg PPAP_APP_IMAGE="ppap-app:${PPAP_BUILD_SHA}" \
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

Browser E2E (`tests/e2e`) needs `uv sync --locked --group e2e` and `uv run --no-sync python -m playwright install chromium`. The journeys drive the baked API/Worker/Web/Controller images in isolated-dev through public HTTP; CI runs them in Product integration:

```sh
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake release
bash scripts/run-compose-tests.sh tests/e2e -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml down -v --remove-orphans
```

Fact-answer and checker-workspace journeys under `tests/integration/product/` document their own scope and local-only overrides in their module docstrings; they run with the Product integration prerequisites above.
