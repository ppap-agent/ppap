# 外部方案比较与待验证选择

检索日：2026-09-18。下面引用一手文档，证明候选提供什么，不证明已经适配 PPAP；本轮未跑框架性能对比。既不按星数选型，也不把旧栈永久化。

| 环节 | 当前资产 / 候选 / 最小实现 | 当前判断及改变它所需的证据 |
|---|---|---|
| Web | Vue 双入口与现有 PC；Vue+Vite；React 框架；最小 SPA | 暂选复用 Vue 业务体验、改为一套构建。React 重写尚无已证实收益；比较真实准备/审阅/回访路径，而不是空白页速度 |
| Product API | Java 平台、Python FastAPI 资产；Spring Boot；最小 Python HTTP 服务 | 暂选 FastAPI/Pydantic/SQLAlchemy，减少跨语言维护但保留明确领域边界；如 Java 独有接口/库迁移风险显著，再保留限期适配，不全量翻译 |
| 持久执行 | PG claim；Temporal；DBOS；最小 PG 队列 | 当前问题可先沿用 PG claim+fencing。Temporal 的工作流恢复和 DBOS 的持久步骤值得研究，但必须证明事务受理、外部效果和运行成本更好；不能用框架宣传当 exactly-once 证明 |
| 文档理解 | 现有格式/规则测试；Docling；按格式小库组合 | Docling 是优先实验候选，不立即替换。必须比较合并单元格、跨页表、签章、中文、原文 locator 与最终规则结果；通用格式支持不等于审核等价 |
| Audit Agent | 确定性检查器+模型调用；Agents SDK、LangGraph、Pydantic AI | 不先引入通用多 Agent 框架。可借鉴类型化输出、检查点和 tracing；仅当 PPAP 需要这些语义且能减少自维护内容时采用。框架不拥有产品轮次/规则真相 |
| 身份 | UAC/JIT；Authlib 等协议库；自建 OAuth 协议 | 优先协议库而不是自写 OAuth。自行拥有最小租户/身份映射；飞书标识、授权续期和企业协作必须实测，不能假设通用 OIDC 邮箱合并适用 |
| 存储 | URL/文件表；S3-compatible 条件写；本地缓存 | 用不可变对象身份和产品授权；供应商条件写/签名行为逐项测。派生产物可缓存，不能把本地路径当跨主机文件身份 |
| 可观察性 | 多端日志；OpenTelemetry；少量结构日志 | 先贯通轮次/尝试与真实故障阶段，再引入必要 tracing。trace 不是业务事实；默认采集的模型载荷必须审查 |
| 验证/工具 | 原有 pytest/corpus；Playwright；uv/pnpm 工作区 | 复用测试知识，增加少数真实用户路径。不为 monorepo 预设大型构建平台；PWA 敏感缓存由产品政策控制 |

## 一手参考

- [Vue 工具链](https://vuejs.org/guide/scaling-up/tooling.html)、[React 创建应用](https://react.dev/learn/creating-a-react-app)：不同合理路径，不以框架名字决定用户体验。
- [FastAPI BackgroundTasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)、[Spring Boot 指南](https://spring.io/guides/gs/spring-boot/)：请求服务能力；进程内后台任务不是持久审核队列。
- [PostgreSQL SELECT / SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html)：队列消费者可用的锁语义，不提供跨外部效果的恰好一次保证。
- [Temporal 工作流执行](https://docs.temporal.io/workflow-execution)、[DBOS Python](https://docs.dbos.dev/python/programming-guide)：持久执行候选，须检查与产品原子受理的组合。
- [Docling 支持格式](https://docling-project.github.io/docling/usage/supported_formats/)、[文档模型](https://docling-project.github.io/docling/reference/docling_document/)：结构化文档与来源表示；旧 Office 格式仍有转换依赖。
- [Agents SDK](https://openai.github.io/openai-agents-python/agents/)、[tracing](https://openai.github.io/openai-agents-python/tracing/)、[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Pydantic AI 持久执行](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)：模型编排候选，不代替质量 oracle。
- [Authlib Starlette 文档](https://docs.authlib.org/en/v1.6.9/client/starlette.html)：协议复用，不证明本产品授权矩阵。
- [S3 条件写](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)、[OpenTelemetry traces](https://opentelemetry.io/docs/concepts/signals/traces/)、[Playwright 实践](https://playwright.dev/docs/best-practices)、[Vite PWA](https://vite-pwa-org.netlify.app/guide/)：窄边界复用。

## 必须用 PPAP 验证的实验

文档候选以同一授权样本和审阅 oracle 比较逐规则结果、证据定位、时间/内存、失败模式、分发许可。数据恢复实验比较进程终止、重复受理、过期 claim、外部调用结果未知，不用“最终跑成功一次”代替恢复证明。

技术选择必须有可重复命令、输入身份、旧/新结果与差异判断。没有这些证据时保持候选状态；不要为了路线图看起来确定而杜撰 benchmark。

## 文档系统来源

参考 [Grok architecture](https://github.com/Harness-X-Harness/codex/blob/grok/rust-v0.154.0/grok/docs/architecture.md) 与 [AGENTS 的 Grok 部分](https://github.com/Harness-X-Harness/codex/blob/grok/rust-v0.154.0/AGENTS.md)：Northstar、行为证明、发布契约分工，直接导航到事实拥有者，不复制其 Rust 目录。

工作方法参考 [context-reduce](https://github.com/ronhuafeng/skills-release/tree/main/catalog/engineering/context-reduce)、[plan-issue-roadmap](https://github.com/ronhuafeng/skills-release/tree/main/catalog/engineering/plan-issue-roadmap)、[live-story-review](https://github.com/ronhuafeng/skills-release/tree/main/catalog/engineering/live-story-review)、[live-gate-review](https://github.com/ronhuafeng/skills-release/tree/main/catalog/engineering/live-gate-review)。本轮已阅读；它们不是产品 runtime 依赖。
