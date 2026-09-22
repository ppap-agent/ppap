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
- 可选 capability 缺配置只影响该 capability，不阻止无关开发。Deep 由 `PPAP_DEEP_CONTROLLER_SOCKET` 表示是否配置；未配置时 Core 仍可启动，Deep 轮次明确失败而不回退 Standard。Live Deep 用 `deploy/isolated-dev/deep-live.yaml` 挂 UDS；gVisor runtime 名称和 guest 镜像只属于 Controller 进程环境，不进入 Worker typed Settings。production 默认不向客户提供 Deep（`public-config.deep_offered=false`，API 拒绝 `analysis_mode=deep`）；isolated-dev/test 仍可用于协议和隔离验证。只有真实 executor 通过验收后，才设置 `PPAP_DEEP_CUSTOMER_ENABLED`。
- 已有组织内配置可以直接用于迁移/开发；终态配置归本仓 Settings。
- 新增配置项前先确认它代表独立运行决策，而不是可推导内部细节。

## 当前命令

Local 与 CI 使用同一组 native 工具：pip、unittest/pytest、git、compileall、Docker Buildx bake、Docker Compose、psql。

唯一产品前端使用 React + TypeScript。在 `apps/web` 中运行 `pnpm install --frozen-lockfile` 和 `pnpm build`，详情见 [Web Client](../apps/web/README.md)。浏览器联调的页面来源必须在 API 的允许来源中；本地 Compose 使用 `http://127.0.0.1:4173`。

```sh
python -m pip install -e ".[dev]"
git show --format= --check HEAD -- . ':!assets' ':!tests/fixtures'
python -m compileall -q packages services tests
python -m unittest discover -s tests/unit -v

export PPAP_BUILD_SHA="$(git rev-parse HEAD)"
docker buildx bake
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example up -d --wait
docker compose -f deploy/compose.yaml --env-file deploy/compose.env.example down -v --remove-orphans

docker compose -f deploy/isolated-dev/compose.yaml up --build -d --wait
docker compose -f deploy/isolated-dev/compose.yaml down -v --remove-orphans
```

unit 用 `python -m unittest discover -s tests/unit -v`。corpus 与浏览器 E2E 见 [TESTING](TESTING.md)。生产镜像由 `docker buildx bake` 打 `ppap-api` / `ppap-worker` / `ppap-web`，标签为同一 `PPAP_BUILD_SHA`。`deploy/app/Dockerfile` 在装完 Debian/pip 之后才接收 `PPAP_BUILD_SHA`，apt 对 502 会重试，这样换 Git SHA 不会重跑 `apt-get`。schema 由 `psql` 执行 `migrations/`；空库先跑 `migrations/bootstrap.sql`。依赖由根目录 `pyproject.toml` 拥有；`requirements.txt` 是同一组 pin 的可读副本，不要另写安装入口。

完整 corpus 命令见 [TESTING](TESTING.md)。以上是合入后的真实入口。

## 工程工具链

当前实现是 Python 3.12、单一 `pyproject.toml`。质量检查是 `git show --check` 与 `compileall`；测试是 unittest / pytest；镜像是 Docker Bake；运行时是 Docker Compose 或 InsForge native lifecycle。CI 的 CPython 安装与 [ppap-agent](https://github.com/ppap-agent/ppap-agent) 相同：`astral-sh/setup-uv@v7`（uv 0.9.26）+ `uv python install 3.12`；本仓没有 `uv.lock`，所以用 `uv pip install -e ".[dev]"`，不用 `uv sync --frozen`。schema / build / e2e 需要 Docker Engine、Compose 和 Buildx。self-hosted runner 上若 Engine 已可用，CI 只补 Compose/Buildx 插件，不会用 Debian `docker.io` 覆盖已安装的 `docker-ce`；仅在完全没有 Docker CLI 时才安装 `docker.io`。每次还会清掉遗留的 `iptables-legacy` FORWARD DROP，避免 Compose 用户网络丢包。后续若把 Ruff、Pyright 或 Vite 收进来，直接调用那些 CLI，不另起 PPAP dispatcher。PC 在飞书宿主中通过 `POST /login/feishu/host` 自动登录；普通浏览器仍走 `/login/feishu` OAuth。生产回调域名、事件订阅、网页可信域名和客户入口切换属于 #15，不在宿主登录代码里直接改现网。

#15 剩余：把本仓客户入口切到 current `ppap`；compose 验证不等于客户 cut-over。不拆除 `ppap-agent`：它是算法验证基地和参考实现，并继续服务其它用户。

## 迁移工作

选择迁移粒度时以**最快形成稳定客户路径且降低终态上下文**为准。一个 package/能力域整体迁入更快时就整体迁入；迁入后立即把 tests/config 归本仓并删除跨仓长期依赖和无消费者代码。

Engine migration 的具体 baseline 见 #3；飞书 capability baseline 见 #11。不要把旧算法已有 failure 变成迁移 blocker。

## 最短交付路径

当前 Issue → 复用/迁入现成资产 → 跑 baseline/当前测试 → 修新增 regression 或产品缺口 → 必要真实环境验证 → 当前 PR → 尽快打通客户路径。
