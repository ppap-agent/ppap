# 开发

普通工作只需要当前 owning Issue、相关源码和最近测试。Issue 表达验收结果，不表达实施顺序。

组织内资产的默认使用规则见 [AGENTS.md](../AGENTS.md)，这里不重复权限政策。

## 当前配置

应用配置的 executable authority 是 typed Settings、[`.env.dev`](../.env.dev) / [`.env.production`](../.env.production) 里的默认值，以及 [`.env.example`](../.env.example)。

- Secret 只通过本地 gitignored `.env`、GitHub Secrets 或部署 Secret 注入。
- 已提交的 `.env.dev` 填 isolated-dev Settings 默认值；[`.env.production`](../.env.production) 填生产仍可沿用的默认值，口令、S3 endpoint、公开 URL 和 LLM 留空。生产不能沿用 isolated-dev 口令和 `http://127.0.0.1:9000`。
- 生产 compose 用 Docker Compose 的 `--env-file` 注入。CI fixture 是 `deploy/compose.env.example`。真实部署把填好密钥的文件交给同一个 `--env-file`。`init-db` 读取 bootstrap/migrator 口令并用 `psql` 执行 `migrations/bootstrap.sql` 与 `migrations/apply.sql`；`init-bucket` 用 Silo `mcli` 确保对象桶。API/Worker 只插值各自拥有的字段（含选定的 `PPAP_PG_HOST`/`PPAP_PG_PORT`，以及 API 的飞书变量），不含 bootstrap/migrator 或对方角色口令。
- 把 API/Worker 接到已有的 self-hosted InsForge PostgreSQL 时，用 `docker compose -f deploy/insforge/compose.yaml --env-file deploy/insforge/env.example up -d`。该 overlay 不启动 postgres，加入外部 `insforge-network`。Custom Compute 规格在 `deploy/insforge/compute/`。
- `.doc` 转换是默认行为，没有开关变量。
- 可选 capability 缺配置只影响该 capability，不阻止无关开发。Deep 由 `PPAP_DEEP_CONTROLLER_SOCKET` 表示是否配置；未配置时 Core 仍可启动，Deep 轮次明确失败而不回退 Standard。Live Deep 用 `deploy/isolated-dev/deep-live.yaml` 挂 UDS。Controller 默认 process runtime；gVisor runtime 名称、guest 镜像和 `PPAP_DEEP_RUNTIME` 只属于 Controller 进程环境，不进入 Worker typed Settings。production 默认不向客户提供 Deep（`public-config.deep_offered=false`，API 拒绝 `analysis_mode=deep`）；isolated-dev/test 仍可用于协议和隔离验证。TKE current 在 process sidecar 真实 executor 可用后设置 `PPAP_DEEP_CUSTOMER_ENABLED`。
- 预览切流用 `PPAP_CURRENT_TENANT_ALLOWLIST`（逗号分隔的 Feishu `tenant_key`）。空名单不限制。生产目录写入海岸线租户。`PPAP_USAGE_CHARGE_ENABLED=false` 时受理不写 `usage_events`，预估点数为 0。
- 已有组织内配置可以直接用于迁移/开发；终态配置归本仓 Settings。
- 新增配置项前先确认它代表独立运行决策，而不是可推导内部细节。

## 当前命令

Local 与 CI 使用同一组 user-space 工具：uv、unittest/pytest、git、compileall、pnpm、Node、Python Playwright，以及 InsForge 的 `node deploy/insforge/verify-config.mjs` 与 pinned `@insforge/cli`。`verify-config.mjs` 是 static config contract：Compose 语法与插值由 `docker compose -f deploy/insforge/compose.yaml --env-file deploy/insforge/env.example config` 拥有，只需要 Docker Compose CLI，不需要 daemon 或 Buildx，也不启动容器。live native lifecycle 是 `bash deploy/insforge/smoke-local.sh`，需要 `local start` 使用的 runtime Docker capability。Docker Engine、Compose 和 Buildx 是 runner/platform capability，由宿主提供；需要完整 Docker 的 job 只做 fail-fast 检测。schema 通过 isolated-dev PostgreSQL 容器内的 `psql` 执行 `migrations/`。

