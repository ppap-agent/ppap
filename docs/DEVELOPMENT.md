# 开发

普通工作只需要当前 owning Issue、相关源码和最近测试。Issue 表达验收结果，不表达实施顺序。

组织内资产的默认使用规则见 [AGENTS.md](../AGENTS.md)，这里不重复权限政策。

## 当前配置

应用配置的 executable authority 是 typed Settings、[`.env.dev`](../.env.dev) / [`.env.production`](../.env.production) 里的默认值，以及 [`.env.example`](../.env.example)。

- Secret 只通过本地 gitignored `.env`、GitHub Secrets 或部署 Secret 注入。
- 已提交的 `.env.dev` 填 isolated-dev Settings 默认值；[`.env.production`](../.env.production) 填生产仍可沿用的默认值，口令、S3 endpoint、公开 URL 和 LLM 留空。生产不能沿用 isolated-dev 口令和 `http://127.0.0.1:9000`。
- 生产 compose 用 Docker Compose 的 `--env-file` 注入。CI fixture 是 `deploy/compose.env.example`。真实部署把填好密钥的文件交给同一个 `--env-file`。`init-db` 读取 bootstrap/migrator 口令，先用 `psql` 执行 `migrations/bootstrap.sql`，再通过 `sh /migrations/apply.sh` 按 `NNNN_*.sql` 文件名顺序执行 product migrations；`init-bucket` 用 Silo `mcli` 确保对象桶。API/Worker 只插值各自拥有的字段（含选定的 `PPAP_PG_HOST`/`PPAP_PG_PORT`，以及 API 的飞书变量），不含 bootstrap/migrator 或对方角色口令。
- 把 API/Worker 接到已有的 self-hosted InsForge PostgreSQL 时，用 `docker compose -f deploy/insforge/compose.yaml --env-file deploy/insforge/env.example up -d`。该 overlay 不启动 postgres，加入外部 `insforge-network`。Custom Compute 规格在 `deploy/insforge/compute/`。
- `.doc` 转换是默认行为，没有开关变量。
- 可选 capability 缺配置只影响该 capability，不阻止无关开发。Deep 由 `PPAP_DEEP_CONTROLLER_SOCKET` 表示是否配置；未配置时 Core 仍可启动，Deep 轮次明确失败而不回退 Standard。Live Deep 用 `deploy/isolated-dev/deep-live.yaml` 挂 UDS。Controller 默认 process runtime；gVisor runtime 名称、guest 镜像和 `PPAP_DEEP_RUNTIME` 只属于 Controller 进程环境，不进入 Worker typed Settings。production 默认不向客户提供 Deep（`public-config.deep_offered=false`，API 拒绝 `analysis_mode=deep`）；isolated-dev/test 仍可用于协议和隔离验证。TKE current 在 process sidecar 真实 executor 可用后设置 `PPAP_DEEP_CUSTOMER_ENABLED`。
- 预览切流用 `PPAP_CURRENT_TENANT_ALLOWLIST`（逗号分隔的 Feishu `tenant_key`）。空名单不限制。生产目录写入海岸线租户。`PPAP_USAGE_CHARGE_ENABLED=false` 时受理不写 `usage_events`，预估点数为 0。
- 已有组织内配置可以直接用于迁移/开发；终态配置归本仓 Settings。
- 新增配置项前先确认它代表独立运行决策，而不是可推导内部细节。

## 当前命令

Local 与 CI 使用同一 ownership。CI 不再维护与目录结构脱节的测试分类。

Repository contract：

数据库 migration ordering authority 只有 numbered filenames。Compose / InsForge / TKE / Schema CI 共用 `migrations/apply.sh`；仓库不存在 `apply.sql` 或第二份顺序清单。

TKE 的 Kubernetes authority 是 `deploy/tke/kustomization.yaml`。CI 与真实 apply 都通过 `deploy/tke/render-current.sh` 得到同一 rendered object stream；CI 对它执行 kubeconform，`apply-current.sh` 对同一 stream 执行 server-side dry-run 后再 apply。edition proxy 的 nginx 内容只存在于 `deploy/tke/edition-proxy.conf`，ConfigMap 由 Kustomize `configMapGenerator` 生成。

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

