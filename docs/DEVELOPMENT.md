# 开发与真实验证入口

先读根 AGENTS、当前 Issue 和它直接引用的权威。目标应用仍待实现；以下已有命令只属于验收工具，不虚构 Product API/Web 的启动命令。运行栈由 [#2](https://github.com/ppap-agent/ppap/issues/2) 交付，业务状态见 [ROADMAP](ROADMAP.md)。

## 当前可执行命令

Python 3.11+ 可运行工具自测和版本探针。浏览器检查另需 Playwright 与 Chromium。

```sh
python -m unittest discover -s tests/unit -v
python -m py_compile tools/live.py
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

缺配置会明确输出 NOT_PROVEN 并非零退出，不假装通过。每次运行有独立 `.artifacts/live/*.json`；该目录不进 Git，默认不录屏、不保存 raw traffic。各命令需要什么、能证明什么见 [TESTING](TESTING.md)。工具依赖版本是本轮可运行基线，不是未来产品语言选型。

## 环境变量

`.env.example` 中 `PPAP_*` 是本次工具真实使用的配置。应用将来采用哪些变量由 #2 的实现决定，不把此处的建议名字当已存在应用配置。

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

## 应用所需外部依赖

真实数据库、对象存储、模型/OCR、身份和计费依赖由隔离部署提供。不要把本会话与用户机器视为同一文件系统。先检查现有安全配置，仅报告变量是否可用，不输出值。

| 依赖 | 配置内容 | 验证入口 / 缺失处理 |
|---|---|---|
| PostgreSQL | API、Worker、迁移角色各自连接参数 | #2/#8 的连接、权限、事务和故障测试；不以管理员连通证明运行角色正确 |
| S3-compatible | endpoint、bucket、访问凭据和签名行为 | #5 真实上传/下载/不可变性；不假设所有 provider 相同 |
| LLM / OCR | provider、model、endpoint、开发 key 和有效配置 | #3/#6 在受控 corpus 真调用；无 key 不自动改用伪模型 |
| 飞书 | 现有 app、开发秘密、授权回调与用户 token 安全存储 | #4/#11 实际 OAuth/Drive；不要仅由 scope 文本断定用户有资料权限 |
| Partner AI | 当前产品标识、测试租户、凭据、实际政策 | #12 的真实权益、用量和回执；不能用本地固定点数替代 |
| 旧引擎过渡 | endpoint、鉴权和真实规则所属 projectId | 只作迁移对照；版本探针不提交任务，任务执行需明确测试输入；终态移除旧 HTTP 依赖 |

已有合法开发凭据可写到 gitignored `.env`；仓库只保留占位示例。不要从公开日志或历史泄漏值猜当前凭据，也不要为“修复安全”擅自轮换共享生产 key。

## 一项工作的最短交付路径

检查当前 Issue/PR → 读相关源码/权威 → 实现一个可验收结果 → 运行最近测试及需要的 Live Gate → 更新直接相关文档 → commit & push → 回读 Issue/提交与证据。语义变更同时审阅 Story；只调整 UI 选择器不得改弱断言。

构建、静态检查、工具自测、产品 Live 分别报告。用已确认产品政策判断失败，不靠降低 oracle 让测试变绿。无法取得外部账号或运行环境时，保留明确阻塞和复跑命令，继续不依赖该阻塞的工作。
