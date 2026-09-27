# 测试

测试保护当前行为，不维护第二套产品状态。

迁移 baseline 的具体 Git revision 和当前观察结果只由 owning Issue 保存：
- engine/corpus parity：[#3](https://github.com/ppap-agent/ppap/issues/3)
- 飞书 capability parity：[#11](https://github.com/ppap-agent/ppap/issues/11)

迁移完成后，本仓测试成为 current authority，不继续复制 legacy baseline 到长期文档。

## 测试层级

- **logic / unit**：稳定规则和数据不变量。
- **corpus regression**：完整 engine parity；baseline 原有 failure 不属于 migration regression。
- **integration**：数据库、对象存储、Worker、授权和必要外部接口。Deep 的受理、版本、fencing 与传输边界由 `tests/integration/test_deep_mode.py` 验证；语义 guest 与容器权限/模型 grant 的确定性逻辑由 `packages/ppap_deep/tests/test_deep_runtime.py`、`packages/ppap_deep/tests/test_deep_semantic.py`、`packages/ppap_deep/tests/test_deep_isolation.py` 验证。脚本化 runtime 只证明协议，不证明真实审核。`tests/live/test_deep_isolated.py` 使用独立 `ppap-deep` 容器、真实 Grok 和模型，但仅是玩具 XLSX 的真实模型协议 smoke，不是客户 A1–A12 样例验收；它不进入默认 unit CI。
- **E2E**：少量完整客户路径。#7 覆盖 Web 审核闭环；#11 覆盖飞书来源进入同一产品闭环。#57 把同一条浏览器路径延伸到 PC 导航、结果/依据/预览、历史轮次、复核和用量。飞书 Drive 导入与文档关联文件的确定性回归在 `services/api/tests/test_feishu_drive.py` 与 `services/api/tests/test_feishu_doc_links.py`（mocked Open API）。docs-addon 宿主登录到普通 round 的确定性回归在 `services/api/tests/test_feishu_docs_addon.py`（mocked host code + Open API）。客户路径 `/addon`、`/audit-files` 与 `/usage` 的静态入口在 `tests/repository/test_web_server.py`。Web 只调 Product API 的仓库边界、helper、路由、reducer 与 public-config 都由 `apps/web` 的 `pnpm test` 拥有；跨栈黑盒浏览器行为由 Python Playwright（`tests.e2e`）拥有。InsForge overlay 的 **static config contract** 由 `node deploy/insforge/verify-config.mjs` 拥有：Compose 语法与插值交给 `docker compose config`，再检查 Compose 无法知道的少量 PPAP invariant。**live native lifecycle** 是 `bash deploy/insforge/smoke-local.sh`（`@insforge/cli local start/status/db query/stop`），只在 InsForge 相关 path 变化、手动 `workflow_dispatch` 或每周 schedule 上运行。

规则排列组合留在低层，不复制到 E2E。

## Runner capability

CI 依赖 capability，而不是 Ubuntu/Debian 或 `sudo`：

- **user-space**：workflow 可以安装 uv/Python、Node、Corepack/pnpm、项目依赖和 Playwright。
- **Docker**：需要启动容器或 Buildx 的 job（schema / Docker integration / PSW / InsForge lifecycle）要求 `command -v docker`、`docker info`、`docker compose version`、`docker buildx version` 已经可用。缺任一能力时 fail-fast：`runner does not satisfy ppap-docker capability`。workflow 不安装 Docker、不启动 daemon、不 chmod socket、不修改 iptables。
- **schema**：使用 isolated-dev PostgreSQL 容器内的 `psql`（`docker compose exec -T postgres psql`），不要求 host `psql`。
- **PSW `.doc`**：从已构建的 `ppap-worker` 派生一次性测试镜像，通过 build context COPY tests/fixtures（不 bind-mount 仓库路径），并只安装 pytest。生产 worker 镜像不变，也不在 host 上 apt-install LibreOffice 或 gcc。
- **Compose 路径**：现有 Compose stack 仍会把仓库内 `migrations/` bind-mount 进容器。因此 schema / Docker integration / InsForge lifecycle 需要 Docker daemon 能看到 runner 使用的同一份仓库路径。这是既有 Compose 生命周期，不新增 host-workspace bind mount。
- **Compose isolation**：CI 为 production、isolated-dev E2E 和 schema 使用 `ppap-ci-<purpose>-<run_id>-<run_attempt>` 作为 Compose project name，避免默认目录名互相污染。cleanup 在独立 `if: always()` step；失败时先 dump `compose ps -a` 和对应 stack 的 service logs。
- **非 Docker job**：Web contract、Python unit 和普通 corpus 不要求 Docker。
- **InsForge static config**：只需要 Docker Compose CLI（`docker compose config`）。不需要 Docker daemon 或 Buildx，也不启动容器。CLI 缺失时由 `verify-config.mjs` 给出明确错误。
- **InsForge live lifecycle**：需要 `local start` 使用的 runtime Docker capability（CI 用 `assert-docker` fail-fast）。

## 当前可运行入口

Single-owner Python tests 与 production owner co-locate：`packages/*/tests` 或 `services/*/tests`。根 `tests/` 只承载跨 owner/system-level contracts；当前已使用 `tests/integration` 与 `tests/repository`，其余根级目录继续保持各自既有职责。

Web 合并契约分两段。类型、生产构建和前端白盒/契约单测不依赖 Docker；代表性浏览器路径在 isolated-dev 上。CI 里它们分别是 `Web contract` 与 `Docker integration`（同一 SHA 只 `docker buildx bake` 一次，先 production runtime/backup，再 isolated-dev Python Playwright E2E）。Python deterministic tests 先按 production owner 物理归档；当前 CI 仍用 `unittest discover` 执行这些 owner directories，并保留 `compileall`。`push` 只在 `main` 上触发，功能分支只由 `pull_request` 跑完整测试，避免同一次提交跑两遍；两种事件仍使用不同的 concurrency group，不会互相取消。corpus 仍会跑，不因为一次 Web 改动被跳过。规则导入 reducer、helpers、host login 与 public-config 门控在 `pnpm test`。CI 的浏览器 E2E 跑 Python Playwright：`tests.e2e.test_web_audit`、`tests.e2e.test_ui_a11y`、`tests.e2e.test_rules` 和 `tests.e2e.test_rule_interactions`（isolated-dev 产品路径与 route mock）。`pnpm lint` 使用 `@shadcn/lint` 检查 Tailwind 组件的重绘、颜色、任意值、内联样式、静态 class 和未知 class，并用 ESLint `no-restricted-imports` 禁止直接导入 `@insforge/sdk` 与 PostgREST 客户端；基础 shadcn 源码目录作为组件实现排除，业务页面与 PPAP 组合层必须通过。浏览器里的键盘、焦点、视口、减少动效和粗指针计算样式在 `tests.e2e.test_ui_a11y`（含规则编辑）；审核复核的键盘与单一 H1 在 `tests.e2e.test_web_audit`。`/enter-legacy`、OnlyOffice 回退和现有 deep link 仍是有消费者的兼容能力，不由这条样式契约删除。默认 PR CI 跑 `node deploy/insforge/verify-config.mjs`（static config contract：原生 `docker compose config` + PPAP invariant）。InsForge `local start/status/db query/stop` 是 live native lifecycle：`InsForge lifecycle` workflow 在 `deploy/insforge/**`、该 workflow 本身或 `.node-version` 变化时自动运行，并保留 `workflow_dispatch` 与每周 schedule（`local start` 会拉取上游 setup.sh/镜像，CLI pin 不能冻结该 runtime；self-hosted runner 上每周一次足够低频）。无关 PR 不跑 live lifecycle。

```sh
cd apps/web
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test
pnpm lint
pnpm build
cd ../..
node deploy/insforge/verify-config.mjs
uv sync --locked --extra dev
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
uv run --no-sync pytest tests/corpus/v2_assets --ftype excel -q
uv run --no-sync pytest tests/corpus/v2_assets --ftype pdf -q
uv run --no-sync pytest tests/corpus/v2_assets --ftype scanned -q
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
  python -m pytest tests/corpus/test_psw_cross_format_regression.py tests/corpus/test_psw_cross_format_audit.py -q

export COMPOSE_PROJECT_NAME=ppap-schema
docker compose -f deploy/isolated-dev/compose.yaml up -d postgres --wait
docker compose -f deploy/isolated-dev/compose.yaml run --rm init-db
uv run --no-sync python -m unittest discover -s tests/schema -v
docker compose -f deploy/isolated-dev/compose.yaml down -v
# schema CI collects tests/schema/test_*.py against isolated-dev PostgreSQL.
# tests use container-native psql via docker compose exec -T postgres psql

export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
export COMPOSE_PROJECT_NAME=ppap-prod
docker buildx bake
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait
uv run --no-sync python -m unittest tests.deploy.test_runtime tests.deploy.test_backup -v
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans
```

PSW 的 `.doc` 用例需要 LibreOffice 和 Linux 上可执行的 `ppap-local-converter-sandbox`。CI 与推荐本地路径是先 `docker buildx bake worker`，再构建 `tests/corpus/Dockerfile.psw` 派生测试镜像并在其中跑 pytest（worker 已含 LibreOffice 与 sandbox；测试文件经 build context COPY，不 bind-mount 仓库）。`.doc` 转换是默认行为，没有开关变量；转换器或沙箱缺失时该项记 ERR，不跳过格式。需要 Paddle 的 PDF 转换优先使用 `tests/fixtures/ocr_cache`；未命中时再读 `PADDLE_OCR_URL`。人工 expected 以 corpus 内 CSV/XLSX 为准，不要改它来迎合实现。

#7 的代表性浏览器 E2E 覆盖待审+参考、提交、离开后回访、打开本轮原件版本（或看到 locator-gap）、原件预览、复核、显式重审，以及从账号菜单查看用量和从主导航查看审核结果：

```sh
uv sync --locked --extra dev --extra e2e
uv run --no-sync python -m playwright install chromium
export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
export COMPOSE_PROJECT_NAME=ppap-e2e
docker buildx bake
docker compose -f deploy/isolated-dev/compose.yaml up -d --wait --no-build
uv run --no-sync python -m unittest tests.e2e.test_web_audit tests.e2e.test_ui_a11y tests.e2e.test_rules tests.e2e.test_rule_interactions -v
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
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml up --build -d --wait
PPAP_DEEP_LIVE=1 uv run --no-sync python -m unittest tests.live.test_deep_isolated -v
docker compose -f deploy/isolated-dev/compose.yaml -f deploy/isolated-dev/deep-live.yaml down -v --remove-orphans
```

Overlay 显式设置 `PPAP_DEEP_EXECUTOR_REVISION=ppap-deep-v2`、`PPAP_DEEP_RUNTIME=container-only`、`PPAP_DEEP_SANDBOX_PROFILE=off`。旧内核的 `off` 不提供强文件系统或出口网络隔离；只允许此容器级模式，不要声称是 gVisor。此 live 协议 smoke 上传玩具但有效的 XLSX 与已发布规则，要求带实际单元格证据的语义结果和已记录的模型用量；固定 checker、脚本 guest 或仅有 PASS 状态都不算审核证明。客户 A1–A12 仍须另用真实样例验收。默认 isolated-dev 仍不发布 Silo 端口，remote-dev 的现有配置不受此 overlay 影响。

模型 grant 的默认额度是 4,000,000 输入 token、100,000 输出 token、128 次请求和 40 分钟。输入额度按提供方返回的已确认累计用量停止后续请求，不是严格的费用硬上限：加密推理历史无法在请求前准确计数，故最多一笔受 8 MiB 请求体上限约束的在途请求可能令输入用量超额。输出请求按剩余额度限制；用量缺失即关闭 grant，不把请求字节误记为 token。单测验证这些边界；live smoke 仍需记录真实提供方用量。本轮开发已获授权可依据实测提高这些上限，但每次调整仍须保留有限额度并说明原因和实际用量，不以无限重跑替代修复。CETUS 完整规则实测触及原 1,600,000 输入额度后提高至当前值；后续联合审核在原 20 分钟截止时仍未执行完毕，因此时长提高至 40 分钟。这不是新的完成率保证。

上下文窗口和自动压缩沿用 grok-build 对所选模型的默认配置；PPAP 不写 `context_window` 或自定义压缩阈值，也不为了验收强制压缩。单测只证明没有覆盖配置及事件计数正确；真实压缩仍以 `compact_boundary` 事件及压缩后规则、证据完整性为准。读取单次提示用量时，将互斥的未缓存、缓存读取、缓存写入三项相加，不使用累计输入额度判断上下文长度。

组织内测试资产和可访问环境按 [AGENTS.md](../AGENTS.md) 的默认规则直接使用。

`services/api/tests/test_rules.py` 通过真实 Excel/Word 提取与模拟外部 LLM 验证规则解析，再通过 Product API / Worker 验证受理、预览、发布、版本不变与来源授权。`packages/ppap_excel_audit_engine/tests/test_rule_ai_client.py` 验证每调用超时、零自动重试及错误日志脱敏。真实 PostgreSQL 契约在 `tests/schema/`：CI 用 `unittest discover -s tests/schema` 收集全部 `test_*.py`，共用 isolated-dev PostgreSQL fixture。其中包括 empty/previous schema 升级、规则解析的并发受理/租约/迟到完成/来源授权、审核绑定与用量分配。新增同类测试放到该目录即可进入 schema CI。`tests/e2e/test_rules.py` 使用结构化规则文件走真实上传、人工编辑、发布和重访；`tests/e2e/test_rule_interactions.py` 在浏览器 API 边界控制迟到响应，验证整页操作互斥、刷新只读、显式重跑，以及 `deep_offered` 对审核模式控件的门控。这些测试不调用外部模型服务。