Distribution closure：

```sh
# 不先执行 root uv sync；脚本为目标 member 构建 wheel closure，
# 在 /tmp clean venv 中安装、从非 repo cwd import，并运行 owner tests。
python3 scripts/validate_distribution.py --discover
python3 scripts/validate_distribution.py --member ppap-core

# regression proof：删除一个真实 dependency declaration 必须 fail closed。
python3 scripts/validate_distribution.py --prove-missing-dependency
```

service member 运行 owner tests 时需要与 CI 相同的 isolated-dev PostgreSQL fixture。root `uv sync --locked` 仍用于快速 workspace 开发，但不再作为 distribution correctness proof。

Python module groups：

```sh
# One pytest process per physical owner. CI discovers every packages/*/tests and services/*/tests.
uv run --no-sync pytest packages/ppap_application/tests
uv run --no-sync pytest packages/ppap_core/tests
uv run --no-sync pytest packages/ppap_corpus_contracts/tests
uv run --no-sync pytest packages/ppap_deep/tests
uv run --no-sync pytest packages/ppap_excel_audit_engine/tests
uv run --no-sync pytest packages/ppap_ocr_adapter/tests
uv run --no-sync pytest packages/ppap_product_store/tests

export COMPOSE_PROJECT_NAME=ppap-api
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest services/api/tests
export COMPOSE_PROJECT_NAME=ppap-worker
uv run --no-sync pytest services/worker/tests
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

Root system contracts：

```sh
export COMPOSE_PROJECT_NAME=ppap-product-integration
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest tests/integration/product
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans

export COMPOSE_PROJECT_NAME=ppap-schema
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest tests/schema
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

Browser E2E 是 Python Playwright（`uv run --no-sync pytest tests/e2e`），对着 isolated-dev。生产 runtime/backup 是 `uv run --no-sync pytest tests/integration/runtime`。Corpus 的 Excel/PDF/scanned/V3/PSW 命令见 [TESTING](TESTING.md)。Pairs/PSW 分别由 `tests/corpus/pairs` 与 `tests/corpus/psw` 的物理目录发现；纯 Corpus contract tests 跟随 `ppap-corpus-contracts` owner。

默认 merge contract 是 required groups：Web、Repository contract、Python modules、Distribution closure、Schema、Corpus、Product integration。`CI Gate` 只汇总这些 required groups，不把“是否 deterministic”当成入 Gate 的条件。Corpus 即使 cache miss 调用真实 OCR / model 仍是 required。当前不做 change-aware path skipping。`tests/live` 表示不进入默认 merge contract、需要显式执行的真实环境验证，不是所有带外部依赖的测试；InsForge native lifecycle 同样不进入默认 Gate。

single-owner Python tests 位于各自 `packages/*/tests` / `services/*/tests`。pytest fixture lifecycle 直接由 owner `conftest.py` 提供；不动态加载 sibling support/test modules，也不通过 `request.instance` 建立注入框架。根 `tests/` 只保留 `repository`、`schema`、`corpus`、`integration`、`e2e`、`live` 六类 system-level authority。`tests/integration/product/conftest.py` 通过 pytest-native fixtures 提供该 suite 的数据库 reset/fixture seeding 与 `MemoryObjects` object-storage boundary fake，不实现第二套产品状态机；仓库明确不保留 `store_fixture.py` 这类 test-module import seam。API / Worker tests 各自保留 owner-local copy，不把 integration 当 library。pytest 不注入仓库根 `pythonpath`。

## 工程工具链

当前实现是 Python 3.12、uv workspace（每个 production owner 一份 `pyproject.toml` + 统一 `src-layout`）、已提交的 `uv.lock`，以及 `apps/web` 的 pnpm/TypeScript。质量检查是 `git show --check` 与 `compileall`；Python 测试统一由 pytest collect/execute；现有 `unittest.TestCase` 仅作为测试类写法保留；前端白盒/契约测试是 `pnpm test`；跨栈浏览器 E2E 是 Python Playwright；镜像是 Docker Bake；运行时是 Docker Compose 或 InsForge native lifecycle（`@insforge/cli@0.2.8`）。

