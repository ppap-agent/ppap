# 开发与真实验证入口

先读根 AGENTS、当前 Issue 和它直接引用的权威。目标应用仍待实现；以下已有命令只属于验收工具，不虚构 Product API/Web 的启动命令。运行栈由 [#2](https://github.com/ppap-agent/ppap/issues/2) 交付，业务状态见 [ROADMAP](ROADMAP.md)。

## 配置原则

Core PPAP 的目标开发体验是：**一个 Secret + 两个显式 LLM 配置即可启动核心栈**。

```text
LLM_API_KEY   # Secret，必需
LLM_BASE_URL  # Config，必需
LLM_MODEL     # Config，必需
```

这里的协议边界是 OpenAI-compatible，不绑定 OpenAI 厂商，因此 Base URL 和 Model 不能由代码偷偷假定。只有 API key 是秘密；Base URL 和 Model 是普通运行配置。

隔离开发环境的 PostgreSQL、S3-compatible object storage、内部开发账号/角色等由仓库提供安全且固定的 dev defaults。普通 Cursor/本地开发不应为了启动 core stack 再配置数据库或对象存储 Secrets。生产部署仍使用最小权限角色和部署 Secret。

飞书、Partner AI、legacy engine、未来独立 OCR provider 都是按 capability 接入的外部依赖。缺少它们的凭据不得阻止 Core PPAP 启动；只有真正进入相应能力时才应明确报告 unavailable / NOT_PROVEN。

当前 Cursor Cloud 若已经存在：

```text
OPENAI_API_KEY
OPENAI_BASE_URL
MODEL_NAME
```

请在环境侧一次性改名或映射为：

```text
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
```

不要在应用代码中长期维护两套名称，否则配置会重新出现双重 authority。

飞书接入阶段使用：

```text
FEISHU_APP_ID       # 普通配置
FEISHU_APP_SECRET   # Secret
```

用户 access/refresh token 是运行时身份数据，不是 Cloud Agent 的静态 Secret。

## #2 实现时必须保持的配置边界

- 建立一个 typed settings owner；API 和 Worker 复用配置语义，不在各模块散落 `os.getenv`。
- Core readiness 只依赖真正的核心基础设施；可选 integration 的缺失通过 capability 状态暴露，而不是让整个 API 启动失败。
- Web 只能获得明确允许公开的构建时/运行时配置，绝不能打包 server Secret。
- `/health` 只证明进程存活；`/ready` 证明 core dependencies 可用；飞书、Partner AI 等单独报告 capability readiness。
- 新增环境变量前先证明没有安全默认值、可靠推导值或已有 canonical owner。不要为了未来可能使用而预留变量。
- 若未来出现第二种真正不兼容的 LLM 协议，再引入 provider abstraction；目前不要增加 `LLM_PROVIDER`、`LLM_VENDOR` 等重复选择器。

## 当前可执行命令

Python 3.11+ 可运行工具自测和版本探针。浏览器检查另需 Playwright 与 Chromium。

```sh
python -m unittest discover -s tests/unit -v
python -m py_compile tools/live.py tools/legacy_engine_gate.py
python -m pip install -r tests/live/requirements.txt
python -m playwright install chromium
cp .env.example .env
# 只加载自己维护的本地配置，不 source 遗留来源的未知文件。
set -a; . ./.env; set +a
python tools/live.py audit
python tools/live.py history
python tools/live.py review
python tools/live.py isolation
```

缺 Live 配置会明确输出 NOT_PROVEN 并非零退出，不假装通过。每次运行有独立 `.artifacts/live/*.json`；该目录不进 Git，默认不录屏、不保存 raw traffic。各命令需要什么、能证明什么见 [TESTING](TESTING.md)。工具依赖版本是本轮可运行基线，不是未来产品语言选型。

## Live 验证变量

`.env.example` 中 `PPAP_*` 只服务当前 Live/migration 工具，不是 Core PPAP 的启动配置。

| 名称 | 使用方与用途 |
|---|---|
| PPAP_ENVIRONMENT | 安全的隔离环境标签（1–32 位字母、数字、下划线或连字符） |
| PPAP_WEB_URL | 目标隔离 Web 的实际入口，不能填旧 host 代替新产品 |
| PPAP_BUILD_SHA | 本次部署的完整源码身份；页面 build-revision 必须读回相同值 |
| PPAP_AUTH_STATE | 授权测试账号的 Playwright storage_state 文件；已有 session 不证明 OAuth 首次登录 |
| PPAP_OTHER_TENANT_STATE | 另一隔离租户的状态文件，用于 UI 拒绝子证明 |
| PPAP_SAMPLE / PPAP_ORACLE | 有权使用的真实原件和事先审阅的规则/证据期望；oracle 不是当前运行自己生成 |
| PPAP_EXISTING_ROUND_URL | 该测试账号可访问、与 oracle 对应的已完成轮次；不得把任意客户记录当测试夹具 |
| PPAP_REPLACEMENT_SAMPLE | 与旧资料同名而字节不同的授权原件，用于历史版本测试 |
| PPAP_LIVE_TIMEOUT_SECONDS | 一次运行等待终态的上限，默认 300 秒；不表示产品 SLA，不触发重新提交 |
| PPAP_VERSION_URL | 只读版本探针地址；其成功仅证明前置，不证明 PPAP 产品完成 |

迁移期 `PPAP_LEGACY_*` 只由旧引擎对照工具使用。终态删除这些配置与 HTTP 依赖，而不是让它们变成正常启动要求。

## 外部 capability 的加入时机

| 能力 | 什么时候需要配置 | Core 缺失时 |
|---|---|---|
| OpenAI-compatible LLM | 从首个真实审核开始：`LLM_API_KEY` + `LLM_BASE_URL` + `LLM_MODEL` | 审核能力不可用；其它无关管理/诊断路径不应假装成功 |
| Feishu OAuth / Drive / Docs | #4/#11：`FEISHU_APP_ID` + `FEISHU_APP_SECRET` | Feishu capability disabled；普通 Web/Core 可用 |
| Partner AI | #12 根据真实 API 契约再增加必要配置 | usage reconciliation capability disabled；不得伪造余额 |
| 独立 OCR provider | 只有 #3 corpus 证明本地 parser/LibreOffice/multimodal LLM 不足时 | 不提前增加 Secret |
| legacy engine | 仅迁移对照期间 | 不影响终态 Core；最终删除 |

已有合法开发凭据只写入 Cloud Secret 或 gitignored 本地 `.env`；仓库只保留空值和安全 dev defaults。不要从历史泄漏值猜当前凭据，也不要为“修复安全”擅自轮换共享生产 key。

## 一项工作的最短交付路径

检查当前 Issue/PR → 读相关源码/权威 → 实现一个可验收结果 → 运行最近测试及需要的 Live Gate → 更新直接相关文档 → commit & push → 回读 Issue/提交与证据。语义变更同时审阅 Story；只调整 UI 选择器不得改弱断言。

构建、静态检查、工具自测、产品 Live 分别报告。用已确认产品政策判断失败，不靠降低 oracle 让测试变绿。无法取得外部账号或运行环境时，保留明确阻塞和复跑命令，继续不依赖该阻塞的工作。