唯一产品前端使用 React + TypeScript。在 `apps/web` 中运行 `pnpm install --frozen-lockfile`、`pnpm typecheck`、`pnpm test`、`pnpm lint` 和 `pnpm build`，详情见 [Web Client](../apps/web/README.md)。浏览器 E2E 是 Python Playwright（`uv run --no-sync python -m unittest tests.e2e...`），对着 isolated-dev。浏览器联调的页面来源必须在 API 的允许来源中；本地 Compose 使用 `http://127.0.0.1:4173`。

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

export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake
COMPOSE_PROJECT_NAME=ppap-prod docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait
COMPOSE_PROJECT_NAME=ppap-prod docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans

COMPOSE_PROJECT_NAME=ppap-dev docker compose -f deploy/isolated-dev/compose.yaml up --build -d --wait
COMPOSE_PROJECT_NAME=ppap-dev docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

unit 用 `uv run --no-sync python -m unittest discover -s packages/ppap_core/tests -t . -v
uv run --no-sync python -m unittest discover -s packages/ppap_deep/tests -t . -v
uv run --no-sync python -m unittest discover -s packages/ppap_excel_audit_engine/tests -t . -v
uv run --no-sync python -m unittest discover -s packages/ppap_ocr_adapter/tests -t . -v
uv run --no-sync python -m unittest discover -s services/api/tests -t . -v
uv run --no-sync python -m unittest discover -s services/worker/tests -t . -v
uv run --no-sync python -m unittest discover -s tests/integration -t . -v
uv run --no-sync python -m unittest discover -s tests/repository -t . -v`。schema 用 `uv run --no-sync python -m unittest discover -s tests/schema -v`（isolated-dev PostgreSQL，容器内 `psql`）。corpus 与浏览器 E2E 见 [TESTING](TESTING.md)。生产镜像由 `docker buildx bake` 打 `ppap-api` / `ppap-worker` / `ppap-web`，标签为同一 `PPAP_BUILD_SHA`。`deploy/app/Dockerfile` 在装完 Debian/pip 之后才接收 `PPAP_BUILD_SHA`，apt 对 502 会重试，这样换 Git SHA 不会重跑 `apt-get`。schema 由 PostgreSQL 容器内的 `psql` 执行 `migrations/`；空库先跑 `migrations/bootstrap.sql`。Python 依赖由已提交的 `uv.lock` 拥有；`pyproject.toml` 是声明，`requirements.txt` 是同一组 pin 的可读副本，不要另写安装入口。

完整 corpus 命令见 [TESTING](TESTING.md)。以上是合入后的真实入口。

## 工程工具链

当前实现是 Python 3.12、单一 `pyproject.toml`、已提交的 `uv.lock`，以及 `apps/web` 的 pnpm/TypeScript。质量检查是 `git show --check` 与 `compileall`；Python 测试是 unittest / pytest；前端白盒/契约测试是 `pnpm test`；跨栈浏览器 E2E 是 Python Playwright；镜像是 Docker Bake；运行时是 Docker Compose 或 InsForge native lifecycle（`@insforge/cli@0.2.8`）。

CI 以项目原生 metadata / lockfile 为 authority，不再另写一套安装语义：

- Python 依赖：`uv.lock`。unit / schema / corpus 使用 `uv sync --locked --extra dev`；Docker integration / Playwright E2E 另加 `--extra e2e`。安装之后用 `uv run --no-sync` 执行命令，避免 `uv run` 按默认 extra 再 sync 一次。
- Python 解释器：`.python-version`（3.12）。`pyproject.toml` 的 `requires-python = ">=3.12"` 是兼容范围。
- Node：`.node-version`（22）。workflow 通过 `actions/setup-node` 的 `node-version-file` 读取。
- pnpm：`apps/web/package.json` 的 `packageManager`。`corepack enable` 后 Corepack 直接使用该字段；workflow、Web Dockerfile 和 isolated-dev Vite overlay 不再重复 `pnpm@…` 字面量。安装仍是 `pnpm install --frozen-lockfile`。
- GitHub Actions：第三方/官方 action 钉到不可变 commit SHA，行尾注释写清已解析的 tag（例如 `actions/checkout@11bd719… # v4.2.2`）。本地 composite action 用路径。当前 SHA 来自 GitHub 上对应 release tag 的剥皮 commit，不是编造的 hash。
- uv 二进制：由 `astral-sh/setup-uv` 的 `version: "0.9.26"` 引导安装。这是 bootstrap pin；安装 uv 本身必须发生在读取 `uv.lock` 之前。