CI 以项目原生 metadata / lockfile 为 authority，不再另写一套安装语义：

- Python 依赖：`uv.lock`。`dev` 是默认 dependency group，Repository contract / Python modules / schema / corpus 使用 `uv sync --locked`。Product integration / Playwright E2E 另加 `--group e2e`。安装之后用 `uv run --no-sync` 执行命令，避免 `uv run` 再 sync 一次。 Distribution closure 不复用这个完整 workspace environment：它构建目标 distribution 的 workspace wheel closure，并在 clean venv 中显式安装；registry dependency 版本仍由 `uv.lock` 生成的 constraints 固定。
- Container build graph：`docker-bake.hcl` 是 API / Worker / Web Dockerfile、context、build args 与 output tag 的唯一 authority。isolated-dev、production、InsForge、remote-dev Compose 只消费 image；本地、remote runner 与 CI 都先运行 `docker buildx bake`，再用 `docker compose ... up --no-build`。`scripts/verify-container-graph.mjs` 只读取 `docker buildx bake --print` 与 `docker compose config --format json` 的 native output 做 PPAP image-identity policy。
- Python 解释器：`.python-version`（3.12）。`pyproject.toml` 的 `requires-python = ">=3.12"` 是兼容范围。
- Node：`.node-version`（22）。workflow 通过 `actions/setup-node` 的 `node-version-file` 读取。
- pnpm：`apps/web/package.json` 的 `packageManager`。`corepack enable` 后 Corepack 只从当前目录向上读取该字段，不看 `pnpm --dir`。因此 workflow、Web Dockerfile 和 isolated-dev Vite overlay 都在 `apps/web` 里启动 pnpm，不再重复 `pnpm@…` 字面量。安装仍是 `pnpm install --frozen-lockfile`。CI 的 pnpm 消费 job 在 Node setup 前通过 `GITHUB_ENV` 设置 `COREPACK_HOME=$RUNNER_TEMP/corepack`，让 Node setup 与后续命令共用当前 runner 用户可写的缓存，不依赖系统目录写权限。
- GitHub Actions：第三方/官方 action 钉到不可变 commit SHA，行尾注释写清已解析的 tag（例如 `actions/checkout@11bd719… # v4.2.2`）。本地 composite action 用路径。当前 SHA 来自 GitHub 上对应 release tag 的剥皮 commit，不是编造的 hash。
- uv 二进制：由 `astral-sh/setup-uv` 的 `version: "0.9.26"` 引导安装。这是 bootstrap pin；安装 uv 本身必须发生在读取 `uv.lock` 之前。

有意保留的重复（消除它们需要模板化 Dockerfile 或另做包装层）：

- `deploy/app/Dockerfile` 继续用 `python:3.12-slim-bookworm`。uv 来自钉死 digest 的 `ghcr.io/astral-sh/uv:0.9.26`。生产安装是镜像内 `uv sync --frozen --no-dev --no-editable`，不设置 `PYTHONPATH`，也不用 pip。
- `deploy/web/Dockerfile` 的 `node:22-alpine` 与 `deploy/isolated-dev/web-dev.yaml` 的 `node:22-bookworm` 仍写在镜像 tag 里。
- `deploy/deep/package.json` 与 `package-lock.json` 拥有 `@xai-official/grok@1.0.40`。镜像用 `npm ci` 安装，`npm audit signatures` 校验 registry 签名，`verify-grok.mjs` 只固定解压后的 guest 二进制。

