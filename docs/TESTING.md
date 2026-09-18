# 验证：产品结果、前置条件和工具自测分开

合同见 [STORIES](STORIES.md)，产品要求见 [NORTHSTAR](NORTHSTAR.md)，真实命令见 [DEVELOPMENT](DEVELOPMENT.md)。本文件拥有证明的聚合规则，不重新定义产品。

## 已可运行的检查

| Gate | 命令 | 证明边界 | 未覆盖的必要证明 |
|---|---|---|---|
| G01 | `python tools/live.py audit` | 一个已审阅 case 的真实上传/准备/提交，关闭原 context，以已有 session 在新 context 回访同轮次、完整结果、下载摘要和证据 | 其它支持 case、多文件复杂路径、首次 OAuth；由 #3/#6/#7/#9 承接 |
| G02-ui | `python tools/live.py isolation` | 同一对象的有权 UI 正例与另一租户 UI 拒绝 | OAuth 和直接 API/对象存储的授权不能由 UI 隐藏证明；#4 必须补齐 |
| G03 | `python tools/live.py history` | 对已有轮次固化同名新内容后，下载和结果仍为旧版本 | S3 条件写、并发 finalize、过期租约；#5 的真实存储/集成测试 |
| G05-review | `python tools/live.py review` | 真实保存一次复核，重新访问可读，机器结果和原件不变 | 并发冲突、显式重审分轮和权限；#10 必须补齐 |
| P00 | `python tools/live.py probe --url URL --expected-build SHA` | 真实只读端点返回预期构建身份 | 不是产品 Story，不执行审核 |
| P03 | `python tools/stack.py verify` | 本机 isolated-dev 进程、`/health`、`/ready`（PG+对象存储）、版本身份、公开配置无密钥、Worker heartbeat | 不是用户 Story；不证明登录、审核、证据或飞书 |
| P04 | `python tools/stack.py verify-auth` | 预存 session 的跨租户对象拒绝、同租户只读非复核、logout、飞书 OAuth 启动 URL；Web 含 access-denied/login hooks | 不是 S02 真实 OAuth 登录；app_ticket 接收与 Drive 授权仍缺公网回调 |
| P05 | `python tools/stack.py verify-uploads` | 真实 PG/MinIO 上暂存→SHA-256 固化、同名新字节保留旧版本下载、跨租户拒绝、重复 finalize / 覆盖暂存、过期暂存回收、SSRF 拒绝；Web 含 upload/download hooks | 不是 S03 审核历史；不使用客户样本；不把 ETag 当 SHA-256 |
| P01 | 每次 push 由 `Push preflight` 调用 `LLM readiness`；亦可手动运行 | GitHub Secrets `OPENAI_API_KEY / OPENAI_BASE_URL / MODEL_NAME` 映射到 canonical LLM 配置后能完成一次 Responses 请求并返回 Response envelope | 不证明模型质量、规则准确性、OCR、审核结果或任何用户 Story |
| P02 | 每次 push 由 `Push preflight` 调用 `Feishu readiness`；亦可手动运行 | 当前商店应用的 App ID/Secret 能让飞书接受 `app_ticket/resend` 请求 | 会触发一次 app_ticket 推送；不证明 ticket 已被新系统接收、tenant token、用户 OAuth、Drive/Docs、评论写回或 Partner AI |

P01/P02 是彼此独立的 reusable 外部依赖探针。每次 push 的 `Push preflight` 并行调用两者，只有两者成功才继续 deterministic verification；它们也可单独手动运行。P01 只发送固定无业务内容；P02 只验证商店应用静态凭据并触发一次 app_ticket 重推。两者不 checkout 仓库、不安装 SDK、不自动 retry、不上传 artifact，也不把 secret/token/原始响应写入日志。

这些是可执行的子证明/前置证明，不是十个 Stories 均已实现。`stack.py` 已提供 isolated-dev API/Worker/PG/对象存储/本机 Web；UI driver 尚未与审核页面实测对齐。没有登录态、样本和 oracle 时 G01–G05 应失败。S04 故障注入、S06 飞书、S07 外部账单、S08 数据迁移、S09 恢复和 S10 交付的完整 runner 由对应 Issues 随产品实现交付，不用假脚本提前填“已覆盖”。

## UI driver 与 oracle

`tools/live.py` 使用小型 `data-testid` 映射访问用户可见控件。这是候选测试适配，不是要求产品保持某个组件结构。目标 UI 落地时替换此处的控件映射，不改变 Story 的用户结果。缺选择器可能是适配错误，不能自动归咎产品。

现有 hooks：build-revision、source-upload、prepare-audit、rule-selector、submit-audit、round-id、execution-state、rule-version、finding、finding-id、finding-status、evidence-state、view-evidence、evidence-panel、evidence-locator、evidence-source-sha256、next-action、download-original、document-version-id、access-denied、login-feishu、logout、current-user、review-reason、save-review、review-entry。

显示值的候选映射是 completed 与 available/missing；它们不是新产品状态机的强制命名。可将真实 UI 状态映射成等价语义。列表顺序不决定结果；同一 case 中检查项集合由事先审阅 oracle 决定，不能任意减少集合。

复制 [示例](../tests/live/oracle.example.json) 到 gitignored `.local/oracle.json`，替换成 #3 认可样本的真实原件摘要、规则版本、检查项和 locator。`authority` 指向审阅记录与版本。示例不能直接作为成功 oracle。

## 结果与失败

产品 Live 每个运行输出 `PROVEN` 或 `NOT_PROVEN`，保存 gate/Story 内容摘要、目标构建、时间、运行 ID 和最小语义证据。依赖 probe 同样只对自己的边界给结果。绝不输出原件、session、用户名、深链或异常原文。

产品子证明的 `PROVEN` 只证明表中该子集。最终接受要求：S01–S10 的所有必要子证明、支持矩阵和迁移/恢复条件都在同一发布候选及已核验环境下成立。任何必要项缺失、未执行、跳过或 NOT_PROVEN，完整产品都为 NOT_PROVEN。绝不能把 P00、P01、P02 或工具自测汇总成产品成功。

归因与结果分开：确证 DNS 失败可归 external_environment；超时、选择器不匹配、未配置或终态缺失默认 inconclusive，直到证据指出责任。新修复可新开 proof run，旧失败不得覆盖。

## 静态审阅记录

审阅依据为本轮 NORTHSTAR、当前 #2–#16 的验收语义，以及 Live Story / Live Gate 方法。工具自测只检验 gate 自身的判定和安全失败，不模拟 PPAP 产品成功。真实依赖正常开发费用已授权。