有意保留的重复（消除它们需要模板化 Dockerfile 或另做包装层）：

- `deploy/app/Dockerfile` 继续用 `python:3.12-slim-bookworm` 和 `pip install`；生产镜像不走 CI 的 uv 项目环境。
- `deploy/web/Dockerfile` 的 `node:22-alpine` 与 `deploy/isolated-dev/web-dev.yaml` 的 `node:22-bookworm` 仍写在镜像 tag 里。
- `deploy/deep/Dockerfile` 为安装 `@xai-official/grok` 使用独立的 `node:22.14.0-bookworm-slim@sha256:…` 和 `pnpm@10.6.1`，不是 Web 的 `packageManager` authority。

CI 依赖 capability，而不是发行版。workflow 可以安装 uv/Python、Node、Corepack/pnpm 和项目依赖；不得用 `sudo` / `apt` / `yum` / `apk` / `systemctl` 修复 runner，也不得安装或启动 Docker。schema / Docker integration / PSW corpus / InsForge lifecycle 要求 runner 已提供 Docker Engine、Compose 和 Buildx；缺任一能力时 fail-fast 输出 `runner does not satisfy ppap-docker capability`。Web contract、Python unit 和普通 corpus 不要求 Docker。InsForge static config 只需要 Docker Compose CLI（`docker compose config`），不需要 daemon 或 Buildx，也不启动容器；CLI 缺失时由 `verify-config.mjs` 给出明确错误。live InsForge lifecycle 需要 `local start` 使用的 runtime Docker capability，只在 InsForge 相关 path、手动 dispatch 或每周 schedule 上运行。`Docker integration` 对同一 `PPAP_BUILD_SHA` 只 `docker buildx bake` 一次，打 `ppap-api` / `ppap-worker` / `ppap-web`，生产 runtime/backup 与 isolated-dev E2E 复用这些镜像（E2E 使用 `compose up --no-build`）。PSW 仍单独 `docker buildx bake worker`，从该 worker 镜像派生测试镜像（LibreOffice 与 sandbox 已在其中），通过 build context COPY 测试文件，不 bind-mount 仓库路径。CI Compose 使用 `ppap-ci-<purpose>-<run_id>-<run_attempt>` 作为 project name，cleanup 在独立 `if: always()` step。现有 Compose stack 仍 bind-mount 仓库内 `migrations/`，因此 schema / Docker integration / InsForge lifecycle 需要 Docker daemon 能看到 runner 使用的同一份仓库路径。这是既有 Compose 生命周期，不新增 host-workspace bind mount。后续若把 Ruff、Pyright 或 Vite 收进来，直接调用那些 CLI，不另起 PPAP dispatcher。PC 在飞书宿主中通过 `POST /login/feishu/host` 自动登录；普通浏览器仍走 `/login/feishu` OAuth。生产回调域名、事件订阅、网页可信域名和客户入口切换属于 #15，不在宿主登录代码里直接改现网。

#15 剩余：把本仓客户入口切到 current `ppap`；compose 验证不等于客户 cut-over。不拆除 `ppap-agent`：它是算法验证基地和参考实现，并继续服务其它用户。

## 迁移工作

选择迁移粒度时以**最快形成稳定客户路径且降低终态上下文**为准。一个 package/能力域整体迁入更快时就整体迁入；迁入后立即把 tests/config 归本仓并删除跨仓长期依赖和无消费者代码。

Engine migration 的具体 baseline 见 #3；飞书 capability baseline 见 #11。不要把旧算法已有 failure 变成迁移 blocker。

## 最短交付路径

当前 Issue → 复用/迁入现成资产 → 跑 baseline/当前测试 → 修新增 regression 或产品缺口 → 必要真实环境验证 → 当前 PR → 尽快打通客户路径。
