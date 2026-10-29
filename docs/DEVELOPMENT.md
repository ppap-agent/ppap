# 开发

普通工作只需要当前 owning Issue、相关源码和最近测试。Issue 表达验收结果，不表达实施顺序。

组织内资产的默认使用规则见 [AGENTS.md](../AGENTS.md)，这里不重复权限政策。

## 当前配置

应用配置的 executable authority 是 typed Settings、[`.env.dev`](../.env.dev) / [`.env.production`](../.env.production) 里的默认值，以及 [`.env.example`](../.env.example)。

配置值按显式 `Settings(...)` 参数、进程环境、显式指定的 dotenv 文件、默认值依次取值。代码字段名和环境变量别名遵循同一优先级；显式 `False`、空字符串、空来源列表不能被较低优先级的值替换。`pydantic-settings` 最低为 2.12.0，以使用其别名合并修复；不在本项目另建配置合并层。

- Secret 只通过本地 gitignored `.env`、GitHub Secrets 或部署 Secret 注入。
- 已提交的 `.env.dev` 填 isolated-dev Settings 默认值；[`.env.production`](../.env.production) 填生产仍可沿用的默认值，口令、S3 endpoint、公开 URL 和 LLM 留空。生产不能沿用 isolated-dev 口令和 `http://127.0.0.1:9000`。
- 生产 compose 用 Docker Compose 的 `--env-file` 注入。CI fixture 是 `deploy/compose.env.example`。真实部署把填好密钥的文件交给同一个 `--env-file`。`init-db` 读取 bootstrap/migrator 口令，先用 `psql` 执行 `migrations/bootstrap.sql`，再通过 `sh /migrations/apply.sh` 按 `NNNN_*.sql` 文件名顺序执行 product migrations；`init-bucket` 用 Silo `mcli` 确保对象桶。API/Worker 只插值各自拥有的字段（含选定的 `PPAP_PG_HOST`/`PPAP_PG_PORT`，以及 API 的飞书变量），不含 bootstrap/migrator 或对方角色口令。
- isolated-dev Compose 默认关闭业务收费，避免无飞书授权的浏览器流程测试调用真实计费；需要验证收费时显式设置 `PPAP_USAGE_CHARGE_ENABLED=true` 并提供额度接口。此默认值不改变生产 Compose 或 Settings 的收费政策。
- 把 API/Worker 接到已有的 self-hosted InsForge PostgreSQL 时，用 `docker compose -f deploy/insforge/compose.yaml --env-file deploy/insforge/env.example up -d`。该 overlay 不启动 postgres，加入外部 `insforge-network`。Custom Compute 规格在 `deploy/insforge/compute/`。
- `.doc` 转换是默认行为，没有开关变量。
- 可选 capability 缺配置只影响该 capability，不阻止无关开发。Deep 由 `PPAP_DEEP_CONTROLLER_SOCKET` 表示是否配置；未配置时 Core 仍可启动，Deep 轮次明确失败而不回退 Standard。Live Deep 用 `deploy/isolated-dev/deep-live.yaml` 挂 UDS。Controller 工厂要求显式选择 `PPAP_DEEP_RUNTIME=container-only`，并将 `PPAP_DEEP_SANDBOX_PROFILE` 设为 `off` 或 `ppap`；这些运行配置不进入 Worker RPC，guest 命令和路径由可信 Controller 管理。生产默认不向客户提供 Deep，需显式启用 `PPAP_DEEP_CUSTOMER_ENABLED`；isolated-dev/test 仍可用于协议验证。开关、sidecar 启动和 UDS 可用均不证明客户审核质量或强隔离，具体限制见 [Deep 部署说明](../deploy/deep/README.md)。
- 飞书任务通知由 API 进程发送，`PPAP_FEISHU_NOTIFICATIONS_ENABLED` 默认 `false`；开启还需配置该应用的 ID、Secret 和有效授权。`GET/PUT /audit-tasks/{id}/notification` 读取、保存创建人的完成通知设置，修改必须带 `expected_revision`；该设置只控制完成通知，不控制待确认或开始审核通知。通知未进入发送边界前可修改，进入后只读回状态。发送结果未知不自动重发；这些接口不证明飞书已送达或已读取。
- 已授权的单任务通知联调可调用 `dispatch_task_notification(..., task_id=...)`，范围在领取工作及整理过期发送状态前生效；保持常驻 API 的全局通知开关关闭，避免顺带处理历史队列。历史补发须另行授权，仍使用已有意图、阶段校验和回执，不重置已发送或未知状态。
- 机器人已启用不代表具有发送权限；最小发送权限为 `im:message:send_as_bot`，须在发布及租户授权后实际生效。明确的飞书 2xx／4xx 非零错误码记为发送失败；超时、5xx 或无有效回执仍为未知。联调以真实消息 ID 和客户端可见性核对送达，不为此扩大聊天读取权限，也不把发送成功当作用户已读。
- 报告附件上传使用 `im:resource`。真实接口错误仍可能列出旧的 `im:resource:upload`，但控制台不可新增该项；不能只照错误文字申请权限。资源权限不等于读取聊天消息权限；机器人或链接发送可用也不证明附件上传可用。
- 组织分享按能力验收：向实时 `user_info` 核验的本人发送链接，不依赖群目录或消息附件权限；群组选择仍核对机器人所在群及内部组织边界，PDF／ZIP 附件仍走消息文件上传。目录未授权与上传未授权只限制对应能力，不改成允许未知接收对象，也不阻塞已有报告预览或网页下载。附件准备阶段被权限拒绝时没有发送消息；授权恢复后读回不自动重发，只能显式恢复原请求的失败目标。
- 权限发布与安装组织授权分别核对。[查询租户授权状态](https://open.feishu.cn/document/application-v6/scope/list)返回的权限必须满足 `grant_status=1`；`2` 是未授权，不能仅凭名称出现在列表中认定生效。[获取用户信息](https://open.feishu.cn/document/server-docs/contact-v3/user/get)还区分接口权限和字段权限：`contact:contact.base:readonly` 可使接口成功，但不保证姓名或账号状态可读。缺字段时接收资格保持未知，并核对字段权限及通讯录数据范围；不能把 HTTP 200／code 0 当成完整组织分享验收。
- 新版通知卡片设计采用[卡片 JSON 2.0](https://open.feishu.cn/document/feishu-cards/card-json-v2-structure)，显式声明 `schema: "2.0"`；正文组件放在 `body.elements`，使用 `markdown`，按钮使用 `behaviors`，不混用 1.0 的 `div.text`／`action` 结构。2.0 要求飞书客户端 7.20 及以上；动态文件名等内容须先按[官方富文本规则](https://open.feishu.cn/document/feishu-cards/card-json-v2-components/content-components/rich-text)转义。JSON 语法检查或设计预览不等于发送及送达验收。
- 同任务 ZIP 通知链接使用 `/audit?task=...&report=1&packageRequest=...`；跨任务包使用 `/audit?view=archive&packageRequest=...`，两者均经过当前身份入口。报告中心和档案页通过既有 `/audit-report-packages/{request_id}` 读取固定集合，失败只读回原请求，不自动创建新包；档案页在发送创建请求前保存该地址，刷新后只读恢复。`reports-zip-link` 是显式预览场景，不证明真实飞书送达。
- 卡片直接确认需配置 `FEISHU_VERIFICATION_TOKEN`，并将新版 `card.action.trigger` 回调订阅到现有 `/integrations/feishu/events`；不使用旧版回调结构。仅配置 Token 不证明平台订阅已生效。回调通过原消息 ID 和当前创建人身份恢复同一确认事务；消息发送未知或内容被截断时只能打开页面。当前支持 HTTPS 下的 Verification Token 校验，不接受加密回调；真实回调和三秒响应要求仍须在客户等价环境验收。
- `app_ticket` 通过新版 `POST /integrations/feishu/events` 独立接收，使用现有 `FEISHU_VERIFICATION_TOKEN` 与应用 ID 校验，保存至自己的 PostgreSQL；不调用 UAC、不读取旧 Redis、不增加转发或轮询。沿用 HTTPS + Verification Token 模式；配置加密回调前必须另行支持并验证解密，当前遇到加密体明确拒绝。事件 P1 `ts` 为秒（最多十八位小数），P2 `header.create_time` 为毫秒，统一为 Decimal 秒并以 PostgreSQL numeric 保存，避免浮点或微秒舍入丢失顺序；缺失、非法或超过本机时间五分钟的时间戳拒绝。单条 guarded upsert 只接受更晚的事件，重复、迟到或同时间不同值不替换，也不刷新接收时间；时间相同无法判断新旧，须观察后续推送或显式请求重推，不自行给 ticket 排序。应用 ID 存在冲突则拒绝。数据库成功提交后才返回成功，响应丢失可安全重投。
- 旧回调尚未切换时，已授权的人工联调可只读共享 UAC 当前票据；这不是新版运行时回退。先核对 UAC 与目标应用身份，再确认实际键名、键和值的编码：普通字符串键与 Java 序列化键可以同名并存，旧字符串值不能证明 UAC 正在使用它。票据须通过真实应用／租户 token 交换验证；后续权限还要独立读取实际授权并调用对应接口。只读诊断不写旧缓存、不改旧回调，也不因缓存存在或 token 可用就宣称分享权限已经生效。
- `GET /capabilities` 的 `app_ticket` 只表示本地是否有记录，不证明票据仍有效、定时推送已连通或能够调用飞书；`app_ticket_event_timestamp` 是秒级 Unix 时间戳字符串，`app_ticket_received_at` 是 ISO 写入时间，均不含密钥，重复事件不能刷新它们。迁移保留原票据，但不伪造其事件时间；未带事件时间的历史初始化不能覆盖已经由回调更新的记录。正式切换与连续更新验收见 [TKE 配置](../deploy/tke/README.md#飞书回调切换尚未执行)。
- `FeishuTicketUpdates.tla` 是一个应用、两个事件修订和一个异应用的有限 Safety 检查；正向配置为 3 个不同状态、深度 2。两个故障配置分别暴露旧值覆盖（`LatestWins`，深度 3）与错绑应用（`AppBound`，深度 2）。它不证明鉴权、时间戳含义、SQL 持久性、飞书自动投递或响应时限。API/真实 PostgreSQL 测试检查认证、原子排序和重新建立存储连接后的读回，不证明实际平台投递或数据库故障恢复。
- 预览切流用 `PPAP_CURRENT_TENANT_ALLOWLIST`（逗号分隔的 Feishu `tenant_key`）。空名单不限制。生产目录写入海岸线租户。`PPAP_USAGE_CHARGE_ENABLED=false` 时受理不写 `usage_events`；预计点数按匹配文件和标准单价计算，不是已扣费回执。
- 已有组织内配置可以直接用于迁移/开发；终态配置归本仓 Settings。
- 新增配置项前先确认它代表独立运行决策，而不是可推导内部细节。

本地开发也可作为现网的本地客户端：本地 Web 通过产品 API 使用同一份真实数据；需要验证未部署的服务代码时，本地候选 API 可连接现网 PostgreSQL、对象存储与飞书。浏览器不直连数据库，租户与对象授权仍由 API 执行。共享数据不代表新旧代码或 Schema 自动兼容，连接前须核对候选所需接口与数据库结构；这种联调不运行初始化、迁移、测试重置或额外 Worker。写入仍是真实业务操作，不能因入口在本地而视作模拟；会产生收费、通知或审核受理的动作须按本次联调范围控制。

## 当前命令

Local 与 CI 使用同一 ownership。CI 不再维护与目录结构脱节的测试分类。

Repository contract：

数据库 migration ordering authority 只有 numbered filenames。Compose / InsForge / TKE / Schema CI 共用 `migrations/apply.sh`；仓库不存在 `apply.sql` 或第二份顺序清单。

TKE 的 Kubernetes authority 是 `deploy/tke/kustomization.yaml`。CI 与真实 apply 都通过 `deploy/tke/render-current.sh` 得到同一 rendered object stream；CI 对它执行 kubeconform；日常部署及完整检查的触发条件只认[权威发布流程](../deploy/tke/README.md#authoritative-release-procedure)，`apply-current.sh` 调用同一 `release-current.py` 部署入口。edition proxy 的 nginx 内容只存在于 `deploy/tke/edition-proxy.conf`，ConfigMap 由 Kustomize `configMapGenerator` 生成。

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

## POC 规则资产导出

原始 DOCX 是规则资产的内容依据；[`POC Rule Assets`](../assets/poc测试/poc规则/AGENTS.md) 规定生成文件和媒体路径。仅在需要重新生成资产时，从仓库根目录执行：

```sh
cd assets/poc测试/poc规则/markdown
pandoc '../10规则-控制计划.docx' --wrap=none -t gfm -o '10规则-控制计划.docx.md' --extract-media='media/10规则-控制计划'
```

在输出目录运行 Pandoc，使图片链接保持 `media/10规则-控制计划/media/image*.png`，对应文件位于该目录的 `media/10规则-控制计划/` 下。生成后检查 diff、所有图片引用和对应文件；只移除已确认不再被引用的旧生成图片，不手改规则正文。若内容变化会影响预期行为，按 [TESTING](TESTING.md) 在当前 `tests/corpus/v3_poc` 入口验证，不恢复旧仓的测试路径，也不据导出成功声明审核通过。

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

  第三方依赖先仅凭根 metadata/lockfile 安装（`--no-install-workspace`），再复制源码并安装非 editable 工作区包；两步共用 BuildKit uv cache mount，下载缓存不进入镜像层。`.dockerignore` 排除各层测试目录，测试改动不影响生产构建缓存。缓存跨云效 runner 的复用由构建平台决定，不能保证 cache mount 在新 runner 上仍存在；现有 inline layer cache 保留。
- `deploy/web/Dockerfile` 的 `node:22-alpine` 与 `deploy/isolated-dev/web-dev.yaml` 的 `node:22-bookworm` 仍写在镜像 tag 里。
- `deploy/deep/package.json` 与 `package-lock.json` 拥有 `@xai-official/grok@1.0.70`。镜像用 `npm ci` 安装，`npm audit signatures` 校验 registry 签名，`verify-grok.mjs` 只固定解压后的 guest 二进制。运行时版本检查与执行修订使用 `ppap_core.deep_policy.GROK_VERSION`，须与部署依赖版本一致。

CI 依赖 capability，而不是发行版。workflow 可以安装 uv/Python、Node、Corepack/pnpm 和项目依赖；不得用 `sudo` / `apt` / `yum` / `apk` / `systemctl` 修复 runner，也不得安装或启动 Docker。Python modules 的 services variant、Schema、Corpus PSW、Product integration 和 InsForge lifecycle 要求 runner 已提供 Docker Engine、Compose 和 Buildx；缺任一能力时 fail-fast 输出 `runner does not satisfy ppap-docker capability`。Web、无 PostgreSQL 的 Python modules 和普通 Corpus 不要求 Docker。Repository contract 需要 Docker Engine、Compose 和 Buildx：`docker compose config`、`docker buildx bake --check`/`--print`、nginx 黑盒、Kustomize 与 kubeconform。`actionlint` 1.7.7 检查 workflow 语法。InsForge static config 仍由 `verify-config.mjs` 调用 `docker compose config`。live InsForge lifecycle 需要 `local start` 使用的 runtime Docker capability，只在 InsForge 相关 path、手动 dispatch 或每周 schedule 上运行。LLM / Feishu readiness 是真实外部 probe，手动 dispatch 或每周 schedule，不进 `CI Gate`。Product integration 对同一 `PPAP_BUILD_SHA` 只 `docker buildx bake` 一次，生产 runtime/backup 与 isolated-dev E2E 复用这些镜像（E2E 使用 `compose up --no-build`）。PSW 是 Corpus matrix 的 purpose-built container variant，单独 `docker buildx bake worker` 后从 worker 派生测试镜像。CI Compose 使用 `ppap-ci-<purpose>-<run_id>-<run_attempt>` 作为 project name，cleanup 在独立 `if: always()` step。现有 Compose stack 仍 bind-mount 仓库内 `migrations/`，因此 services、Schema、Product integration 和 InsForge lifecycle 需要 Docker daemon 能看到 runner 使用的同一份仓库路径。后续若把 Ruff、Pyright 或 Vite 收进来，直接调用原生 CLI，不另起 PPAP dispatcher。

Product integration 的 runtime/backup 与 E2E 通过 `bash scripts/run-compose-tests.sh <pytest-directory> <compose-options>` 启动：先由 Docker 分配 loopback 临时端口，再固定实际端口并同步 API/Web 的公开地址后执行测试，备份恢复仍使用同一组地址。`PPAP_API_HOST_PORT`、`PPAP_WEB_HOST_PORT` 只控制宿主映射，默认仍为 `8080`/`4173`；脚本不清理其他任务或常驻服务，workflow 保留失败诊断及本任务的 `always()` 清理。

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

本地文件上传可在 `POST /files` 或新版本请求中提供成对的 `request_id` 与 `sha256`。该请求固定调用人、元信息和内容摘要，`FileVersion.id` 就是请求 ID；重放不创建第二版本。`GET /file-uploads/{request_id}` 只读原请求，未读到记录仍是 unknown，不许可新请求重传。服务明确返回 `resume_allowed` 时，用户可用原请求和相同内容继续；绑定上传的 `PUT /blob` 幂等并完成该版本，已就绪内容不能替换。旧无请求 ID 的三步上传仍保留。文件大小上限为 200 MiB；本地读取/散列与 API 仍使用整份文件，不承诺流式传输或跨设备恢复未传完的字节。

文件内容预检查使用 `POST /file-validations`（同一 `request_id`、文件与版本幂等受理）及 `GET /file-validations/{validation_id}` 读回。`GET /files/{file_id}/versions/{version_id}/validation` 可恢复该版本最新的检查请求；每次读取仍检查当前文件权限。它复用现有 Worker 队列，不创建草稿、审核轮次或用量记录，也不调用模型或匹配规则。任务租约失效显示 `unknown`，不自动重新领取；原 Worker 的有效回执仍可收敛。

`FileVersion.ready` 只表示原件已保存；预检查 job 的 `succeeded` 只表示检查已完成。只有其 `result.state=valid` 才表示可进入后续识别。损坏、加密、无内容与检查基础设施错误分开表达，安全预算超限为 `error`，不是文件审核 NG。OOXML 检查有展开量、条目及 XML 访问预算；旧 DOC 保留既有 25 MiB 受限转换上限。该接口不表示 200 MB 上传链路、新版任务持久化或后续审核已经全部接通。

确认前的任务使用 `POST /audit-tasks`、`GET /audit-tasks` 和 `GET /audit-tasks/{id}`。创建请求固定文件版本、每份预检查回执、选中/未选/已移除来源、模式和客户倾向；同一请求 ID 不允许替换输入。它复用私有 `audit_drafts` 和逐文件识别 job，在同一事务分配 UTC 日编号并入队，不创建审核轮次或消费。Worker 只消费创建时冻结的生效候选；按客户×文件类型及已明确确认的不限客户范围匹配，不用范围未确认的规则兜底。读取固定 job，不取该文件最新识别；提取/模型失败与未匹配分开。

`GET /audit-tasks` 只返回任务摘要及完整文件身份、选择状态，不构造规则正文、审核结果或报告；打开任务后再读 `/audit-tasks/{id}`。摘要不能替换完整任务快照；若恢复回执落后于已知摘要，重新读完整详情，不拼接两个修订的字段。原件读取在同一连接内核对当前授权、文件及版本；轮次入口还核对冻结输入归属，不复用过期授权。

返回修改后，仍用 `POST /audit-tasks`，同时提交原 `task_id`、当前 `expected_revision` 和新的 `request_id`。初始输入修订为 0；重新识别在同一事务增加修订、保存完整来源并替换逐文件 job 引用。旧 job 回执只归原 job；Worker 读到旧代次时跳过文件解析和模型调用，已开始的外部调用不承诺可取消。`GET /audit-tasks/{id}/recognitions/{request_id}` 读取原请求受理回执及当前任务；回执修订与当前输入修订可以不同。`unknown` 不证明请求未受理，不能据此自动重发或生成新请求。

`POST /audit-tasks/{id}/end` 必须带 `expected_revision`，仅结束全部选中文件已明确未匹配的任务；失败或未知不能当作零匹配。该动作可同时保存未匹配文件的 `removals`（版本与原因），不要求再次识别，也不产生审核或消费。任务沿用私有草稿授权，不出现在旧草稿清单，也不能通过旧草稿修改/受理接口绕过固定输入。已移除来源可以没有预检查回执；这不允许选中文件跳过预检查。

`POST /audit-tasks/{id}/confirm` 只接收 `request_id` 和 `expected_revision`，复用同一受理事务冻结当前修订中每份选中且已匹配的文件/规则，并创建唯一 Round 与原执行 job；未匹配来源在该事务中记为移除，不进入执行。`GET /audit-tasks/{id}?request_id=...` 只读原确认结果，未查到成功回执仍为待核对，不是重发许可。新 Task Round 使用显式对象授权，不通过旧同租户 Round 入口开放原件，也不允许旧 resubmit 绕过任务约束；旧 Round 的共享口径不变。收费政策未定期间，新的确认接口仅在 `PPAP_USAGE_CHARGE_ENABLED=false` 下开放，不能据此宣称逐文件收费已完成。

登录入口使用 `/public-config.login`，禁用新登录不撤销已有合法会话。首次匿名 `/me` 的 401 不使随后的宿主登录结果失效；已登录业务请求的 401 仍隐藏产品。OAuth 失败只使用已消费的服务端 state 返回原任务及有限错误码，不携带 provider 原始错误、不自动再次授权；伪造或重放 state 仍回安全默认入口。返回地址不恢复尚未提交的浏览器文件字节。`ProductAccessBootstrap.tla` 只检查一次匿名探测、宿主成功返回和已登录失效的有限 Safety，不证明真实 OAuth、Cookie、跨标签页或最终完成。

V3.1 的 `/audit` 与 `/__audit-native` 共用 `audit.html → src/audit/native-main.tsx`，开发服务器与正式 Web 构建均使用这一入口，默认首页不切换；4185 仍是独立 fixtures。复用同一个 `AuditIntake`、登录、上传、预检查、Task/Round 读取和原件阅读器。新版入口仅走网络，不回退到旧版 PWA 壳。集群经 edition proxy 打开时，先使用 `/enter-current?return_to=<同源完整审核地址>` 登录并选择 current，再返回精确的任务/报告 URL；该路由仍执行租户名单限制，不授予任务访问权。真实 Round 投影保留机器观察、覆盖与独立人工记录；P5 并列展示已保存的人工判断、理由、版本及冻结文件引用，不改变机器结论或报告资格，也不将人工位置文字伪装为自动定位。实际 PASS/NG 与证据完整性分开：缺少定位或证据验证标记仅单独提示，不改判、不单独阻止任务结束或报告生成。Standard 在同一个 job/attempt 内隔离逐文件读取、准备和执行异常，保留其他文件结果；不可读取的冻结文件不进入可用上下文，依赖它的查询产生执行错误，不冒充未提交文档。Task 与历史 Round 阅读共用文件异常投影，单条错误不覆盖其他已完成条款。Deep 的逐文件隔离与整体跨文件语义审核质量仍待验证；PDF/ZIP、飞书与用量的实现及验收状态以各 owning Issue 为准。

`/audit?view=requirements` 提供用户上传审核规则的入口，复用现有 `RequirementLibraryPanel` 和 `/rules` 的原件上传、条款解析、人工核对、版本发布与读回，不另建规则库。上传原件不等于规则已生效；保存版本后刷新客户匹配目录，既有任务仍使用原冻结版本。刷新只更新目录，不重建持有预检查请求身份的适配器。规则页切换、搜索和版本导航须保留外层 `history.state`，避免返回审核时丢失未确认文件及勾选。隐藏面板的迟到上传或保存只更新记录，不能切换页面或对象；重新打开同一草稿详情时可采用其已保存回执。本机草稿恢复后返回原生规则入口，不跳入预览页。

规则目录先读 `/rule-library` 的当前租户元信息；仅打开所选版本时读取正文与原件元信息，版本比较再读明确的前置版本。版本按显式 predecessor 分组，同范围独立规则不合并。离开面板后停止新读取，返回时刷新；取消或错身份的回执不能显示为当前版本，读取已发布版本不能替换本机草稿。

`/__audit-native?round=<id>` 复用 P5 阅读已授权的历史 Round，不要求补建 Task 或事项。`GET /rounds/{id}?read_view=true` 从本轮冻结输入提供文件级结果与原件元信息；普通 Round 读取响应保持不变。旧 engine/presence 和无明确条款关联的 Finding、人工记录单独保留，不猜测条款关系、不增加正式报告资格；原件继续走本轮授权路径。返回入口仍指向同一 Round 的原审核页面。

P5 与历史阅读器的“记录人工复核”复用 `PUT /rounds/{id}/reviews`，保存原始记录键、已读版本、判断、理由、建议及本轮冻结引用。响应丢失先读回；未知请求在当前页面内跨弹窗／文件切换保留，只允许核对后显式重试相同内容。版本冲突需明确采用服务器版本再编辑；刷新后以服务器记录为准，不承诺恢复未提交输入。保存成功使旧任务读取失效，再读取当前 Task／Round；人工记录不改写机器结果或报告资格。

Deep 已通过主机来源／trace 校验的新结果以 `host_source_trace_v1` 随 coverage 保存；共享文件投影核对 Round、job、attempt、executor revision、规则正文与条款结果关联后，才显示已验证依据。旧结果、Standard 位置文字、人工判断和不匹配的验证记录不被提升为已验证证据。按 #163 最新 D1，证据标记不决定 PASS/NG：实际检查器结果仍照实展示，定位和范围缺口单独提示。Schema 测试用实际 XLSX 解析和受控 query proof 经过既有完成事务验证读回，不调用模型、不证明客户审核质量。

新 Task 的完整审核结果自动进入现有 `report_pdf` 队列，Worker 用 PyMuPDF 生成带中文与冻结来源位置的逐文件 PDF，再以当前 attempt 发布对象元信息。P5 与 PDF 共用实际条款结果的计数总结；可选证据缺失只作信息提示，不改变 PASS/NG，不另调用模型生成结论或编造整改建议。下载文件名按 Unicode 字符限制为 255 字符，缩短来源名称时保留 `-审核报告.pdf` 后缀；名称不是报告身份。`/__audit-native` 的现有 P6 通过完整 Task/Round/FileVersion/result-attempt/report/artifact 引用下载；仍按当前 Task 与原件权限鉴权，不代表组织报告分享政策已获批。明确关闭业务计费的环境允许创建人显式恢复失败 PDF：复用同一报告与冻结输入，以新生成 attempt 发布；回执丢失只读取原请求，不重审、不重开已完成 Task。计费启用时拒绝新的恢复请求，已受理回执仍可读，不据此确定 D4 收费政策。飞书及独立档案仍未接通；不把旧整轮飞书报告当成此 PDF。`FileAuditReports.tla` 是三份输入、两次生成尝试的有限 Safety 模型，五个故障配置分别检查迟到发布、输入错绑、不符合报告资格的输入、过早完成与恢复重开任务；不保证请求回执、对象存储响应、最终完成或自动清理。

P6 的首次 ZIP 使用 `POST /audit-report-packages`，以请求 ID 读取状态并下载指定 package/artifact。工作复用已就绪 PDF，不重新审核或调用计费接口；Task 完成状态不因打包而重开。相同规范化报告集合只建一次工作；每个请求单独保存回执，响应丢失只读取原请求，失败集合不能换创建请求 ID 重建。明确关闭业务计费时，现有重试操作调用 `POST /audit-report-packages/{source_request_id}/retries`，绑定原包与已读 attempt；同包别名竞争只受理当前失败代次，原冻结 PDF 集合不变。恢复回执继续用原恢复请求 ID 读取，迟到 Worker 不能发布；私有旧 attempt 对象可能保留，不声称已清理。档案允许创建人从自己的多个私有任务中选择报告；创建、读回、恢复与下载均检查全部来源任务、轮次与报告权限，跨任务包不投影到任一任务报告中心。组织内分享仍使用独立分享授权，不因拥有 ZIP 而授予私有任务或原件权限。最多 100 份且总量不超过 200 MiB，逐份校验后写临时 ZIP；任一来源失败只报告该项，绝不发布部分 ZIP。包内名称有唯一序号且限 255 个 UTF-8 字节，`报告清单.txt` 保存实际名称与冻结引用。`ReportPackageAcceptance.tla` 检查两个请求、两个规范化集合的首次绑定与单次入队；`ReportPackageRecovery.tla` 检查两个调用者、三个生成代次的恢复新鲜度、冻结集合与迟到发布，三个故障配置分别破坏对应不变量。二者都是有限 Safety 检查，不证明实际字节、权限、计费、外部 I/O 或最终完成。

V3.1 原有“联系我们”浮层通过已登录的 `GET /support` 读取服务配置；`PPAP_SUPPORT_PHONE`、`PPAP_SUPPORT_EMAIL`、`PPAP_SUPPORT_SERVICE_HOURS` 只填写已确认可公开的信息。可选 `PPAP_SUPPORT_QR_FILE` 是 API 进程内可读的本地文件路径；容器使用时需将该资产以只读方式挂载或随镜像提供，不将宿主路径自动变成公开 URL。二维码只接受单帧 PNG/JPEG/WebP，最多 2 MiB、4 Mi 像素；读取固定 `config_version`，联系方式或图片变化后旧请求返回冲突，需重新读取配置。两项接口不缓存、不创建任务、用量或工单；未配置联系方式且无可用图片时明确显示不可用。此接线不表示真实客服资料已确认，也不表示模板库或运营提报已接通。

确认、识别和任务读取失败时，求助快照保留该操作已收到的 `X-Request-ID`，与业务请求编号及 `client-*` 页面参考编号分开；无响应头、离线或本地校验失败不制造服务编号。快照只在用户打开支持时捕获，晚到回执和服务配置读取不改写它，也不将诊断编号写入审核结果。

真实联调入口的“补充模板”复用既有文件上传、原请求读回和固定版本下载；ZIP、Excel、Word、图片附件只保存原字节，不解压执行、不创建审核或消费。暂存且服务明确允许继续时，用户可显式继续同一请求与原文件；关闭弹窗保留本页附件，刷新恢复未提交草稿不在此承诺内。查看／下载先读取授权的版本元信息，再核对下载内容的类型、长度、响应 SHA-256 与实际摘要。附件上传不等于运营收到：未提供真实提报与回执读取接口时禁用提交，不生成虚假未知回执或成功态。目标多维表格与真实落表、原提交读回仍待接通；`feedback-attachments`、`feedback-upload-resume` 仅验证对应界面交互。

`GET /audit-customers` 只读当前租户的最新发布规则链，按 engine 规则中的明确客户聚合上传页的客户选项。客户键与匹配器同用去空白、casefold，不是飞书身份或文件夹权限，也不从历史规则恢复已失效客户。

模板入口统一打开[《PPAP Audit 模板使用说明》](https://icoastline.feishu.cn/wiki/M5KqwJYWDiuUYNkGoiXcL1bgnbe)，模板反馈跳到该文档的「联系我们」章节。链接由 `apps/web/src/audit/template-links.ts` 持有，在新窗口打开，不改变当前任务、客户、文件选择或审核草稿。文档的内容、附件与访问权限由飞书管理；产品不复制目录、不代理模板下载，也不自动接收反馈材料。审核规则上传与模板文档是不同入口。

自动匹配分别报告客户未知、类型未知、缺少该类型规则、缺少客户对应规则、客户范围未确认及多个适用候选；多候选不按时间、顺序或模型排名静默选择。客户为空不等于“不限定客户”：只有保存版本中的明确确认使其具备不限客户的匹配资格，范围未确认的版本和草稿不参与。返回核对或保存规则后，显式重新识别同一任务才更新候选快照，不改写旧结果或已冻结轮次。打开客户下拉框会重读目录，不重建上传页面或改变勾选。特殊特性清单作为固定版本的比对资料随本轮保留，不单独审核、计费或生成报告。

真实联调的飞书选择复用当前用户的云文件列表与导入接口。导入携带 `request_id` 时，现有文件版本上传记录先绑定发起人、源 token/type；同请求并发读取同一版本，不再次导出。首次捕获的文件名、类型、长度和摘要在持锁会话的事务中核对并固定，再写对象；失去锁会话的旧处理器不能另连写回。响应丢失读取 `/file-uploads/{request_id}`，仅服务允许时显式继续同一请求。已保存字节优先完成原版本；原字节未保存且远端内容已变化时失败，不替换为新内容。导入后仍需预检查，不自动创建审核或用量；这不决定跨任务档案身份或组织分享政策。`FeishuImportReceipts.tla` 只检查两个处理器、两个源、两个摘要下的源绑定和字节不变 Safety，含失锁后的迟到 provider 回应及对象写入；不证明实际 OAuth、SQL 锁、飞书导出、存储耐久性或最终完成。

飞书选择器按 Stitch `639a2a5ebe7841d1adfc76ae28577f93`（桌面）与 `719e87ef8fb9452e9955b8c2762fd62d`（手机）实现目录树与完整已选清单。文件夹只展开／收起，不递归勾选；各目录独立按需读取、翻页和重试，收起后保留本窗口缓存，迟到响应不能重开目录或进入新窗口。已选集合逐行呈现完整文件名、类型图标与选择时的目录，可逐个取消或清空，不因筛选、翻页、收起目录或窗口缩放而丢失。搜索只筛选已加载文件，保留匹配项的父目录，不调用全盘搜索；清除筛选恢复原展开状态。桌面左右分栏，手机上下分栏；搜索和添加固定，长清单各自滚动。确认添加仍走原真实飞书导入与原请求恢复接口；浏览或取消不导入、不创建审核。`drive-tree` 验证并列目录、同源文件去重及分页失败恢复，`drive-folders-late` 验证收起或关闭后迟到读取；预览不证明实际飞书授权或导出。识别前的移除只编辑上传选择，不显示任务移除历史；开始识别后仍保留排除依据，不将排除文件放入执行或费用输入。

## UI-first 交互预览

在 `apps/web` 运行 `pnpm preview:workbench`，打开 `http://127.0.0.1:4185/__preview?page=audit`。场景切换会重置页面；宽度与主题切换保留当前交互。识别中和飞书读取中场景用预览台的“返回一次示例读取／令一次示例读取失败”控制响应，包括弹窗内的读取，不用定时器假装真实进度。

桌面产品导航可折叠为带文字提示的图标栏，仍保留产品入口和联系我们；折叠仅改变本页显示，不重建任务或写入业务状态。手机 Sheet 始终显示完整文字；产品栏与任务栏保留各自的 1024／1280 px 断点，报告页仍可打开任务列表。

`matching-gaps` 在取消选择控制计划后，显示 PFMEA 已匹配规则但缺少跨文件比对资料；返回补选后再识别可清除提示。`restore-progress` 恢复固定样例的部分结果：有效 NG 与运行、排队项分开，刷新不推进样例，主动“读取审核结果”后才显示最终结果。`restore-task-conflict`、`restore-report-conflict` 分别演示文件未结束或首次报告仍生成时，任务汇总误报完成；有效逐文件依据和已就绪报告仍保留。带原请求回执的报告重生成不重开审核任务。这些场景不代表任意上传已保存或真实审核已经执行。

开始识别前的上传草稿按每次“新建审核”分别保留在本页内存，浏览器历史仅记录草稿 ID。返回可恢复文件、选择、模式和传输回执；在途回执先读取原请求，不自动重传。草稿创建任务后，旧历史入口只恢复该任务，不再独立创建第二个任务。刷新与跨设备恢复仍依赖真实业务持久化，不能由本地草稿替代。`checking-loading` 可手动交付预检查迟到响应，验证离开并返回后旧响应失效。

`observe-matching`、`restore-observe` 演示当前可见任务的自动读取；每次返回仍由事件控制条显式交付，不用计时器生成进度。界面默认每 3 秒读一次，隐藏或离开面板暂停，重新可见/聚焦补拉；失败按 6/12/24/30 秒退避，成功恢复基础间隔。未知的报告生成、ZIP 或分享请求保留专用核对入口，不由通用自动读取覆盖。`restore-observe-regression`、`restore-observe-missing-progress` 演示拒绝同轮较早或缺失的进度，`restore-observe-unresolved` 演示未知报告请求的暂停；`match-rule-missing` 演示缺少候选规则时禁止确认。其他手动场景显式关闭自动读取，以便单独控制原来的交互。

`restore-estimate` 展示服务合同中的任务结束时间区间及观察时间；结束包含 D3 规定的首次报告处理。`restore-estimate-overdue`、`restore-estimate-invalid`、`restore-estimate-reversed` 分别演示上界已过、缺少时区和区间倒序；无可靠估计时明确说明，不用浏览器倒计时增加审核计数。过期说明随现有读取或界面操作更新，终态不再展示进行中的估计。固定样例仅验证显示，不代表真实服务已能预测耗时。P4/P5 与历史文件信息区分零件名称 `part` 和可选零件号 `part_number`；`confirm-metadata-conflict` 演示拒绝确认回执静默替换识别信息，历史详情使用所选轮次的信息，不取档案顶部的信息替代。

原件读取使用 `scene=original-error`、`original-loading` 或 `original-unavailable`，选择三类文件示例后进入审核并核对 Excel 原件。它们复用产品原件阅读器，演示读取失败、重读和乱序返回；下载仅返回该冻结版本的明确示例 XLSX，不是客户原件，也不验证真实 Office、排版或定位。任意上传不生成替代原件，不向产品 API 发送示例身份。

`restore-display-ready` 演示 D8 的四字段显示更正；原始识别信息仍可展开核对，冻结文件、规则、结果与既有报告不变。`restore-display-rejected/unknown/conflict/loading/denied/stale/read-failed` 分别演示明确拒绝、未知结果、版本冲突、迟到回执、撤权、旧投影和读取失败。未决保存关闭后保留原请求，只读回、不自动重发；此恢复限于当前页面生命周期。`archive-display` 按所选历史轮次显示更正，不能编辑。预览使用 `task=preview-recovery-task`，或在预览台选择对应场景。

真实联调入口的同一编辑器使用 `POST /audit-tasks/{id}/display-info` 保存、`GET /audit-tasks/{id}/display-info/{request_id}` 读回原请求。请求使用规范 UUID 和完整四字段快照；`null` 清空字段。服务在同一事务核对私有任务所有权、Task/Round 当前 review 许可和显示修订，保存不创建审核、用量或报告。撤销 review 后可按保留的 read 许可核对旧回执，但不能新保存；读权限也失效时拒绝读回。读取不到原请求仍是 unknown，不许可自动重发。Task 重新读取及 API 重启后使用数据库中的显示更正；历史 Round 原始信息不改写，文件档案的真实更正投影仍待接入。

文件档案及历次审核使用 `scene=archive-ready`。`archive-task-allowed` 单独演示有来源任务读取许可时，经完整 P5/报告页返回所选历史，不改开最新审核。历史报告地址包含完整冻结结果引用，切换主题和刷新后仍读取同一次审核；这只恢复固定样例，不表示任意上传已持久化。档案、来源任务、原件和报告分别使用服务提供的访问结果，不从一项权限推定另一项权限。

档案列表采用 Stitch `bfd8b8db93484b159a6ce9d171447c80`，历史详情采用 `bf7f2f7d5c4742a193c29da18e7688bf`。文件表格占主区全宽，完整文件名与客户／车型／零件信息同列；分类和高级筛选使用 Sheet，最新完成审核与进行中的轮次分开。历史页只显示一次所选原件名称，通过“历史审核记录”切换轮次，复用当前审核工作台的结果、依据、原件和报告组件。筛选、分页和返回不替换已选报告的冻结引用；时间使用服务返回的审核时间，不将其声称为完成时间。两页在窄桌面自动收起产品导航，用户仍可手动展开。

历史读取复用同一只读事务，批量核对文件、权限、预检查和规则；独立轮次查询经同一连接批量发送。首次历史深链不重建阅读器，不读取隐藏的档案或任务列表，不等待客户目录；指定详情与历史清单并行读取，但仍由清单确认选择，旧读取不能覆盖新选择。请求日志只使用本请求已认证的身份，不额外查库，也不跨请求复用认证。

历史链接带 `auditRef` 或 `archiveReport=1` 却缺少文档身份时，显示可返回的错误，不静默显示档案列表；只有文档身份的正常入口仍可打开默认历史。

`reports-zip-failed` 在同一固定报告集合中显示逐报告准备状态与失败原因；改变当前勾选不改变旧包明细。`reports-zip-write-failed/pending-failed/legacy/no-retry` 分别区分最终 ZIP 写入失败、尚未确认、未提供逐项状态及服务未允许重试。`reports-zip-outcome-*` 故意返回缺项、重复、错误报告引用、空失败原因或矛盾就绪状态；只读原请求即可恢复，不重新打包。下载与文件分享使用同一明细校验；“打包准备完成”不表示有部分 ZIP 可下载，也不改变单 PDF 状态。这些仍是 UI-draft 合同与受控预览，不确定真实重试或收费政策。

`reports-zip-foreign` 在读取报告状态时返回内部自洽但属于其他任务／轮次的 ZIP；任务页拒绝下载、读回该包或另行打包，重新读取任务可恢复正确范围。当前勾选不决定旧包范围，档案页仍可跨任务打包授权的固定历史报告。

报告分享的未发送草稿在当前报告页内保留接收人与交付方式；更换冻结报告范围或选择“新的分享”才重置。重开会重新核对目录，未读到的接收人保持待核对，可单独核对或移除；目录失败不允许发送，旧读取迟到也不能恢复资格。`reports-share-directory-org-changed` 演示组织变化后重新选择，`reports-share-external` 演示关闭期间读到另一笔未决请求：原请求优先，读回成功仍保留其结果，不自动发送旧草稿。这不承诺刷新、跨设备保存或真实飞书授权与送达。

支持浮层使用 `support-ready`、`support-error`、`support-qr-error` 和 `support-loading`，分别走查复制/邮箱链接/图片放大、读取恢复、图片失败和返回后迟到读取。电话不可拨打，邮箱是保留的 `.invalid` 地址，图片明确不是服务二维码；不代表已取得真实客服配置或完成飞书连接。Browser Back 关闭旧浮层并保留当前文件，二维码放大关闭后回到触发按钮。

`restore-usage-notice` 与 `restore-usage-completed` 分别演示审核中、已结束任务的用量提示。从提示联系支持可复制当前任务、轮次和原提示；关闭后保留文件、条款与筛选，不执行扣费或重审。

报告分享、审核受理等未确认或失败操作可进入同一支持浮层，复制打开时的原请求、尝试及固定对象信息。支持快照只读，不自动重发、发送附件或创建工单。关闭后优先回原按钮，按钮消失时回原弹窗；普通全局支持不保留上一次操作信息。本页 `client-` 编号只是参考，不冒充服务端 trace。

用量记录使用 `__preview?page=audit&view=usage&scene=usage-ready`。四卡读取服务提供的自然月汇总，不由明细筛选计算；每行固定一份文件的一次 500 点业务消费，免费／付费分配和技术重试不增加行数。历史入口复用冻结审核引用，不回退最新结果。`usage-quota-unknown`、`usage-revoked`、`usage-race` 分别演示未知额度、撤权和迟到读取；只读刷新不触发外部收费。这是共享 DTO 与明确样例，不是现有整轮用量账的逐文件投影，也不验证真实结算。

在仓库根目录运行 `PPAP_PREVIEW_URL=http://127.0.0.1:4185 uv run --no-sync pytest tests/e2e/test_audit_preview.py tests/e2e/test_template_links_preview.py tests/e2e/test_support_preview.py tests/e2e/test_usage_preview.py -q`。需安装 `e2e` dependency group。这些测试只证明界面交互；不证明真实上传、OAuth、解析、匹配、审核、收费或持久化。预览服务不代理业务 API，生产入口不包含 fixture adapter；真实产品浏览器测试不因此替换或跳过。

上述预览测试未配置 `PPAP_PREVIEW_URL` 时，可在未安装 Playwright 的环境中收集，并按原条件跳过；显式配置地址时必须安装 `e2e` group，缺少依赖会使收集失败。