CI 依赖 capability，而不是发行版。workflow 可以安装 uv/Python、Node、Corepack/pnpm 和项目依赖；不得用 `sudo` / `apt` / `yum` / `apk` / `systemctl` 修复 runner，也不得安装或启动 Docker。Python modules 的 services variant、Schema、Corpus PSW、Product integration 和 InsForge lifecycle 要求 runner 已提供 Docker Engine、Compose 和 Buildx；缺任一能力时 fail-fast 输出 `runner does not satisfy ppap-docker capability`。Web、无 PostgreSQL 的 Python modules 和普通 Corpus 不要求 Docker。Repository contract 需要 Docker Engine、Compose 和 Buildx：`docker compose config`、`docker buildx bake --check`/`--print`、nginx 黑盒、Kustomize 与 kubeconform。`actionlint` 1.7.7 检查 workflow 语法。InsForge static config 仍由 `verify-config.mjs` 调用 `docker compose config`。live InsForge lifecycle 需要 `local start` 使用的 runtime Docker capability，只在 InsForge 相关 path、手动 dispatch 或每周 schedule 上运行。LLM / Feishu readiness 是真实外部 probe，手动 dispatch 或每周 schedule，不进 `CI Gate`。Product integration 对同一 `PPAP_BUILD_SHA` 只 `docker buildx bake` 一次，生产 runtime/backup 与 isolated-dev E2E 复用这些镜像（E2E 使用 `compose up --no-build`）。PSW 是 Corpus matrix 的 purpose-built container variant，单独 `docker buildx bake worker` 后从 worker 派生测试镜像。CI Compose 使用 `ppap-ci-<purpose>-<run_id>-<run_attempt>` 作为 project name，cleanup 在独立 `if: always()` step。现有 Compose stack 仍 bind-mount 仓库内 `migrations/`，因此 services、Schema、Product integration 和 InsForge lifecycle 需要 Docker daemon 能看到 runner 使用的同一份仓库路径。后续若把 Ruff、Pyright 或 Vite 收进来，直接调用原生 CLI，不另起 PPAP dispatcher。

#15 剩余：把本仓客户入口切到 current `ppap`；compose 验证不等于客户 cut-over。不拆除 `ppap-agent`：它是算法验证基地和参考实现，并继续服务其它用户。

## 迁移工作

选择迁移粒度时以**最快形成稳定客户路径且降低终态上下文**为准。一个 package/能力域整体迁入更快时就整体迁入；迁入后立即把 tests/config 归本仓并删除跨仓长期依赖和无消费者代码。

Engine migration 的具体 baseline 见 #3；飞书 capability baseline 见 #11。不要把旧算法已有 failure 变成迁移 blocker。

## 最短交付路径

当前 Issue → 复用/迁入现成资产 → 跑 baseline/当前测试 → 修新增 regression 或产品缺口 → 必要真实环境验证 → 当前 PR → 尽快打通客户路径。


## Web wire contract

FastAPI/Pydantic OpenAPI is the machine authority for Web JSON wire shapes. The Web does not maintain a second DTO registry.

The generated TypeScript artifact is owned by the standard `openapi-typescript` CLI pinned in `apps/web/package.json` and `apps/web/pnpm-lock.yaml`. From `apps/web`, run:

```sh
pnpm api-types
```

That command serializes the real FastAPI `app.openapi()` schema and pipes it to `openapi-typescript --alphabetize`. CI runs the same generation and rejects any diff in `apps/web/src/generated/openapi.ts`. The generated artifact owns wire types only; retry/readback policy, localized error copy, labels, caches, and UI view-model state remain handwritten.

UI-first contracts without a runtime operation are explicit OpenAPI components marked `x-contract-stage: ui-draft`. They share the same generation path, but do not document fake endpoints or imply persistence, matching, billing, or authorization has been implemented. Preview adapters stay outside the production build entries.

文件内容预检查使用 `POST /file-validations`（同一 `request_id`、文件与版本幂等受理）及 `GET /file-validations/{validation_id}` 读回。`GET /files/{file_id}/versions/{version_id}/validation` 可恢复该版本最新的检查请求；每次读取仍检查当前文件权限。它复用现有 Worker 队列，不创建草稿、审核轮次或用量记录，也不调用模型或匹配规则。任务租约失效显示 `unknown`，不自动重新领取；原 Worker 的有效回执仍可收敛。

