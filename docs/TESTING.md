# 测试

测试保护当前行为，不维护第二套产品状态。

迁移 baseline 的具体 Git revision 和当前观察结果只由 owning Issue 保存：
- engine/corpus parity：[#3](https://github.com/ppap-agent/ppap/issues/3)
- 飞书 capability parity：[#11](https://github.com/ppap-agent/ppap/issues/11)

迁移完成后，本仓测试成为 current authority，不继续复制 legacy baseline 到长期文档。

## 测试层级

- **logic / unit**：稳定规则和数据不变量。
- **corpus regression**：完整 engine parity；baseline 原有 failure 不属于 migration regression。
- **integration**：数据库、对象存储、Worker、授权和必要外部接口。Deep 的受理、版本、fencing 与传输边界由 `tests/integration/product/test_deep_mode.py` 验证；语义 guest 与容器权限/模型 grant 的确定性逻辑由 `packages/ppap_deep/tests/test_deep_runtime.py`、`packages/ppap_deep/tests/test_deep_semantic.py`、`packages/ppap_deep/tests/test_deep_isolation.py` 验证。脚本化 runtime 只证明协议，不证明真实审核。`tests/live/test_deep_isolated.py` 使用独立 `ppap-deep` 容器、真实 Grok 和模型，但仅是玩具 XLSX 的真实模型协议 smoke，不是客户 A1–A12 样例验收；它不进入默认 merge contract。
- **E2E**：少量完整客户路径。#7 覆盖 Web 审核闭环；#11 覆盖飞书来源进入同一产品闭环。#57 把同一条浏览器路径延伸到 PC 导航、结果/依据/预览、历史轮次、复核和用量。飞书 Drive 导入与文档关联文件的确定性回归在 `tests/integration/product/test_feishu_drive.py` 与 `services/api/tests/test_feishu_doc_links.py`（mocked Open API）。docs-addon 宿主登录到普通 round 的确定性回归在 `tests/integration/product/test_feishu_docs_addon.py`（mocked host code + Open API）。客户路径 `/addon`、`/audit-files` 与 `/usage` 由 nginx 自己检查：`nginx -t` 加上容器内的路由请求，在 `tests/repository/test_web_server.py`。Web 只调 Product API 的仓库边界、helper、路由、reducer 与 public-config 都由 `apps/web` 的 `pnpm test` 拥有；跨栈黑盒浏览器行为由 Python Playwright（`tests.e2e`）拥有。InsForge overlay 的 **static config contract** 由 `node deploy/insforge/verify-config.mjs` 拥有：Compose 语法与插值交给 `docker compose config`，再检查 Compose 无法知道的少量 PPAP invariant。**live native lifecycle** 是 `bash deploy/insforge/smoke-local.sh`（`@insforge/cli local start/status/db query/stop`），只在 InsForge 相关 path 变化、手动 `workflow_dispatch` 或每周 schedule 上运行。

规则排列组合留在低层，不复制到 E2E。

## Runner capability

CI 依赖 capability，而不是 Ubuntu/Debian 或 `sudo`：

- **user-space**：workflow 可以安装 uv/Python、Node、Corepack/pnpm、项目依赖和 Playwright。
- **Docker**：需要启动容器或 Buildx 的 job（service modules / schema / Corpus PSW / Product integration / InsForge lifecycle）要求 `command -v docker`、`docker info`、`docker compose version`、`docker buildx version` 已经可用。缺任一能力时 fail-fast：`runner does not satisfy ppap-docker capability`。workflow 不安装 Docker、不启动 daemon、不 chmod socket、不修改 iptables。
- **schema**：使用 isolated-dev PostgreSQL 容器内的 `psql`（`docker compose exec -T postgres psql`），不要求 host `psql`。
- **PSW `.doc`**：从已构建的 `ppap-worker` 派生一次性测试镜像，通过 build context COPY tests/corpus/fixtures（不 bind-mount 仓库路径），并只安装 pytest。生产 worker 镜像不变，也不在 host 上 apt-install LibreOffice 或 gcc。
- **Compose 路径**：现有 Compose stack 仍会把仓库内 `migrations/` bind-mount 进容器。因此 service modules / schema / Product integration / InsForge lifecycle 需要 Docker daemon 能看到 runner 使用的同一份仓库路径。这是既有 Compose 生命周期，不新增 host-workspace bind mount。
- **Compose isolation**：CI 为 production、isolated-dev E2E 和 schema 使用 `ppap-ci-<purpose>-<run_id>-<run_attempt>` 作为 Compose project name，避免默认目录名互相污染。cleanup 在独立 `if: always()` step；失败时先 dump `compose ps -a` 和对应 stack 的 service logs。
- **非 Docker job**：Web、无 PostgreSQL 的 Python modules 和普通 Corpus 不要求 Docker。Repository contract 需要 Docker：`docker compose config`、`docker buildx bake --check` / `--print`、nginx 路由、以及 Kustomize / kubeconform 镜像。GitHub Actions 语法由固定版本的 `actionlint` 1.7.7 检查。
- **InsForge static config**：只需要 Docker Compose CLI（`docker compose config`）。不需要 Docker daemon 或 Buildx，也不启动容器。CLI 缺失时由 `verify-config.mjs` 给出明确错误。
- **InsForge live lifecycle**：需要 `local start` 使用的 runtime Docker capability（CI 用 `assert-docker` fail-fast）。

## 当前可运行入口

Single-owner Python tests 与 production owner co-locate：`packages/*/tests` 或 `services/*/tests`。根 `tests/` 只承载 system-level authority：`repository`、`schema`、`corpus`、`integration`、`e2e`、`live`。CI 直接消费这些物理 owner，不维护第二套 test manifest。pytest 不以 `pythonpath` 注入仓库根；production import 只来自 uv workspace 安装后的 packaging metadata。每个 production owner 使用统一 `src/<import_package>/` layout。

默认 merge contract 以 **required** groups 为准，不把“是否 deterministic”当成入 Gate 的条件。默认 `Tests` workflow 每个 PR 都运行：

- **Web**：`apps/web` typecheck / test / lint / build。
- **Repository contract**：`tests/repository`，加上 `docker compose config`、`docker buildx bake --check` / `--print`、固定的 `actionlint`、canonical TKE Kustomize render 的 kubeconform，以及 InsForge static config contract。TKE CI 与 `apply-current.sh` 共用 `deploy/tke/render-current.sh`；edition proxy ConfigMap 由 `configMapGenerator` 从唯一的 `edition-proxy.conf` 生成。pytest 只保留原生工具不知道的 PPAP rendered-object policy。
- **Python modules**：CI 发现每一个含 `test_*.py` 的 `packages/*/tests` 和 `services/*/tests`，各自一个 pytest 进程。API 与 Worker 不共享进程。需要 PostgreSQL 的只有 `services/*/tests`。纯 Corpus contract tests 位于 `packages/ppap_corpus_contracts/tests`，由同一物理 owner discovery 自动纳入。
- **Distribution closure**：对每个 production workspace member 单独构建 wheel 和其声明的 workspace transitive wheels；在 `/tmp` clean venv 中显式安装这些 wheels，并用 `uv.lock` 约束 registry dependency 版本。从非仓库 cwd import 后再运行该 owner tests。这个 job 不执行 root `uv sync`、不使用 editable install、`PYTHONPATH` 或 `sys.path` bootstrap。删除 `ppap-product-store → ppap-core` 的真实 dependency declaration 有单独 mutation proof，必须使 isolated import fail closed。
- **Schema**：`tests/schema`，继续使用 real PostgreSQL。Empty、previous、重复 apply、`schema_migrations` 顺序和故意失败 migration 都通过 production `migrations/apply.sh` runner 验证；numbered filenames 是唯一 ordering authority。
- **Corpus**：Excel / PDF / scanned / Pairs / V3 / PSW format matrix；Pairs 以 `tests/corpus/pairs`、PSW 以 `tests/corpus/psw` 作为物理 suite authority，新 `test_*.py` 无需修改 workflow；PSW 继续使用 purpose-built worker-derived container。Corpus 是 CI Gate 的 required child：cache miss 时可以调用真实 OCR / model，这不把它变成 live 或 optional。默认 V3 dataset / expected XLSX 缺失或解析后无有效 case 时 fail closed，不 skip。进入 required V3 的 case 必须至少有一条人工 PASS/NG expectation；零断言 case fail closed。V2 每个 required variant 在执行前对账 oracle 选中的全部 case 与真实 asset；任一缺失或歧义映射失败，不跳过部分样本。V2 baseline exception 与 PDF↔Excel Pairs 共用同一个 `ExecutionOutcome`（status / execution_class / 可选 diagnostic_code）；比较整个 structured outcome，不把任意 ERR 当成同一行为。已知 mismatch 消失则要求清理 record。V2 PDF↔Excel pairs 按 oracle 文件 stem 自动配对，在 pair+rule 粒度比较；已知格式分歧写入 structured baseline，不是第二套 human oracle。新的、改变的或已愈合的 divergence 失败；aggregate 一致性百分比不是 merge gate。配对歧义或发现不到任何 pair 时 fail closed。Excel / PDF / scanned 不重复执行 pair suite。
- **Product integration**：`tests/integration/product` + canonical single bake + `tests/integration/runtime` + Python Playwright `tests/e2e`。
- **Container build authority**：`docker-bake.hcl` 唯一拥有 API / Worker / Web build graph。Compose 只消费 images；Repository contract 从 Bake/Compose native rendered output 核对 image identity 与无 `build` graph，Integration 对同一 `PPAP_BUILD_SHA` 只 Bake 一次并复用。
- **CI Gate**：不运行测试，只汇总以上 required groups（包括 Distribution closure）；任一 required group failed / cancelled / skipped 时 Gate 失败。

外部依赖本身不改变 test ownership，也不决定测试是否 required。`tests/live` 表示不进入默认 merge contract、需要显式执行的真实环境验证，不是“所有带外部依赖的测试”。Root pytest 的 `norecursedirs = ["tests/live"]` 只阻止裸 pytest recursion 意外收集 live tests；显式指定 `tests/live` 目录或具体文件仍会收集并执行。InsForge native live lifecycle 同样不进入默认 `CI Gate`。LLM readiness 与 Feishu readiness 是真实 endpoint / credential probe：保留 `workflow_dispatch`，并每周 schedule 一次；它们不进入默认 PR `CI Gate`，也不复制产品业务测试。当前不做 git-diff/path-aware skipping；required groups 每个 PR 都执行。

Repository contract：

```sh
uv sync --locked
git show --format= --check HEAD -- . ':!assets' ':!tests/corpus/fixtures'
uv run --no-sync python -m compileall -q packages services tests
node deploy/insforge/verify-config.mjs
docker compose -f deploy/isolated-dev/compose.yaml config --quiet >/dev/null
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example config --quiet >/dev/null
docker buildx bake --print >/dev/null
docker buildx bake --check
node scripts/verify-container-graph.mjs
deploy/tke/render-current.sh \
  | docker run --rm -i ghcr.io/yannh/kubeconform:v0.6.7 -strict -summary -kubernetes-version 1.28.0 -
uv run --no-sync pytest tests/repository
```

Distribution closure（这是 distribution correctness proof；root `uv sync` 只保留为快速 workspace smoke）：

```sh
python3 scripts/validate_distribution.py --discover
python3 scripts/validate_distribution.py --member ppap-core
python3 scripts/validate_distribution.py --prove-missing-dependency
```

service distribution 的 owner tests 与普通 service module job 一样，需要先启动 isolated-dev PostgreSQL fixture。

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

Cross-owner product contracts：

```sh
export COMPOSE_PROJECT_NAME=ppap-product-integration
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest tests/integration/product
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

Corpus：

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
docker run --rm "ppap-psw-test:${PPAP_BUILD_SHA}" \
  pytest tests/corpus/psw -q
```

Schema：

```sh
export COMPOSE_PROJECT_NAME=ppap-schema
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest tests/schema
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

Product integration 使用 #100 的单次完整 bake 与隔离 Compose 生命周期：

```sh
uv sync --locked --group e2e
uv run --no-sync python -m playwright install chromium
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"

export COMPOSE_PROJECT_NAME=ppap-product-integration
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync pytest tests/integration/product
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans

docker buildx bake

export COMPOSE_PROJECT_NAME=ppap-prod
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait
uv run --no-sync pytest tests/integration/runtime
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans

export COMPOSE_PROJECT_NAME=ppap-e2e
docker compose -f deploy/isolated-dev/compose.yaml up -d --wait --no-build
uv run --no-sync pytest tests/e2e
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

PSW 的 `.doc` 用例需要 LibreOffice 和 Linux 上可执行的 `ppap-local-converter-sandbox`。CI 与推荐本地路径是先 `docker buildx bake worker`，再构建 `tests/corpus/Dockerfile.psw` 派生测试镜像并在其中跑 pytest（worker 已含 LibreOffice 与 sandbox；测试镜像对同一 `uv.lock` 执行 `uv sync --frozen --no-editable`，装上默认 dev dependency group；测试文件经 build context COPY，不 bind-mount 仓库）。`.doc` 转换是默认行为，没有开关变量；转换器或沙箱缺失时该项记 ERR，不跳过格式。需要 Paddle 的 PDF 转换优先使用 `tests/corpus/fixtures/ocr_cache`；未命中时再读 `PADDLE_OCR_URL`。这仍是 required corpus regression，不是 live。人工 expected 以 corpus 内 CSV/XLSX 为准，不要改它来迎合实现。

#7 的代表性浏览器 E2E 覆盖待审+参考、提交、离开后回访、打开本轮原件版本（或看到 locator-gap）、原件预览、复核、显式重审，以及从账号菜单查看用量和从主导航查看审核结果：

```sh
uv sync --locked --group e2e
uv run --no-sync python -m playwright install chromium
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
export COMPOSE_PROJECT_NAME=ppap-e2e
docker buildx bake
docker compose -f deploy/isolated-dev/compose.yaml up -d --wait --no-build
uv run --no-sync pytest tests/e2e
docker compose -f deploy/isolated-dev/compose.yaml down -v
```

Live Deep 会调用付费模型。先在仓库根目录的 `.env` 配置 `LLM_API_KEY`、HTTPS `LLM_BASE_URL` 和 `LLM_MODEL`。以下命令构建专用 amd64 Deep 镜像，Controller 留在容器内，Worker/API 只通过 UDS 访问；无需 gVisor、Docker socket 或宿主机 Grok 配置：

```sh
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake worker --set worker.platform=linux/amd64 --load
docker buildx build --platform linux/amd64 --load \
  --build-arg PPAP_APP_IMAGE="ppap-worker:$PPAP_BUILD_SHA" \
  -f deploy/deep/Dockerfile -t "ppap-deep:$PPAP_BUILD_SHA" .
mkdir -p /tmp/ppap-deep
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml up --no-build -d --wait
PPAP_DEEP_LIVE=1 uv run --no-sync pytest tests/live/test_deep_isolated.py
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml down -v --remove-orphans
```

Overlay 显式设置 `PPAP_DEEP_EXECUTOR_REVISION=ppap-deep-v2`、`PPAP_DEEP_RUNTIME=container-only`、`PPAP_DEEP_SANDBOX_PROFILE=off`。旧内核的 `off` 不提供强文件系统或出口网络隔离；只允许此容器级模式，不要声称是 gVisor。此 live 协议 smoke 上传玩具但有效的 XLSX 与已发布规则，要求带实际单元格证据的语义结果和已记录的模型用量；固定 checker、脚本 guest 或仅有 PASS 状态都不算审核证明。客户 A1–A12 仍须另用真实样例验收。默认 isolated-dev 仍不发布 Silo 端口，remote-dev 的现有配置不受此 overlay 影响。

模型 grant 的默认额度是 4,000,000 输入 token、100,000 输出 token、128 次请求和 40 分钟。输入额度按提供方返回的已确认累计用量停止后续请求，不是严格的费用硬上限：加密推理历史无法在请求前准确计数，故最多一笔受 8 MiB 请求体上限约束的在途请求可能令输入用量超额。输出请求按剩余额度限制；用量缺失即关闭 grant，不把请求字节误记为 token。单测验证这些边界；live smoke 仍需记录真实提供方用量。本轮开发已获授权可依据实测提高这些上限，但每次调整仍须保留有限额度并说明原因和实际用量，不以无限重跑替代修复。CETUS 完整规则实测触及原 1,600,000 输入额度后提高至当前值；后续联合审核在原 20 分钟截止时仍未执行完毕，因此时长提高至 40 分钟。这不是新的完成率保证。

上下文窗口和自动压缩沿用 grok-build 对所选模型的默认配置；PPAP 不写 `context_window` 或自定义压缩阈值，也不为了验收强制压缩。单测只证明没有覆盖配置及事件计数正确；真实压缩仍以 `compact_boundary` 事件及压缩后规则、证据完整性为准。读取单次提示用量时，将互斥的未缓存、缓存读取、缓存写入三项相加，不使用累计输入额度判断上下文长度。

组织内测试资产和可访问环境按 [AGENTS.md](../AGENTS.md) 的默认规则直接使用。

`tests/integration/product/test_rules.py` 通过真实 Excel/Word 提取与模拟外部 LLM 验证规则解析，再通过 Product API / Worker 验证受理、预览、发布、版本不变与来源授权。`packages/ppap_excel_audit_engine/tests/test_rule_ai_client.py` 验证每调用超时、零自动重试及错误日志脱敏。真实 PostgreSQL 契约在 `tests/schema/`：CI 用 `pytest tests/schema` 收集全部 `test_*.py`，共用 isolated-dev PostgreSQL fixture。其中包括 empty/previous schema 升级、规则解析的并发受理/租约/迟到完成/来源授权、审核绑定与用量分配。新增同类测试放到该目录即可进入 schema CI。`tests/e2e/test_web_rules.py` 使用结构化规则文件走真实上传、人工编辑、发布和重访；`tests/e2e/test_rule_interactions.py` 在浏览器 API 边界控制迟到响应，验证整页操作互斥、刷新只读、显式重跑，以及 `deep_offered` 对审核模式控件的门控。这些测试不调用外部模型服务。

## Web OpenAPI contract

API owner tests validate that every major Web-consumed endpoint has an explicit successful JSON response schema and that JSON write endpoints have an explicit request schema. Repository CI then installs the frozen Web lockfile, runs `pnpm --dir apps/web api-types`, and fails if `apps/web/src/generated/openapi.ts` differs from the committed artifact. Web CI typechecks/tests/lints/builds the generated consumers.
