# 开发

普通工作只需要当前 owning Issue、相关源码和最近测试。Issue 表达验收结果，不表达实施顺序。

组织内资产的默认使用规则见 [AGENTS.md](../AGENTS.md)，这里不重复权限政策。

## 当前配置

应用配置的 executable authority 是 typed Settings、[`.env.dev`](../.env.dev) / [`.env.production`](../.env.production) 里的默认值，以及 [`.env.example`](../.env.example)。

- Secret 只通过本地 gitignored `.env`、GitHub Secrets 或部署 Secret 注入。
- 已提交的 `.env.dev` 填 isolated-dev Settings 默认值；[`.env.production`](../.env.production) 填生产仍可沿用的默认值，口令、S3 endpoint、公开 URL 和 LLM 留空。生产不能沿用 isolated-dev 口令和 `http://127.0.0.1:9000`。
- 生产 compose 默认注入 `deploy/compose.env.example`（CI fixture）。真实部署把填好密钥的文件交给同一入口：`PPAP_COMPOSE_ENV=.env python tools/dev.py deploy` 或 `python tools/dev.py deploy --env-file .env`。`init` 读取整份选定文件；API/Worker 只插值各自拥有的字段（含选定的 `PPAP_PG_HOST`/`PPAP_PG_PORT`，以及 API 的飞书变量），不含 bootstrap/migrator 或对方角色口令。
- 把 API/Worker 接到已有的 self-hosted InsForge PostgreSQL 时，改用 `python tools/dev.py deploy --compose-file deploy/insforge/compose.yaml --env-file deploy/insforge/env.example`。该 overlay 不启动 postgres，加入外部 `insforge-network`。Custom Compute 规格在 `deploy/insforge/compute/`。
- `.doc` 转换是默认行为，没有开关变量。
- 可选 capability 缺配置只影响该 capability，不阻止无关开发。
- 已有组织内配置可以直接用于迁移/开发；终态配置归本仓 Settings。
- 新增配置项前先确认它代表独立运行决策，而不是可推导内部细节。

## 当前命令

Canonical 入口是 `python tools/dev.py`。isolated-dev 进程仍由 `python tools/stack.py` 管理。

```sh
python tools/dev.py install
python tools/dev.py quality
python tools/dev.py test
python tools/dev.py build
python tools/dev.py deploy
python tools/dev.py upgrade
python tools/dev.py undeploy
python tools/dev.py backup
python tools/dev.py restore
python tools/dev.py rollback
python tools/dev.py migrate

python tools/stack.py up
python tools/stack.py verify
python tools/stack.py seed-auth
python tools/stack.py verify-auth
python tools/stack.py verify-uploads
python tools/stack.py verify-audit
python tools/stack.py down
```

`python tools/dev.py test` 跑 unit。corpus 与浏览器 E2E 见 [TESTING](TESTING.md)。生产制品：`python tools/dev.py build` 写 `dist/identity.json` 与 `dist/web`；`PPAP_BUILD_IMAGES=1 python tools/dev.py build` 额外打 `ppap-api` / `ppap-worker` / `ppap-web` 镜像，标签为同一 Git revision。`python tools/dev.py migrate` 把配置的 PostgreSQL 从当前 schema 升到 latest（不对 isolated-dev 集群做 bootstrap；本地空库用 `python tools/stack.py migrate`）。依赖由根目录 `pyproject.toml` 拥有；`requirements.txt` 是同一组 pin 的可读副本，不要另写安装入口。

完整 corpus 命令见 [TESTING](TESTING.md)。以上是合入后的真实入口。

## 工程工具链

当前实现是 Python 3.12、单一 `pyproject.toml`、`python tools/dev.py`。CI 调用与本地相同的 install/quality/test/build/migrate/deploy/backup。后续若把 Ruff、Pyright 或 Vite 收进来，仍走这一入口，不另起流水线逻辑。#15 剩余：客户入口切到新主路径之后再 sunset `ppap-agent`；compose 验证不等于客户 cut-over。

## 迁移工作

选择迁移粒度时以**最快形成稳定客户路径且降低终态上下文**为准。一个 package/能力域整体迁入更快时就整体迁入；迁入后立即把 tests/config 归本仓并删除跨仓长期依赖和无消费者代码。

Engine migration 的具体 baseline 见 #3；飞书 capability baseline 见 #11。不要把旧算法已有 failure 变成迁移 blocker。

## 最短交付路径

当前 Issue → 复用/迁入现成资产 → 跑 baseline/当前测试 → 修新增 regression 或产品缺口 → 必要真实环境验证 → 当前 PR → 尽快打通客户路径。