`FileVersion.ready` 只表示原件已保存；预检查 job 的 `succeeded` 只表示检查已完成。只有其 `result.state=valid` 才表示可进入后续识别。损坏、加密、无内容与检查基础设施错误分开表达，安全预算超限为 `error`，不是文件审核 NG。OOXML 检查有展开量、条目及 XML 访问预算；旧 DOC 保留既有 25 MiB 受限转换上限。该接口不表示 200 MB 上传链路、新版任务持久化或后续审核已经全部接通。

确认前的任务使用 `POST /audit-tasks`、`GET /audit-tasks` 和 `GET /audit-tasks/{id}`。创建请求固定文件版本、每份预检查回执、选中/未选/已移除来源、模式和客户倾向；同一请求 ID 不允许替换输入。它复用私有 `audit_drafts` 和逐文件识别 job，在同一事务分配 UTC 日编号并入队，不创建审核轮次或消费。Worker 只消费创建时冻结的生效候选；按客户×文件类型匹配，不用无客户规则兜底。读取固定 job，不取该文件最新识别；提取/模型失败与未匹配分开。

`POST /audit-tasks/{id}/end` 仅结束全部选中文件已明确未匹配的任务；失败或未知不能当作零匹配。任务沿用私有草稿授权，不出现在旧草稿清单，也不能通过旧草稿修改/受理接口绕过固定输入。本阶段只提供识别和零匹配终结，不提供既有任务输入修改、正式受理或计费；完整 `AuditTask` 的审核/报告部分仍为 UI-draft，页面数据接入及真实模型质量另行验证。

## UI-first 交互预览

在 `apps/web` 运行 `pnpm preview:workbench`，打开 `http://127.0.0.1:4185/__preview?page=audit`。场景切换会重置页面；宽度与主题切换保留当前交互。识别中和飞书读取中场景用预览台的“返回一次示例读取／令一次示例读取失败”控制响应，包括弹窗内的读取，不用定时器假装真实进度。

桌面产品导航可折叠为带文字提示的图标栏，仍保留四个入口和联系我们；折叠仅改变本页显示，不重建任务或写入业务状态。手机 Sheet 始终显示完整文字；产品栏与任务栏保留各自的 1024／1280 px 断点，报告页仍可打开任务列表。

`matching-gaps` 在取消选择控制计划后，显示 PFMEA 已匹配规则但缺少跨文件比对资料；返回补选后再识别可清除提示。`restore-progress` 恢复固定样例的部分结果：有效 NG 与运行、排队项分开，刷新不推进样例，主动“读取审核结果”后才显示最终结果。`restore-task-conflict`、`restore-report-conflict` 分别演示文件未结束或首次报告仍生成时，任务汇总误报完成；有效逐文件依据和已就绪报告仍保留。带原请求回执的报告重生成不重开审核任务。这些场景不代表任意上传已保存或真实审核已经执行。

开始识别前的上传草稿按每次“新建审核”分别保留在本页内存，浏览器历史仅记录草稿 ID。返回可恢复文件、选择、模式和传输回执；在途回执先读取原请求，不自动重传。草稿创建任务后，旧历史入口只恢复该任务，不再独立创建第二个任务。刷新与跨设备恢复仍依赖真实业务持久化，不能由本地草稿替代。`checking-loading` 可手动交付预检查迟到响应，验证离开并返回后旧响应失效。

`observe-matching`、`restore-observe` 演示当前可见任务的自动读取；每次返回仍由事件控制条显式交付，不用计时器生成进度。界面默认每 3 秒读一次，隐藏或离开面板暂停，重新可见/聚焦补拉；失败按 6/12/24/30 秒退避，成功恢复基础间隔。未知的报告生成、ZIP 或分享请求保留专用核对入口，不由通用自动读取覆盖。`restore-observe-regression`、`restore-observe-missing-progress` 演示拒绝同轮较早或缺失的进度，`restore-observe-unresolved` 演示未知报告请求的暂停；`match-rule-missing` 演示缺少候选规则时禁止确认。其他手动场景显式关闭自动读取，以便单独控制原来的交互。

