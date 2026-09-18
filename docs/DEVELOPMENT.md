# 开发与真实验证入口

先读根 AGENTS、当前 Issue 和它直接引用的权威。目标应用仍待实现；以下已有命令属于验收/依赖验证工具，不虚构 Product API/Web 的启动命令。运行栈由 [#2](https://github.com/ppap-agent/ppap/issues/2) 交付，业务状态见 [ROADMAP](ROADMAP.md)。

## Core LLM 配置

PPAP 的当前 LLM 边界是 **Responses-compatible API**，不是 Chat Completions，也不绑定 OpenAI 厂商。当前 isolated-dev profile 有两个可覆盖的仓库默认值：

```text
LLM_BASE_URL=https://api.trustedtunnel.app/v1
LLM_MODEL=gpt-5.6-luna
```

因此普通 Cursor Cloud / 本地 isolated-dev 启动只要求一个 Secret：

```text
LLM_API_KEY
```

应用最终应将请求发送到 `<LLM_BASE_URL>/responses`。当前只有这一种真实协议，不增加 `LLM_PROTOCOL`、`LLM_PROVIDER` 或 `LLM_VENDOR`。只有出现第二种真实且不兼容的协议后才重新抽象。

Base URL 和 Model 是普通配置，不是秘密；但 Cursor Cloud 目前把所有环境项都存进 Secret store 也完全可接受。**存储机制不改变安全分类。** 如果 Cursor 已经注入 `LLM_BASE_URL` / `LLM_MODEL`，它们覆盖仓库 isolated-dev defaults。

当前 Cursor 若仍使用历史名称：

```text
OPENAI_API_KEY
OPENAI_BASE_URL
MODEL_NAME
```

环境侧应一次性改名/映射为 `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL`。应用代码不维护两套 env authority。因为 Base URL / Model 已有当前 isolated-dev defaults，实际必须迁移的只有 API key 名称。

这些 defaults 是当前团队开发 profile，不是 PPAP 产品定义，也不是生产 provider 承诺。生产部署必须在部署配置中显式确定其 endpoint/model；不得无意继承 isolated-dev provider。

## 外部环境 readiness

环境可用性由两个独立的原生 GitHub Actions probe 拥有，不在应用或 Python 工具中维护第二套实现。每次 push 由 [`Push preflight`](../.github/workflows/preflight.yml) 并行调用两者；它们也保留 `workflow_dispatch` 供单独排查：

- [LLM readiness](../.github/workflows/llm-readiness.yml)：直接用 `curl + jq` 对 GitHub Secrets 中现有的 `OPENAI_API_KEY / OPENAI_BASE_URL / MODEL_NAME` 发一次 `/responses` 请求；Base URL 与 Model 在请求前会去掉首尾空白，并在 workflow 边界映射为 canonical `LLM_*` 语义。应用代码仍只认 `LLM_*`。它只证明当前 GitHub 配置的 endpoint、key、model 与 Responses envelope 可用。
- [Feishu readiness](../.github/workflows/feishu-readiness.yml)：当前真实 App 已由飞书错误码确认是**应用商店应用**。probe 用 `FEISHU_APP_ID / FEISHU_APP_SECRET` 调 `app_ticket/resend`，只证明商店应用凭据被飞书接受；该调用会触发一次 app_ticket 推送事件。它不证明 app_ticket 已被当前新系统接收，也不证明 app_access_token / tenant_access_token、用户 OAuth、Drive/Docs 权限或 Partner AI。

两者都是 reusable workflow（`workflow_call`）并保留 `workflow_dispatch`。每次 push 的 `Push preflight` 必须等 LLM 与 Feishu 两者都成功后，才调用 deterministic verification。probe 本身不 checkout 仓库、不安装 SDK、不上传 artifact、不自动 retry；失败只输出脱敏的协议字段，不输出 key/token/raw response。PR 的 deterministic verification 仍可独立运行，因为 PR 不应依赖仓库 Secrets。探针成功不能替代 #3/#6 的审核 corpus，也不能替代 #4/#11 的飞书用户路径。

## Core 与 optional integrations

隔离开发环境的 PostgreSQL、S3-compatible object storage、内部开发账号/角色由仓库提供安全 dev defaults。普通 Cursor/本地开发不应为了启动 core stack 再配置数据库或对象存储 Secrets。生产仍使用最小权限角色和部署 Secret。

飞书、Partner AI、legacy engine、未来独立 OCR provider 都是按 capability 接入的外部依赖。缺少它们的凭据不得阻止 Core PPAP 启动；只有进入相应能力时才明确报告 unavailable / NOT_PROVEN。

飞书接入阶段使用：

```text
FEISHU_APP_ID       # 普通配置
FEISHU_APP_SECRET   # Secret
```

用户 access/refresh token 是运行时身份数据，不是 Cloud Agent 的静态 Secret。

## #2 实现时必须保持的配置边界

- 建立一个 typed settings owner；API 和 Worker 复用配置语义，不在模块中散落 `os.getenv`。
- isolated-dev profile 可以给 Base URL / Model 和 core infrastructure 提供仓库 defaults；生产 deployment 显式提供实际 endpoint/model/credentials。
- Core readiness 区分配置完整与外部 readiness probe。不要只因字符串存在就声称依赖 ready。
- `/health` 只证明进程存活；`/ready` 证明 core dependencies 可用；飞书、Partner AI 等单独报告 capability readiness。
- Web 只能获得明确允许公开的配置，绝不能打包 server Secret。
- 可选 integration 缺配置不能让整个 API 启动失败，也不能触发 fake quota / fake provider fallback。
- 新增环境变量前先证明没有安全 default、可靠推导值或已有 canonical owner。

## 当前可执行命令

Python 3.11+ 可运行工具自测和依赖探针。浏览器检查另需 Playwright 与 Chromium。

```sh
python -m unittest discover -s tests/unit -v
python -m py_compile tools/live.py tools/legacy_engine_gate.py

python -m pip install -r tests/live/requirements.txt
python -m playwright install chromium
cp .env.example .env
set -a; . ./.env; set +a
python tools/live.py audit
python tools/live.py history
python tools/live.py review
python tools/live.py isolation
```

Live product gates 会把证据写入 gitignored `.artifacts/`。LLM/Feishu readiness 只在对应 GitHub Actions run 中给出成功/失败，不保存 provider token、模型原文或飞书 token。

## Live 验证变量

`.env.example` 中 `PPAP_*` 只服务当前 Live/migration 工具，不是 Core PPAP 的启动配置。

| 名称 | 使用方与用途 |
|---|---|
| PPAP_ENVIRONMENT | 安全的隔离环境标签 |
| PPAP_WEB_URL | 目标隔离 Web 的实际入口 |
| PPAP_BUILD_SHA | 本次部署的完整源码身份 |
| PPAP_AUTH_STATE | 授权测试账号的 Playwright storage_state |
| PPAP_OTHER_TENANT_STATE | 另一隔离租户状态，用于拒绝子证明 |
| PPAP_SAMPLE / PPAP_ORACLE | 有权使用的真实原件和事先审阅的规则/证据期望 |
| PPAP_EXISTING_ROUND_URL | 与 oracle 对应的已完成轮次 |
| PPAP_REPLACEMENT_SAMPLE | 同名不同字节的授权原件 |
| PPAP_LIVE_TIMEOUT_SECONDS | 单次 proof run 等待终态上限，不表示 SLA |
| PPAP_VERSION_URL | 只读版本探针地址 |

迁移期 `PPAP_LEGACY_*` 只由旧引擎对照工具使用。终态删除这些配置与 HTTP 依赖，而不是让它们变成正常启动要求。

## 外部 capability 的加入时机

| 能力 | 什么时候需要配置 | Core 缺失时 |
|---|---|---|
| Responses-compatible LLM | isolated-dev 只需 `LLM_API_KEY`；Base URL/Model 有可覆盖 defaults | 审核能力不可用；其它无关路径不得假装审核成功 |
| Feishu OAuth / Drive / Docs | #4/#11：`FEISHU_APP_ID` + `FEISHU_APP_SECRET` | Feishu capability disabled；普通 Web/Core 可用 |
| Partner AI | #12 根据真实 API 契约再增加必要配置 | usage reconciliation disabled；不得伪造余额 |
| 独立 OCR provider | 只有 #3 corpus 证明现有解析/多模态路径不足时 | 不提前增加 Secret |
| legacy engine | 仅迁移对照期间 | 不影响终态 Core；最终删除 |

已有合法开发凭据只写入 Cloud Secret 或 gitignored 本地 `.env`；仓库只保留安全 isolated-dev defaults 和空 Secret。不要从历史泄漏值猜当前凭据。

## 一项工作的最短交付路径

检查当前 Issue/PR → 读相关源码/权威 → 实现一个可验收结果 → 运行最近测试及需要的 Live Gate → 更新直接相关文档 → commit & push → 回读 Issue/提交与证据。语义变更同时审阅 Story；只调整 UI 选择器不得改弱断言。

构建、静态检查、工具自测、依赖 readiness、产品 Live 分别报告。无法取得外部账号或环境时，保留明确阻塞和复跑命令，继续不依赖该阻塞的工作。