`restore-estimate` 展示服务合同中的任务结束时间区间及观察时间；结束包含 D3 规定的首次报告处理。`restore-estimate-overdue`、`restore-estimate-invalid`、`restore-estimate-reversed` 分别演示上界已过、缺少时区和区间倒序；无可靠估计时明确说明，不用浏览器倒计时增加审核计数。过期说明随现有读取或界面操作更新，终态不再展示进行中的估计。固定样例仅验证显示，不代表真实服务已能预测耗时。P4/P5 与历史文件信息区分零件名称 `part` 和可选零件号 `part_number`；`confirm-metadata-conflict` 演示拒绝确认回执静默替换识别信息，历史详情使用所选轮次的信息，不取档案顶部的信息替代。

原件读取使用 `scene=original-error`、`original-loading` 或 `original-unavailable`，选择三类文件示例后进入审核并核对 Excel 原件。它们复用产品原件阅读器，演示读取失败、重读和乱序返回；下载仅返回该冻结版本的明确示例 XLSX，不是客户原件，也不验证真实 Office、排版或定位。任意上传不生成替代原件，不向产品 API 发送示例身份。

`restore-display-ready` 演示 D8 的四字段显示更正；原始识别信息仍可展开核对，冻结文件、规则、结果与既有报告不变。`restore-display-rejected/unknown/conflict/loading/denied/stale/read-failed` 分别演示明确拒绝、未知结果、版本冲突、迟到回执、撤权、旧投影和读取失败。未决保存关闭后保留原请求，只读回、不自动重发；此恢复限于当前页面生命周期。`archive-display` 按所选历史轮次显示更正，不能编辑。预览使用 `task=preview-recovery-task`，或在预览台选择对应场景；真实保存、CAS、权限和持久化仍待 APP-2 接入。

文件档案及历次审核使用 `scene=archive-ready`。`archive-task-allowed` 单独演示有来源任务读取许可时，经完整 P5/报告页返回所选历史，不改开最新审核。历史报告地址包含完整冻结结果引用，切换主题和刷新后仍读取同一次审核；这只恢复固定样例，不表示任意上传已持久化。档案、来源任务、原件和报告分别使用服务提供的访问结果，不从一项权限推定另一项权限。

历史链接带 `auditRef` 或 `archiveReport=1` 却缺少文档身份时，显示可返回的错误，不静默显示档案列表；只有文档身份的正常入口仍可打开默认历史。

`reports-zip-failed` 在同一固定报告集合中显示逐报告准备状态与失败原因；改变当前勾选不改变旧包明细。`reports-zip-write-failed/pending-failed/legacy/no-retry` 分别区分最终 ZIP 写入失败、尚未确认、未提供逐项状态及服务未允许重试。`reports-zip-outcome-*` 故意返回缺项、重复、错误报告引用、空失败原因或矛盾就绪状态；只读原请求即可恢复，不重新打包。下载与文件分享使用同一明细校验；“打包准备完成”不表示有部分 ZIP 可下载，也不改变单 PDF 状态。这些仍是 UI-draft 合同与受控预览，不确定真实重试或收费政策。

`reports-zip-foreign` 在读取报告状态时返回内部自洽但属于其他任务／轮次的 ZIP；任务页拒绝下载、读回该包或另行打包，重新读取任务可恢复正确范围。当前勾选不决定旧包范围，档案页仍可跨任务打包授权的固定历史报告。

报告分享的未发送草稿在当前报告页内保留接收人与交付方式；更换冻结报告范围或选择“新的分享”才重置。重开会重新核对目录，未读到的接收人保持待核对，可单独核对或移除；目录失败不允许发送，旧读取迟到也不能恢复资格。`reports-share-directory-org-changed` 演示组织变化后重新选择，`reports-share-external` 演示关闭期间读到另一笔未决请求：原请求优先，读回成功仍保留其结果，不自动发送旧草稿。共用选择器同样用于模板分享；这不承诺刷新、跨设备保存或真实飞书授权与送达。

客户模板库使用 `scene=templates-ready`。XLSX/ZIP 是明确标识的本机示例字节，不是客户飞书文件。覆盖度使用服务合同中的显式 17 类字典及生效规则类型并集，不能直接以当前 `/rule-types` 的 18 项或模板附件数代替。模板预览固定文件版本；客户 ZIP 固定文件夹修订和完整集合。全局支持未取得有效配置时显示不可用，不虚构联系方式。

`templates-zip-recovery` 演示拒绝不完整模板包后，显式重读同一客户、搜索与页码，再下载新文件夹修订的完整包；重读不自动下载或重建审核任务。重选当前客户保留清单与筛选。确认审核结果未明时，可携带原确认请求、任务标识和说明进入同一支持浮层；关闭后仍只读回原请求，不重复确认。

支持浮层使用 `support-ready`、`support-error`、`support-qr-error` 和 `support-loading`，分别走查复制/邮箱链接/图片放大、读取恢复、图片失败和返回后迟到读取。电话不可拨打，邮箱是保留的 `.invalid` 地址，图片明确不是服务二维码；不代表已取得真实客服配置或完成飞书连接。Browser Back 关闭旧浮层并保留当前文件，二维码放大关闭后回到触发按钮。

`restore-usage-notice` 与 `restore-usage-completed` 分别演示审核中、已结束任务的用量提示。从提示联系支持可复制当前任务、轮次和原提示；关闭后保留文件、条款与筛选，不执行扣费或重审。

模板提报、报告／模板分享和权限修改的未确认或失败操作可进入同一支持浮层，复制打开时的原请求、各自尝试及固定对象信息。原业务弹窗不关闭，迟到结果继续更新；支持快照只读，不自动重发、发送附件或创建工单。关闭后优先回原按钮，按钮消失时回原弹窗；普通全局支持不保留上一次操作信息。`feedback-no-retry` 演示服务未允许再提交时的支持出口。本页 `client-` 编号仍只是参考，不冒充服务端 trace。

模板分享固定完整客户集合，不受搜索和分页影响；链接不自动授权，撤权不能收回已发送的 ZIP 副本。`scene=templates-share-partial` 演示逐目标失败，未知目标只读回，明确失败且服务允许时才单独重试。文件夹权限使用独立的 `permission_revision` 和服务提供的单项动作；`templates-permissions-unknown` 演示读取当前权限不能代替原修改回执。关闭、切客户或撤权后保留原请求的读取入口；`templates-share-race` 和 `templates-permissions-race` 可手动返回迟到回执。这些预览不发送飞书消息，也不写入 Drive 权限。

补充模板的三个入口共用本页附件与提报状态。`scene=feedback-unknown` 演示提交后丢失回执；`feedback-submit-loading` 和 `feedback-upload-loading` 可手动返回迟到结果。关闭后允许读取原请求，只有明确终结且允许重试才建立新尝试。此预览只保留本页内存中的附件字节，不写入飞书运营表，也不自动改变审核规则。

用量记录使用 `__preview?page=audit&view=usage&scene=usage-ready`。四卡读取服务提供的自然月汇总，不由明细筛选计算；每行固定一份文件的一次 500 点业务消费，免费／付费分配和技术重试不增加行数。历史入口复用冻结审核引用，不回退最新结果。`usage-quota-unknown`、`usage-revoked`、`usage-race` 分别演示未知额度、撤权和迟到读取；只读刷新不触发外部收费。这是共享 DTO 与明确样例，不是现有整轮用量账的逐文件投影，也不验证真实结算。

在仓库根目录运行 `PPAP_PREVIEW_URL=http://127.0.0.1:4185 uv run --no-sync pytest tests/e2e/test_audit_preview.py tests/e2e/test_template_preview.py tests/e2e/test_template_feedback_preview.py tests/e2e/test_template_access_preview.py tests/e2e/test_usage_preview.py -q`。需安装 `e2e` dependency group。这些测试只证明界面交互；不证明真实上传、OAuth、解析、匹配、审核、收费或持久化。预览服务不代理业务 API，生产入口不包含 fixture adapter；真实产品浏览器测试不因此替换或跳过。

上述预览测试未配置 `PPAP_PREVIEW_URL` 时，可在未安装 Playwright 的环境中收集，并按原条件跳过；显式配置地址时必须安装 `e2e` group，缺少依赖会使收集失败。
